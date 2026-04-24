-- ─────────────────────────────────────────────────────────
--  017_subscriptions.sql
--  Stripe subscription management per user.
--  One row per user; upserted by the webhook handler.
-- ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscriptions (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  stripe_customer_id       TEXT,
  stripe_subscription_id   TEXT,
  plan                     TEXT NOT NULL DEFAULT 'free',   -- 'free' | 'pro' | 'enterprise'
  billing_interval         TEXT,                            -- 'monthly' | 'yearly'
  status                   TEXT NOT NULL DEFAULT 'active', -- 'active' | 'trialing' | 'past_due' | 'canceled' | 'incomplete'
  current_period_end       TIMESTAMPTZ,
  cancel_at_period_end     BOOLEAN NOT NULL DEFAULT FALSE,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id),
  UNIQUE (stripe_customer_id),
  UNIQUE (stripe_subscription_id)
);

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

-- Users read their own subscription only
CREATE POLICY "subscriptions_select_own" ON public.subscriptions
  FOR SELECT USING (auth.uid() = user_id);

-- Service role manages all rows (webhook handler uses service_role key)
CREATE POLICY "subscriptions_service_all" ON public.subscriptions
  USING (true) WITH CHECK (true);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

CREATE TRIGGER subscriptions_updated_at
  BEFORE UPDATE ON public.subscriptions
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
