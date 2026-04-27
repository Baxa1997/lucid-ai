'use client';

import { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  Zap, Rocket, Crown, Sparkles, Check, CreditCard, ArrowRight,
  AlertCircle, CheckCircle2, Loader2, ExternalLink, Calendar,
  FolderGit2, Coins, Plus, TrendingUp, Lock,
} from 'lucide-react';
import { cn } from '@/lib/utils';

// ── Visual config per plan key (mirrors lib/subscription.js order) ──
const PLAN_VISUALS = {
  free:       { icon: Zap,      accent: 'slate'   },
  starter:    { icon: Sparkles, accent: 'blue'    },
  pro:        { icon: Rocket,   accent: 'orange', featured: true },
  enterprise: { icon: Crown,    accent: 'amber'   },
};

const PLAN_HIGHLIGHTS = {
  free:       ['1 project total', '100k tokens / month', 'Community templates', 'Basic build validation'],
  starter:    ['5 projects / month', '1M tokens / month', 'Custom templates', 'Standard build queue'],
  pro:        ['20 projects / month', '5M tokens / month', 'Code export to GitHub/GitLab', 'CI/CD automation', 'Advanced templates'],
  enterprise: ['Unlimited projects', '25M tokens / month', 'Code export', 'Priority build queue', 'Priority support'],
};

const PACK_VISUALS = {
  small:  { icon: Coins, color: 'slate'  },
  medium: { icon: Coins, color: 'orange' },
  large:  { icon: Coins, color: 'amber'  },
};

function fmtTokens(n) {
  if (n == null) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

function fmtPrice(cents) {
  if (cents == null) return null;
  const dollars = cents / 100;
  return dollars % 1 === 0 ? `$${dollars}` : `$${dollars.toFixed(2)}`;
}

function PlanBadge({ plan }) {
  const colors = {
    free:       'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300',
    starter:    'bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400',
    pro:        'bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] dark:text-orange-400',
    enterprise: 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400',
  };
  return (
    <span className={cn('px-2.5 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider', colors[plan] ?? colors.free)}>
      {plan}
    </span>
  );
}

function UsageBar({ used, limit, label, valueLabel }) {
  const pct = limit && limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const overage = limit && used > limit;
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-slate-500 dark:text-slate-400">{label}</span>
        <span className={cn('text-xs font-semibold', overage ? 'text-red-500' : 'text-slate-700 dark:text-slate-200')}>
          {valueLabel}
        </span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all',
            pct >= 100 ? 'bg-red-500' :
            pct >= 80  ? 'bg-amber-500' :
            'bg-gradient-to-r from-[#dc5426] to-orange-500',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function BillingPage() {
  const searchParams = useSearchParams();
  const [sub, setSub] = useState(null);
  const [loading, setLoading] = useState(true);
  const [checkoutLoading, setCheckoutLoading] = useState('');
  const [portalLoading, setPortalLoading] = useState(false);
  const [banner, setBanner] = useState(null);

  useEffect(() => {
    if      (searchParams.get('success')        === '1') setBanner('success');
    else if (searchParams.get('credit_success') === '1') setBanner('credit_success');
    else if (searchParams.get('canceled')       === '1') setBanner('canceled');
  }, [searchParams]);

  const refreshSubscription = () => {
    fetch('/api/stripe/subscription')
      .then((r) => r.json())
      .then((data) => { setSub(data); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(refreshSubscription, []);

  const handleSubscribe = async (planKey) => {
    setCheckoutLoading(`${planKey}_monthly`);
    try {
      const res = await fetch('/api/stripe/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'subscription', plan: planKey }),
      });
      const data = await res.json();
      if (data.url) window.location.href = data.url;
      else alert(data.error || 'Checkout failed');
    } finally { setCheckoutLoading(''); }
  };

  const handleBuyPack = async (packKey) => {
    setCheckoutLoading(`pack_${packKey}`);
    try {
      const res = await fetch('/api/stripe/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'credit_pack', pack: packKey }),
      });
      const data = await res.json();
      if (data.url) window.location.href = data.url;
      else alert(data.error || 'Checkout failed');
    } finally { setCheckoutLoading(''); }
  };

  const handlePortal = async () => {
    setPortalLoading(true);
    try {
      const res = await fetch('/api/stripe/portal', { method: 'POST' });
      const data = await res.json();
      if (data.url) window.location.href = data.url;
    } finally { setPortalLoading(false); }
  };

  const isPaid       = sub?.isPaid;
  const currentPlan  = sub?.plan ?? 'free';
  const tokensUsed   = sub?.usage?.tokensUsed ?? 0;
  const tokenQuota   = sub?.limits?.monthlyTokenQuota ?? 0;
  const projUsed     = sub?.usage?.projectsCreated ?? 0;
  const projLimit    = sub?.limits?.maxProjectsPerMonth;
  const extraBalance = sub?.extraTokenBalance ?? 0;

  const plans = sub?.catalog?.plans ?? [];
  const packs = sub?.catalog?.creditPacks ?? [];

  return (
    <div className="h-full overflow-y-auto bg-[#fefcfa] dark:bg-[#0d1117]">
      <div className="max-w-5xl mx-auto px-6 py-10">

        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-1">Billing & Usage</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage your subscription, monitor usage, and top up.</p>
        </div>

        {/* Banners */}
        {banner === 'success' && (
          <div className="flex items-center gap-3 mb-6 p-4 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 rounded-xl">
            <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
            <p className="text-sm text-emerald-700 dark:text-emerald-300 font-medium">Payment successful — your plan has been upgraded.</p>
          </div>
        )}
        {banner === 'credit_success' && (
          <div className="flex items-center gap-3 mb-6 p-4 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 rounded-xl">
            <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
            <p className="text-sm text-emerald-700 dark:text-emerald-300 font-medium">Credit pack purchased — extra tokens have been added to your account.</p>
          </div>
        )}
        {banner === 'canceled' && (
          <div className="flex items-center gap-3 mb-6 p-4 bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 rounded-xl">
            <AlertCircle className="w-5 h-5 text-amber-500 shrink-0" />
            <p className="text-sm text-amber-700 dark:text-amber-300 font-medium">Checkout was canceled — no charge was made.</p>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
          </div>
        ) : (
          <>
            {/* ── Current plan + usage card ────────────────── */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-6 mb-8">
              <div className="flex items-center justify-between mb-5">
                <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Current plan</h2>
                <PlanBadge plan={currentPlan} />
              </div>

              {/* Usage meters */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 mb-5">
                <div className="bg-slate-50 dark:bg-[#0d1117] rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <FolderGit2 className="w-4 h-4 text-slate-400" />
                    <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
                      {currentPlan === 'free' ? 'Projects (lifetime)' : 'Projects this month'}
                    </span>
                  </div>
                  <UsageBar
                    used={projUsed}
                    limit={projLimit ?? Infinity}
                    label="Used"
                    valueLabel={projLimit == null ? `${projUsed} / Unlimited` : `${projUsed} / ${projLimit}`}
                  />
                </div>

                <div className="bg-slate-50 dark:bg-[#0d1117] rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <TrendingUp className="w-4 h-4 text-slate-400" />
                    <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Tokens this month</span>
                  </div>
                  <UsageBar
                    used={tokensUsed}
                    limit={tokenQuota}
                    label="Used"
                    valueLabel={`${fmtTokens(tokensUsed)} / ${fmtTokens(tokenQuota)}`}
                  />
                  {extraBalance > 0 && (
                    <p className="mt-2 text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                      <Coins className="w-3 h-3" />
                      +{fmtTokens(extraBalance)} extra tokens (carry-over)
                    </p>
                  )}
                </div>
              </div>

              {/* Renewal + portal */}
              <div className="flex items-center justify-between flex-wrap gap-3 pt-4 border-t border-slate-100 dark:border-slate-800">
                <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                  <Calendar className="w-3.5 h-3.5" />
                  {sub?.currentPeriodEnd
                    ? <>Renews {new Date(sub.currentPeriodEnd).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</>
                    : isPaid ? 'Renewal date unavailable' : 'Free plan — no billing cycle'}
                  {sub?.billingInterval && <span className="ml-1 text-slate-400">· {sub.billingInterval}</span>}
                </div>

                {isPaid && (
                  <button
                    onClick={handlePortal}
                    disabled={portalLoading}
                    className="flex items-center gap-2 text-xs font-semibold text-[#dc5426] dark:text-orange-400 hover:underline disabled:opacity-50"
                  >
                    {portalLoading
                      ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Opening…</>
                      : <><ExternalLink className="w-3.5 h-3.5" /> Manage subscription</>}
                  </button>
                )}
              </div>

              {sub?.cancelAtPeriodEnd && (
                <div className="mt-4 flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  Your plan will cancel at the end of the billing period.
                </div>
              )}
            </div>

            {/* ── Plan cards ──────────────────────────────── */}
            <div className="mb-10">
              <div className="mb-5">
                <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Plans</h2>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {plans.map((p) => {
                  const visuals = PLAN_VISUALS[p.key] ?? PLAN_VISUALS.free;
                  const Icon = visuals.icon;
                  const isCurrent = p.key === currentPlan;

                  const cents = p.monthlyPriceCents;
                  const perLabel = p.monthlyPriceCents > 0 ? '/month' : '';

                  const ckKey = `${p.key}_monthly`;
                  const busy = checkoutLoading === ckKey;
                  const isFree = p.key === 'free';

                  return (
                    <div
                      key={p.key}
                      className={cn(
                        'relative rounded-2xl border p-5 flex flex-col',
                        visuals.featured
                          ? 'border-[#dc5426]/30 dark:border-orange-500/20 bg-orange-50/30 dark:bg-orange-500/5'
                          : 'border-slate-200/80 dark:border-[#2d333b] bg-white dark:bg-[#161b22]',
                        isCurrent && 'ring-2 ring-[#dc5426]/40 dark:ring-orange-500/30',
                      )}
                    >
                      {visuals.featured && (
                        <div className="absolute -top-px left-5 px-2.5 py-0.5 bg-gradient-to-r from-[#dc5426] to-orange-500 text-white text-[10px] font-bold rounded-b-md uppercase tracking-wider">
                          Most popular
                        </div>
                      )}

                      <div className="flex items-center gap-2 mb-3">
                        <Icon className="w-4 h-4 text-[#dc5426]" />
                        <span className="text-sm font-bold text-slate-900 dark:text-white">{p.name}</span>
                        {isCurrent && (
                          <span className="ml-auto text-[10px] uppercase tracking-wider font-bold text-[#dc5426] dark:text-orange-400">
                            Current
                          </span>
                        )}
                      </div>

                      <div className="mb-4">
                        <div className="flex items-baseline gap-0.5">
                          <span className="text-3xl font-extrabold text-slate-900 dark:text-white">
                            {fmtPrice(cents) ?? 'Free'}
                          </span>
                          {perLabel && <span className="text-xs text-slate-400 ml-1">{perLabel}</span>}
                        </div>
                      </div>

                      <ul className="space-y-2 mb-5 flex-1">
                        {(PLAN_HIGHLIGHTS[p.key] ?? []).map((h) => (
                          <li key={h} className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-400">
                            <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" strokeWidth={2.5} />
                            {h}
                          </li>
                        ))}
                      </ul>

                      {isFree ? (
                        <button
                          disabled
                          className="w-full py-2.5 rounded-xl text-xs font-bold text-slate-400 bg-slate-100 dark:bg-slate-800/40"
                        >
                          {isCurrent ? 'Your plan' : 'Free forever'}
                        </button>
                      ) : isCurrent ? (
                        <button
                          onClick={handlePortal}
                          disabled={portalLoading}
                          className="w-full py-2.5 rounded-xl text-xs font-bold border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800/50 flex items-center justify-center gap-2"
                        >
                          {portalLoading
                            ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Opening…</>
                            : <><ExternalLink className="w-3.5 h-3.5" /> Manage</>}
                        </button>
                      ) : (
                        <button
                          onClick={() => handleSubscribe(p.key)}
                          disabled={busy}
                          className={cn(
                            'w-full py-2.5 rounded-xl text-xs font-bold flex items-center justify-center gap-1.5 transition-all active:scale-[0.98]',
                            visuals.featured
                              ? 'bg-gradient-to-r from-[#dc5426] to-orange-500 text-white hover:opacity-90 shadow-sm shadow-orange-600/20'
                              : 'bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100',
                            busy && 'opacity-60',
                          )}
                        >
                          {busy
                            ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Redirecting…</>
                            : <><CreditCard className="w-3.5 h-3.5" /> Choose {p.name} <ArrowRight className="w-3 h-3" /></>}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* ── Credit packs (Pro+ only) ────────────────── */}
            <div>
              <div className="flex items-center justify-between mb-5 flex-wrap gap-2">
                <div>
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                    Buy extra tokens
                    {!sub?.canBuyCreditPacks && (
                      <span className="px-2 py-0.5 rounded-full bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] dark:text-orange-400 text-[10px] font-bold uppercase tracking-wider">
                        Pro only
                      </span>
                    )}
                  </h2>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                    {sub?.canBuyCreditPacks
                      ? 'One-time purchase. Tokens never expire and are used after your monthly quota is exhausted.'
                      : 'Available on Pro and Enterprise plans. Upgrade to top up your tokens.'}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                {packs.map((pack) => {
                  const visuals = PACK_VISUALS[pack.key] ?? PACK_VISUALS.small;
                  const Icon = visuals.icon;
                  const busy = checkoutLoading === `pack_${pack.key}`;
                  const ratio = pack.tokens / (pack.priceCents / 100); // tokens per dollar
                  const locked = !sub?.canBuyCreditPacks;

                  return (
                    <div
                      key={pack.key}
                      className={cn(
                        'rounded-2xl border border-slate-200/80 dark:border-[#2d333b] bg-white dark:bg-[#161b22] p-5 transition-opacity',
                        locked && 'opacity-60',
                      )}
                    >
                      <div className="flex items-center gap-2 mb-3">
                        <Icon className="w-4 h-4 text-[#dc5426]" />
                        <span className="text-sm font-bold text-slate-900 dark:text-white">{pack.label}</span>
                      </div>

                      <div className="mb-4">
                        <div className="flex items-baseline gap-1">
                          <span className="text-2xl font-extrabold text-slate-900 dark:text-white">
                            {fmtTokens(pack.tokens)}
                          </span>
                          <span className="text-xs text-slate-400">tokens</span>
                        </div>
                        <p className="text-[11px] text-slate-400 mt-0.5">
                          {fmtTokens(Math.round(ratio))} tokens per $1
                        </p>
                      </div>

                      <button
                        onClick={() => handleBuyPack(pack.key)}
                        disabled={busy || locked}
                        // Native title attr renders as a tooltip on hover.
                        title={locked ? 'Upgrade to Pro to buy credit packs' : undefined}
                        className={cn(
                          'w-full py-2.5 rounded-xl text-xs font-bold border flex items-center justify-center gap-2',
                          locked
                            ? 'border-slate-200 dark:border-slate-700 text-slate-400 dark:text-slate-500 cursor-not-allowed'
                            : 'border-slate-200 dark:border-slate-700 text-slate-900 dark:text-white hover:bg-slate-50 dark:hover:bg-slate-800/50',
                        )}
                      >
                        {busy
                          ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Redirecting…</>
                          : locked
                            ? <><Lock className="w-3.5 h-3.5" /> Pro only</>
                            : <><Plus className="w-3.5 h-3.5" /> Buy for {fmtPrice(pack.priceCents)}</>}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
