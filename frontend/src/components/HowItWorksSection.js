'use client';

import { motion, useInView } from 'framer-motion';
import { useRef } from 'react';
import {
  Sparkles, GitBranch, Pencil, ArrowRight,
} from 'lucide-react';

/* Three paths users actually take through Lucid AI.
   ─────────────────────────────────────────────
   Path 1 — Zero-setup: describe → Lucid generates → iterate → deploy.
   Path 2 — Generate then export: build with Lucid → connect a repo → push or ZIP.
   Path 3 — Edit an existing repo: connect → pick a repo → describe a change → PR.
*/

const paths = [
  {
    eyebrow: 'Path 01',
    pathTitle: 'Zero-setup',
    icon: Sparkles,
    accent: 'from-emerald-500 to-cyan-500',
    chipBg: 'bg-emerald-50 dark:bg-emerald-500/10',
    chipText: 'text-emerald-700 dark:text-emerald-300',
    chipBorder: 'border-emerald-100 dark:border-emerald-500/20',
    summary:
      'No GitHub, no GitLab — Lucid AI provisions and configures everything for you.',
    steps: [
      { title: 'Describe your project',  body: 'Type what you want in chat. A landing page, a CRM, an internal tool — anything.' },
      { title: 'Lucid spins up the sandbox', body: 'A Docker workspace is created, dependencies installed, scaffolding generated.' },
      { title: 'Iterate live in chat',  body: 'Add features, change copy, fix bugs. Preview updates instantly.' },
      { title: 'Deploy to a Lucid domain', body: 'One-click hosting on a managed domain.', soon: true },
    ],
  },
  {
    eyebrow: 'Path 02',
    pathTitle: 'Generate then export',
    icon: GitBranch,
    accent: 'from-violet-500 to-indigo-500',
    chipBg: 'bg-violet-50 dark:bg-violet-500/10',
    chipText: 'text-violet-700 dark:text-violet-300',
    chipBorder: 'border-violet-100 dark:border-violet-500/20',
    summary:
      'Build it with Lucid first, then move it into your own GitHub, GitLab, or Bitbucket.',
    steps: [
      { title: 'Describe your project',  body: 'Same chat-first flow — Lucid scaffolds and ships features in a sandbox.' },
      { title: 'Connect your Git provider', body: 'OAuth into GitHub, GitLab, or Bitbucket. Tokens are AES-256-CBC encrypted.' },
      { title: 'Push to your repo',  body: 'Lucid creates the repo and pushes the initial commit. Branch + PR for changes after.' },
      { title: 'Or download as ZIP', body: 'Grab the project as a self-contained archive — no Git needed.', soon: true },
    ],
  },
  {
    eyebrow: 'Path 03',
    pathTitle: 'Edit an existing repo',
    icon: Pencil,
    accent: 'from-orange-500 to-rose-500',
    chipBg: 'bg-orange-50 dark:bg-orange-500/10',
    chipText: 'text-orange-700 dark:text-orange-300',
    chipBorder: 'border-orange-100 dark:border-orange-500/20',
    summary:
      'Bring code you already own. Connect a repo and let Lucid ship changes against it.',
    steps: [
      { title: 'Connect your Git provider', body: 'Sign in with GitHub, GitLab, or Bitbucket. We only see the repos you pick.' },
      { title: 'Pick the project', body: 'Choose any repo you can write to. The agent clones it into its sandbox.' },
      { title: 'Describe the change', body: 'Add a feature, fix a bug, refactor a module. Tests run before the PR opens.' },
      { title: 'Branch + PR ready for review', body: 'You stay the reviewer. Merge when you’re happy.' },
    ],
  },
];

export default function HowItWorksSection() {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: '-100px' });

  return (
    <section
      id="how-it-works"
      ref={ref}
      className="px-6 sm:px-10 py-20 lg:py-28 max-w-[1400px] mx-auto w-full">
      <div className="text-center mb-14 max-w-2xl mx-auto">
        <motion.span
          initial={{ opacity: 0, y: 10 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5 }}
          className="inline-block text-[11px] uppercase tracking-[0.18em] font-semibold text-[#dc5426] mb-3">
          How it works
        </motion.span>
        <motion.h2
          initial={{ opacity: 0, y: 10 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5, delay: 0.08 }}
          className="text-[36px] sm:text-[44px] lg:text-[52px] font-normal text-slate-900 dark:text-slate-100 tracking-[-0.04em] leading-[1.1] mb-4">
          Three ways to get there.
        </motion.h2>
        <motion.p
          initial={{ opacity: 0, y: 10 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5, delay: 0.15 }}
          className="text-[16px] text-slate-500 dark:text-slate-400 leading-relaxed font-medium">
          Start from scratch with zero setup, generate-then-export to your own Git, or hand Lucid the keys to a repo you already own.
        </motion.p>
      </div>

      <div className="grid lg:grid-cols-3 gap-5">
        {paths.map((p, i) => (
          <motion.div
            key={p.pathTitle}
            initial={{ opacity: 0, y: 20 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ duration: 0.5, delay: 0.15 + i * 0.1 }}
            className="relative h-full bg-white dark:bg-slate-900 rounded-3xl border border-slate-200 dark:border-slate-800 p-6 lg:p-7 hover:border-slate-300 dark:hover:border-slate-700 transition-colors flex flex-col">
            {/* Header */}
            <div className="flex items-center gap-3 mb-5">
              <div className={`w-11 h-11 rounded-xl bg-gradient-to-br ${p.accent} flex items-center justify-center text-white shrink-0`}>
                <p.icon className="w-5 h-5" strokeWidth={2} />
              </div>
              <div className="min-w-0">
                <div className="text-[10.5px] font-mono font-semibold text-slate-400 dark:text-slate-500 tracking-[0.14em] uppercase">
                  {p.eyebrow}
                </div>
                <h3 className="text-[18px] font-bold text-slate-900 dark:text-slate-100 leading-tight">
                  {p.pathTitle}
                </h3>
              </div>
            </div>

            <p className="text-[14px] text-slate-600 dark:text-slate-400 leading-relaxed mb-6">
              {p.summary}
            </p>

            {/* Step list with rail */}
            <ol className="relative space-y-4 border-l border-dashed border-slate-200 dark:border-slate-800 pl-6 ml-2 flex-1">
              {p.steps.map((s, j) => (
                <li key={s.title} className="relative">
                  <span className="absolute -left-[33px] top-0 grid h-6 w-6 place-items-center rounded-full bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 text-[10.5px] font-mono font-semibold text-slate-600 dark:text-slate-300">
                    {j + 1}
                  </span>
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <h4 className="text-[14px] font-semibold text-slate-900 dark:text-slate-100 leading-tight">
                      {s.title}
                    </h4>
                    {s.soon && (
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded-md text-[9.5px] font-bold uppercase tracking-[0.08em] border ${p.chipBg} ${p.chipText} ${p.chipBorder}`}>
                        soon
                      </span>
                    )}
                  </div>
                  <p className="text-[12.5px] text-slate-500 dark:text-slate-400 leading-relaxed">
                    {s.body}
                  </p>
                </li>
              ))}
            </ol>
          </motion.div>
        ))}
      </div>
    </section>
  );
}
