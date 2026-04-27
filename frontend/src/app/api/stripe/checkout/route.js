import { NextResponse } from 'next/server';
import Stripe from 'stripe';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';
import { CREDIT_PACKS, canBuyCreditPack } from '@/lib/subscription';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, {
  apiVersion: '2024-06-20',
});

// ── Subscription price catalog (Stripe price IDs from env) ──
// All plans are monthly-only for now.
const SUBSCRIPTION_PRICES = {
  starter_monthly:    process.env.STRIPE_STARTER_MONTHLY_PRICE_ID,
  pro_monthly:        process.env.STRIPE_PRO_MONTHLY_PRICE_ID,
  enterprise_monthly: process.env.STRIPE_ENTERPRISE_MONTHLY_PRICE_ID,
};

// ── One-time credit-pack price IDs ──
const CREDIT_PACK_PRICES = {
  small:  process.env.STRIPE_CREDIT_PACK_SMALL_PRICE_ID,
  medium: process.env.STRIPE_CREDIT_PACK_MEDIUM_PRICE_ID,
  large:  process.env.STRIPE_CREDIT_PACK_LARGE_PRICE_ID,
};

/**
 * POST /api/stripe/checkout
 *
 * Body (subscription mode):
 *   { mode: 'subscription', plan: 'starter'|'pro'|'enterprise' }
 *   (interval is always 'monthly' — yearly billing is not offered yet)
 *
 * Body (credit pack mode):
 *   { mode: 'credit_pack', pack: 'small'|'medium'|'large' }
 *
 * Backwards compatible: if `mode` is omitted, defaults to subscription.
 */
export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try { body = await req.json(); } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const mode = body.mode ?? 'subscription';
  const supabase = await getSupabaseServerClient();

  // ── Resolve / create the Stripe customer ────────────────
  let stripeCustomerId;
  const { data: subRow } = await supabase
    .from('subscriptions')
    .select('stripe_customer_id')
    .eq('user_id', ctx.userId)
    .maybeSingle();
  stripeCustomerId = subRow?.stripe_customer_id;

  if (!stripeCustomerId) {
    const customer = await stripe.customers.create({
      email: ctx.user.email,
      metadata: { supabase_user_id: ctx.userId },
    });
    stripeCustomerId = customer.id;

    // Persist immediately so webhooks can match by customer id
    await supabase.from('subscriptions').upsert(
      { user_id: ctx.userId, stripe_customer_id: stripeCustomerId, plan: 'free', status: 'active' },
      { onConflict: 'user_id' },
    );
  }

  const appUrl = process.env.NEXT_PUBLIC_APP_URL || 'http://localhost:3000';

  // ─────────────────────────────────────────────────────────
  //  Credit pack — one-time payment
  // ─────────────────────────────────────────────────────────
  if (mode === 'credit_pack') {
    // Gate: only Pro / Enterprise subscribers may buy credit packs.
    const eligibility = await canBuyCreditPack(ctx.userId);
    if (!eligibility.allowed) {
      return NextResponse.json(
        { error: eligibility.reason, upgradeRequired: true },
        { status: 403 },
      );
    }

    const packKey = body.pack;
    const pack = CREDIT_PACKS[packKey];
    const priceId = CREDIT_PACK_PRICES[packKey];

    if (!pack) {
      return NextResponse.json({ error: `Unknown credit pack: ${packKey}` }, { status: 400 });
    }
    if (!priceId) {
      return NextResponse.json(
        { error: `Stripe price ID for "${packKey}" pack is not configured` },
        { status: 500 },
      );
    }

    const session = await stripe.checkout.sessions.create({
      customer: stripeCustomerId,
      mode: 'payment',
      line_items: [{ price: priceId, quantity: 1 }],
      success_url: `${appUrl}/dashboard/engineer/billing?credit_success=1`,
      cancel_url:  `${appUrl}/dashboard/engineer/billing?canceled=1`,
      payment_intent_data: {
        metadata: {
          supabase_user_id: ctx.userId,
          purchase_type:    'credit_pack',
          pack_key:         packKey,
          tokens_added:     String(pack.tokens),
        },
      },
      metadata: {
        supabase_user_id: ctx.userId,
        purchase_type:    'credit_pack',
        pack_key:         packKey,
        tokens_added:     String(pack.tokens),
      },
    });

    return NextResponse.json({ url: session.url });
  }

  // ─────────────────────────────────────────────────────────
  //  Subscription — recurring
  // ─────────────────────────────────────────────────────────
  const plan = body.plan ?? 'pro';
  const priceKey = `${plan}_monthly`;
  const priceId = SUBSCRIPTION_PRICES[priceKey];

  if (!priceId) {
    return NextResponse.json(
      { error: `Unknown plan: ${plan}` },
      { status: 400 },
    );
  }

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
