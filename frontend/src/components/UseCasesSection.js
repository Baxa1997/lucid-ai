'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { GitPullRequest, MessageSquare, ShieldCheck, Activity } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';

/* ── Shared chrome — same chat-style look across all three mockups ── */

function ChatChrome({ title, children }) {
  return (
    <div className="bg-white rounded-xl shadow-2xl overflow-hidden w-full max-w-md border border-white/20">
      <div className="bg-slate-50 border-b border-slate-100 flex items-center px-4 py-2.5 gap-2">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="text-[9px] text-slate-400 font-mono ml-3">{title}</div>
        <div className="ml-auto inline-flex items-center gap-1.5 text-[9px] text-emerald-500 font-medium">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          live
        </div>
      </div>
      <div className="h-[280px] p-3 text-left text-slate-700">{children}</div>
    </div>
  );
}

function ShipFromPromptMockup() {
  return (
    <ChatChrome title="lucid-ai · acme/marketing-site">
      <div className="space-y-2">
        <div className="ml-auto inline-block max-w-[80%] px-3 py-1.5 rounded-md bg-violet-50 border border-violet-100 text-[10px] text-slate-700">
          Add Stripe checkout to /pricing.
        </div>
        <div className="text-[9.5px] text-blue-600 font-semibold">Plan · 4 steps</div>
        <div className="text-[9.5px] font-mono text-slate-600 space-y-0.5">
          <div><span className="text-emerald-500">▸</span> read  app/pricing/page.tsx</div>
          <div><span className="text-emerald-500">▸</span> edit  app/pricing/page.tsx</div>
          <div><span className="text-emerald-500">▸</span> add   app/api/checkout/route.ts</div>
          <div><span className="text-emerald-500">▸</span> shell npm test --silent</div>
        </div>
        <div className="text-[9.5px] text-emerald-500 font-medium">✓ 18 tests passing</div>
        <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-blue-50 border border-blue-100 text-[9.5px] font-semibold text-blue-700">
          <GitPullRequest className="w-3 h-3" />
          PR #312 · feat/stripe-checkout
        </div>
      </div>
    </ChatChrome>
  );
}

function BacklogMockup() {
  return (
    <ChatChrome title="lucid-ai · acme/api">
      <div className="space-y-2">
        <div className="ml-auto inline-block max-w-[85%] px-3 py-1.5 rounded-md bg-violet-50 border border-violet-100 text-[10px] text-slate-700">
          Fix #847 — pagination cursor returns duplicates on the last page.
        </div>
        <div className="text-[9.5px] text-blue-600 font-semibold">Reproducing · cursor=eyJpZCI6...</div>
        <div className="text-[9.5px] font-mono text-slate-600 space-y-0.5">
          <div><span className="text-emerald-500">▸</span> read  src/pagination/cursor.ts</div>
          <div><span className="text-emerald-500">▸</span> diff  off-by-one in tail-window</div>
          <div><span className="text-emerald-500">▸</span> shell pytest tests/pagination</div>
        </div>
        <div className="text-[9.5px] text-emerald-500 font-medium">✓ Bug reproduced + fixed · regression test added</div>
        <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-blue-50 border border-blue-100 text-[9.5px] font-semibold text-blue-700">
          <GitPullRequest className="w-3 h-3" />
          PR #913 · fix/pagination-tail
        </div>
      </div>
    </ChatChrome>
  );
}

function FromTemplateMockup() {
  return (
    <ChatChrome title="lucid-ai · new project from template">
      <div className="space-y-2">
        <div className="ml-auto inline-block max-w-[85%] px-3 py-1.5 rounded-md bg-violet-50 border border-violet-100 text-[10px] text-slate-700">
          Start from CRM Dashboard. Make leads stage Kanban-style.
        </div>
        <div className="text-[9.5px] text-blue-600 font-semibold">Scaffolding · CRM Dashboard</div>
        <div className="text-[9.5px] font-mono text-slate-600 space-y-0.5">
          <div><span className="text-emerald-500">▸</span> clone template · 47 files</div>
          <div><span className="text-emerald-500">▸</span> add   components/KanbanBoard.tsx</div>
          <div><span className="text-emerald-500">▸</span> edit  app/leads/page.tsx</div>
          <div><span className="text-emerald-500">▸</span> shell pnpm dev</div>
        </div>
        <div className="text-[9.5px] text-emerald-500 font-medium">✓ Preview ready · http://localhost:3000</div>
        <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-blue-50 border border-blue-100 text-[9.5px] font-semibold text-blue-700">
          <GitPullRequest className="w-3 h-3" />
          Initial commit pushed
        </div>
      </div>
    </ChatChrome>
  );
}

/* ── Use case data — real ways people use Lucid AI ── */

const features = [
  {
    title: "Ship features from a prompt",
    color: "from-[#dc5426] to-orange-400",
    bullets: [
      "Describe the change in plain English",
      "Agent reads your codebase, plans, edits",
      "Tests run before the PR opens",
      "Branch and PR appear in your repo",
    ],
    mockup: <ShipFromPromptMockup />,
  },
  {
    title: "Clear your backlog",
    color: "from-orange-600 to-[#dc5426]",
    bullets: [
      "Paste an issue or link a GitHub/GitLab ticket",
      "Agent reproduces the bug in its sandbox",
      "Adds a regression test along with the fix",
      "Ready-for-review PR — you stay the reviewer",
    ],
    mockup: <BacklogMockup />,
  },
  {
    title: "Build from a template",
    color: "from-orange-400 to-[#dc5426]",
    bullets: [
      "Start from a curated, working template",
      "Iterate in chat — components, routes, copy",
      "Live preview from the Docker sandbox",
      "Push to a new GitHub or GitLab repo",
    ],
    mockup: <FromTemplateMockup />,
  },
];

/* ── Three honest capability cards (replaces the old generic ones) ── */

const bottomCards = [
  {
    title: "Repo-aware agent",
    icon: <MessageSquare className="w-5 h-5 text-[#dc5426]" />,
    iconBg: "bg-orange-100",
    items: [
      "Reads your codebase before acting",
      "Honors existing conventions & lint rules",
      "Tool-call diffs you can review in chat",
    ],
  },
  {
    title: "Sandboxed execution",
    icon: <ShieldCheck className="w-5 h-5 text-[#dc5426]" />,
    iconBg: "bg-orange-100",
    items: [
      "Per-session Docker workspace",
      "Resource limits + orphan cleanup",
      "Code runs nowhere near your prod",
    ],
  },
  {
    title: "Streaming events",
    icon: <Activity className="w-5 h-5 text-[#dc5426]" />,
    iconBg: "bg-orange-100",
    items: [
      "WebSocket from the agent runtime",
      "Plan, tool calls, test output — all live",
      "Stop or redirect the agent mid-task",
    ],
  },
];

/* ── Accordion styling ── */
const COLLAPSED_WIDTH = '80px';
const TRANSITION_DURATION = '600ms';
const TRANSITION_EASING = 'cubic-bezier(0.4, 0, 0.2, 1)';

const contentVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.06, delayChildren: 0.2 } },
  exit:    { opacity: 0, transition: { duration: 0.15 } },
};

const itemVariants = {
  hidden:  { opacity: 0, x: -16, filter: 'blur(4px)' },
  visible: { opacity: 1, x: 0,  filter: 'blur(0px)', transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] } },
  exit:    { opacity: 0, x: -8, transition: { duration: 0.12 } },
};

const mockupVariants = {
  hidden:  { opacity: 0, scale: 0.92, y: 20 },
  visible: { opacity: 1, scale: 1, y: 0, transition: { duration: 0.5, delay: 0.25, ease: [0.25, 0.46, 0.45, 0.94] } },
  exit:    { opacity: 0, scale: 0.95, transition: { duration: 0.15 } },
};

export default function UseCasesSection() {
  const [activeFeature, setActiveFeature] = useState(0);
  const [progressKey, setProgressKey] = useState(0);
  const timerRef = useRef(null);

  const goToNextFeature = useCallback(() => {
    setActiveFeature(prev => (prev + 1) % features.length);
    setProgressKey(k => k + 1);
  }, []);

  const handleFeatureSelect = useCallback((index) => {
    if (index === activeFeature) return;
    setActiveFeature(index);
    setProgressKey(k => k + 1);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(goToNextFeature, 5000);
  }, [goToNextFeature, activeFeature]);

  useEffect(() => {
    timerRef.current = setInterval(goToNextFeature, 5000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [goToNextFeature]);

  return (
    <section id="use-cases" className="px-6 sm:px-10 pb-24 max-w-[1400px] mx-auto w-full">
      <div className="text-center mb-14 max-w-2xl mx-auto">
        <span className="inline-block text-[11px] uppercase tracking-[0.18em] font-semibold text-[#dc5426] mb-3">
          Use cases
        </span>
        <h2 className="text-[36px] sm:text-[44px] lg:text-[52px] font-normal text-slate-900 dark:text-slate-100 tracking-[-0.04em] leading-[1.1] mb-4">
          What people actually do with it
        </h2>
        <p className="text-[16px] text-slate-500 dark:text-slate-400 leading-relaxed font-medium">
          From shipping a feature in one chat turn to clearing a stale backlog — Lucid hands you a branch and a PR, not a code editor to babysit.
        </p>
      </div>

      <div className="relative flex flex-col lg:flex-row gap-3 h-auto lg:h-[480px] mb-10">
        {features.map((feature, index) => {
          const isActive = activeFeature === index;
          return (
            <div
              key={index}
              onClick={() => handleFeatureSelect(index)}
              onMouseEnter={() => handleFeatureSelect(index)}
              className="relative rounded-2xl overflow-hidden cursor-pointer min-h-[120px] lg:min-h-0"
              style={{
                flexGrow: isActive ? 1 : 0,
                flexShrink: isActive ? 1 : 0,
                flexBasis: isActive ? 'auto' : COLLAPSED_WIDTH,
                transition: `flex-grow ${TRANSITION_DURATION} ${TRANSITION_EASING}, flex-shrink ${TRANSITION_DURATION} ${TRANSITION_EASING}, flex-basis ${TRANSITION_DURATION} ${TRANSITION_EASING}`,
                willChange: 'flex-grow, flex-shrink, flex-basis',
              }}
            >
              <div className={cn("absolute inset-0 bg-gradient-to-br", feature.color)} />
              <div
                className="absolute inset-0 bg-white/0 hover:bg-white/5 transition-colors duration-300 z-[5]"
                style={{ pointerEvents: isActive ? 'none' : 'auto' }}
              />

              <AnimatePresence mode="wait">
                {isActive && (
                  <motion.div
                    key={`content-${index}`}
                    variants={contentVariants}
                    initial="hidden"
                    animate="visible"
                    exit="exit"
                    className="relative z-10 h-full flex flex-col lg:flex-row p-6 md:p-10 pb-16"
                  >
                    <div className="lg:flex-1 flex flex-col justify-center lg:pr-8">
                      <motion.h3
                        variants={itemVariants}
                        className="text-2xl sm:text-3xl font-bold text-white mb-6"
                      >
                        {feature.title}
                      </motion.h3>
                      <ul className="space-y-3">
                        {feature.bullets.map((b, i) => (
                          <motion.li
                            key={i}
                            variants={itemVariants}
                            className="flex items-center gap-3 text-white/90 text-base font-medium"
                          >
                            <div className="w-2 h-2 rounded-full bg-white shrink-0" />
                            {b}
                          </motion.li>
                        ))}
                      </ul>
                    </div>

                    <motion.div
                      variants={mockupVariants}
                      className="lg:flex-[1.3] flex items-center justify-center mt-6 lg:mt-0"
                    >
                      {feature.mockup}
                    </motion.div>
                  </motion.div>
                )}
              </AnimatePresence>

              <AnimatePresence>
                {isActive && (
                  <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 6 }}
                    transition={{ duration: 0.35, delay: 0.3 }}
                    className="absolute bottom-5 left-0 right-0 z-20 flex items-center justify-center gap-2.5"
                  >
                    {features.map((_, pIndex) => (
                      <div
                        key={pIndex}
                        onClick={(e) => { e.stopPropagation(); handleFeatureSelect(pIndex); }}
                        className="relative rounded-full overflow-hidden cursor-pointer group/bar"
                        style={{
                          width: activeFeature === pIndex ? '56px' : '32px',
                          height: '4px',
                          transition: 'width 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
                        }}
                      >
                        <div className="absolute inset-0 bg-white/25 rounded-full group-hover/bar:bg-white/40 transition-colors" />
                        {activeFeature === pIndex && (
                          <div
                            key={progressKey}
                            className="absolute inset-y-0 left-0 bg-white rounded-full"
                            style={{ animation: 'progressFill 5s linear forwards' }}
                          />
                        )}
                      </div>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>

      <div className="grid md:grid-cols-3 gap-4">
        {bottomCards.map((card, i) => (
          <div key={i} className="bg-slate-100/80 dark:bg-slate-800/60 rounded-2xl p-6 md:p-8 hover:bg-white dark:hover:bg-slate-800 hover:shadow-lg dark:hover:shadow-black/20 transition-all duration-300 cursor-default border border-slate-200/60 dark:border-slate-700/60 hover:border-slate-300 dark:hover:border-slate-600">
            <div className="flex justify-between items-start mb-8">
              <h4 className="text-xl font-bold text-slate-900 dark:text-slate-100 leading-tight">{card.title}</h4>
              <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center shrink-0", card.iconBg)}>
                {card.icon}
              </div>
            </div>
            <ul className="space-y-3">
              {card.items.map((item, j) => (
                <li key={j} className="flex items-center gap-2.5 text-[14px] font-medium text-slate-500 dark:text-slate-400">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#dc5426] dark:bg-orange-400 shrink-0" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
