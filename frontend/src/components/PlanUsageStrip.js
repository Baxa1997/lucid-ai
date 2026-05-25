"use client";

// ─────────────────────────────────────────────────────────
//  PlanUsageStrip — a compact bar that mirrors the bottom-of-workspace
//  plan & usage chip into the main dashboard.
//
//  Shows:
//    • current plan badge (Free / Starter / Pro / Enterprise)
//    • projects-this-month X / Y      (or X lifetime for Free)
//    • tokens-this-month X / Y + carry-over balance
//    • Manage / Upgrade button, contextual to plan & usage state
//
//  Re-uses the same shape returned by /api/stripe/subscription so the
//  caller can pass the snapshot down without re-fetching.
// ─────────────────────────────────────────────────────────

import { useMemo } from "react";
import { Zap, Sparkles, Rocket, Crown, CreditCard, ExternalLink, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { isTokenBypassActive } from "@/lib/devQuotaBypass";

const PLAN_VISUALS = {
  free:       { icon: Zap,      label: "Free",       color: "text-slate-600 dark:text-slate-300", bg: "bg-slate-100 dark:bg-slate-800/60" },
  starter:    { icon: Sparkles, label: "Starter",    color: "text-blue-600 dark:text-blue-300",   bg: "bg-blue-50 dark:bg-blue-500/10" },
  pro:        { icon: Rocket,   label: "Pro",        color: "text-[#dc5426] dark:text-orange-300", bg: "bg-orange-50 dark:bg-orange-500/10" },
  enterprise: { icon: Crown,    label: "Enterprise", color: "text-amber-600 dark:text-amber-300", bg: "bg-amber-50 dark:bg-amber-500/10" },
};

function fmtTokens(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

function pctClass(pct) {
  if (pct >= 100) return "bg-red-500";
  if (pct >= 80)  return "bg-amber-500";
  return "bg-gradient-to-r from-[#dc5426] to-orange-500";
}

export default function PlanUsageStrip({ subscription, className }) {
  const data = useMemo(() => {
    if (!subscription) return null;
    const plan = subscription.plan || "free";
    const projUsed  = subscription.usage?.projectsCreated ?? 0;
    const projLimit = subscription.limits?.maxProjectsPerMonth;
    const tokUsed   = subscription.usage?.tokensUsed ?? 0;
    const tokQuota  = subscription.limits?.monthlyTokenQuota ?? 0;
    const extra     = subscription.extraTokenBalance ?? 0;

    const projPct = projLimit && projLimit > 0 ? Math.min(100, (projUsed / projLimit) * 100) : 0;
    const tokPct  = tokQuota  && tokQuota  > 0 ? Math.min(100, (tokUsed  / tokQuota)  * 100) : 0;

    const tokenBypass = isTokenBypassActive();
    // Project cap is always real; token cap honors the dev bypass.
    const atProj  = projLimit != null && projUsed >= projLimit;
    const atTok   = !tokenBypass && tokUsed >= tokQuota && extra <= 0;
    const atLimit = atProj || atTok;

    return { plan, projUsed, projLimit, tokUsed, tokQuota, extra, projPct, tokPct, atProj, atTok, atLimit, bypass: tokenBypass };
  }, [subscription]);

  if (!data) return null;

  const visuals = PLAN_VISUALS[data.plan] ?? PLAN_VISUALS.free;
  const Icon = visuals.icon;
  const isFree = data.plan === "free";
  const periodLabel = isFree ? "lifetime" : "this month";

  return (
    <div
      className={cn(
        "w-full bg-white dark:bg-[#161b22] border border-slate-200/80 dark:border-[#2d333b] rounded-2xl px-5 py-4",
        className,
      )}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        {/* Plan chip */}
        <div className="flex items-center gap-2.5 min-w-[140px]">
          <div className={cn("w-9 h-9 rounded-xl flex items-center justify-center shrink-0", visuals.bg)}>
            <Icon className={cn("w-4 h-4", visuals.color)} />
          </div>
          <div className="leading-tight">
            <p className="text-[10px] uppercase tracking-[0.14em] font-bold text-slate-400 dark:text-slate-500">
              Current plan
            </p>
            <p className={cn("text-[14px] font-bold flex items-center gap-1.5", visuals.color)}>
              {visuals.label}
              {data.bypass && (
                <span className="px-1.5 py-px rounded text-[9px] uppercase tracking-wide font-bold bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300 border border-amber-300/60 dark:border-amber-500/30">
                  Dev bypass
                </span>
              )}
            </p>
          </div>
        </div>

        {/* Projects meter */}
        <div className="flex-1 min-w-[180px]">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">
              Projects {periodLabel}
            </span>
            <span
              className={cn(
                "text-[12px] font-semibold",
                data.atProj ? "text-red-500" : "text-slate-700 dark:text-slate-200",
              )}>
              {data.projUsed} / {data.projLimit ?? "∞"}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all", pctClass(data.projPct))}
              style={{ width: `${data.projLimit ? data.projPct : 0}%` }}
            />
          </div>
        </div>

        {/* Tokens meter */}
        <div className="flex-1 min-w-[200px]">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">
              Tokens this month
            </span>
            <span
              className={cn(
                "text-[12px] font-semibold",
                data.atTok ? "text-red-500" : "text-slate-700 dark:text-slate-200",
              )}>
              {fmtTokens(data.tokUsed)} / {fmtTokens(data.tokQuota)}
              {data.extra > 0 && (
                <span className="ml-1 text-emerald-600 dark:text-emerald-400">+{fmtTokens(data.extra)}</span>
              )}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all", pctClass(data.tokPct))}
              style={{ width: `${data.tokPct}%` }}
            />
          </div>
        </div>

        {/* CTA */}
        <a
          href="/dashboard/billing"
          className={cn(
            "inline-flex items-center gap-1.5 px-3.5 py-2 rounded-[10px] text-[12px] font-semibold transition-all shrink-0",
            data.atLimit || isFree
              ? "bg-gradient-to-r from-[#dc5426] to-orange-500 text-white hover:opacity-90 shadow-sm shadow-orange-600/20"
              : "bg-slate-100 dark:bg-slate-800/60 text-slate-700 dark:text-slate-200 hover:bg-slate-200 dark:hover:bg-slate-700/60",
          )}>
          {data.atLimit || isFree ? (
            <>
              <CreditCard className="w-3.5 h-3.5" /> Upgrade
            </>
          ) : (
            <>
              <ExternalLink className="w-3.5 h-3.5" /> Manage plan
            </>
          )}
        </a>
      </div>

      {data.atLimit && (
        <div className="mt-3 pt-3 border-t border-slate-100 dark:border-slate-800 flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 text-red-500 shrink-0 mt-0.5" />
          <p className="text-[12px] text-red-600 dark:text-red-300 leading-snug">
            {data.atTok
              ? "Monthly token quota exhausted. New chats are blocked until you upgrade or buy a credit pack."
              : isFree
                ? "Free plan project used. Upgrade to keep building."
                : "Monthly project limit reached. Upgrade for more headroom."}
          </p>
        </div>
      )}
    </div>
  );
}
