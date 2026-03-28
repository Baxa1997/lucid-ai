import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { extractDesignTokens } from '@/lib/figma';

// ─────────────────────────────────────────────────────────
//  POST /api/enhance-prompt
//  Takes wizard selections → calls Claude → returns a
//  detailed technical spec for OpenHands.
//
//  If figmaUrl is provided, extracts design tokens from
//  Figma and appends them to the enhanced prompt.
// ─────────────────────────────────────────────────────────

const ANTHROPIC_BASE = 'https://api.anthropic.com/v1/messages';

const SYSTEM_PROMPT = `You are a senior software architect. Given a short project description and tech stack, generate a detailed technical specification for an AI coding agent to build. Include: pages and routes, components needed, data models, auth requirements, API endpoints, and UI style direction. Be specific and actionable. Output plain text only.`;

export async function POST(req) {
  // ── Auth guard ──────────────────────────────────────────
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;

  // ── Parse body ──────────────────────────────────────────
  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const { stack, backend, description, figmaUrl } = body;

  if (!description || !description.trim()) {
    return NextResponse.json(
      { error: 'Description is required' },
      { status: 400 }
    );
  }

  // ── Extract Figma design tokens (non-blocking) ─────────
  let designTokens = null;
  if (figmaUrl && figmaUrl.trim()) {
    try {
      designTokens = await extractDesignTokens(figmaUrl.trim());
      if (designTokens) {
        console.log('[enhance-prompt] Figma tokens extracted successfully');
      }
    } catch (err) {
      console.error('[enhance-prompt] Figma extraction failed (falling back to AI-generated UI):', err.message);
      // Silent fallback — do not block the build
    }
  }

  // ── Build user message ─────────────────────────────────
  const stackLabel = {
    'html-css': 'HTML & CSS (no framework, no build tools, plain static files)',
    nextjs: 'Next.js',
    react: 'React (Vite)',
    vue: 'Vue.js',
    angular: 'Angular',
    auto: 'Auto-select best stack',
  }[stack] || stack || 'Not specified';

  const backendLabel = {
    none: 'No backend (frontend only)',
    supabase: 'Supabase (managed)',
    own: 'Custom backend (MCP)',
  }[backend] || backend || 'Not specified';

  const userMessage = `Project type: Web application. Stack: ${stackLabel}. Backend: ${backendLabel}. Description: ${description.trim()}`;

  // ── Call Claude ─────────────────────────────────────────
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: 'ANTHROPIC_API_KEY is not configured' },
      { status: 500 }
    );
  }

  try {
    const response = await fetch(ANTHROPIC_BASE, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 4096,
        system: SYSTEM_PROMPT,
        messages: [{ role: 'user', content: userMessage }],
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      console.error('[enhance-prompt] Claude API error:', response.status, err);
      return NextResponse.json(
        { error: 'Failed to generate enhanced prompt' },
        { status: 502 }
      );
    }

    const data = await response.json();
    let enhancedPrompt =
      data.content?.[0]?.text || 'Failed to parse Claude response';

    // ── Append Figma design system if available ────────────
    if (designTokens) {
      const tokenJson = JSON.stringify(designTokens, null, 2);
      enhancedPrompt += `\n\n---\n\nDesign system from Figma: ${tokenJson}\n\nIMPORTANT: Use the above design system for ALL styling decisions. Apply the exact colors, fonts, and spacing values — do not invent new colors, font families, or spacing. Reference the component names and layout structure when building the UI. These values are extracted from the client's Figma design and must be followed precisely.`;
    }

    return NextResponse.json({
      enhancedPrompt,
      hasDesignTokens: !!designTokens,
    });
  } catch (err) {
    console.error('[enhance-prompt] Network error:', err);
    return NextResponse.json(
      { error: 'Failed to reach Claude API' },
      { status: 502 }
    );
  }
}
