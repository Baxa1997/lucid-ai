"use client";

// ── Limit-reached upgrade modal ──────────────────────────────────────
// Shown when a gated action hits the plan's project or token limit
// (402 + upgradeRequired from the API). Visual twin of the dashboard
// composer's inline upgrade modal so the two read as one system.
//
// The Skip button is the TESTING escape hatch: it re-runs the blocked
// action with bypassLimits=true (the only way past the server gates).
// It is intentionally low-emphasis and labeled as testing-only.
import {X, CreditCard, Rocket} from "lucide-react";

export default function LimitUpgradeModal({
  limitType = "project", // 'project' | 'token'
  planName = "Free",
  onClose,
  onSkip,
  skipLabel = "Skip for testing",
}) {
  const isToken = limitType === "token";

  const title = isToken ? "Token quota reached" : "Project limit reached";
  const subtitle = isToken
    ? `You've used all your monthly tokens on the ${planName} plan. Upgrade or buy a credit pack to keep building.`
    : planName === "Free"
      ? "You've used your free project. Upgrade to keep building with more projects, tokens, and code export."
      : `You've hit your monthly project limit on the ${planName} plan. Upgrade for more headroom.`;

  const features = isToken
    ? [
        "More monthly tokens (5M on Pro)",
        "Or buy a one-time credit pack",
        "Code export to GitHub & GitLab",
        "Priority build queue",
      ]
    : [
        "More projects per month",
        "Higher monthly token quota",
        "Code export to GitHub & GitLab",
        "Advanced AI templates",
      ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="relative w-full max-w-md bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] shadow-2xl p-8">
        <button
          onClick={onClose}
          aria-label="Close"
          className="absolute top-4 right-4 p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06]">
          <X className="w-4 h-4" />
        </button>
        <div className="w-12 h-12 rounded-2xl bg-orange-50 dark:bg-orange-500/10 flex items-center justify-center mb-5">
          <Rocket className="w-6 h-6 text-[#dc5426]" />
        </div>
        <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-2">{title}</h2>
        <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">{subtitle}</p>
        <div className="space-y-2 mb-6">
          {features.map((f) => (
            <div
              key={f}
              className="flex items-center gap-2.5 text-sm text-slate-600 dark:text-slate-300">
              <div className="w-4 h-4 rounded-full bg-blue-100 dark:bg-blue-500/20 flex items-center justify-center shrink-0">
                <svg viewBox="0 0 10 10" className="w-2.5 h-2.5 text-blue-700 dark:text-blue-400">
                  <path
                    d="M2 5l2 2 4-4"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    fill="none"
                  />
                </svg>
              </div>
              {f}
            </div>
          ))}
        </div>
        <a
          href="/dashboard/billing"
          className="w-full py-3 rounded-xl text-sm font-bold flex items-center justify-center gap-2 bg-gradient-to-r from-[#dc5426] to-orange-500 text-white hover:opacity-90 shadow-sm shadow-orange-600/20 transition-all active:scale-[0.98]">
          <CreditCard className="w-4 h-4" />
          {isToken ? "View Plans & Credit Packs" : "View Plans — from $19/mo"}
        </a>
        {onSkip && (
          <button
            onClick={onSkip}
            className="w-full mt-3 py-2.5 rounded-xl text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-all">
            {skipLabel}
          </button>
        )}
      </div>
    </div>
  );
}
