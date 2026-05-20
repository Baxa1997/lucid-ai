import { NextResponse } from 'next/server';
import Stripe from 'stripe';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// POST /api/stripe/portal
// Opens the Stripe Customer Portal so the user can manage/cancel.
//
// All error paths return JSON — never an empty body. Production hit
// a "Unexpected end of JSON input" crash when Stripe SDK errors
// propagated as raw 500s, so we wrap everything that can throw.
export async function POST() {
  try {
    if (!process.env.STRIPE_SECRET_KEY) {
      return NextResponse.json(
        { error: 'Billing is not configured for this deployment. Missing STRIPE_SECRET_KEY env var.' },
        { status: 503 }
      );
    }
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, { apiVersion: '2024-06-20' });
    const authResult = await requireAuth();
    if (!authResult.ok) return authResult.response;
    const { ctx } = authResult;

    const supabase = await getSupabaseServerClient();
    const { data: sub } = await supabase
      .from('subscriptions')
      .select('stripe_customer_id')
      .eq('user_id', ctx.userId)
      .maybeSingle();

    if (!sub?.stripe_customer_id) {
      return NextResponse.json(
        { error: 'No Stripe customer found. Please subscribe first.' },
        { status: 400 }
      );
    }

    const appUrl = process.env.NEXT_PUBLIC_APP_URL || 'http://localhost:3000';
    const session = await stripe.billingPortal.sessions.create({
      customer: sub.stripe_customer_id,
      return_url: `${appUrl}/dashboard/engineer/billing`,
    });

    return NextResponse.json({ url: session.url });
  } catch (err) {
    console.error('Stripe portal error:', err);
    return NextResponse.json(
      { error: err?.message || 'Failed to open billing portal' },
      { status: 500 }
    );
  }
}
