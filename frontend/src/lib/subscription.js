// ─────────────────────────────────────────────────────────
//  Server-side subscription helpers (Next.js API routes only)
// ─────────────────────────────────────────────────────────

import { getSupabaseServerClient } from '@/lib/supabase/server';

export const PLAN_LIMITS = {
  free:       { maxProjects: 1,        canExport: false },
  pro:        { maxProjects: Infinity, canExport: true  },
  enterprise: { maxProjects: Infinity, canExport: true  },
};

/**
 * Fetch the user's subscription row. Returns a synthetic 'free' row
 * when no subscription exists yet.
 */
export async function getUserSubscription(userId) {
  const supabase = await getSupabaseServerClient();
  const { data } = await supabase
    .from('subscriptions')
    .select('*')
    .eq('user_id', userId)
    .maybeSingle();

  return data ?? { plan: 'free', status: 'active', user_id: userId };
}

/**
 * Returns true if the subscription is considered active/paid.
 * 'trialing' is treated as paid (full access).
 */
export function isActivePlan(subscription) {
  if (!subscription) return false;
  if (subscription.plan === 'free') return false;
  return ['active', 'trialing'].includes(subscription.status);
}

/**
 * Count distinct projects the user has created (by counting distinct
 * project_ids in chat_sessions — the canonical source of truth).
 */
export async function getUserProjectCount(userId) {
  const supabase = await getSupabaseServerClient();
  const { data, error } = await supabase
    .from('chat_sessions')
    .select('project_id')
    .eq('user_id', userId);

  if (error || !data) return 0;

  const unique = new Set(data.map((r) => r.project_id).filter(Boolean));
  return unique.size;
}

/**
 * Returns { allowed: boolean, reason?: string } for creating a new project.
 */
export async function canCreateProject(userId) {
  const sub = await getUserSubscription(userId);
  if (isActivePlan(sub)) return { allowed: true };

  const count = await getUserProjectCount(userId);
  const limit = PLAN_LIMITS[sub.plan]?.maxProjects ?? 1;

  if (count >= limit) {
    return {
      allowed: false,
      reason: `Free plan allows ${limit} project. Upgrade to Pro for unlimited projects.`,
      projectCount: count,
      limit,
      plan: sub.plan,
    };
  }
  return { allowed: true, projectCount: count, limit, plan: sub.plan };
}

/**
 * Returns { allowed: boolean, reason?: string } for code export.
 */
export async function canExportCode(userId) {
  const sub = await getUserSubscription(userId);
  if (PLAN_LIMITS[sub.plan]?.canExport || isActivePlan(sub)) {
    return { allowed: true };
  }
  return {
    allowed: false,
    reason: 'Code export is available on the Pro plan. Upgrade to unlock.',
    plan: sub.plan,
  };
}
