import { NextResponse } from 'next/server';
import Stripe from 'stripe';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, {
  apiVersion: '2024-06-20',
});

const PRICE_IDS = {
  pro_monthly:    process.env.STRIPE_PRO_MONTHLY_PRICE_ID,
  pro_yearly:     process.env.STRIPE_PRO_YEARLY_PRICE_ID,
};

// POST /api/stripe/checkout
// Body: { plan: 'pro', interval: 'monthly' | 'yearly' }
export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try { body = await req.json(); } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const { plan = 'pro', interval = 'monthly' } = body;
  const priceKey = `${plan}_${interval}`;
  const priceId = PRICE_IDS[priceKey];

  if (!priceId) {
    return NextResponse.json({ error: `Unknown plan/interval: ${priceKey}` }, { status: 400 });
  }

  const supabase = await getSupabaseServerClient();

  // Re-use existing Stripe customer if one exists
  let stripeCustomerId;
  const { data: sub } = await supabase
    .from('subscriptions')
    .select('stripe_customer_id')
    .eq('user_id', ctx.userId)
    .maybeSingle();

  stripeCustomerId = sub?.stripe_customer_id;

  if (!stripeCustomerId) {
    const customer = await stripe.customers.create({
      email: ctx.user.email,
      metadata: { supabase_user_id: ctx.userId },
    });
    stripeCustomerId = customer.id;

    // Persist customer_id immediately so we can match webhooks
    await supabase.from('subscriptions').upsert(
      { user_id: ctx.userId, stripe_customer_id: stripeCustomerId, plan: 'free', status: 'active' },
      { onConflict: 'user_id' }
    );
  }

  const appUrl = process.env.NEXT_PUBLIC_APP_URL || 'http://localhost:3000';

  const session = await stripe.checkout.sessions.create({
    customer: stripeCustomerId,
    mode: 'subscription',
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: `${appUrl}/dashboard/engineer/billing?success=1`,
    cancel_url:  `${appUrl}/dashboard/engineer/billing?canceled=1`,
    allow_promotion_codes: true,
    subscription_data: {
      metadata: { supabase_user_id: ctx.userId },
    },
  });

  return NextResponse.json({ url: session.url });
}
