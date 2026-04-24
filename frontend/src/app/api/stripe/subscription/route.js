import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getUserSubscription, getUserProjectCount, PLAN_LIMITS } from '@/lib/subscription';

// GET /api/stripe/subscription
// Returns the user's current plan, status, and usage.
export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const [sub, projectCount] = await Promise.all([
    getUserSubscription(ctx.userId),
    getUserProjectCount(ctx.userId),
  ]);

  const limits = PLAN_LIMITS[sub.plan] ?? PLAN_LIMITS.free;
  const isPaid = ['active', 'trialing'].includes(sub.status) && sub.plan !== 'free';

  return NextResponse.json({
    plan: sub.plan,
    status: sub.status,
    billingInterval: sub.billing_interval,
    currentPeriodEnd: sub.current_period_end,
    cancelAtPeriodEnd: sub.cancel_at_period_end,
    isPaid,
    limits: {
      maxProjects: limits.maxProjects === Infinity ? null : limits.maxProjects,
      canExport: limits.canExport,
    },
    usage: {
      projectsCreated: projectCount,
    },
  });
}
