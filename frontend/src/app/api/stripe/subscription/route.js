import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import {
  getUserSubscription,
  getCurrentPeriodUsage,
  getUserLifetimeProjectCount,
  effectivePlanKey,
  PLANS,
  CREDIT_PACKS,
  CREDIT_PACK_ELIGIBLE_PLANS,
} from '@/lib/subscription';

// ─────────────────────────────────────────────────────────
// GET /api/stripe/subscription
// Returns the user's current plan, status, monthly usage,
// and credit-pack balance.
// ─────────────────────────────────────────────────────────
export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const sub = await getUserSubscription(ctx.userId);
  const planKey = effectivePlanKey(sub);
  const plan = PLANS[planKey] ?? PLANS.free;

  const [usage, lifetimeProjects] = await Promise.all([
    getCurrentPeriodUsage(ctx.userId),
    getUserLifetimeProjectCount(ctx.userId),
  ]);

  const tokensUsed = usage.inputTokens + usage.outputTokens;
  const isPaid = ['active', 'trialing'].includes(sub.status) && sub.plan !== 'free';

  return NextResponse.json({
    plan: planKey,
    rawPlan: sub.plan,
    status: sub.status,
    billingInterval: sub.billing_interval,
    currentPeriodEnd: sub.current_period_end,
    cancelAtPeriodEnd: sub.cancel_at_period_end,
    isPaid,

    limits: {
      maxProjectsPerMonth: plan.maxProjectsPerMonth === Infinity ? null : plan.maxProjectsPerMonth,
      monthlyTokenQuota:   plan.monthlyTokenQuota,
      canExport:           plan.canExport,
      canUseCustomDomain:  plan.canUseCustomDomain ?? false,
      prioritySupport:     plan.prioritySupport,
    },

    usage: {
      // Free plan tracks lifetime projects (1 ever); paid plans track this month.
      projectsCreated: planKey === 'free' ? lifetimeProjects : usage.projectsCreated,
      tokensUsed,
      inputTokens:  usage.inputTokens,
      outputTokens: usage.outputTokens,
    },

    extraTokenBalance: Number(sub.extra_token_balance ?? 0),

    canBuyCreditPacks: CREDIT_PACK_ELIGIBLE_PLANS.has(planKey),

    catalog: {
      plans: Object.values(PLANS).map((p) => ({
        key: p.key,
        name: p.name,
        monthlyPriceCents: p.monthlyPriceCents,
        yearlyPriceCents:  p.yearlyPriceCents,
        maxProjectsPerMonth: p.maxProjectsPerMonth === Infinity ? null : p.maxProjectsPerMonth,
        monthlyTokenQuota: p.monthlyTokenQuota,
        canExport: p.canExport,
        prioritySupport: p.prioritySupport,
      })),
      creditPacks: Object.values(CREDIT_PACKS).map((c) => ({
        key:   c.key,
        label: c.label,
        tokens:     c.tokens,
        priceCents: c.priceCents,
      })),
    },
  });
}
