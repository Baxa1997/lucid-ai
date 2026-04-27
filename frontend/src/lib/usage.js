// ─────────────────────────────────────────────────────────
//  Usage recorder (server-side, used by API routes after every LLM call)
//
//  Strategy when a call straddles the quota line:
//    1. Spend as much as fits inside the monthly plan quota
//       (recorded in usage_periods so the meter advances).
//    2. Spend the rest from extra_token_balance via consume_extra_tokens()
//       (atomic SQL function that clamps to available balance).
//
//  Both writes use the service-role client so they run regardless of
//  the request's RLS context.
// ─────────────────────────────────────────────────────────

import { createClient } from '@supabase/supabase-js';
import { PLANS, currentPeriodStart, getCurrentPeriodUsage, effectivePlanKey, getUserSubscription } from '@/lib/subscription';

let _adminSingleton = null;
function adminClient() {
  if (_adminSingleton) return _adminSingleton;
  _adminSingleton = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY,
    { auth: { persistSession: false, autoRefreshToken: false } },
  );
  return _adminSingleton;
}

/**
 * Record token consumption for a user. Called AFTER an LLM response so
 * we know the exact input/output token counts.
 *
 * Spends from monthly quota first; falls back to extra_token_balance.
 * Never throws — failures are logged and swallowed so a metering bug
 * cannot break the user-facing call path.
 */
export async function recordTokenUsage(userId, inputTokens = 0, outputTokens = 0) {
  if (!userId) return;
  const totalRequested = Number(inputTokens) + Number(outputTokens);
  if (totalRequested <= 0) return;

  try {
    const supabase = adminClient();
    const sub = await getUserSubscription(userId);
    const planKey = effectivePlanKey(sub);
    const plan = PLANS[planKey] ?? PLANS.free;

    const usage = await getCurrentPeriodUsage(userId);
    const used  = usage.inputTokens + usage.outputTokens;
    const remainingQuota = Math.max(0, plan.monthlyTokenQuota - used);

    // Allocation: drain the monthly bucket first, then bill extras.
    const fromQuota = Math.min(totalRequested, remainingQuota);
    const fromExtra = totalRequested - fromQuota;

    if (fromQuota > 0) {
      // Split fromQuota proportionally between input and output for the row.
      const ratio = totalRequested > 0 ? fromQuota / totalRequested : 0;
      const inputForQuota  = Math.round(Number(inputTokens)  * ratio);
      const outputForQuota = fromQuota - inputForQuota;

      const { error } = await supabase.rpc('increment_usage', {
        p_user_id:        userId,
        p_period_start:   currentPeriodStart(),
        p_input_tokens:   inputForQuota,
        p_output_tokens:  outputForQuota,
        p_projects_delta: 0,
      });
      if (error) console.error('[usage] increment_usage RPC failed:', error.message);
    }

    if (fromExtra > 0) {
      const { error } = await supabase.rpc('consume_extra_tokens', {
        p_user_id:   userId,
        p_requested: fromExtra,
      });
      if (error) console.error('[usage] consume_extra_tokens RPC failed:', error.message);
    }
  } catch (err) {
    console.error('[usage] recordTokenUsage failed:', err?.message ?? err);
  }
}

/**
 * Bump the projects_created counter for the current month.
 * Call this immediately after a new project is successfully created.
 */
export async function recordProjectCreated(userId) {
  if (!userId) return;
  try {
    const supabase = adminClient();
    const { error } = await supabase.rpc('increment_usage', {
      p_user_id:        userId,
      p_period_start:   currentPeriodStart(),
      p_input_tokens:   0,
      p_output_tokens:  0,
      p_projects_delta: 1,
    });
    if (error) console.error('[usage] increment_usage (project) failed:', error.message);
  } catch (err) {
    console.error('[usage] recordProjectCreated failed:', err?.message ?? err);
  }
}
