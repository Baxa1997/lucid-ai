import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { createClient } from '@supabase/supabase-js';

const ADMIN_EMAILS = (process.env.ADMIN_EMAILS || '').split(',').map((e) => e.trim().toLowerCase()).filter(Boolean);

function adminClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY,
  );
}

// GET /api/admin/stats
export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const userEmail = ctx.user.email?.toLowerCase();
  if (!ADMIN_EMAILS.includes(userEmail)) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  }

  const supabase = adminClient();

  const [
    { count: totalUsers },
    { data: subscriptions },
    { data: recentUsers },
    { count: totalProjects },
    { data: recentSessions },
  ] = await Promise.all([
    supabase.from('users').select('*', { count: 'exact', head: true }),
    supabase.from('subscriptions').select('plan, status, billing_interval, created_at'),
    supabase.from('users').select('id, email, name, avatar_url, created_at').order('created_at', { ascending: false }).limit(20),
    supabase.from('chat_sessions').select('*', { count: 'exact', head: true }),
    supabase.from('chat_sessions').select('user_id, project_id, title, created_at').order('created_at', { ascending: false }).limit(10),
  ]);

  const planCounts = { free: 0, pro: 0, enterprise: 0 };
  const activePaid = (subscriptions || []).filter(
    (s) => ['active', 'trialing'].includes(s.status) && s.plan !== 'free'
  );
  (subscriptions || []).forEach((s) => {
    const key = ['pro', 'enterprise'].includes(s.plan) && ['active', 'trialing'].includes(s.status)
      ? s.plan
      : 'free';
    planCounts[key] = (planCounts[key] || 0) + 1;
  });
  planCounts.free = (totalUsers || 0) - planCounts.pro - planCounts.enterprise;

  const monthlyRevenue = activePaid.reduce((sum, s) => {
    const amount = s.plan === 'pro'
      ? (s.billing_interval === 'yearly' ? 24 : 30)
      : (s.billing_interval === 'yearly' ? 96 : 120);
    return sum + amount;
  }, 0);

  // Signups per day for the last 14 days
  const now = Date.now();
  const signupsByDay = {};
  for (let i = 13; i >= 0; i--) {
    const d = new Date(now - i * 86400000).toISOString().slice(0, 10);
    signupsByDay[d] = 0;
  }
  (recentUsers || []).forEach((u) => {
    const d = u.created_at?.slice(0, 10);
    if (d && d in signupsByDay) signupsByDay[d]++;
  });

  return NextResponse.json({
    totalUsers: totalUsers || 0,
    totalProjects: totalProjects || 0,
    planCounts,
    activePaidCount: activePaid.length,
    monthlyRevenue,
    recentUsers: recentUsers || [],
    recentSessions: recentSessions || [],
    signupsByDay,
  });
}
