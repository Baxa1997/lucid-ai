import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { canConsumeTokens } from '@/lib/subscription';
import { recordTokenUsage } from '@/lib/usage';

// ─────────────────────────────────────────────────────────
//  POST /api/recommend-stack
//  Given a project description, picks the best framework.
// ─────────────────────────────────────────────────────────

const ANTHROPIC_BASE = 'https://api.anthropic.com/v1/messages';

const SYSTEM_PROMPT = `You are a frontend framework advisor. Given a project description, pick the single best frontend framework from: Next.js, React, Vue.js.

Rules:
- Admin panel / dashboard → React (shadcn/ui + Recharts)
- Analytics / reporting tool → React
- CRM / ERP / project management → React
- Inventory / warehouse management → Vue.js (lightweight, fast iteration)
- Internal tools / back-office → Vue.js
- Logistics / fleet management → Vue.js
- E-commerce or marketplace → Next.js (SEO, SSR)
- Website or landing page → Next.js (SEO, SSR)
- Blog or content site → Next.js (SSG)
- SaaS marketing / product site → Next.js
- Portfolio → Next.js
- If description mentions "Vue" explicitly → Vue.js
- Default fallback → Next.js

Reply with ONLY valid JSON: {"framework":"<name>","reason":"<one short sentence why>"}
No markdown, no extra text.`;

export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  // bypassLimits is a testing escape hatch from the upgrade modal's
  // "Skip for testing" button — full LLM call still runs and tokens are
  // still recorded post-call, only the pre-call gate is skipped.
  if (body.bypassLimits) {
    console.warn(`[recommend-stack] bypassLimits=true for user=${ctx.userId} (testing override)`);
  } else {
    const tokenGate = await canConsumeTokens(ctx.userId);
    if (!tokenGate.allowed) {
      return NextResponse.json(
        { error: tokenGate.reason, upgradeRequired: true, limitType: 'token' },
        { status: 402 },
      );
    }
  }

  const { description } = body;
  if (!description?.trim()) {
    return NextResponse.json(
      { error: 'Description is required' },
      { status: 400 }
    );
  }

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
        model: 'claude-sonnet-4-6',
        max_tokens: 128,
        system: SYSTEM_PROMPT,
        messages: [{ role: 'user', content: description.trim() }],
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      console.error('[recommend-stack] Claude API error:', response.status, err);
      return NextResponse.json(
        { error: 'Failed to get recommendation' },
        { status: 502 }
      );
    }

    const data = await response.json();
    const raw = data.content?.[0]?.text || '';

    // Meter token consumption (fire-and-forget; never blocks the response)
    recordTokenUsage(
      ctx.userId,
      data.usage?.input_tokens ?? 0,
      data.usage?.output_tokens ?? 0,
    );

    // Parse the JSON response
    try {
      const parsed = JSON.parse(raw);
      // Normalize framework name → id
      const nameMap = {
        'next.js': 'nextjs',
        'nextjs': 'nextjs',
        'react': 'react',
        'vue': 'vue',
        'vue.js': 'vue',
        'vuejs': 'vue',
      };
      const stackId = nameMap[parsed.framework?.toLowerCase()] || 'nextjs';
      return NextResponse.json({
        stack: stackId,
        reason: parsed.reason || 'Best fit for your project',
      });
    } catch {
      // Fallback: try to extract framework name from raw text
      const lower = raw.toLowerCase();
      if (lower.includes('vue')) return NextResponse.json({ stack: 'vue', reason: 'Best fit for your project' });
      if (lower.includes('react') && !lower.includes('next')) return NextResponse.json({ stack: 'react', reason: 'Best fit for your project' });
      return NextResponse.json({ stack: 'nextjs', reason: 'Best fit for your project' });
    }
  } catch (err) {
    console.error('[recommend-stack] Network error:', err);
    return NextResponse.json(
      { error: 'Failed to reach Claude API' },
      { status: 502 }
    );
  }
}
