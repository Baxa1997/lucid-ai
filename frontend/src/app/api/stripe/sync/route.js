import { NextResponse } from 'next/server';
import Stripe from 'stripe';
import { createClient } from '@supabase/supabase-js';
import { requireAuth } from '@/lib/gatekeeper';

// ─────────────────────────────────────────────────────────
// POST /api/stripe/sync
//
// Pull the user's latest subscription state from Stripe and upsert it
// into our `subscriptions` table. This is the safety-net for cases
// where the webhook is not delivered (local dev without `stripe listen`,
// or a transient webhook failure in production).
//
// The billing page calls this after returning from Stripe Checkout with
// `?success=1` so the new plan reflects immediately, without waiting
// for the webhook round-trip.
// ─────────────────────────────────────────────────────────

function adminClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY,
  );
}

// Mirror of the resolver in webhook/route.js — kept in sync intentionally.
function planFromPriceId(priceId) {
  if (!priceId) return 'free';
  if (priceId === process.env.STRIPE_STARTER_MONTHLY_PRICE_ID)    return 'starter';
  if (priceId === process.env.STRIPE_PRO_MONTHLY_PRICE_ID)        return 'pro';
  if (priceId === process.env.STRIPE_ENTERPRISE_MONTHLY_PRICE_ID) return 'enterprise';
  return 'pro';
}

function intervalFromSubscription(subscription) {
  const item = subscription.items?.data?.[0];
  return item?.price?.recurring?.interval === 'year' ? 'yearly' : 'monthly';
}

export async function POST() {
  try {
    if (!process.env.STRIPE_SECRET_KEY) {
      return NextResponse.json(
        { error: 'Billing is not configured (missing STRIPE_SECRET_KEY).' },
        { status: 503 },
      );
    }

    const authResult = await requireAuth();
    if (!authResult.ok) return authResult.response;
    const { ctx } = authResult;

    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, { apiVersion: '2024-06-20' });
    const supabase = adminClient();

    // Look up the user's stripe_customer_id (created lazily by /checkout)
    const { data: row } = await supabase
      .from('subscriptions')
      .select('stripe_customer_id')
      .eq('user_id', ctx.userId)
      .maybeSingle();

    const customerId = row?.stripe_customer_id;
    if (!customerId) {
      // Nothing to sync yet — user hasn't started checkout.
      return NextResponse.json({ ok: true, synced: false, plan: 'free' });
    }

    // Pull the most recent subscription for this customer. We don't restrict
    // by status — `incomplete` / `past_due` / `canceled` should also be
    // reflected so the UI shows accurate state.
    const list = await stripe.subscriptions.list({
      customer: customerId,
      status: 'all',
      limit: 1,
    });

    const subscription = list.data?.[0];
    if (!subscription) {
      // Customer exists but no subscription — leave the row at free.
      await supabase.from('subscriptions').upsert(
        {
          user_id: ctx.userId,
          stripe_customer_id: customerId,
          plan: 'free',
          status: 'active',
        },
        { onConflict: 'user_id' },
      );
      return NextResponse.json({ ok: true, synced: true, plan: 'free' });
    }

    const item = subscription.items?.data?.[0];
    const priceId = item?.price?.id;
    const plan = planFromPriceId(priceId);
    const interval = intervalFromSubscription(subscription);
    const periodEndUnix =
      item?.current_period_end ??
      subscription.current_period_end ??
      null;
    const currentPeriodEndIso = periodEndUnix
      ? new Date(periodEndUnix * 1000).toISOString()
      : null;

    await supabase.from('subscriptions').upsert(
      {
        user_id: ctx.userId,
        stripe_customer_id: customerId,
        stripe_subscription_id: subscription.id,
        plan,
        billing_interval: interval,
        status: subscription.status,
        current_period_end: currentPeriodEndIso,
        cancel_at_period_end: subscription.cancel_at_period_end ?? false,
      },
      { onConflict: 'user_id' },
    );

    return NextResponse.json({
      ok: true,
      synced: true,
      plan,
      status: subscription.status,
    });
  } catch (err) {
    console.error('[stripe/sync] failed:', err);
    return NextResponse.json(
      { error: err?.message || 'Stripe sync failed' },
      { status: 500 },
    );
  }
}
