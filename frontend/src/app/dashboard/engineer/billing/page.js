'use client';

import { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  Zap, Rocket, Check, CreditCard, ArrowRight,
  AlertCircle, CheckCircle2, Loader2, ExternalLink, Calendar, FolderGit2,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const PLANS = [
  {
    key: 'free',
    name: 'Free',
    monthly: 0,
    yearly: 0,
    icon: Zap,
    color: 'slate',
    highlights: ['1 project', 'AI code generation', 'Community templates', 'Basic build validation'],
  },
  {
    key: 'pro',
    name: 'Pro',
    monthly: 30,
    yearly: 24,
    icon: Rocket,
    color: 'orange',
    highlights: ['Unlimited projects', 'Code export to GitHub/GitLab', 'CI/CD automation', 'Advanced templates', 'Priority build queue'],
    featured: true,
  },
];

function PlanBadge({ plan }) {
  const colors = {
    free:       'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300',
    pro:        'bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] dark:text-orange-400',
    enterprise: 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400',
  };
  return (
    <span className={cn('px-2.5 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider', colors[plan] ?? colors.free)}>
      {plan}
    </span>
  );
}

export default function BillingPage() {
  const searchParams = useSearchParams();
  const [sub, setSub] = useState(null);
  const [loading, setLoading] = useState(true);
  const [checkoutLoading, setCheckoutLoading] = useState('');
  const [portalLoading, setPortalLoading] = useState(false);
  const [interval, setInterval] = useState('monthly');
  const [banner, setBanner] = useState(null);

  useEffect(() => {
    if (searchParams.get('success') === '1') setBanner('success');
    else if (searchParams.get('canceled') === '1') setBanner('canceled');
  }, [searchParams]);

  useEffect(() => {
    fetch('/api/stripe/subscription')
      .then((r) => r.json())
      .then((data) => { setSub(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleUpgrade = async (plan, billingInterval) => {
    setCheckoutLoading(`${plan}_${billingInterval}`);
    try {
      const res = await fetch('/api/stripe/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan, interval: billingInterval }),
      });
      const data = await res.json();
      if (data.url) window.location.href = data.url;
    } finally {
      setCheckoutLoading('');
    }
  };

  const handlePortal = async () => {
    setPortalLoading(true);
    try {
      const res = await fetch('/api/stripe/portal', { method: 'POST' });
      const data = await res.json();
      if (data.url) window.location.href = data.url;
    } finally {
      setPortalLoading(false);
    }
  };

  const isPaid = sub?.isPaid;
  const currentPlan = sub?.plan ?? 'free';

  return (
    <div className="h-full overflow-y-auto bg-[#fefcfa] dark:bg-[#0d1117]">
      <div className="max-w-3xl mx-auto px-6 py-10">

        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-1">Billing & Plan</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Manage your subscription and usage.</p>
        </div>

        {/* Success / Canceled banners */}
        {banner === 'success' && (
          <div className="flex items-center gap-3 mb-6 p-4 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 rounded-xl">
            <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
            <p className="text-sm text-emerald-700 dark:text-emerald-300 font-medium">
              Payment successful — your plan has been upgraded!
            </p>
          </div>
        )}
        {banner === 'canceled' && (
          <div className="flex items-center gap-3 mb-6 p-4 bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 rounded-xl">
            <AlertCircle className="w-5 h-5 text-amber-500 shrink-0" />
            <p className="text-sm text-amber-700 dark:text-amber-300 font-medium">
              Checkout was canceled — no charge was made.
            </p>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
          </div>
        ) : (
          <>
            {/* Current status card */}
            <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-6 mb-8">
              <div className="flex items-center justify-between mb-5">
                <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Current plan</h2>
                <PlanBadge plan={currentPlan} />
              </div>

              <div className="grid grid-cols-2 gap-4 mb-5">
                <div className="bg-slate-50 dark:bg-[#0d1117] rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <FolderGit2 className="w-4 h-4 text-slate-400" />
                    <span className="text-xs text-slate-500 dark:text-slate-400">Projects</span>
                  </div>
                  <p className="text-xl font-bold text-slate-900 dark:text-white">
                    {sub?.usage?.projectsCreated ?? 0}
                    <span className="text-sm font-normal text-slate-400 ml-1">
                      / {sub?.limits?.maxProjects ?? 1}
                    </span>
                  </p>
                </div>
                <div className="bg-slate-50 dark:bg-[#0d1117] rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Calendar className="w-4 h-4 text-slate-400" />
                    <span className="text-xs text-slate-500 dark:text-slate-400">Renews</span>
                  </div>
                  <p className="text-sm font-semibold text-slate-900 dark:text-white">
                    {sub?.currentPeriodEnd
                      ? new Date(sub.currentPeriodEnd).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                      : isPaid ? '—' : 'Never'}
                  </p>
                </div>
              </div>

              {isPaid && (
                <button
                  onClick={handlePortal}
                  disabled={portalLoading}
                  className="flex items-center gap-2 text-sm font-medium text-[#dc5426] dark:text-orange-400 hover:underline disabled:opacity-50"
                >
                  {portalLoading
                    ? <><Loader2 className="w-4 h-4 animate-spin" /> Opening portal…</>
                    : <><ExternalLink className="w-4 h-4" /> Manage subscription</>}
                </button>
              )}

              {sub?.cancelAtPeriodEnd && (
                <div className="mt-4 flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  Your plan will cancel at the end of the billing period.
                </div>
              )}
            </div>

            {/* Upgrade section — only show when not on paid plan */}
            {!isPaid && (
              <div>
                <div className="flex items-center justify-between mb-5">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Upgrade your plan</h2>
                  <div className="flex items-center bg-slate-100 dark:bg-slate-800 rounded-full p-0.5">
                    {['monthly', 'yearly'].map((i) => (
                      <button
                        key={i}
                        onClick={() => setInterval(i)}
                        className={cn(
                          'px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all',
                          interval === i
                            ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-sm'
                            : 'text-slate-500 dark:text-slate-400'
                        )}
                      >
                        {i === 'yearly' ? 'Yearly (–20%)' : 'Monthly'}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {PLANS.filter((p) => p.key !== 'free').map((plan) => {
                    const price = interval === 'yearly' ? plan.yearly : plan.monthly;
                    const key = `${plan.key}_${interval}`;
                    const busy = checkoutLoading === key;

                    return (
                      <div
                        key={plan.key}
                        className={cn(
                          'relative rounded-2xl border p-6',
                          plan.featured
                            ? 'border-[#dc5426]/30 dark:border-orange-500/20 bg-orange-50/30 dark:bg-orange-500/5'
                            : 'border-slate-200/80 dark:border-[#2d333b] bg-white dark:bg-[#161b22]'
                        )}
                      >
                        {plan.featured && (
                          <div className="absolute -top-px left-6 px-2.5 py-0.5 bg-gradient-to-r from-[#dc5426] to-orange-500 text-white text-[10px] font-bold rounded-b-md uppercase tracking-wider">
                            Most Popular
                          </div>
                        )}
                        <div className="flex items-center gap-2 mb-3">
                          <plan.icon className="w-4 h-4 text-[#dc5426]" />
                          <span className="text-sm font-bold text-slate-900 dark:text-white">{plan.name}</span>
                        </div>
                        <div className="flex items-baseline gap-0.5 mb-4">
                          <span className="text-3xl font-extrabold text-slate-900 dark:text-white">${price}</span>
                          <span className="text-xs text-slate-400 ml-1">/mo{interval === 'yearly' && ' · billed yearly'}</span>
                        </div>
                        <ul className="space-y-2 mb-5">
                          {plan.highlights.map((h) => (
                            <li key={h} className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-400">
                              <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" strokeWidth={2.5} />
                              {h}
                            </li>
                          ))}
                        </ul>
                        <button
                          onClick={() => handleUpgrade(plan.key, interval)}
                          disabled={busy}
                          className={cn(
                            'w-full py-2.5 rounded-xl text-sm font-bold flex items-center justify-center gap-2 transition-all active:scale-[0.98]',
                            plan.featured
                              ? 'bg-gradient-to-r from-[#dc5426] to-orange-500 text-white hover:opacity-90 shadow-sm shadow-orange-600/20'
                              : 'bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100'
                          )}
                        >
                          {busy
                            ? <><Loader2 className="w-4 h-4 animate-spin" /> Redirecting…</>
                            : <><CreditCard className="w-4 h-4" /> Upgrade to {plan.name} <ArrowRight className="w-3.5 h-3.5" /></>}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
