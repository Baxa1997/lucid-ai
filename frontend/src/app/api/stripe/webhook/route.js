import { NextResponse } from 'next/server';
import Stripe from 'stripe';
import { createClient } from '@supabase/supabase-js';

export const config = { api: { bodyParser: false } };

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, {
  apiVersion: '2024-06-20',
});

// Service-role admin client — bypasses RLS for webhook updates
function adminClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY,
  );
}

function planFromPriceId(priceId) {
  if (!priceId) return 'free';
  if (
    priceId === process.env.STRIPE_PRO_MONTHLY_PRICE_ID ||
    priceId === process.env.STRIPE_PRO_YEARLY_PRICE_ID
  ) return 'pro';
  if (
    priceId === process.env.STRIPE_ENTERPRISE_MONTHLY_PRICE_ID ||
    priceId === process.env.STRIPE_ENTERPRISE_YEARLY_PRICE_ID
  ) return 'enterprise';
  return 'pro'; // Unknown price → treat as pro
}

function intervalFromSubscription(subscription) {
  const item = subscription.items?.data?.[0];
  return item?.price?.recurring?.interval === 'year' ? 'yearly' : 'monthly';
}

async function upsertSubscription(supabase, subscription, userId) {
  const priceId = subscription.items?.data?.[0]?.price?.id;
  const plan = planFromPriceId(priceId);
  const interval = intervalFromSubscription(subscription);

  await supabase.from('subscriptions').upsert(
    {
      user_id: userId,
      stripe_customer_id: String(subscription.customer),
      stripe_subscription_id: subscription.id,
      plan,
      billing_interval: interval,
      status: subscription.status,
      current_period_end: new Date(subscription.current_period_end * 1000).toISOString(),
      cancel_at_period_end: subscription.cancel_at_period_end,
    },
    { onConflict: 'user_id' }
  );
}

// POST /api/stripe/webhook
export async function POST(req) {
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
          { onConflict: 'user_id' }
        );
      }
      break;
    }

    case 'checkout.session.completed': {
      const session = event.data.object;
      if (session.mode === 'subscription' && session.subscription) {
        const userId = session.metadata?.supabase_user_id;
        if (userId) {
          const fullSub = await stripe.subscriptions.retrieve(String(session.subscription));
          await upsertSubscription(supabase, fullSub, userId);
        }
      }
      break;
    }

    default:
      break;
  }

  return NextResponse.json({ received: true });
}
