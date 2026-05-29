// ─────────────────────────────────────────────────────────
//  POST /api/intent-check
//
//  Reads the user's latest composer message + recent conversation
//  history, asks Gemini (mode='new' for first-time project description,
//  mode='edit' for in-workspace change requests) to decide whether to
//  proceed or ask a clarifying question.
//
//  AUTH: Vertex AI + ADC (Application Default Credentials). This matches
//  the rest of the platform (see ai_engine/app/services/gemini_http.py).
//  The old AI-Studio path (generativelanguage.googleapis.com +
//  GEMINI_API_KEY) is gone — it kept breaking when keys expired and
//  doesn't share IAM with the ai_engine. ADC works locally via
//  `gcloud auth application-default login` and in prod via service
//  account JSON / Workload Identity.
//
//  Three outcomes:
//
//   • isProject:true  + summary  → dashboard navigates to the
//     workspace, passing `summary` as the project description.
//   • isProject:false + reply    → dashboard appends `reply` to the
//     mini-chat history and waits for the user to type again.
//   • API/auth error             → fail CLOSED: return isProject:false
//     with a retry message so junk / unverified prompts never reach
//     the build pipeline.
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import { GoogleAuth } from 'google-auth-library';
import { requireAuth } from '@/lib/gatekeeper';
import { canConsumeTokens } from '@/lib/subscription';
import { recordTokenUsage } from '@/lib/usage';
import { isTokenBypassActive } from '@/lib/devQuotaBypass';

const MODEL = 'gemini-3-flash-preview';

// ── ADC token cache (mirror ai_engine/gemini_http.py) ─────────
// Tokens are valid ~1h; refresh 5 minutes before expiry. A single
// in-process auth client + cached token avoids hitting the OAuth2
// endpoint on every request.
let _auth = null;
let _cachedToken = null;
let _cachedExpiry = 0;

function getAuthClient() {
  if (_auth) return _auth;
  _auth = new GoogleAuth({
    scopes: ['https://www.googleapis.com/auth/cloud-platform'],
  });
  return _auth;
}

async function getAdcToken() {
  const now = Date.now() / 1000;
  if (_cachedToken && now < _cachedExpiry - 300) return _cachedToken;
  const client = await getAuthClient().getClient();
  const { token } = await client.getAccessToken();
  if (!token) throw new Error('Empty ADC token');
  _cachedToken = token;
  // google-auth-library handles its own expiry internally — we mirror
  // a conservative 50min TTL here since the library doesn't surface it.
  _cachedExpiry = now + 3000;
  return token;
}

function buildVertexUrl(model) {
  const location = process.env.GOOGLE_CLOUD_LOCATION || 'global';
  const project = process.env.GOOGLE_CLOUD_PROJECT;
  if (!project) {
    throw new Error('GOOGLE_CLOUD_PROJECT env var is missing — Vertex auth requires a project');
  }
  const host = location === 'global'
    ? 'aiplatform.googleapis.com'
    : `${location}-aiplatform.googleapis.com`;
  return `https://${host}/v1/projects/${project}/locations/${location}/publishers/google/models/${model}:generateContent`;
}

const SYSTEM_PROMPT_NEW = `You are a warm, helpful clarifying assistant for Lucid AI — a platform that builds web apps and websites from natural-language descriptions.

Given the conversation so far, decide whether the user has expressed a CLEAR PROJECT INTENT.

══ WHAT COUNTS AS CLEAR ══
A project intent is CLEAR only when BOTH are known (either stated or strongly implied):
  1. PROJECT TYPE / STRUCTURE — landing page, full website, web app, portfolio, blog, store, dashboard, etc.
  2. FIELD / DOMAIN — what the project is FOR (the business, product, person, or topic). "landing page" on its own is NOT clear because a landing page for a restaurant looks nothing like a landing page for a SaaS tool — we MUST know the field before we can build.

If type is known but field is missing, ASK for the field. If field is known but type is ambiguous, ASK for the type. If both are missing, ask whichever question feels most natural.

DO NOT accept naked project types as clear: "landing page", "a website", "an app", "build me a site", "dashboard", "portfolio" — these are all MISSING the field. ALWAYS ask what they're for.

══ OUTPUT ══
If clear (type + field both known): isProject=true and a clean one-sentence summary that incorporates the full conversation context.

If unclear (anything missing — gibberish, too vague, type-only, field-only): isProject=false and a SHORT, FRIENDLY clarifying question. Be warm and natural — like a sharp designer scoping a project, not a robot.

Output ONLY a JSON object with no markdown, in this exact shape:
{
  "isProject": boolean,
  "reply": "friendly clarifying question — only when isProject is false. Maximum 30 words. Always end with a question.",
  "summary": "concise one-sentence project description incorporating all context — only when isProject is true."
}

══ EXAMPLES — CLEAR (isProject=true) ══
- "modern coffee shop landing page"
- "fitness coach portfolio with booking"
- "landing page for my bakery"
- "todo app with dark mode"
- "I want a website for my dental clinic"
- "CRM dashboard for sales reps"
- "Italian restaurant in Brooklyn"

══ EXAMPLES — UNCLEAR (isProject=false) ══
- "landing page" → ask: "Got it — what's this landing page for? A business, product, or person?"
- "a website" → ask: "Sure! What's the website for?"
- "build me a site" → ask: "What kind of site — what business or topic?"
- "dashboard" → ask: "What kind of dashboard, and for what data or workflow?"
- "portfolio" → ask: "What kind of portfolio — a designer, photographer, developer?"
- "I need an app" → ask what the app does
- "build something cool" → too vague
- "qwerty asdf", "asdasd" → random characters
- "hello", "hi" → greeting, no project info
- "how are you" → social, no project info

Always end clarifying replies with a real question. Keep replies under 30 words.`;

const SYSTEM_PROMPT_EDIT = `You are a warm, helpful clarifying assistant for Lucid AI. The user is currently inside an EXISTING project workspace and types a message to modify the app.

Decide whether the user's message is an ACTIONABLE CHANGE REQUEST for the app (something a developer could act on — e.g., "make the hero bigger", "add a contact form", "change the theme to dark mode") OR not (greeting, gibberish, social chat, completely vague).

If actionable: respond with isProject=true and pass the original request through as summary (you may lightly clean it up but keep the user's intent).

If NOT actionable: respond with isProject=false and a SHORT, FRIENDLY clarifying question asking what change they'd like. Be warm and natural — they're already in their project, so ask about edits, not new projects.

Output ONLY a JSON object with no markdown, no commentary, in this exact shape:
{
  "isProject": boolean,
  "reply": "friendly clarifying question about what to change — only when isProject is false. Maximum 30 words. End with a question.",
  "summary": "the actionable change request, lightly cleaned — only when isProject is true."
}

Examples that ARE actionable edits:
- "make the hero blue"
- "add a footer with social links"
- "use a darker color scheme"
- "the buttons look weird, can you fix them"
- "shorten the headline"

Examples that need clarification:
- "how are you" → social greeting
- "asdasdas" → gibberish
- "hi there" → no edit info
- "do something" → too vague — ask what
- "make it better" → ask what specifically

Always end clarifying replies with a real question. Keep replies under 30 words.`;

function failClosed(reason) {
  // Helper for every fail path. The dashboard treats this exactly like
  // a real Gemini "needs clarification" response and shows the retry
  // message in the mini-chat. The workspace launches NEVER happen on
  // this path. Wording is kept in sync with useAgentSession.js's legacy
  // rescue text so backend-rejected and frontend-rejected gibberish
  // read identically.
  if (reason) console.error('[intent-check] fail-closed:', reason);
  return NextResponse.json({
    isProject: false,
    reply: "I couldn't quite read that. Could you describe what you'd like to build or change? For example: \"a landing page for my coffee shop\" or \"make the hero darker\".",
    verifierUnavailable: true,
  });
}

export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try { body = await req.json(); } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const prompt = (body.prompt ?? '').trim();
  const history = Array.isArray(body.history) ? body.history.slice(-10) : [];
  const mode = body.mode === 'edit' ? 'edit' : 'new';
  const systemPrompt = mode === 'edit' ? SYSTEM_PROMPT_EDIT : SYSTEM_PROMPT_NEW;

  if (!prompt) {
    return NextResponse.json({
      isProject: false,
      reply: mode === 'edit'
        ? "What would you like to change?"
        : 'Hey! What would you like to build today?',
    });
  }

  // Token gate — honored unless the local dev bypass is active.
  if (!isTokenBypassActive()) {
    const tokenGate = await canConsumeTokens(ctx.userId);
    if (!tokenGate.allowed) {
      return NextResponse.json(
        { error: tokenGate.reason, upgradeRequired: true, limitType: 'token' },
        { status: 402 },
      );
    }
  }

  // Resolve Vertex URL + ADC token. Either missing project or ADC failure
  // is treated as a verifier outage — fail closed so junk never reaches
  // the workspace launch path.
  let url;
  let token;
  try {
    url = buildVertexUrl(MODEL);
    token = await getAdcToken();
  } catch (err) {
    return failClosed(`vertex auth: ${err.message}`);
  }

  // Multi-turn contents: prior turns + the latest user message.
  const contents = [];
  for (const m of history) {
    if (!m || !m.role || !m.content) continue;
    const role = m.role === 'assistant' ? 'model' : 'user';
    contents.push({ role, parts: [{ text: String(m.content) }] });
  }
  contents.push({ role: 'user', parts: [{ text: prompt }] });

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: systemPrompt }] },
        contents,
        generationConfig: {
          temperature: 0.6,
          // gemini-3-flash-preview uses internal reasoning tokens that
          // count against maxOutputTokens. Empirically: ~300 thinking +
          // ~100 output for our structured-JSON reply. A 200 cap was
          // burning the entire budget on thinking and emitting zero
          // text (finishReason=MAX_TOKENS, empty content). 1500 leaves
          // generous headroom while staying well under the model's hard
          // limit, and the actual billed token count stays low for
          // short prompts.
          maxOutputTokens: 1500,
          responseMimeType: 'application/json',
        },
      }),
    });

    if (!res.ok) {
      const t = await res.text();
      return failClosed(`Vertex ${res.status}: ${t.slice(0, 300)}`);
    }
    const data = await res.json();

    // Record token usage (fire-and-forget; never blocks the response).
    const usage = data?.usageMetadata || {};
    recordTokenUsage(
      ctx.userId,
      usage.promptTokenCount ?? 0,
      usage.candidatesTokenCount ?? 0,
    );

    const text = data?.candidates?.[0]?.content?.parts?.[0]?.text || '';
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      return failClosed(`non-JSON model output: ${text.slice(0, 200)}`);
    }

    if (parsed.isProject) {
      const summary = String(parsed.summary || prompt).trim();
      return NextResponse.json({ isProject: true, summary });
    }
    return NextResponse.json({
      isProject: false,
      reply: String(
        parsed.reply || "Could you tell me a bit more about what you'd like to build?",
      ).trim(),
    });
  } catch (err) {
    return failClosed(`network: ${err.message}`);
  }
}
