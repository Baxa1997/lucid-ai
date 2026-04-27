// ─────────────────────────────────────────────────────────
//  Server-side subscription + usage helpers (Next.js API routes only)
//
//  Plan model (base44-style, monthly billing only):
//    free       — 1 project,  100k tokens / month
//    starter    — 5 projects, 1M   tokens / month  ($19/mo)
//    pro        — 20 projects,5M   tokens / month  ($59/mo)
//    enterprise — ∞ projects, 15M  tokens / month  ($299/mo)
//
//  Quotas are MONTHLY and reset on the 1st of each calendar month
//  (UTC) — independent of Stripe billing interval.
//
//  When a user blows past the monthly token quota, they can buy
//  one-time credit packs that top up `subscriptions.extra_token_balance`.
//  Extra tokens DO NOT expire and DO NOT reset.
// ─────────────────────────────────────────────────────────

import { getSupabaseServerClient } from '@/lib/supabase/server';

// ── Plan definitions ─────────────────────────────────────
export const PLANS = {
  free: {
    key: 'free',
    name: 'Free',
    monthlyPriceCents: 0,
    yearlyPriceCents: 0,
    maxProjectsPerMonth: 1,
    monthlyTokenQuota: 100_000,
    maxPublishedProjects: 2,
    canExport: false,
    canUseCustomTemplates: false,
    canUseCustomDomain: false,
    prioritySupport: false,
  },
  starter: {
    key: 'starter',
    name: 'Starter',
    monthlyPriceCents: 1900,
    yearlyPriceCents: null, // monthly only
    maxProjectsPerMonth: 5,
    monthlyTokenQuota: 1_000_000,
    maxPublishedProjects: Infinity,
    canExport: false,
    canUseCustomTemplates: true,
    canUseCustomDomain: true,
    prioritySupport: false,
  },
  pro: {
    key: 'pro',
    name: 'Pro',
    monthlyPriceCents: 5900,
    yearlyPriceCents: null, // monthly only for now
    maxProjectsPerMonth: 20,
    monthlyTokenQuota: 5_000_000,
    maxPublishedProjects: Infinity,
    canExport: true,
    canUseCustomTemplates: true,
    canUseCustomDomain: true,
    prioritySupport: false,
  },
  enterprise: {
    key: 'enterprise',
    name: 'Enterprise',
    monthlyPriceCents: 29900,
    yearlyPriceCents: null,
    maxProjectsPerMonth: Infinity,
    monthlyTokenQuota: 15_000_000,
    maxPublishedProjects: Infinity,
    canExport: true,
    canUseCustomTemplates: true,
    canUseCustomDomain: true,
    prioritySupport: true,
  },
};

// ── Credit packs (one-time top-ups) ──────────────────────
// Sized so each pack stays profitable at Sonnet 4.6 prices even
// in worst-case (output-heavy) usage. Extras cost more per token
// than included plan tokens — extras don't have plan-fee subsidy.
//
// Pro/Enterprise only — gated server-side in /api/stripe/checkout
// and surfaced via canBuyCreditPacks in /api/stripe/subscription.
export const CREDIT_PACKS = {
  small:  { key: 'small',  tokens:   300_000, priceCents:  500, label: 'Starter pack' },
  medium: { key: 'medium', tokens: 1_500_000, priceCents: 2000, label: 'Builder pack' },
  large:  { key: 'large',  tokens: 4_000_000, priceCents: 5000, label: 'Power pack'   },
};

/** Plans allowed to buy one-time credit packs. */
export const CREDIT_PACK_ELIGIBLE_PLANS = new Set(['pro', 'enterprise']);

// ── Period helpers ───────────────────────────────────────
/** First day of the current calendar month (UTC), as 'YYYY-MM-DD'. */
export function currentPeriodStart(now = new Date()) {
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, '0');
  return `${y}-${m}-01`;
}

// ── Subscription fetch ───────────────────────────────────
/**
 * Returns the user's subscription row, synthesizing a 'free' row if none exists.
 */
export async function getUserSubscription(userId) {
  const supabase = await getSupabaseServerClient();
  const { data } = await supabase
    .from('subscriptions')
    .select('*')
    .eq('user_id', userId)
    .maybeSingle();

  return data ?? {
    plan: 'free',
    status: 'active',
    user_id: userId,
    extra_token_balance: 0,
  };
}

export function isActivePlan(subscription) {
  if (!subscription) return false;
  if (subscription.plan === 'free') return false;
  return ['active', 'trialing'].includes(subscription.status);
}

/** Effective plan key: paid plans only count if status is active/trialing. */
export function effectivePlanKey(subscription) {
  if (!subscription) return 'free';
  if (subscription.plan === 'free') return 'free';
  return isActivePlan(subscription) ? subscription.plan : 'free';
}

// ── Usage fetch ──────────────────────────────────────────
/**
 * Returns this calendar month's usage row for the user, or zeros.
 */
export async function getCurrentPeriodUsage(userId) {
  const supabase = await getSupabaseServerClient();
  const periodStart = currentPeriodStart();
  const { data } = await supabase
    .from('usage_periods')
    .select('input_tokens, output_tokens, projects_created')
    .eq('user_id', userId)
    .eq('period_start', periodStart)
    .maybeSingle();

  return {
    inputTokens:     Number(data?.input_tokens     ?? 0),
    outputTokens:    Number(data?.output_tokens    ?? 0),
    projectsCreated: Number(data?.projects_created ?? 0),
  };
}

/**
 * Lifetime project count (distinct project_id in chat_sessions).
 * Used as a fallback for the legacy "1 free project ever" semantics on the free
 * plan — once a free user has created their lifetime project they cannot create
 * another, even in a new month.
 */
export async function getUserLifetimeProjectCount(userId) {
  const supabase = await getSupabaseServerClient();
  const { data } = await supabase
    .from('chat_sessions')
    .select('project_id')
    .eq('user_id', userId);

  if (!data) return 0;
  const unique = new Set(data.map((r) => r.project_id).filter(Boolean));
  return unique.size;
}

// ── Gate: project creation ───────────────────────────────
/**
 * Free plan: 1 lifetime project (matches the "1 free project per user" rule
 * the upgrade modal advertises).
 * Paid plans: N projects per calendar month.
 */
export async function canCreateProject(userId) {
  const sub = await getUserSubscription(userId);
  const planKey = effectivePlanKey(sub);
  const plan = PLANS[planKey] ?? PLANS.free;

  if (planKey === 'free') {
    const lifetime = await getUserLifetimeProjectCount(userId);
    if (lifetime >= plan.maxProjectsPerMonth) {
      return {
        allowed: false,
        reason: 'Free plan includes 1 project. Upgrade to create more.',
        plan: planKey,
        limitType: 'project',
        usage:  lifetime,
        limit:  plan.maxProjectsPerMonth,
      };
    }
    return { allowed: true, plan: planKey, limitType: 'project', usage: lifetime, limit: plan.maxProjectsPerMonth };
  }

  if (plan.maxProjectsPerMonth === Infinity) {
    return { allowed: true, plan: planKey, limitType: 'project', usage: 0, limit: null };
  }

  const usage = await getCurrentPeriodUsage(userId);
  if (usage.projectsCreated >= plan.maxProjectsPerMonth) {
    return {
      allowed: false,
      reason: `${plan.name} plan includes ${plan.maxProjectsPerMonth} projects per month. Upgrade for more.`,
      plan: planKey,
      limitType: 'project',
      usage:  usage.projectsCreated,
      limit:  plan.maxProjectsPerMonth,
    };
  }
  return { allowed: true, plan: planKey, limitType: 'project', usage: usage.projectsCreated, limit: plan.maxProjectsPerMonth };
}

// ── Gate: token consumption (called BEFORE an LLM call) ──
/**
 * Returns { allowed, reason?, ... }.
 * Allows the call if monthly quota OR extra_token_balance has room.
 * The actual usage gets recorded post-call in recordTokenUsage().
 */
export async function canConsumeTokens(userId, estimatedTokens = 0) {
  const sub = await getUserSubscription(userId);
  const planKey = effectivePlanKey(sub);
  const plan = PLANS[planKey] ?? PLANS.free;

  const usage = await getCurrentPeriodUsage(userId);
  const used  = usage.inputTokens + usage.outputTokens;
  const remainingQuota = Math.max(0, plan.monthlyTokenQuota - used);
  const extraBalance   = Number(sub.extra_token_balance ?? 0);
  const totalAvailable = remainingQuota + extraBalance;

  if (estimatedTokens > 0 && totalAvailable < estimatedTokens) {
    return {
      allowed: false,
      reason: `Monthly token quota exhausted. Upgrade your plan or buy a credit pack.`,
      plan: planKey,
      limitType: 'token',
      tokensUsed: used,
      tokensIncluded: plan.monthlyTokenQuota,
      extraTokensRemaining: extraBalance,
    };
  }

  // Even without an estimate, block when both buckets are empty
  if (totalAvailable <= 0) {
    return {
      allowed: false,
      reason: `Monthly token quota exhausted. Upgrade your plan or buy a credit pack.`,
      plan: planKey,
      limitType: 'token',
      tokensUsed: used,
      tokensIncluded: plan.monthlyTokenQuota,
      extraTokensRemaining: extraBalance,
    };
  }

  return {
    allowed: true,
    plan: planKey,
    limitType: 'token',
    tokensUsed: used,
    tokensIncluded: plan.monthlyTokenQuota,
    extraTokensRemaining: extraBalance,
  };
}

// ── Gate: credit pack purchase (Pro+ only) ───────────────
export async function canBuyCreditPack(userId) {
  const sub = await getUserSubscription(userId);
  const planKey = effectivePlanKey(sub);
  if (CREDIT_PACK_ELIGIBLE_PLANS.has(planKey)) {
    return { allowed: true, plan: planKey };
  }
  return {
    allowed: false,
    reason: 'Credit packs are available on Pro and Enterprise plans. Upgrade to top up your tokens.',
    plan: planKey,
  };
}

// ── Gate: publish project to Vercel ──────────────────────
// Free plan = 2 lifetime published projects, Starter+ = unlimited.
// `projectId` lets republishes through even if the user is at the cap.
export async function canPublishProject(userId, projectId) {
  const sub = await getUserSubscription(userId);
  const planKey = effectivePlanKey(sub);
  const plan = PLANS[planKey] ?? PLANS.free;

  if (!Number.isFinite(plan.maxPublishedProjects)) {
    return { allowed: true, plan: planKey, limit: Infinity };
  }

  const supabase = await getSupabaseServerClient();
  const { data, error } = await supabase
    .from('project_deployments')
    .select('project_id')
    .eq('user_id', userId)
    .eq('status', 'deployed');

  if (error) {
    console.warn('[canPublishProject] count failed:', error.message);
    return { allowed: true, plan: planKey, limit: plan.maxPublishedProjects };
  }

  const distinct = new Set((data || []).map((r) => r.project_id).filter(Boolean));
  const count = distinct.size;
  const isRepublish = !!projectId && distinct.has(projectId);

  if (isRepublish) {
    return { allowed: true, plan: planKey, limit: plan.maxPublishedProjects, count };
  }
  if (count >= plan.maxPublishedProjects) {
    return {
      allowed: false,
      reason: `You've published ${count}/${plan.maxPublishedProjects} projects on the ${plan.name} plan. Upgrade to Starter for unlimited published projects.`,
      plan: planKey,
      limit: plan.maxPublishedProjects,
      count,
    };
  }
  return { allowed: true, plan: planKey, limit: plan.maxPublishedProjects, count };
}

// ── Gate: code export ────────────────────────────────────
export async function canExportCode(userId) {
  const sub = await getUserSubscription(userId);
  const planKey = effectivePlanKey(sub);
  const plan = PLANS[planKey] ?? PLANS.free;

  if (plan.canExport) return { allowed: true, plan: planKey };
  return {
    allowed: false,
    reason: 'Code export is available on Pro and Enterprise plans.',
    plan: planKey,
  };
}

// ── Back-compat: legacy export some routes still import ──
// The old PLAN_LIMITS shape — kept so existing imports don't break.
export const PLAN_LIMITS = Object.fromEntries(
  Object.values(PLANS).map((p) => [
    p.key,
    {
      maxProjects:        p.maxProjectsPerMonth,
      monthlyTokenQuota:  p.monthlyTokenQuota,
      canExport:          p.canExport,
    },
  ])
);

/** Legacy alias kept for the admin stats route. */
export async function getUserProjectCount(userId) {
  return getUserLifetimeProjectCount(userId);
}
