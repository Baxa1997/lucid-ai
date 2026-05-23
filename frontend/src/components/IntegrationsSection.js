'use client';

import { motion, useInView } from 'framer-motion';
import { useRef } from 'react';
import { cn } from '@/lib/utils';

/* The actual stack, grouped by role.
   ─────────────────────────────────────────────
   · Code & Sandbox — GitHub, GitLab, Docker
   · Auth & Database — Supabase
   · LLM routing — Anthropic, OpenAI, Google AI
   · Auto-detected frameworks — Next.js, React, Vue.js (more as added)
*/

const groups = [
  {
    label: 'Code & Sandbox',
    note: 'OAuth in, branch + PR out. The agent runs in an isolated container.',
    items: [
      {
        name: 'GitHub',
        role: 'OAuth · repos · PRs',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
          </svg>
        ),
      },
      {
        name: 'GitLab',
        role: 'OAuth · repos · MRs',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M23.955 13.587l-1.342-4.135-2.664-8.189a.455.455 0 00-.867 0L16.418 9.45H7.582L4.918 1.263a.455.455 0 00-.867 0L1.387 9.452.045 13.587a.924.924 0 00.331 1.023L12 23.054l11.624-8.443a.92.92 0 00.331-1.024"/>
          </svg>
        ),
      },
      {
        name: 'Docker',
        role: 'Per-session sandbox',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M13.983 11.078h2.119a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.119a.185.185 0 00-.185.185v1.888c0 .102.083.185.185.185zm-2.954-5.43h2.118a.186.186 0 00.186-.186V3.574a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.186zm0 2.716h2.118a.187.187 0 00.186-.186V6.29a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.887c0 .102.082.186.185.186zm-2.93 0h2.12a.186.186 0 00.184-.186V6.29a.185.185 0 00-.185-.185H8.1a.185.185 0 00-.185.185v1.887c0 .102.083.186.185.186zm-2.964 0h2.119a.186.186 0 00.185-.186V6.29a.185.185 0 00-.185-.185H5.136a.186.186 0 00-.186.185v1.887c0 .102.084.186.186.186zm5.893 2.715h2.118a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185zm-2.93 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.083.185.185.185zm-2.964 0h2.119a.185.185 0 00.185-.185V9.006a.185.185 0 00-.184-.186H5.136a.186.186 0 00-.186.186v1.887c0 .102.084.185.186.185zm-2.92 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.186.186 0 00-.186.186v1.887c0 .102.084.185.186.185zM23.763 9.89c-.065-.051-.672-.51-1.954-.51-.338.001-.676.03-1.01.087-.248-1.7-1.653-2.53-1.716-2.566l-.344-.199-.227.327c-.287.438-.492.92-.612 1.426-.23.97-.09 1.882.403 2.661-.595.332-1.55.413-1.744.42H.751a.751.751 0 00-.75.748 11.687 11.687 0 00.692 4.062c.545 1.428 1.355 2.48 2.41 3.124 1.18.722 3.1 1.137 5.275 1.137.983.003 1.963-.086 2.93-.266a12.228 12.228 0 003.823-1.389c.98-.567 1.86-1.288 2.61-2.136 1.252-1.418 1.998-2.997 2.553-4.4h.221c1.372 0 2.215-.549 2.68-1.009.309-.293.55-.65.707-1.046l.098-.288z"/>
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Auth & Database',
    note: 'OAuth-based identity, row-level isolation via Postgres RLS.',
    items: [
      {
        name: 'Supabase',
        role: 'Auth · Postgres · RLS',
        logo: (
          <svg viewBox="0 0 109 113" fill="currentColor" className="w-7 h-7">
            <path d="M63.7076 110.284C60.8481 113.885 55.0502 111.912 54.9813 107.314L53.9738 40.0627H99.1935C107.384 40.0627 111.952 49.5228 106.859 55.9374L63.7076 110.284Z"/>
            <path d="M45.317 2.07103C48.1765 -1.53037 53.9745 0.442937 54.0434 5.041L54.4849 72.2922H9.83113C1.64038 72.2922 -2.92775 62.8321 2.1655 56.4175L45.317 2.07103Z"/>
          </svg>
        ),
      },
    ],
  },
  {
    label: 'LLM routing',
    note: 'LiteLLM under the hood — pick the model that fits the task.',
    items: [
      {
        name: 'Anthropic',
        role: 'Claude · default',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M13.827 3.52h3.603L24 20.477h-3.603l-1.378-3.55h-7.503l-1.379 3.55H6.534L13.827 3.52zm-.704 9.834h4.711l-2.355-6.068-2.356 6.068z"/>
            <path d="M0 20.477L6.293 3.52h3.604L3.603 20.477H0z"/>
          </svg>
        ),
      },
      {
        name: 'OpenAI',
        role: 'GPT · o-series',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .039.052v5.583a4.504 4.504 0 0 1-4.495 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.872zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z"/>
          </svg>
        ),
      },
      {
        name: 'Google AI',
        role: 'Gemini',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M12 24A12 12 0 1112 0a12 12 0 010 24zm5.27-13.16l-5.13 5.14L9.6 13.4l-.94.94 3.48 3.48 6.07-6.07-.94-.94z"/>
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Auto-detected stack',
    note: 'No config — the agent reads your manifest and picks the right tools.',
    items: [
      {
        name: 'Next.js',
        role: 'Sites · marketing',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M11.572 0c-.176.005-.585.045-.956.07-8.508.766-16.47 7.32-17.834 16.07-.5-3.27.31-6.7 2.34-9.4C18.31-2.94 21 12 .025 11.524c.21.046.42.087.63.123 4.6.7 8.96 3.42 10.85 7.84.36.84.96 2.16 1.16 3.43.16.94.16 1.97 0 2.9-.16.95-.55 1.87-1.06 2.66-.06.09-.12.18-.18.27 0 .01 0 .02 0 .03zM15.92 14.6c-.05-.07-.1-.14-.16-.21-.07-.09-.18-.16-.28-.22-.3-.17-.66-.16-.95.01-.3.17-.5.5-.5.84v6.74h.95v-5.4L21.55 24h.59c.18-.01.36-.06.51-.16.16-.1.27-.27.32-.45.04-.18.02-.36-.05-.53-.07-.16-.21-.29-.37-.36L15.92 14.6z"/>
          </svg>
        ),
      },
      {
        name: 'React',
        role: 'Dashboards · admin',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <circle cx="12" cy="12" r="2.05"/>
            <g fill="none" stroke="currentColor" strokeWidth="1" opacity="0.85">
              <ellipse cx="12" cy="12" rx="10.5" ry="4" />
              <ellipse cx="12" cy="12" rx="10.5" ry="4" transform="rotate(60 12 12)" />
              <ellipse cx="12" cy="12" rx="10.5" ry="4" transform="rotate(120 12 12)" />
            </g>
          </svg>
        ),
      },
      {
        name: 'Vue.js',
        role: 'Dashboards · admin',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M24,1.61H14.06L12,5.16,9.94,1.61H0L12,22.39ZM12,14.08,5.16,2.23H9.59L12,6.41l2.41-4.18h4.43Z"/>
          </svg>
        ),
      },
      {
        name: 'FastAPI',
        role: 'APIs · backends',
        logo: (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-7 h-7">
            <path d="M12 0C5.375 0 0 5.375 0 12s5.375 12 12 12 12-5.375 12-12S18.625 0 12 0zm-.624 21.62v-7.528h-4.5L13 2.38v7.528h4.5z"/>
          </svg>
        ),
      },
    ],
  },
];

export default function IntegrationsSection() {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: '-100px' });

  return (
    <section
      id="integrations"
      ref={ref}
      className="px-6 sm:px-10 py-20 lg:py-28 max-w-[1400px] mx-auto w-full">
      {/* Header — Templates style */}
      <div className="text-center mb-14 max-w-2xl mx-auto">
        <motion.span
          initial={{ opacity: 0, y: 10 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5 }}
          className="inline-block text-[11px] uppercase tracking-[0.18em] font-semibold text-[#dc5426] mb-3">
          The stack
        </motion.span>
        <motion.h2
          initial={{ opacity: 0, y: 10 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5, delay: 0.08 }}
          className="text-[36px] sm:text-[44px] lg:text-[52px] font-normal text-slate-900 dark:text-slate-100 tracking-[-0.04em] leading-[1.1] mb-4">
          Built on tools you already trust
        </motion.h2>
        <motion.p
          initial={{ opacity: 0, y: 10 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5, delay: 0.15 }}
          className="text-[16px] text-slate-500 dark:text-slate-400 leading-relaxed font-medium">
          Repos via GitHub or GitLab. Sandboxed execution in Docker. Auth and data on Supabase. Model routing across Anthropic, OpenAI, and Google.
        </motion.p>
      </div>

      {/* Categorized stack as a single connected rail.
          Each row: left = number + label + note. Right = compact item chips. */}
      <div className="rounded-3xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 overflow-hidden divide-y divide-slate-200 dark:divide-slate-800">
        {groups.map((g, gi) => (
          <motion.div
            key={g.label}
            initial={{ opacity: 0, y: 16 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ duration: 0.45, delay: 0.18 + gi * 0.07 }}
            className="grid lg:grid-cols-[260px_1fr] gap-6 lg:gap-8 px-6 sm:px-8 py-7 lg:py-9 hover:bg-slate-50/60 dark:hover:bg-white/[0.015] transition-colors">
            {/* LEFT — number, label, note */}
            <div>
              <div className="text-[10.5px] font-mono font-semibold text-[#dc5426] dark:text-orange-400 tracking-[0.18em] uppercase mb-2">
                {String(gi + 1).padStart(2, '0')} · {g.label.split(' ')[0]}
              </div>
              <h3 className="text-[20px] font-semibold text-slate-900 dark:text-slate-100 tracking-tight leading-tight mb-2">
                {g.label}
              </h3>
              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">
                {g.note}
              </p>
            </div>

            {/* RIGHT — compact chips */}
            <div className="flex flex-wrap gap-2.5 content-start">
              {g.items.map((item) => (
                <div
                  key={item.name}
                  className={cn(
                    "group/chip flex items-center gap-3 px-4 py-3 rounded-xl border",
                    "bg-slate-50 dark:bg-white/[0.025] border-slate-200 dark:border-slate-800",
                    "hover:bg-white dark:hover:bg-white/[0.05] hover:border-slate-300 dark:hover:border-slate-700 hover:shadow-sm",
                    "transition-all min-w-[180px]"
                  )}>
                  <div className="shrink-0 w-7 h-7 grid place-items-center text-slate-800 dark:text-slate-200">
                    {item.logo}
                  </div>
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-semibold text-slate-900 dark:text-slate-100 leading-tight">
                      {item.name}
                    </div>
                    <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 leading-tight">
                      {item.role}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        ))}
      </div>
    </section>
  );
}
