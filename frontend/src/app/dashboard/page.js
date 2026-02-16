'use client';

import { Code2, BookOpen, ArrowRight, Sparkles, Zap } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import useFlowStore from '@/store/useFlowStore';

export default function DashboardPage() {
  const router = useRouter();
  const { setSelectedGoal, user } = useFlowStore();
  const [hoveredCard, setHoveredCard] = useState(null);

  const handleGoalSelect = (goal, path) => {
    setSelectedGoal(goal);
    router.push(path);
  };

  return (
    <div className="min-h-screen bg-white relative overflow-hidden">

      {/* Subtle background pattern */}
      <div className="absolute inset-0 bg-grid-pattern opacity-50" />

      {/* Accent gradient top */}
      <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-blue-500/40 to-transparent" />

      {/* Header */}
      <header className="relative z-20 h-16 border-b border-slate-100 flex items-center justify-between px-8 bg-white/80 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shadow-sm">
            <Zap className="w-4 h-4 text-white fill-current" />
          </div>
          <span className="font-bold text-slate-900 text-sm tracking-tight">Lucid AI</span>
          <span className="text-[9px] font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full border border-blue-100">BETA</span>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500 font-medium">{user.name}</span>
          <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-[10px] font-bold text-white shadow-sm">
            {user.avatar}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="relative z-10 flex flex-col items-center justify-center min-h-[calc(100vh-64px)] px-8">
        
        {/* Hero Text */}
        <div className="text-center mb-14 animate-slide-up">
          <div className="inline-flex items-center gap-2 bg-blue-50 border border-blue-100 px-3 py-1.5 rounded-full mb-5">
            <Sparkles className="w-3.5 h-3.5 text-blue-600" />
            <span className="text-[11px] font-semibold text-blue-600">AI-Powered Development Platform</span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-extrabold text-slate-900 tracking-tight leading-[1.1] mb-4">
            What would you like<br />
            <span className="text-gradient-blue">to do today?</span>
          </h1>
          <p className="text-slate-500 text-base font-medium max-w-md mx-auto leading-relaxed">
            Choose your workflow and let the AI handle the heavy lifting.
          </p>
        </div>

        {/* Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full max-w-3xl animate-slide-up" style={{ animationDelay: '0.15s' }}>

          {/* AI Software Engineer Card */}
          <button
            onClick={() => handleGoalSelect('engineer', '/dashboard/engineer')}
            onMouseEnter={() => setHoveredCard('engineer')}
            onMouseLeave={() => setHoveredCard(null)}
            className="group relative bg-white rounded-2xl border border-slate-200 p-8 text-left transition-all duration-300 hover:shadow-card-hover hover:border-blue-200 hover:-translate-y-1 overflow-hidden"
          >
            {/* Top accent */}
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-blue-500 to-blue-600 scale-x-0 group-hover:scale-x-100 transition-transform duration-300 origin-left" />

            <div className="w-12 h-12 bg-blue-50 border border-blue-100 rounded-xl flex items-center justify-center mb-6 group-hover:bg-blue-600 group-hover:border-blue-600 transition-all duration-300">
              <Code2 className="w-6 h-6 text-blue-600 group-hover:text-white transition-colors" />
            </div>

            <span className="inline-block text-[9px] font-bold text-emerald-600 bg-emerald-50 border border-emerald-100 px-2 py-0.5 rounded-full mb-4 uppercase tracking-wider">
              Recommended
            </span>

            <h3 className="text-xl font-bold text-slate-900 mb-2">AI Software Engineer</h3>
            <p className="text-sm text-slate-500 leading-relaxed mb-6">
              Build, debug, and ship features automatically. Let the AI write production-ready code from your instructions.
            </p>

            <div className="flex items-center gap-2 text-blue-600 font-semibold text-sm">
              <span>Get started</span>
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </div>
          </button>

          {/* Project Documentation Card */}
          <button
            onClick={() => handleGoalSelect('documentation', '/dashboard/docs')}
            onMouseEnter={() => setHoveredCard('docs')}
            onMouseLeave={() => setHoveredCard(null)}
            className="group relative bg-white rounded-2xl border border-slate-200 p-8 text-left transition-all duration-300 hover:shadow-card-hover hover:border-violet-200 hover:-translate-y-1 overflow-hidden"
          >
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-violet-500 to-purple-600 scale-x-0 group-hover:scale-x-100 transition-transform duration-300 origin-left" />

            <div className="w-12 h-12 bg-violet-50 border border-violet-100 rounded-xl flex items-center justify-center mb-6 group-hover:bg-violet-600 group-hover:border-violet-600 transition-all duration-300">
              <BookOpen className="w-6 h-6 text-violet-600 group-hover:text-white transition-colors" />
            </div>

            <span className="inline-block text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-100 px-2 py-0.5 rounded-full mb-4 uppercase tracking-wider">
              Instant
            </span>

            <h3 className="text-xl font-bold text-slate-900 mb-2">Project Documentation</h3>
            <p className="text-sm text-slate-500 leading-relaxed mb-6">
              Generate and maintain comprehensive documentation for your codebase. Auto-synced and always up-to-date.
            </p>

            <div className="flex items-center gap-2 text-violet-600 font-semibold text-sm">
              <span>Start writing</span>
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </div>
          </button>
        </div>

        {/* Footer note */}
        <p className="text-[11px] text-slate-400 font-medium mt-10 tracking-wide animate-slide-up" style={{ animationDelay: '0.3s' }}>
          u-code · AI-Powered Development Platform
        </p>
      </main>
    </div>
  );
}
