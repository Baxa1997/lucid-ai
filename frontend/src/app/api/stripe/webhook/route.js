import { NextResponse } from 'next/server';
import Stripe from 'stripe';
import { createClient } from '@supabase/supabase-js';

export const config = { api: { bodyParser: false } };

// Service-role admin client — bypasses RLS for webhook updates
function adminClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY,
  );
}

// ── Subscription price → plan key resolution ──
// Resolves the plan tier (free/starter/pro/enterprise) from the
// Stripe price ID that was charged. Unknown price IDs default to
// 'pro' so paid users never silently lose access.
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

async function upsertSubscription(supabase, subscription, userId) {
  const item = subscription.items?.data?.[0];
  const priceId = item?.price?.id;
  const plan = planFromPriceId(priceId);
  const interval = intervalFromSubscription(subscription);

  // Stripe's "Clover" API (2026-01-28) moved current_period_end off the
  // Subscription object and onto each item. Older API versions still keep
  // it at the top level, so try both before giving up.
  const periodEndUnix =
    item?.current_period_end ??
    subscription.current_period_end ??
    null;
  const currentPeriodEndIso = periodEndUnix
    ? new Date(periodEndUnix * 1000).toISOString()
    : null;

  await supabase.from('subscriptions').upsert(
    {
      user_id: userId,
      stripe_customer_id: String(subscription.customer),
      stripe_subscription_id: subscription.id,
      plan,
      billing_interval: interval,
      status: subscription.status,
      current_period_end: currentPeriodEndIso,
      cancel_at_period_end: subscription.cancel_at_period_end ?? false,
    },
    { onConflict: 'user_id' },
  );
}

// ── Credit-pack handling ──
// Idempotent: the credit_pack_purchases table has a UNIQUE constraint
// on stripe_payment_intent_id. If Stripe redelivers the event we
// detect the existing row and skip the balance bump.
async function applyCreditPack(supabase, session) {
  const userId = session.metadata?.supabase_user_id;
  const packKey = session.metadata?.pack_key;
  const tokensAdded = Number(session.metadata?.tokens_added ?? 0);
  const paymentIntentId = String(session.payment_intent ?? '');

  if (!userId || !packKey || !tokensAdded || !paymentIntentId) {
    console.warn('[webhook] credit pack session missing metadata', {
      userId, packKey, tokensAdded, paymentIntentId,
    });
    return;
  }

  // Idempotency check — unique constraint on stripe_payment_intent_id
  const { data: existing } = await supabase
    .from('credit_pack_purchases')
    .select('id')
    .eq('stripe_payment_intent_id', paymentIntentId)
    .maybeSingle();

  if (existing) {
    console.log('[webhook] credit pack already applied for', paymentIntentId);
    return;
  }

  // Record the purchase
  const { error: purchaseErr } = await supabase.from('credit_pack_purchases').insert({
    user_id:                  userId,
    pack_key:                 packKey,
    tokens_added:             tokensAdded,
    amount_cents:             session.amount_total ?? 0,
    currency:                 session.currency ?? 'usd',
    stripe_session_id:        String(session.id),
    stripe_payment_intent_id: paymentIntentId,
  });

  if (purchaseErr) {
    console.error('[webhook] insert credit_pack_purchases failed:', purchaseErr.message);
    return;
  }

  // Bump the carry-over balance. Use raw SQL via rpc-style update so
  // concurrent purchases don't clobber each other.
  const { data: subRow } = await supabase
    .from('subscriptions')
    .select('extra_token_balance, stripe_customer_id')
    .eq('user_id', userId)
    .maybeSingle();

  const newBalance = Number(subRow?.extra_token_balance ?? 0) + tokensAdded;

  await supabase.from('subscriptions').upsert(
    {
      user_id: userId,
      stripe_customer_id: subRow?.stripe_customer_id ?? String(session.customer ?? ''),
      extra_token_balance: newBalance,
    },
    { onConflict: 'user_id' },
  );
}

// ─────────────────────────────────────────────────────────
// POST /api/stripe/webhook
// ─────────────────────────────────────────────────────────
export async function POST(req) {
  const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, { apiVersion: '2024-06-20' });
  const body = await req.text();
  const sig  = req.headers.get('stripe-signature');

  let event;
  try {
    event = stripe.webhooks.constructEvent(body, sig, process.env.STRIPE_WEBHOOK_SECRET);
  } catch (err) {
    console.error('[stripe/webhook] Signature verification failed:', err.message);
    return NextResponse.json({ error: 'Invalid signature' }, { status: 400 });
  }

  const supabase = adminClient();

  async function resolveUserId(customerId) {
    const { data } = await supabase
      .from('subscriptions')
      .select('user_id')
      .eq('stripe_customer_id', customerId)
      .maybeSingle();
    return data?.user_id ?? null;
  }

  switch (event.type) {
    case 'customer.subscription.created':
    case 'customer.subscription.updated': {
      const sub = event.data.object;
      const userId =
        sub.metadata?.supabase_user_id ||
        (await resolveUserId(String(sub.customer)));
      if (userId) await upsertSubscription(supabase, sub, userId);
      break;
    }

    case 'customer.subscription.deleted': {
      const sub = event.data.object;
      const userId = await resolveUserId(String(sub.customer));
      if (userId) {
        await supabase.from('subscriptions').upsert(
          {
            user_id: userId,
            stripe_customer_id: String(sub.customer),
            stripe_subscription_id: sub.id,
            plan: 'free',
            status: 'canceled',
            cancel_at_period_end: false,
          },
          { onConflict: 'user_id' },
        );
      }
      break;
    }

    case 'checkout.session.completed': {
      const session = event.data.object;

      // Subscription checkout
      if (session.mode === 'subscription' && session.subscription) {
        const userId = session.metadata?.supabase_user_id;
        if (userId) {
          const fullSub = await stripe.subscriptions.retrieve(String(session.subscription));
          await upsertSubscription(supabase, fullSub, userId);
        }
        break;
      }

      // One-time credit pack checkout
      if (session.mode === 'payment' && session.metadata?.purchase_type === 'credit_pack') {
        await applyCreditPack(supabase, session);
        break;
      }

      break;
    }

    default:
      break;
  }

  return NextResponse.json({ received: true });
}
