'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Map, ListChecks, FileText } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';

/* ── Mockup Components ── */

function DocsMockup() {
  return (
    <div className="bg-white rounded-xl shadow-2xl overflow-hidden w-full max-w-md border border-white/20">
      <div className="bg-slate-50 border-b border-slate-100 flex items-center px-4 py-2.5 gap-2">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="text-[9px] text-slate-400 font-mono ml-3">lucid-ai / docs-generator</div>
        <div className="ml-auto text-slate-300 text-xs">...</div>
      </div>
      <div className="flex h-[280px] text-[10px] font-mono">
        <div className="flex-[1.2] bg-slate-50 p-3 border-r border-slate-100 flex flex-col gap-2 text-left">
          <div className="text-slate-400 text-[9px]">Lucid AI · 9:15 AM</div>
          <div className="text-slate-700 leading-relaxed text-[10px]">
            Scanning repository structure and generating comprehensive documentation...
          </div>
          <div className="mt-1 text-blue-600">$ lucid docs generate --source ./src</div>
          <div className="text-emerald-600 text-[9px] mt-1 leading-relaxed">
            ✓ Parsed 147 files<br />
            ✓ Extracted 23 API endpoints<br />
            ✓ Generated 12 doc pages
          </div>
          <div className="text-blue-600 mt-1">$ lucid docs publish</div>
          <div className="text-emerald-600">Published to docs.yourapp.com</div>
        </div>
        <div className="flex-1 bg-white p-2.5 flex flex-col">
          <div className="text-[9px] font-bold text-slate-700 mb-2">Generated Docs Preview</div>
          <div className="flex-1 bg-slate-50 rounded-lg p-2 flex flex-col gap-1.5 text-[9px] border border-slate-100">
            <div className="font-bold text-slate-800 text-[10px]"># API Reference</div>
            <div className="text-slate-500 text-[9px]">## Authentication</div>
            <div className="bg-slate-900 text-emerald-400 p-1.5 rounded text-[8px]">POST /api/auth/login</div>
            <div className="text-slate-500 text-[9px]">## Users</div>
            <div className="bg-slate-900 text-cyan-400 p-1.5 rounded text-[8px]">GET /api/users/:id</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AnalyticsMockup() {
  return (
    <div className="bg-white rounded-xl shadow-2xl overflow-hidden w-full max-w-md border border-white/20">
      <div className="bg-slate-50 border-b border-slate-100 flex items-center px-4 py-2.5 gap-2">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="text-[9px] text-slate-400 font-mono ml-3">lucid-ai / analytics</div>
        <div className="ml-auto text-slate-300 text-xs">...</div>
      </div>
      <div className="flex h-[280px] text-[10px] font-mono">
        <div className="flex-[1.2] bg-slate-50 p-3 border-r border-slate-100 flex flex-col gap-2 text-left">
          <div className="text-slate-400 text-[9px]">Lucid AI · 7:02 AM</div>
          <div className="text-slate-700 text-[10px]">I have completed the requested visualizations:</div>
          <div className="mt-1 border border-slate-200 rounded-lg p-2 bg-white">
            <div className="h-1 w-full bg-blue-200 rounded mb-1" />
            <div className="h-1 w-3/4 bg-cyan-200 rounded mb-1" />
            <div className="h-1 w-1/2 bg-blue-300 rounded mb-1" />
            <div className="h-1 w-2/3 bg-cyan-300 rounded" />
            <div className="text-[8px] text-slate-400 mt-1">Price Trend Analysis</div>
          </div>
          <div className="text-slate-400 text-[9px] mt-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-slate-300" /> Session Ended
          </div>
        </div>
        <div className="flex-1 bg-[#1e293b] p-2.5 flex flex-col text-[9px] text-slate-300">
          <div className="flex gap-2 mb-2 text-[8px]">
            <span className="text-blue-400 border-b border-blue-400 pb-0.5">Shell</span>
            <span className="text-slate-500">Browser</span>
            <span className="text-slate-500">Editor</span>
            <span className="text-slate-500">Planner</span>
          </div>
          <div className="text-emerald-400">$ python3 analyze_data.py</div>
          <div className="text-slate-400 mt-1 text-[8px] leading-relaxed">
            Summary Statistics:<br />
            year: resale_price<br />
            mean: 443860.53<br />
            std: 119141.32
          </div>
        </div>
      </div>
    </div>
  );
}

function AppDevMockup() {
  return (
    <div className="bg-white rounded-xl shadow-2xl overflow-hidden w-full max-w-md border border-white/20">
      <div className="bg-slate-50 border-b border-slate-100 flex items-center px-4 py-2.5 gap-2">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
          <div className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div className="text-[9px] text-slate-400 font-mono ml-3">lucid-ai / app-builder</div>
      </div>
      <div className="flex h-[280px] text-[10px] font-mono">
        <div className="flex-[1.2] bg-slate-50 p-3 border-r border-slate-100 flex flex-col gap-2 text-left">
          <div className="text-slate-400 text-[9px]">Lucid AI · 3:15 PM</div>
          <div className="text-slate-700 text-[10px]">Fixed 3 critical bugs and added test coverage.</div>
          <div className="text-blue-600 mt-1">$ npm run test -- --coverage</div>
          <div className="text-emerald-600 text-[9px] mt-1 leading-relaxed">
            PASS src/UserDashboard.test.tsx<br />
            ✓ renders user profile (12ms)<br />
            ✓ handles loading state (8ms)<br />
            ✓ fetches data on mount (15ms)
          </div>
          <div className="text-emerald-600 mt-1">Tests: 3 passed, 3 total</div>
        </div>
        <div className="flex-1 bg-white p-2.5 flex flex-col">
          <div className="border border-slate-100 rounded p-1 flex items-center gap-1.5 mb-2 text-[9px]">
            <span className="text-slate-300">@</span>
            <span className="text-slate-400">localhost:3000</span>
          </div>
          <div className="flex-1 bg-slate-50 rounded-lg p-2 flex flex-col gap-1.5 text-[9px] border border-slate-100">
            <div className="h-4 w-full bg-blue-100 rounded" />
            <div className="flex gap-1.5 flex-1">
              <div className="flex-1 bg-white rounded border border-slate-100 p-1">
                <div className="h-1.5 w-8 bg-slate-200 rounded mb-1" />
                <div className="h-1 w-full bg-slate-100 rounded" />
              </div>
              <div className="flex-1 bg-white rounded border border-slate-100 p-1">
                <div className="h-1.5 w-8 bg-slate-200 rounded mb-1" />
                <div className="h-1 w-full bg-slate-100 rounded" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Feature Data ── */

const features = [
  {
    title: "Documentation",
    color: "from-blue-500 to-cyan-400",
    bullets: ["Auto-generate docs from code or URL", "API reference & SDK documentation", "Compete with enterprise doc platforms"],
    mockup: <DocsMockup />
  },
  {
    title: "Data Engineering + Analysis",
    color: "from-blue-600 to-cyan-500",
    bullets: ["Data warehouse migrations", "ETL development", "Data cleaning and preprocessing"],
    mockup: <AnalyticsMockup />
  },
  {
    title: "Application Development",
    color: "from-emerald-400 to-cyan-400",
    bullets: ["Bug fixes & edge case resolution", "Unit and E2E testing", "Building SaaS integrations", "Frontend & backend debugging"],
    mockup: <AppDevMockup />
  }
];

/* ── Bottom Cards Data ── */

const bottomCards = [
  {
    title: "Planning",
    icon: <Map className="w-5 h-5 text-blue-600" />,
    iconBg: "bg-blue-100",
    items: ["Roadmap generation", "Sprint & milestone planning", "Architecture decisions"]
  },
  {
    title: "Bug & Issue Triage",
    icon: <ListChecks className="w-5 h-5 text-cyan-600" />,
    iconBg: "bg-cyan-100",
    items: ["Automated on-call response", "Ticket resolution", "CI/CD autotriage"]
  },
  {
    title: "Documentation",
    icon: <FileText className="w-5 h-5 text-violet-600" />,
    iconBg: "bg-violet-100",
    items: ["Enterprise-grade docs", "API reference generation", "Maintaining documentation"]
  }
];

/* ── Accordion Panel Styles (CSS-driven for GPU acceleration) ── */

const COLLAPSED_WIDTH = '80px';
const TRANSITION_DURATION = '600ms';
const TRANSITION_EASING = 'cubic-bezier(0.4, 0, 0.2, 1)';

/* ── Stagger variants for Framer Motion content ── */

const contentVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.06,
      delayChildren: 0.2,
    }
  },
  exit: {
    opacity: 0,
    transition: { duration: 0.15 }
  }
};

const itemVariants = {
  hidden: { opacity: 0, x: -16, filter: 'blur(4px)' },
  visible: { 
    opacity: 1, 
    x: 0, 
    filter: 'blur(0px)',
    transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] }
  },
  exit: { opacity: 0, x: -8, transition: { duration: 0.12 } }
};

const mockupVariants = {
  hidden: { opacity: 0, scale: 0.92, y: 20 },
  visible: { 
    opacity: 1, 
    scale: 1, 
    y: 0,
    transition: { duration: 0.5, delay: 0.25, ease: [0.25, 0.46, 0.45, 0.94] }
  },
  exit: { opacity: 0, scale: 0.95, transition: { duration: 0.15 } }
};

/* ── Main Section Component ── */

export default function UseCasesSection() {
  const [activeFeature, setActiveFeature] = useState(0);
  const [progressKey, setProgressKey] = useState(0);
  const timerRef = useRef(null);

  const goToNextFeature = useCallback(() => {
    setActiveFeature(prev => (prev + 1) % features.length);
    setProgressKey(k => k + 1);
  }, []);

  const handleFeatureSelect = useCallback((index) => {
    if (index === activeFeature) return; // No-op if already active
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
    <section className="px-6 sm:px-10 pb-24 max-w-[1400px] mx-auto w-full">
      <div className="mb-16">
        <h2 className="text-4xl sm:text-5xl font-bold text-slate-900 dark:text-slate-100 mb-6">
          Use <span className="text-blue-500 dark:text-blue-400">cases</span>
        </h2>
        <p className="text-lg text-slate-500 dark:text-slate-400 max-w-2xl leading-relaxed">
          From implementing new features to fixing thousands of lint errors, 
          Lucid AI can clear your backlog, modernize your codebase, and 
          help you build more.
        </p>
      </div>

      {/* ── HORIZONTAL ACCORDION ── */}
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
              {/* Gradient Background */}
              <div className={cn("absolute inset-0 bg-gradient-to-br", feature.color)} />

              {/* Subtle inner glow overlay on hover for collapsed panels */}
              <div 
                className="absolute inset-0 bg-white/0 hover:bg-white/5 transition-colors duration-300 z-[5]"
                style={{ pointerEvents: isActive ? 'none' : 'auto' }}
              />

              {/* Expanded Content — AnimatePresence for mount/unmount */}
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
                    {/* LEFT: Text Content */}
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

                    {/* RIGHT: Mockup */}
                    <motion.div 
                      variants={mockupVariants}
                      className="lg:flex-[1.3] flex items-center justify-center mt-6 lg:mt-0"
                    >
                      {feature.mockup}
                    </motion.div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Progress bars — ONLY inside the active/opened panel */}
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
                            style={{
                              animation: 'progressFill 5s linear forwards'
                            }}
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

      {/* ── BOTTOM 3 CARDS ── */}
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
                  <div className="w-1.5 h-1.5 rounded-full bg-blue-500 dark:bg-blue-400 shrink-0" />
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
