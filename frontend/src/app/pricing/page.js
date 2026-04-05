'use client';

import { useState } from 'react';
import { Check, Zap, Sparkles, Rocket, Building2, HelpCircle } from 'lucide-react';
import Link from 'next/link';
import Navbar from '@/components/Navbar';
import Footer from '@/components/Footer';
import { cn } from '@/lib/utils';

const plans = [
  {
    name: 'Free',
    description: 'Get started with AI code generation at zero cost. Perfect for exploring.',
    monthly: 0,
    yearly: 0,
    icon: Zap,
    accent: 'slate',
    credits: [
      { label: 'generation credits', value: '50', unit: '/mo' },
      { label: 'project slots', value: '2', unit: '' },
    ],
    highlights: [
      'Core AI code generation',
      'Community templates',
      'GitHub export',
      'Basic build validation',
    ],
    note: null,
    featured: false,
  },
  {
    name: 'Starter',
    description: 'For individuals building serious side projects and MVPs.',
    monthly: 15,
    yearly: 12,
    icon: Sparkles,
    accent: 'blue',
    credits: [
      { label: 'generation credits', value: '200', unit: '/mo' },
      { label: 'project slots', value: '5', unit: '' },
    ],
    highlights: [
      'Everything in Free',
      'Custom domain connect',
      'GitLab integration',
      'Priority build queue',
      'Figma import',
    ],
    note: null,
    featured: false,
  },
  {
    name: 'Pro',
    description: 'Advanced tools for professional developers and small teams.',
    monthly: 30,
    yearly: 24,
    icon: Rocket,
    accent: 'violet',
    credits: [
      { label: 'generation credits', value: '500', unit: '/mo' },
      { label: 'project slots', value: '15', unit: '' },
    ],
    highlights: [
      'Everything in Starter',
      'CI/CD automation',
      'Team collaboration',
      'Advanced templates',
      'AI model selection',
      'GoDaddy DNS setup',
      'Early access to beta features',
    ],
    note: null,
    featured: true,
  },
  {
    name: 'Enterprise',
    description: 'Scale your engineering with dedicated support and unlimited capacity.',
    monthly: 120,
    yearly: 96,
    icon: Building2,
    accent: 'amber',
    credits: [
      { label: 'generation credits', value: '2,000', unit: '/mo' },
      { label: 'project slots', value: 'Unlimited', unit: '' },
    ],
    highlights: [
      'Everything in Pro',
      'Unlimited projects',
      'Dedicated support',
      'Custom templates',
      'SLA guarantee',
      'SSO / SAML',
      'Priority infrastructure',
      'Premium support',
    ],
    note: null,
    featured: false,
  },
];

const allPlanFeatures = [
  'AI-powered code generation',
  'Integrated CI/CD pipelines',
  'GitHub & GitLab integration',
  'Build validation & auto-fix',
  'Template marketplace',
  'Cloud deployment',
  'Custom domain support',
  'Authentication built-in',
  'Real-time collaboration',
  'Debugging & troubleshooting tools',
];

export default function PricingPage() {
  const [billing, setBilling] = useState('monthly');

  const getPrice = (plan) => billing === 'yearly' ? plan.yearly : plan.monthly;

  const accentClasses = {
    slate:  { bg: 'bg-slate-100 dark:bg-slate-800', text: 'text-slate-600 dark:text-slate-300', btn: 'bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100' },
    blue:   { bg: 'bg-blue-50 dark:bg-blue-500/10', text: 'text-blue-600 dark:text-blue-400', btn: 'bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100' },
    violet: { bg: 'bg-violet-50 dark:bg-violet-500/10', text: 'text-violet-600 dark:text-violet-400', btn: 'bg-violet-600 text-white hover:bg-violet-700 shadow-sm shadow-violet-600/20' },
    amber:  { bg: 'bg-amber-50 dark:bg-amber-500/10', text: 'text-amber-600 dark:text-amber-400', btn: 'bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100' },
  };

  return (
    <div className="min-h-screen bg-[#FDFDFD] dark:bg-slate-950 text-slate-900 dark:text-slate-100 font-inter transition-colors duration-200">
      <Navbar />

      <main className="pt-28 pb-20">

        {/* ── Hero ── */}
        <section className="text-center px-6 mb-16">
          <h1 className="text-4xl sm:text-5xl lg:text-[56px] font-extrabold tracking-tight leading-tight mb-4">
            Plans from first idea<br className="hidden sm:block" /> to full scale
          </h1>
          <p className="text-lg text-slate-500 dark:text-slate-400 max-w-xl mx-auto mb-8">
            Start for free. Upgrade when you&apos;re ready.
          </p>

          {/* Billing Toggle */}
          <div className="inline-flex items-center bg-slate-100 dark:bg-slate-800 rounded-full p-1">
            <button
              onClick={() => setBilling('yearly')}
              className={cn(
                "px-5 py-2 rounded-full text-sm font-semibold transition-all duration-200",
                billing === 'yearly'
                  ? "bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-sm"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              )}
            >
              Yearly <span className="text-emerald-600 dark:text-emerald-400 text-xs font-bold ml-1">(save 20%)</span>
            </button>
            <button
              onClick={() => setBilling('monthly')}
              className={cn(
                "px-5 py-2 rounded-full text-sm font-semibold transition-all duration-200",
                billing === 'monthly'
                  ? "bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-sm"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              )}
            >
              Monthly
            </button>
          </div>
        </section>

        {/* ── Pricing Cards ── */}
        <section className="max-w-6xl mx-auto px-6 mb-24">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-0 border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden bg-white dark:bg-slate-900">
            {plans.map((plan, idx) => {
              const price = getPrice(plan);
              const ac = accentClasses[plan.accent];

              return (
                <div
                  key={plan.name}
                  className={cn(
                    "relative flex flex-col p-6",
                    idx < plans.length - 1 && "lg:border-r border-b lg:border-b-0 border-slate-200 dark:border-slate-800",
                    plan.featured && "bg-slate-50/50 dark:bg-white/[0.02]"
                  )}
                >
                  {plan.featured && (
                    <div className="absolute -top-px left-1/2 -translate-x-1/2 px-3 py-1 bg-violet-600 text-white text-[10px] font-bold rounded-b-lg uppercase tracking-wider">
                      Most Popular
                    </div>
                  )}

                  {/* Plan Name */}
                  <h3 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 mb-1.5">{plan.name}</h3>
                  <p className="text-[12px] text-slate-400 dark:text-slate-500 leading-relaxed mb-5 min-h-[32px]">
                    {plan.description}
                  </p>

                  {/* Price */}
                  <div className="flex items-baseline gap-0.5 mb-4">
                    <span className="text-[32px] font-extrabold text-slate-900 dark:text-slate-100 leading-none">
                      ${price}
                    </span>
                    {price > 0 && (
                      <span className="text-[13px] text-slate-400 dark:text-slate-500 font-medium">/mo</span>
                    )}
                    {billing === 'yearly' && price > 0 && (
                      <span className="text-[11px] text-slate-400 dark:text-slate-500 ml-2">Billed annually</span>
                    )}
                  </div>

                  {/* Credits — single-line each */}
                  <div className="space-y-1.5 mb-5">
                    {plan.credits.map((c, i) => (
                      <p key={i} className="text-[12px] text-slate-500 dark:text-slate-400 whitespace-nowrap">
                        <span className="font-bold text-slate-700 dark:text-slate-300">{c.value}</span>{' '}
                        {c.label}{c.unit}{' '}
                        <HelpCircle className="w-3 h-3 text-slate-300 dark:text-slate-600 inline -mt-0.5" />
                      </p>
                    ))}
                  </div>

                  {/* CTA */}
                  <Link
                    href="/login"
                    className={cn(
                      "w-full py-2.5 rounded-xl text-[13px] font-bold text-center transition-all active:scale-[0.98] mb-5 block",
                      ac.btn
                    )}
                  >
                    Get started
                  </Link>

                  {/* Highlights */}
                  <div className="flex-1">
                    <p className="text-[11px] font-bold text-slate-700 dark:text-slate-300 mb-2.5">
                      Plan highlights:
                    </p>
                    <ul className="space-y-1.5">
                      {plan.highlights.map((h) => (
                        <li key={h} className="flex items-start gap-1.5 text-[12px] text-slate-500 dark:text-slate-400">
                          <Check className="w-3 h-3 text-slate-800 dark:text-slate-300 shrink-0 mt-0.5" strokeWidth={2.5} />
                          {h}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* ── Every Plan Includes ── */}
        <section className="bg-slate-900 dark:bg-[#0d1117] py-20 px-6">
          <div className="max-w-5xl mx-auto flex flex-col lg:flex-row gap-12 lg:gap-20">
            <div className="lg:w-[340px] shrink-0">
              <h2 className="text-3xl sm:text-4xl font-extrabold text-white leading-tight">
                Eliminate costly, complex add-ons. Every Lucid AI plan includes:
              </h2>
            </div>
            <div className="flex-1 grid grid-cols-1 sm:grid-cols-2 gap-x-12 gap-y-4">
              {allPlanFeatures.map((f) => (
                <div key={f} className="flex items-center gap-3">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" strokeWidth={2.5} />
                  <span className="text-[14px] text-slate-300">{f}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

      </main>

      <Footer />
    </div>
  );
}
