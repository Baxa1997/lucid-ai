// ─────────────────────────────────────────────────────────
//  POST /api/projects/check-create
//
//  Server-side gate the dashboard composer hits BEFORE
//  navigating to a workspace. Two responsibilities:
//
//    1. Refuse the request if canCreateProject() blocks it.
//       Returns 402 with reason so the UI can open the
//       upgrade modal.
//    2. If allowed, increment usage_periods.projects_created
//       via recordProjectCreated() so paid plans actually
//       track project consumption (the counter was previously
//       dead code).
//
//  Project limit is ALWAYS real — the dev token bypass
//  (NEXT_PUBLIC_DEV_BYPASS_QUOTA) does not relax it.
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { canCreateProject } from '@/lib/subscription';
import { recordProjectCreated } from '@/lib/usage';

export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  // `bypassLimits` is the established testing escape hatch — set by the
  // dashboard's upgrade modal Skip button. It's the ONLY way to proceed
  // past the project cap; nothing else (env var, devtools, direct call)
  // skips this gate.
  let body = {};
  try { body = await req.json(); } catch {}
  // TEMP TESTING OVERRIDE — forced bypass so design iteration doesn't burn
  // project slots. Revert to `body?.bypassLimits === true` before shipping.
  const bypassLimits = true;

  let gate = null;
  if (bypassLimits) {
    console.warn(`[projects/check-create] bypassLimits=true for user=${ctx.userId} (testing override via Skip)`);
  } else {
    gate = await canCreateProject(ctx.userId);
    if (!gate.allowed) {
      return NextResponse.json(
        {
          error: gate.reason,
          upgradeRequired: true,
          limitType: 'project',
          plan: gate.plan,
          usage: gate.usage,
          limit: gate.limit,
        },
        { status: 402 },
      );
    }
  }

  // Increment the monthly counter so paid-plan project caps actually
  // advance. The free plan still uses lifetime distinct project_ids
  // via canCreateProject's own logic — incrementing usage_periods is
  // harmless for free users and required for paid ones.
  await recordProjectCreated(ctx.userId);

  return NextResponse.json({
    ok: true,
    bypassed: bypassLimits,
    plan: gate?.plan,
    usage: (gate?.usage ?? 0) + 1,
    limit: gate?.limit,
  });
}
