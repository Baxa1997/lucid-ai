'use client';

import { useRef, useState } from 'react';
import { motion, useInView } from 'framer-motion';
import { cn } from '@/lib/utils';
import {
  Brain, Cpu, Network, Shield, Zap, BarChart3,
  Bot, Layers, Workflow, Eye
} from 'lucide-react';

/* ─── AI Model Cards ─── */

const models = [
  {
    name: 'Claude Models',
    provider: 'Anthropic',
    specialty: 'Code Reasoning & Safety',
    description: 'Unmatched at understanding complex codebases, multi-step reasoning, and producing safe, reliable refactors. Our go-to for architecture decisions and critical code changes.',
    color: 'from-amber-500 to-orange-600',
    bgAccent: 'bg-amber-500/10 dark:bg-amber-500/5',
    borderAccent: 'border-amber-200 dark:border-amber-500/20',
    textAccent: 'text-amber-600 dark:text-amber-400',
    icon: Brain,
    subModels: ['Claude 4.6 Sonnet', 'Claude 3.5 Sonnet', 'Claude 3.5 Haiku', 'Claude 4.6 Opus'],
    strengths: [
      { label: 'Code Quality', value: 98 },
      { label: 'Reasoning', value: 97 },
    ],
  },
  {
    name: 'Gemini Models',
    provider: 'Google DeepMind',
    specialty: 'Speed & Scale',
    description: 'Blazing-fast inference with the largest context windows in the industry. Perfect for scanning entire repositories, bulk operations, and real-time code suggestions.',
    color: 'from-blue-500 to-cyan-500',
    bgAccent: 'bg-blue-500/10 dark:bg-blue-500/5',
    borderAccent: 'border-blue-200 dark:border-blue-500/20',
    textAccent: 'text-blue-600 dark:text-blue-400',
    icon: Zap,
    subModels: ['Gemini 3.1 Pro', 'Gemini 3 Pro', 'Gemini 2.5 Flash', 'Gemini 3.0 Flash'],
    strengths: [
      { label: 'Speed', value: 99 },
      { label: 'Context Window', value: 100 },
    ],
  },
  {
    name: 'GPT Models',
    provider: 'OpenAI',
    specialty: 'Versatility & Creativity',
    description: 'The gold standard for general-purpose AI. Excels at understanding developer intent, generating clean readable code, and creative problem-solving across any language.',
    color: 'from-emerald-500 to-teal-600',
    bgAccent: 'bg-emerald-500/10 dark:bg-emerald-500/5',
    borderAccent: 'border-emerald-200 dark:border-emerald-500/20',
    textAccent: 'text-emerald-600 dark:text-emerald-400',
    icon: Cpu,
    subModels: ['GPT-4.1', 'GPT-4o', 'GPT-4o mini', 'o3-mini'],
    strengths: [
      { label: 'Versatility', value: 96 },
      { label: 'Creativity', value: 95 },
    ],
  },
];

/* ─── Agent Capabilities ─── */

const agents = [
  {
    title: 'Planner Agent',
    description: 'Breaks down complex tasks into executable steps',
    icon: Workflow,
    gradient: 'from-violet-600 to-indigo-600',
  },
  {
    title: 'Code Agent',
    description: 'Writes, modifies and refactors production code',
    icon: Layers,
    gradient: 'from-blue-600 to-cyan-500',
  },
  {
    title: 'Review Agent',
    description: 'Catches bugs, security issues, and anti-patterns',
    icon: Eye,
    gradient: 'from-rose-500 to-pink-600',
  },
  {
    title: 'Test Agent',
    description: 'Generates comprehensive unit and integration tests',
    icon: Shield,
    gradient: 'from-emerald-500 to-teal-600',
  },
];

/* ─── Stat Bar ─── */

function StatBar({ label, value, delay, isVisible, color }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 w-20 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={isVisible ? { width: `${value}%` } : {}}
          transition={{ delay: delay, duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
          className={cn("h-full rounded-full bg-gradient-to-r", color || "from-violet-500 to-indigo-500")}
        />
      </div>
      <span className="text-[11px] font-bold text-slate-700 dark:text-slate-300 w-8 text-right">{value}%</span>
    </div>
  );
}

/* ─── Main Component ─── */

export default function AIPowerSection() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-100px' });
  const [hoveredModel, setHoveredModel] = useState(null);

  return (
    <section ref={ref} className="px-6 sm:px-10 py-24 lg:py-32 max-w-[1400px] mx-auto w-full">
      {/* Header */}
      <div className="text-center mb-20">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5 }}
          className="inline-flex items-center gap-2 bg-indigo-50 dark:bg-indigo-500/10 border border-indigo-100 dark:border-indigo-500/20 px-4 py-1.5 rounded-full mb-6"
        >
          <Bot className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
          <span className="text-[12px] font-semibold text-indigo-600 dark:text-indigo-400">AI Infrastructure</span>
        </motion.div>

        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="text-4xl sm:text-5xl font-bold text-slate-900 dark:text-slate-100 mb-5"
        >
          Powered by the{' '}
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-600 to-violet-600 dark:from-indigo-400 dark:to-violet-400">
            best AI models
          </span>
        </motion.h2>

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5, delay: 0.2 }}
          className="text-lg text-slate-500 dark:text-slate-400 max-w-2xl mx-auto leading-relaxed"
        >
          Every model family is supported in full. Lucid AI automatically selects the best model for each task — or let you choose. Each model is used where it excels.
        </motion.p>
      </div>

      {/* ─── AI Models Grid ─── */}
      <div className="grid md:grid-cols-3 gap-5 mb-20">
        {models.map((model, i) => (
          <motion.div
            key={model.name}
            initial={{ opacity: 0, y: 30 }}
            animate={isInView ? { opacity: 1, y: 0 } : {}}
            transition={{ delay: 0.3 + i * 0.12, duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
            onMouseEnter={() => setHoveredModel(i)}
            onMouseLeave={() => setHoveredModel(null)}
            className={cn(
              "relative rounded-2xl p-6 border transition-all duration-300 cursor-default overflow-hidden group",
              "bg-white dark:bg-slate-900",
              model.borderAccent,
              hoveredModel === i && "shadow-xl -translate-y-1"
            )}
          >
            {/* Background glow on hover */}
            <div className={cn(
              "absolute -top-20 -right-20 w-40 h-40 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-500 blur-3xl",
              `bg-gradient-to-br ${model.color}`
            )} />

            <div className="relative z-10">
              {/* Icon + Provider */}
              <div className="flex items-center justify-between mb-4">
                <div className={cn("w-10 h-10 rounded-xl bg-gradient-to-br flex items-center justify-center text-white", model.color)}>
                  <model.icon className="w-5 h-5" />
                </div>
                <span className={cn("text-[10px] font-bold uppercase tracking-wider", model.textAccent)}>
                  {model.provider}
                </span>
              </div>

              {/* Name + Specialty */}
              <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 mb-1">{model.name}</h3>
              <div className={cn("inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold mb-3", model.bgAccent, model.textAccent)}>
                {model.specialty}
              </div>

              <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed mb-4">
                {model.description}
              </p>

              {/* Supported models list */}
              <div className="mb-5">
                <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">Supported Models</p>
                <div className="flex flex-wrap gap-1.5">
                  {model.subModels.map((sub) => (
                    <span
                      key={sub}
                      className={cn(
                        "text-[10px] font-semibold px-2 py-1 rounded-lg border",
                        model.bgAccent,
                        model.borderAccent,
                        model.textAccent
                      )}
                    >
                      {sub}
                    </span>
                  ))}
                </div>
              </div>

              {/* Strength bars */}
              <div className="space-y-2.5">
                {model.strengths.map((s, j) => (
                  <StatBar
                    key={s.label}
                    label={s.label}
                    value={s.value}
                    delay={0.5 + i * 0.1 + j * 0.1}
                    isVisible={isInView}
                    color={model.color}
                  />
                ))}
              </div>
            </div>
          </motion.div>
        ))}
      </div>

      {/* ─── Agent Architecture ─── */}
      <div className="text-center mb-12">
        <motion.h3
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ delay: 0.6, duration: 0.5 }}
          className="text-2xl sm:text-3xl font-bold text-slate-900 dark:text-slate-100 mb-3"
        >
          Multi-Agent Architecture
        </motion.h3>
        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={isInView ? { opacity: 1, y: 0 } : {}}
          transition={{ delay: 0.7, duration: 0.5 }}
          className="text-[15px] text-slate-500 dark:text-slate-400 max-w-lg mx-auto"
        >
          Specialized agents collaborate to deliver production-ready results
        </motion.p>
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {agents.map((agent, i) => (
          <motion.div
            key={agent.title}
            initial={{ opacity: 0, y: 20 }}
            animate={isInView ? { opacity: 1, y: 0 } : {}}
            transition={{ delay: 0.8 + i * 0.1, duration: 0.4 }}
            className="group relative rounded-2xl p-5 border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:shadow-lg hover:-translate-y-1 transition-all duration-300 cursor-default overflow-hidden"
          >
            {/* Top accent line */}
            <div className={cn(
              "absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r scale-x-0 group-hover:scale-x-100 transition-transform duration-500 origin-left",
              agent.gradient
            )} />

            <div className={cn("w-10 h-10 rounded-xl bg-gradient-to-br flex items-center justify-center text-white mb-4", agent.gradient)}>
              <agent.icon className="w-5 h-5" />
            </div>
            <h4 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 mb-1.5">{agent.title}</h4>
            <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">{agent.description}</p>
          </motion.div>
        ))}
      </div>

      {/* ─── Bottom trust badge ─── */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={isInView ? { opacity: 1, y: 0 } : {}}
        transition={{ delay: 1.2, duration: 0.5 }}
        className="flex flex-col items-center mt-16 gap-3"
      >
        <div className="flex items-center gap-4">
          <div className="flex -space-x-2">
            {['from-violet-500 to-indigo-600', 'from-blue-500 to-cyan-500', 'from-amber-500 to-orange-600', 'from-emerald-500 to-teal-600'].map((g, i) => (
              <div key={i} className={cn("w-8 h-8 rounded-full bg-gradient-to-br border-2 border-white dark:border-slate-950 flex items-center justify-center text-white text-[10px] font-bold", g)}>
                {['AI', 'ML', 'LM', 'AG'][i]}
              </div>
            ))}
          </div>
          <span className="text-[13px] text-slate-500 dark:text-slate-400 font-medium">
            4 specialized agents · 12+ AI models · 3 frontier providers · Always improving
          </span>
        </div>
      </motion.div>
    </section>
  );
}
