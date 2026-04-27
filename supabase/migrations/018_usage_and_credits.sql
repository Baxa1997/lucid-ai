-- ─────────────────────────────────────────────────────────
--  018_usage_and_credits.sql
--  Per-user monthly token usage + one-time credit pack purchases.
--
--  Two new tables:
--    • usage_periods       — rolling monthly counters (tokens + projects)
--    • credit_pack_purchases — audit log of one-time top-ups
--
--  Plus: extra_token_balance column on subscriptions for unused
--  credit-pack tokens that carry over (don't expire monthly).
-- ─────────────────────────────────────────────────────────

-- ── 1. Carry-over credit balance on subscriptions ────────
ALTER TABLE public.subscriptions
  ADD COLUMN IF NOT EXISTS extra_token_balance BIGINT NOT NULL DEFAULT 0;

-- ── 2. Monthly usage counters ────────────────────────────
-- One row per (user, period_start). period_start is the first day
-- of the calendar month at UTC. Updated by recordTokenUsage() on
-- every LLM call and by canCreateProject() bookkeeping.
CREATE TABLE IF NOT EXISTS public.usage_periods (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  period_start        DATE NOT NULL,
  input_tokens        BIGINT NOT NULL DEFAULT 0,
  output_tokens       BIGINT NOT NULL DEFAULT 0,
  projects_created    INTEGER NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, period_start)
);

CREATE INDEX IF NOT EXISTS usage_periods_user_period_idx
  ON public.usage_periods (user_id, period_start DESC);

ALTER TABLE public.usage_periods ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "usage_periods_select_own"  ON public.usage_periods;
CREATE POLICY "usage_periods_select_own" ON public.usage_periods
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "usage_periods_service_all" ON public.usage_periods;
CREATE POLICY "usage_periods_service_all" ON public.usage_periods
  USING (true) WITH CHECK (true);

DROP TRIGGER IF EXISTS usage_periods_updated_at ON public.usage_periods;
CREATE TRIGGER usage_periods_updated_at
  BEFORE UPDATE ON public.usage_periods
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ── 3. Credit pack purchases (audit log) ─────────────────
CREATE TABLE IF NOT EXISTS public.credit_pack_purchases (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  pack_key                 TEXT NOT NULL,           -- 'small' | 'medium' | 'large'
  tokens_added             BIGINT NOT NULL,
  amount_cents             INTEGER NOT NULL,
  currency                 TEXT NOT NULL DEFAULT 'usd',
  stripe_session_id        TEXT,
  stripe_payment_intent_id TEXT UNIQUE,             -- idempotency guard
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS credit_pack_purchases_user_idx
  ON public.credit_pack_purchases (user_id, created_at DESC);

ALTER TABLE public.credit_pack_purchases ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "credit_packs_select_own"  ON public.credit_pack_purchases;
CREATE POLICY "credit_packs_select_own" ON public.credit_pack_purchases
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "credit_packs_service_all" ON public.credit_pack_purchases;
CREATE POLICY "credit_packs_service_all" ON public.credit_pack_purchases
  USING (true) WITH CHECK (true);

-- ── 4. Atomic helper: increment usage row by delta ───────
-- Avoids read-modify-write races when many concurrent LLM calls
-- land in the same period.
CREATE OR REPLACE FUNCTION public.increment_usage(
  p_user_id        UUID,
  p_period_start   DATE,
  p_input_tokens   BIGINT,
  p_output_tokens  BIGINT,
  p_projects_delta INTEGER
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  INSERT INTO public.usage_periods
    (user_id, period_start, input_tokens, output_tokens, projects_created)
  VALUES
    (p_user_id, p_period_start, p_input_tokens, p_output_tokens, p_projects_delta)
  ON CONFLICT (user_id, period_start) DO UPDATE
    SET input_tokens     = usage_periods.input_tokens     + EXCLUDED.input_tokens,
        output_tokens    = usage_periods.output_tokens    + EXCLUDED.output_tokens,
        projects_created = usage_periods.projects_created + EXCLUDED.projects_created,
        updated_at       = NOW();
END;
$$;

-- ── 5b. Trigger: bump projects_created on first chat_sessions row ──
-- A "project" is one project_id; a project may have many chat sessions.
-- We only want to bump the monthly counter once per (user, project) pair.
-- The trigger checks whether any earlier chat_sessions row for the same
-- (user_id, project_id) already exists. If not, this insert is the first
-- one and we increment the counter for the current month.
CREATE OR REPLACE FUNCTION public.bump_project_count_on_session()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
  v_period_start DATE := DATE_TRUNC('month', NOW() AT TIME ZONE 'UTC')::DATE;
  v_existing     INT;
BEGIN
  IF NEW.project_id IS NULL OR NEW.user_id IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT COUNT(*) INTO v_existing
    FROM public.chat_sessions
   WHERE user_id    = NEW.user_id
     AND project_id = NEW.project_id
     AND id        <> NEW.id;

  IF v_existing = 0 THEN
    PERFORM public.increment_usage(NEW.user_id, v_period_start, 0, 0, 1);
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS chat_sessions_bump_project_count ON public.chat_sessions;
CREATE TRIGGER chat_sessions_bump_project_count
  AFTER INSERT ON public.chat_sessions
  FOR EACH ROW EXECUTE FUNCTION public.bump_project_count_on_session();

-- ── 6. Atomic helper: consume from extra_token_balance ───
-- Returns the actual amount consumed (clamped to available balance).
-- Used when a user has burned their plan quota and falls back to credit pack.
CREATE OR REPLACE FUNCTION public.consume_extra_tokens(
  p_user_id   UUID,
  p_requested BIGINT
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_consumed BIGINT;
BEGIN
  UPDATE public.subscriptions
     SET extra_token_balance = GREATEST(0, extra_token_balance - p_requested)
   WHERE user_id = p_user_id
     AND extra_token_balance > 0
   RETURNING LEAST(p_requested, extra_token_balance + p_requested) INTO v_consumed;

  RETURN COALESCE(v_consumed, 0);
END;
$$;
