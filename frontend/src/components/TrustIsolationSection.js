'use client';

import { motion, useInView } from 'framer-motion';
import { useRef } from 'react';
import { Lock, Container, KeyRound, ShieldCheck } from 'lucide-react';

const pillars = [
  {
    icon: Lock,
    title: 'Tokens encrypted at rest',
    body: 'GitHub & GitLab PATs are AES-256-CBC encrypted before they hit the database. Only your session can decrypt them.',
    spec: 'AES-256-CBC',
  },
  {
    icon: Container,
    title: 'Per-session Docker sandbox',
    body: 'Every chat gets its own container with resource limits and an isolated workspace. Containers are destroyed when the session ends.',
    spec: 'Resource-capped',
  },
  {
    icon: KeyRound,
    title: 'OAuth via Supabase',
    body: 'Sign in with Google, GitHub, or GitLab. We never see your password; the JWT is what authenticates every API and DB call.',
    spec: 'Google · GitHub · GitLab',
  },
  {
    icon: ShieldCheck,
    title: 'Row-level data isolation',
    body: 'Postgres RLS scopes every row to its owner via auth.uid(). There is no shared multi-tenant table to leak across.',
    spec: 'Postgres RLS',
  },
];

export default function TrustIsolationSection() {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: '-100px' });

  return (
    <section
      id="trust"
      ref={ref}
      className="px-6 sm:px-10 py-20 lg:py-28 max-w-[1400px] mx-auto w-full">
      <div className="grid lg:grid-cols-[1fr_1.15fr] gap-12 lg:gap-20 items-center">
        {/* LEFT — heading + pillars list */}
        <div>
          <motion.span
            initial={{ opacity: 0, y: 10 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ duration: 0.5 }}
            className="inline-block text-[11px] uppercase tracking-[0.18em] font-semibold text-[#dc5426] mb-3">
            Trust &amp; isolation
          </motion.span>
          <motion.h2
            initial={{ opacity: 0, y: 10 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ duration: 0.5, delay: 0.08 }}
            className="text-[36px] sm:text-[44px] lg:text-[52px] font-normal text-slate-900 dark:text-slate-100 tracking-[-0.04em] leading-[1.1] mb-4">
            Your code, your tokens, your boundary.
          </motion.h2>
          <motion.p
            initial={{ opacity: 0, y: 10 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ duration: 0.5, delay: 0.15 }}
            className="text-[16px] text-slate-500 dark:text-slate-400 leading-relaxed font-medium mb-8 max-w-[44ch]">
            The agent runs in a sandbox. Your tokens are encrypted. Every row in our database is scoped to you. The architecture is the security story.
          </motion.p>

          <ul className="divide-y divide-slate-200 dark:divide-slate-800 border-y border-slate-200 dark:border-slate-800">
            {pillars.map((p, i) => (
              <motion.li
                key={p.title}
                initial={{ opacity: 0, x: -8 }}
                animate={inView ? { opacity: 1, x: 0 } : {}}
                transition={{ duration: 0.4, delay: 0.18 + i * 0.06 }}
                className="flex items-start gap-4 py-4">
                <div className="w-9 h-9 rounded-lg bg-slate-900 dark:bg-white flex items-center justify-center shrink-0 mt-0.5">
                  <p.icon className="w-[18px] h-[18px] text-white dark:text-slate-900" strokeWidth={2} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="text-[14.5px] font-semibold text-slate-900 dark:text-slate-100 leading-tight">
                      {p.title}
                    </h3>
                    <span className="text-[10px] font-mono text-slate-400 dark:text-slate-500 tracking-tight">
                      · {p.spec}
                    </span>
                  </div>
                  <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">
                    {p.body}
                  </p>
                </div>
              </motion.li>
            ))}
          </ul>
        </div>

        {/* RIGHT — isolation boundary diagram */}
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={inView ? { opacity: 1, scale: 1 } : {}}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="relative">
          <div className="relative rounded-3xl bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 p-6 sm:p-10 overflow-hidden border border-slate-800">
            {/* grid bg */}
            <div
              aria-hidden
              className="absolute inset-0 opacity-[0.18]"
              style={{
                backgroundImage:
                  'linear-gradient(to right, rgba(255,255,255,.08) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,.08) 1px, transparent 1px)',
                backgroundSize: '32px 32px',
              }}
            />

            {/* Concentric rings — user → JWT → sandbox → agent */}
            <div className="relative flex flex-col items-center text-center">
              {/* Outer: user */}
              <div className="w-full rounded-2xl border border-slate-700 bg-slate-900/60 p-4 mb-3">
                <div className="text-[10.5px] font-mono uppercase tracking-[0.14em] text-slate-500 mb-1">
                  Your session
                </div>
                <div className="text-[13px] font-semibold text-slate-100">
                  Supabase JWT · auth.uid()
                </div>
              </div>

              <div className="w-px h-5 bg-slate-700" />

              {/* Mid: encrypted token */}
              <div className="w-[88%] rounded-2xl border border-amber-500/30 bg-amber-500/[0.08] p-4 mb-3">
                <div className="text-[10.5px] font-mono uppercase tracking-[0.14em] text-amber-300/80 mb-1">
                  Encrypted at rest
                </div>
                <div className="text-[13px] font-semibold text-amber-100">
                  Git PAT · AES-256-CBC
                </div>
                <div className="mt-2 font-mono text-[10.5px] text-amber-200/70 break-all">
                  a3f4…b8c0 · b1de…7f23 · 9e84…a201
                </div>
              </div>

              <div className="w-px h-5 bg-slate-700" />

              {/* Inner: sandbox + agent */}
              <div className="w-[76%] rounded-2xl border-2 border-dashed border-emerald-500/40 bg-emerald-500/[0.06] p-4">
                <div className="text-[10.5px] font-mono uppercase tracking-[0.14em] text-emerald-300/80 mb-1">
                  Per-session container
                </div>
                <div className="text-[13px] font-semibold text-emerald-100 mb-2">
                  Docker sandbox · bind-mount
                </div>
                <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-md bg-emerald-500/15 text-emerald-200 text-[11px] font-mono">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  agent · running
                </div>
              </div>
            </div>

            {/* Corner caption */}
            <div className="absolute top-4 right-4 text-[10px] font-mono text-slate-500 uppercase tracking-[0.16em]">
              boundary
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
