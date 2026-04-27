'use client';

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
    icon: Zap,
    accent: 'slate',
    credits: [
      { label: 'tokens', value: '100k', unit: '/mo' },
      { label: 'project', value: '1', unit: '' },
    ],
    highlights: [
      'Core AI code generation',
      'Community templates',
      'Basic build validation',
    ],
    featured: false,
  },
  {
    name: 'Starter',
    description: 'For individuals building serious side projects and MVPs.',
    monthly: 19,
    icon: Sparkles,
    accent: 'orange',
    credits: [
      { label: 'tokens', value: '1M', unit: '/mo' },
      { label: 'projects', value: '5', unit: '/mo' },
    ],
    highlights: [
      'Everything in Free',
      'Custom templates',
      'Standard build queue',
      'Figma import',
    ],
    featured: false,
  },
  {
    name: 'Pro',
    description: 'Advanced tools for professional developers and small teams.',
    monthly: 59,
    icon: Rocket,
    accent: 'brand',
    credits: [
      { label: 'tokens', value: '5M', unit: '/mo' },
      { label: 'projects', value: '20', unit: '/mo' },
    ],
    highlights: [
      'Everything in Starter',
      'Code export to GitHub & GitLab',
      'CI/CD automation',
      'Advanced templates',
      'Priority build queue',
    ],
    featured: true,
  },
  {
    name: 'Enterprise',
    description: 'Scale your engineering with dedicated support and unlimited capacity.',
    monthly: 299,
    icon: Building2,
    accent: 'amber',
    credits: [
      { label: 'tokens', value: '15M', unit: '/mo' },
      { label: 'projects', value: 'Unlimited', unit: '' },
    ],
    highlights: [
      'Everything in Pro',
      'Unlimited projects',
      'Priority support',
      'SLA guarantee',
      'Priority infrastructure',
    ],
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
  const getPrice = (plan) => plan.monthly;

  const accentClasses = {
    slate:  { bg: 'bg-slate-100 dark:bg-slate-800', text: 'text-slate-600 dark:text-slate-300', btn: 'bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100' },
    orange:  { bg: 'bg-orange-50 dark:bg-orange-500/10', text: 'text-[#dc5426] dark:text-orange-400', btn: 'bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100' },
    brand:   { bg: 'bg-orange-50 dark:bg-orange-500/10', text: 'text-[#dc5426] dark:text-orange-400', btn: 'bg-gradient-to-r from-[#dc5426] to-orange-500 text-white hover:opacity-90 shadow-sm shadow-orange-600/20' },
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
            Start for free. Upgrade when you&apos;re ready. All plans billed monthly.
          </p>
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
                    <div className="absolute -top-px left-1/2 -translate-x-1/2 px-3 py-1 bg-gradient-to-r from-[#dc5426] to-orange-500 text-white text-[10px] font-bold rounded-b-lg uppercase tracking-wider">
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
                  <Check className="w-4 h-4 text-[#dc5426] shrink-0" strokeWidth={2.5} />
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
