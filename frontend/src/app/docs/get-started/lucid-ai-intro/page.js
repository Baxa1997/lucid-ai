'use client';

import { Sparkles, ChevronRight, ArrowRight, Info, CheckCircle2, Terminal, Copy, MessageSquare } from 'lucide-react';
import Link from 'next/link';

export default function LucidAIIntroPage() {
  return (
    <div className="animate-page-enter">
      {/* Breadcrumb — matches Devin's "Get Started" colored breadcrumb */}
      <div className="mb-6">
        <span className="text-[14px] font-semibold text-violet-600 dark:text-violet-400">Get Started</span>
      </div>

      {/* Main Heading */}
      <h1 className="text-[32px] font-bold text-slate-900 dark:text-slate-100 mb-5 tracking-tight leading-[1.2]">
        Introducing Lucid AI
      </h1>

      {/* Intro Text */}
      <p className="text-[16px] text-slate-600 dark:text-slate-400 leading-relaxed mb-8">
        Lucid AI is the AI software engineer, built to help ambitious engineering teams crush their backlogs.
      </p>

      {/* Hero Video/Image placeholder — matches Devin's embedded video */}
      <div className="relative bg-[#0f172a] rounded-2xl mb-10 overflow-hidden shadow-xl">
        <div className="aspect-video flex items-center justify-center relative">
          {/* Browser chrome mockup */}
          <div className="absolute inset-0 flex flex-col">
            {/* Tab bar */}
            <div className="h-9 bg-[#1e293b] flex items-center px-4 gap-2 flex-shrink-0">
              <div className="flex gap-1.5">
                <div className="w-2.5 h-2.5 rounded-full bg-rose-500/70" />
                <div className="w-2.5 h-2.5 rounded-full bg-amber-500/70" />
                <div className="w-2.5 h-2.5 rounded-full bg-green-500/70" />
              </div>
              <div className="ml-4 flex items-center gap-2 bg-[#0f172a] px-3 py-1 rounded-md">
                <div className="w-3 h-3 rounded-full bg-violet-500/40" />
                <span className="text-[11px] text-slate-400 font-mono">Implement split panes</span>
                <span className="text-[10px] text-slate-500 ml-1">Fast</span>
              </div>
            </div>
            {/* Content area */}
            <div className="flex-1 flex">
              {/* Chat panel */}
              <div className="flex-1 flex flex-col p-5 justify-between">
                <div className="space-y-4">
                  <div className="bg-[#1e293b] rounded-xl p-4 max-w-[80%]">
                    <p className="text-[12px] text-slate-300 leading-relaxed">
                      Implement split panes (Horizontal/Vertical Terminal Multiplexing). Send me a video showing the feature when done.
                    </p>
                    <p className="text-[10px] text-slate-500 mt-2">@andrewgcodes/mochi</p>
                  </div>
                  <div className="bg-[#1e293b]/60 rounded-xl p-4 max-w-[85%]">
                    <p className="text-[12px] text-slate-300/80 leading-relaxed">
                      I'll implement split panes (horizontal/vertical terminal multiplexing) in the mochi repo. Let me start by exploring the codebase...
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-4 text-[11px] text-emerald-400">
                  <span>✓ Worked for 1m 18s</span>
                  <span className="text-green-400">+1511</span>
                  <span className="text-red-400">-122</span>
                </div>
              </div>
              {/* IDE panel */}
              <div className="w-[40%] bg-[#1a1a2e] border-l border-white/5 flex items-center justify-center">
                <div className="text-center">
                  <div className="flex items-center justify-center gap-4 mb-3">
                    <span className="text-[10px] text-slate-500 px-2 py-0.5 bg-[#0f172a] rounded">PR #61</span>
                    <span className="text-[10px] text-slate-500 px-2 py-0.5 bg-[#0f172a] rounded">Diff</span>
                    <span className="text-[10px] text-violet-400 px-2 py-0.5 bg-violet-500/10 rounded font-medium">Browser</span>
                  </div>
                  <div className="text-[11px] text-slate-500">about:blank</div>
                </div>
              </div>
            </div>
          </div>
        </div>
        {/* Controls bar */}
        <div className="h-10 bg-[#1e293b] border-t border-white/5 flex items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 rounded-full bg-slate-600 flex items-center justify-center text-white text-[10px]">▶</div>
            <span className="text-[11px] text-slate-400 font-mono">0:00 / 0:35</span>
          </div>
          <div className="flex items-center gap-2 text-slate-500">
            <span className="text-[11px]">⌘I</span>
            <div className="w-6 h-6 rounded-full bg-violet-600 flex items-center justify-center">
              <MessageSquare className="w-3 h-3 text-white" />
            </div>
          </div>
        </div>
      </div>

      {/* Ask a question input — like Devin */}
      <div className="mb-12 relative">
        <input
          type="text"
          placeholder="Ask a question..."
          className="w-full px-4 py-3 text-[14px] bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-500/20 dark:focus:ring-violet-400/20 focus:border-violet-500 dark:focus:border-violet-400 transition-all text-slate-700 dark:text-slate-300 placeholder:text-slate-400 dark:placeholder:text-slate-500"
        />
        <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-2">
          <span className="text-[11px] text-slate-400 dark:text-slate-500">⌘I</span>
          <div className="w-7 h-7 rounded-full bg-gradient-to-r from-violet-600 to-indigo-600 flex items-center justify-center cursor-pointer hover:shadow-lg hover:shadow-violet-500/25 transition-all">
            <ArrowRight className="w-3.5 h-3.5 text-white" />
          </div>
        </div>
      </div>

      {/* Section: Already Signed Up */}
      <section id="already-signed-up" className="mb-10">
        <h2 className="text-[20px] font-bold text-violet-600 dark:text-violet-400 mb-3 tracking-tight">
          <Link href="#" className="hover:underline decoration-violet-300 dark:decoration-violet-600 decoration-2 underline-offset-4">Already Signed Up? Get Started Now:</Link>
        </h2>
        <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed">
          If you already have access, jump straight to your <Link href="#" className="text-violet-600 dark:text-violet-400 font-semibold hover:underline">first session</Link> to start building with Lucid AI.
        </p>
      </section>

      {/* Section: Introduction */}
      <section id="introduction" className="mb-10">
        <h2 className="text-[22px] font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tight">What is Lucid AI?</h2>
        <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-5">
          Lucid AI is designed to work alongside you as a tireless, high-capacity collaborator. 
          It doesn't just write code — it handles the entire development lifecycle:
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-5 bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded-xl">
            <div className="flex items-center gap-3 mb-2">
              <CheckCircle2 className="w-4 h-4 text-violet-600 dark:text-violet-400 flex-shrink-0" />
              <h4 className="font-bold text-[14px] text-slate-900 dark:text-slate-100">End-to-End Execution</h4>
            </div>
            <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">From setting up repos to deploying to production without manual intervention.</p>
          </div>
          <div className="p-5 bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded-xl">
            <div className="flex items-center gap-3 mb-2">
              <Terminal className="w-4 h-4 text-indigo-600 dark:text-indigo-400 flex-shrink-0" />
              <h4 className="font-bold text-[14px] text-slate-900 dark:text-slate-100">Real-time Problem Solving</h4>
            </div>
            <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">Detects bugs, reads documentation, and adapts to new technologies on the fly.</p>
          </div>
        </div>
      </section>

      {/* Section: Key Features */}
      <section id="key-features" className="mb-10">
        <h2 className="text-[22px] font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tight">Key Features</h2>
        <ul className="space-y-3 text-[15px]">
          <li className="flex gap-3">
            <div className="mt-2 w-1.5 h-1.5 rounded-full bg-violet-500 flex-shrink-0" />
            <div>
              <span className="font-semibold text-slate-900 dark:text-slate-100">Self-Correction:</span>{' '}
              <span className="text-slate-600 dark:text-slate-400">Monitors its own work and automatically fixes errors as they arise.</span>
            </div>
          </li>
          <li className="flex gap-3">
            <div className="mt-2 w-1.5 h-1.5 rounded-full bg-violet-500 flex-shrink-0" />
            <div>
              <span className="font-semibold text-slate-900 dark:text-slate-100">Tool Mastery:</span>{' '}
              <span className="text-slate-600 dark:text-slate-400">Uses shells, code editors, and browsers just like a human developer.</span>
            </div>
          </li>
          <li className="flex gap-3">
            <div className="mt-2 w-1.5 h-1.5 rounded-full bg-violet-500 flex-shrink-0" />
            <div>
              <span className="font-semibold text-slate-900 dark:text-slate-100">Context Awareness:</span>{' '}
              <span className="text-slate-600 dark:text-slate-400">Understands your entire codebase, not just the file you're working on.</span>
            </div>
          </li>
        </ul>
      </section>

      {/* Section: How it works */}
      <section id="how-it-works" className="mb-10">
        <h2 className="text-[22px] font-bold text-slate-900 dark:text-slate-100 mb-5 tracking-tight">How it works</h2>
        
        <div id="autonomous-coding" className="mb-8">
          <h3 className="text-[18px] font-bold text-slate-900 dark:text-slate-100 mb-2 tracking-tight">Autonomous Coding</h3>
          <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed">
            Lucid AI breaks down complex requirements into actionable steps. It uses a high-performance reasoning engine to decide which files to modify and how to test the changes.
          </p>
        </div>

        <div id="tool-integration" className="mb-8">
          <h3 className="text-[18px] font-bold text-slate-900 dark:text-slate-100 mb-2 tracking-tight">Tool Integration</h3>
          <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-5">
            You can grant Lucid AI access to your local environment, cloud IDEs, or specific APIs. It safely interacts with these tools to perform tasks.
          </p>
          {/* Code block */}
          <div className="bg-[#0f172a] rounded-xl overflow-hidden">
            <div className="flex justify-between items-center px-4 py-2.5 border-b border-white/5">
              <span className="text-[11px] text-slate-500 font-mono">terminal</span>
              <button className="text-slate-500 hover:text-white transition-colors">
                <Copy className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="p-4 font-mono text-[13px] space-y-1 text-slate-300">
              <div className="flex gap-3">
                <span className="text-violet-400">$</span>
                <span>npm install -g @lucid-ai/cli</span>
              </div>
              <div className="flex gap-3">
                <span className="text-violet-400">$</span>
                <span>lucid login</span>
              </div>
              <div className="flex gap-3">
                <span className="text-violet-400">$</span>
                <span className="text-emerald-400">lucid init "Create a Next.js blog with Tailwind"</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Section: Getting Access */}
      <section id="getting-access" className="mb-10">
        <h2 className="text-[22px] font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tight">Getting Access</h2>
        <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed">
          Lucid AI is currently available through our <Link href="#" className="text-violet-600 dark:text-violet-400 font-semibold hover:underline">Enterprise plan</Link> and select beta users. Visit our <Link href="#" className="text-violet-600 dark:text-violet-400 font-semibold hover:underline">pricing page</Link> to learn more about available plans.
        </p>
      </section>

      {/* Section: Feedback */}
      <section id="feedback" className="mb-10">
        <h2 className="text-[22px] font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tight">Feedback</h2>
        <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed">
          We're always looking to improve. If you have feedback or feature requests, reach out to us at{' '}
          <Link href="#" className="text-violet-600 dark:text-violet-400 font-semibold hover:underline">support@lucidai.dev</Link>.
        </p>
      </section>

      {/* Footer Navigation — matches Devin's prev/next */}
      <div className="mt-16 pt-8 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between">
        <div></div>
        <Link 
          href="/docs/get-started/first-session" 
          className="group flex flex-col items-end gap-0.5 px-4 py-3 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
        >
          <span className="text-[12px] font-medium text-slate-400 dark:text-slate-500">Next</span>
          <div className="flex items-center gap-1.5 text-violet-600 dark:text-violet-400 font-semibold text-[14px]">
            Your First Session
            <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
          </div>
        </Link>
      </div>
    </div>
  );
}
