'use client';

import { useState, useRef, useEffect } from 'react';
import { motion, useInView, AnimatePresence } from 'framer-motion';
import {
  GitBranch, Globe, FileText, Code2, Bug, Rocket,
  Terminal, Sparkles, ArrowRight, Check, Zap, BookOpen,
  ChevronRight
} from 'lucide-react';
import { cn } from '@/lib/utils';

/* ─── Feature Data ─── */

const features = [
  {
    id: 'engineer',
    badge: 'AI Software Engineer',
    badgeColor: 'text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-500/10 border-violet-100 dark:border-violet-500/20',
    title: 'Ship production code,\nautonomously.',
    description: 'Connect your GitHub or GitLab repository. Lucid AI reads your codebase, plans changes, writes code, creates branches, and pushes commits — all without leaving the platform.',
    icon: Code2,
    iconGradient: 'from-violet-600 to-indigo-600',
    capabilities: [
      { text: 'Connect GitHub & GitLab repos', icon: GitBranch },
      { text: 'Understands your full codebase', icon: Terminal },
      { text: 'Writes, tests & debugs code', icon: Bug },
      { text: 'Pushes commits & opens PRs', icon: Rocket },
    ],
    mockup: 'engineer',
  },
  {
    id: 'docs',
    badge: 'Documentation Engine',
    badgeColor: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10 border-blue-100 dark:border-blue-500/20',
    title: 'Beautiful docs,\ngenerated instantly.',
    description: 'Generate comprehensive documentation from your GitHub repo, GitLab project, or any public web URL. Always in sync, always professional.',
    icon: BookOpen,
    iconGradient: 'from-blue-600 to-cyan-500',
    capabilities: [
      { text: 'From GitHub & GitLab repos', icon: GitBranch },
      { text: 'From any public web URL', icon: Globe },
      { text: 'API reference auto-generation', icon: FileText },
      { text: 'Always synced & up-to-date', icon: Check },
    ],
    mockup: 'docs',
  },
];

/* ─── Mockup Components ─── */

function EngineerMockup({ isVisible }) {
  const lines = [
    { num: 1, content: <><span className="text-violet-400">async function</span> <span className="text-blue-400">deployFeature</span>(config) {'{'}</> },
    { num: 2, content: <><span className="pl-4 text-violet-400">const</span> <span className="text-slate-200">branch</span> = <span className="text-violet-400">await</span> <span className="text-slate-200">git.createBranch</span>();</> },
    { num: 3, content: <><span className="pl-4 text-violet-400">const</span> <span className="text-slate-200">changes</span> = <span className="text-violet-400">await</span> <span className="text-slate-200">ai.implement</span>(config);</> },
    { num: 4, content: <span className="pl-4 text-slate-500 italic">// Running 24 test suites...</span> },
    { num: 5, content: <><span className="pl-4 text-violet-400">await</span> <span className="text-slate-200">git.commit</span>(<span className="text-emerald-400">'feat: add auth flow'</span>);</> },
    { num: 6, content: <><span className="pl-4 text-violet-400">await</span> <span className="text-slate-200">git.pushAndCreatePR</span>();</> },
    { num: 7, content: <>{'}'}</> },
  ];

  return (
    <div className="bg-[#1a1b26] rounded-xl border border-white/10 overflow-hidden shadow-2xl">
      {/* Title bar */}
      <div className="bg-[#16161e] border-b border-white/5 px-4 py-2.5 flex items-center gap-3">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="text-[10px] text-slate-500 font-mono ml-2">lucid-ai — feature/auth-flow</div>
      </div>

      {/* Code area */}
      <div className="p-5 font-mono text-[12px] leading-relaxed">
        {lines.map((line, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, x: -20 }}
            animate={isVisible ? { opacity: 1, x: 0 } : {}}
            transition={{ delay: 0.3 + i * 0.12, duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="flex"
          >
            <span className="text-slate-600 w-6 select-none text-right pr-4 text-[10px]">{line.num}</span>
            <div className="text-slate-200">{line.content}</div>
          </motion.div>
        ))}

        {/* Animated result */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={isVisible ? { opacity: 1, y: 0 } : {}}
          transition={{ delay: 1.4, duration: 0.5 }}
          className="mt-4 pt-3 border-t border-white/5"
        >
          <div className="flex items-center gap-2 text-emerald-400 text-[11px] font-medium">
            <Check className="w-3.5 h-3.5" />
            <span>PR #247 created → feature/auth-flow → main</span>
          </div>
          <div className="flex items-center gap-2 text-emerald-400 text-[11px] font-medium mt-1">
            <Check className="w-3.5 h-3.5" />
            <span>All 24 tests passing · 0 lint errors</span>
          </div>
        </motion.div>
      </div>
    </div>
  );
}

function DocsMockup({ isVisible }) {
  const docItems = [
    { title: '# Getting Started', type: 'h1' },
    { title: '## Installation', type: 'h2' },
    { title: 'npm install @lucid-ai/sdk', type: 'code' },
    { title: '## Authentication', type: 'h2' },
    { title: 'POST /api/auth/login', type: 'endpoint' },
    { title: 'GET /api/users/:id', type: 'endpoint' },
    { title: '## Webhooks', type: 'h2' },
  ];

  return (
    <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden shadow-2xl">
      {/* Title bar */}
      <div className="bg-slate-50 dark:bg-slate-800 border-b border-slate-100 dark:border-slate-700 px-4 py-2.5 flex items-center gap-3">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="text-[10px] text-slate-400 font-mono ml-2">docs.yourapp.com</div>
      </div>

      <div className="flex">
        {/* Sidebar */}
        <div className="w-[120px] border-r border-slate-100 dark:border-slate-800 p-3 flex flex-col gap-1.5">
          {['Overview', 'Auth', 'Users', 'Webhooks', 'SDKs'].map((item, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={isVisible ? { opacity: 1, x: 0 } : {}}
              transition={{ delay: 0.2 + i * 0.08 }}
              className={cn(
                "text-[10px] font-medium px-2 py-1.5 rounded-md",
                i === 0
                  ? "bg-violet-50 dark:bg-violet-500/10 text-violet-600 dark:text-violet-400"
                  : "text-slate-400 dark:text-slate-500"
              )}
            >
              {item}
            </motion.div>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 p-4 space-y-2">
          {docItems.map((item, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 8 }}
              animate={isVisible ? { opacity: 1, y: 0 } : {}}
              transition={{ delay: 0.4 + i * 0.1, duration: 0.35 }}
            >
              {item.type === 'h1' && (
                <div className="text-[13px] font-bold text-slate-800 dark:text-slate-100 mb-1">{item.title}</div>
              )}
              {item.type === 'h2' && (
                <div className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mt-2">{item.title}</div>
              )}
              {item.type === 'code' && (
                <div className="bg-slate-900 dark:bg-slate-800 text-emerald-400 text-[10px] px-2.5 py-1.5 rounded-md font-mono">
                  $ {item.title}
                </div>
              )}
              {item.type === 'endpoint' && (
                <div className="bg-slate-50 dark:bg-slate-800 border border-slate-100 dark:border-slate-700 text-[10px] px-2.5 py-1.5 rounded-md font-mono text-blue-600 dark:text-blue-400">
                  {item.title}
                </div>
              )}
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─── Main Section ─── */

export default function FeaturesSection() {
  const [activeFeature, setActiveFeature] = useState(0);

  return (
    <section className="px-6 sm:px-10 py-24 lg:py-32 max-w-[1400px] mx-auto w-full">
      {/* Section Header */}
      <div className="text-center mb-20">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="inline-flex items-center gap-2 bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 px-4 py-1.5 rounded-full mb-6"
        >
          <Sparkles className="w-3.5 h-3.5 text-violet-600 dark:text-violet-400" />
          <span className="text-[12px] font-semibold text-violet-600 dark:text-violet-400">Core Features</span>
        </motion.div>

        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="text-4xl sm:text-5xl font-bold text-slate-900 dark:text-slate-100 mb-5"
        >
          Everything you need to{' '}
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-violet-600 to-indigo-600 dark:from-violet-400 dark:to-indigo-400">
            build faster
          </span>
        </motion.h2>

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.2 }}
          className="text-lg text-slate-500 dark:text-slate-400 max-w-2xl mx-auto leading-relaxed"
        >
          From writing production code to generating beautiful documentation — Lucid AI handles the heavy lifting so your team can focus on what matters.
        </motion.p>
      </div>

      {/* Feature Tabs */}
      <div className="flex items-center justify-center gap-3 mb-16">
        {features.map((feature, i) => (
          <button
            key={feature.id}
            onClick={() => setActiveFeature(i)}
            className={cn(
              "flex items-center gap-2 px-5 py-2.5 rounded-xl text-[14px] font-semibold transition-all duration-300",
              activeFeature === i
                ? "bg-slate-900 dark:bg-white text-white dark:text-slate-900 shadow-lg shadow-slate-900/20 dark:shadow-white/10"
                : "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700"
            )}
          >
            <feature.icon className="w-4 h-4" />
            {feature.badge}
          </button>
        ))}
      </div>

      {/* Feature Content */}
      <AnimatePresence mode="wait">
        {features.map((feature, i) => {
          if (i !== activeFeature) return null;
          return (
            <motion.div
              key={feature.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] }}
              className="grid lg:grid-cols-2 gap-12 lg:gap-20 items-center"
            >
              {/* Left: Details */}
              <div>
                <div className={cn("inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-bold border mb-6", feature.badgeColor)}>
                  <feature.icon className="w-3 h-3" />
                  {feature.badge}
                </div>

                <h3 className="text-3xl sm:text-4xl font-bold text-slate-900 dark:text-slate-100 leading-tight mb-5 whitespace-pre-line">
                  {feature.title}
                </h3>

                <p className="text-[16px] text-slate-500 dark:text-slate-400 leading-relaxed mb-8">
                  {feature.description}
                </p>

                {/* Capability list */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {feature.capabilities.map((cap, j) => (
                    <motion.div
                      key={j}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.15 + j * 0.08, duration: 0.35 }}
                      className="flex items-center gap-3 p-3 rounded-xl bg-slate-50 dark:bg-slate-800/50 border border-slate-100 dark:border-slate-800"
                    >
                      <div className={cn("w-8 h-8 rounded-lg bg-gradient-to-br flex items-center justify-center text-white shrink-0", feature.iconGradient)}>
                        <cap.icon className="w-4 h-4" />
                      </div>
                      <span className="text-[13px] font-medium text-slate-700 dark:text-slate-300">{cap.text}</span>
                    </motion.div>
                  ))}
                </div>
              </div>

              {/* Right: Mockup */}
              <div>
                {feature.mockup === 'engineer' && <EngineerMockup isVisible={activeFeature === 0} />}
                {feature.mockup === 'docs' && <DocsMockup isVisible={activeFeature === 1} />}
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </section>
  );
}
