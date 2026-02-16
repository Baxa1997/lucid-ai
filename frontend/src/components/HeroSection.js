'use client';

import { ChevronRight, Terminal, Zap } from 'lucide-react';
import Link from 'next/link';

export default function HeroSection() {
  return (
    <section className="flex flex-col lg:flex-row items-center justify-center gap-12 lg:gap-20 px-6 sm:px-10 pt-24 pb-12 lg:pt-32 lg:pb-24 max-w-[1400px] mx-auto w-full">
      
      {/* LEFT CONTENT */}
      <div className="flex-1 max-w-xl self-center">
        
        {/* Badge */}
        <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#1e293b] text-slate-300 text-[11px] font-medium mb-6 -mt-8 cursor-pointer hover:bg-[#334155] transition-colors shadow-sm w-fit border border-slate-700/50">
          <span className="bg-[#0f172a] text-cyan-400 px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wide border border-slate-700">New</span>
          <span className="text-white">Introducing Lucid Review</span>
          <ChevronRight className="w-3 h-3 text-slate-500" />
        </div>

        <h1 className="text-4xl sm:text-5xl lg:text-[3rem] font-bold tracking-tight text-slate-900 leading-[1.1] mb-6">
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-violet-600 to-indigo-600">LucidAI</span>: The AI Software engineer
        </h1>
        
        <p className="text-lg text-slate-500 leading-relaxed mb-8 max-w-lg font-medium">
          An autonomous engineering partner that understands your codebase, builds features, and fixes bugs.
        </p>

        <div className="flex items-center gap-4 mb-10">
          <Link href="/login" className="bg-gradient-to-r from-violet-600 to-indigo-600 text-white text-[15px] font-semibold px-8 py-3.5 rounded-lg shadow-lg shadow-violet-500/25 hover:shadow-violet-600/40 hover:-translate-y-0.5 transition-all duration-200 inline-block text-center">
            Get Started
          </Link>
          <button className="bg-white text-slate-700 text-[15px] font-bold px-8 py-3.5 rounded-lg border border-slate-200 hover:border-slate-300 hover:bg-slate-50 transition-all shadow-sm">
            Book a Demo
          </button>
        </div>

        {/* Feature Steps List */}
        <div className="flex flex-col gap-2 w-full max-w-lg">
          {/* Step 1 */}
          <div className="flex items-center gap-4 p-4 rounded-xl bg-white border border-slate-200 shadow-md transform hover:-translate-y-0.5 transition-all cursor-pointer relative overflow-hidden group">
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-violet-600"></div>
            <div className="w-6 h-6 rounded-lg bg-slate-900 text-white flex items-center justify-center text-sm font-bold shadow-sm shrink-0">1</div>
            <div className="flex flex-col">
              <span className="text-slate-900 font-bold text-[15px]">Planning</span>
              <span className="text-slate-500 text-[13px]">Plan your roadmap and architecture</span>
            </div>
            <ChevronRight className="w-4 h-4 text-slate-300 ml-auto" />
          </div>

          {/* Step 2 */}
          <div className="flex items-center gap-4 p-4 rounded-xl border border-transparent hover:bg-slate-50 transition-all cursor-pointer group">
            <div className="w-6 h-6 rounded-lg bg-slate-100 text-slate-500 flex items-center justify-center text-sm font-bold shrink-0 group-hover:bg-white group-hover:shadow-sm transition-all border border-transparent group-hover:border-slate-200">2</div>
            <div className="flex flex-col">
              <span className="text-slate-600 font-bold text-[15px] group-hover:text-slate-900 transition-colors">Professional Documentation</span>
              <span className="text-slate-400 text-[13px] group-hover:text-slate-500 transition-colors">Generate enterprise-grade docs</span>
            </div>
          </div>

          {/* Step 3 */}
          <div className="flex items-center gap-4 p-4 rounded-xl border border-transparent hover:bg-slate-50 transition-all cursor-pointer group">
            <div className="w-6 h-6 rounded-lg bg-slate-100 text-slate-500 flex items-center justify-center text-sm font-bold shrink-0 group-hover:bg-white group-hover:shadow-sm transition-all border border-transparent group-hover:border-slate-200">3</div>
            <div className="flex flex-col">
              <span className="text-slate-600 font-bold text-[15px] group-hover:text-slate-900 transition-colors">Integrations</span>
              <span className="text-slate-400 text-[13px] group-hover:text-slate-500 transition-colors">Connect with GitHub, Linear & Slack</span>
            </div>
          </div>
        </div>

      </div>

      {/* RIGHT CONTENT - IDE MOCKUP (DARK MODE) */}
      <div className="flex-1 w-full max-w-2xl">
        <div className="bg-[#1a1b26] rounded-xl shadow-[0_30px_60px_-12px_rgba(0,0,0,0.25)] border border-slate-800/50 overflow-hidden ring-1 ring-white/10 relative group">
          
          {/* Reflection/Sheen */}
          <div className="absolute inset-0 bg-gradient-to-tr from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"></div>

          {/* Window Header */}
          <div className="bg-[#16161e] border-b border-[#0f0f14] px-4 py-3 flex items-center gap-4">
            {/* Traffic Lights */}
            <div className="flex gap-1.5 shrink-0 opacity-80">
              <div className="w-3 h-3 rounded-full bg-[#cbced3]"></div>
              <div className="w-3 h-3 rounded-full bg-[#cbced3]"></div>
              <div className="w-3 h-3 rounded-full bg-[#cbced3]"></div>
            </div>
            {/* URL Bar */}
            <div className="bg-[#1a1b26] border border-white/5 rounded px-3 py-1 text-[11px] text-slate-400 font-medium w-full max-w-[240px] text-center shadow-inner mx-auto">
              app.lucid.ai/projects/auth-handler
            </div>
            {/* Spacer to balance */}
            <div className="w-10 shrink-0"></div>
          </div>

          {/* Editor Content Area */}
          <div className="flex h-[450px]">
            
            {/* Left Pane: Code */}
            <div className="flex-1 p-6 font-mono text-[13px] overflow-hidden border-r border-white/5 bg-[#1a1b26] relative">
              <div className="flex items-center gap-2 mb-6 text-slate-400">
                <Terminal className="w-3.5 h-3.5 text-violet-400" />
                <span className="font-medium text-xs text-slate-300">AuthMiddleware.ts</span>
              </div>

              <div className="space-y-1.5 relative z-10 leading-relaxed">
                <div className="flex">
                  <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">1</span>
                  <div><span className="text-violet-400">async function</span> <span className="text-blue-400">validateSession</span>(id: <span className="text-emerald-400">string</span>) {'{'}</div>
                </div>
                <div className="flex">
                  <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">2</span>
                  <div className="pl-4"><span className="text-violet-400">const</span> <span className="text-slate-200">user</span> = <span className="text-violet-400">await</span> <span className="text-slate-200">db.find</span>(id);</div>
                </div>
                <div className="flex">
                  <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">3</span>
                </div>
                <div className="flex">
                  <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">4</span>
                  <div className="pl-4"><span className="text-slate-500 italic">// Lucid AI is refactoring this block</span></div>
                </div>
                
                {/* Highlights */}
                <div className="relative">
                  <div className="absolute inset-0 bg-violet-500/10 w-[200%] -ml-12 border-l-2 border-violet-500"></div>
                  <div className="flex relative z-10">
                    <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">5</span>
                    <div className="pl-4"><span className="text-violet-400">if</span> (<span className="text-indigo-300">!user.isActive</span>) {'{'}</div>
                  </div>
                </div>
                
                <div className="relative">
                  <div className="absolute inset-0 bg-violet-500/10 w-[200%] -ml-12 border-l-2 border-violet-500"></div>
                  <div className="flex relative z-10">
                    <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">6</span>
                    <div className="pl-8"><span className="text-violet-400">throw new</span> <span className="text-amber-400">AuthError</span>(<span className="text-emerald-400">'Account locked'</span>);</div>
                  </div>
                </div>
                
                <div className="relative">
                  <div className="absolute inset-0 bg-violet-500/10 w-[200%] -ml-12 border-l-2 border-violet-500"></div>
                  <div className="flex relative z-10">
                    <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">7</span>
                    <div className="pl-4">{'}'}</div>
                  </div>
                </div>

                <div className="flex">
                  <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">8</span>
                  <div className="pl-4"><span className="text-violet-400">return</span> <span className="text-slate-200">user.token</span>;</div>
                </div>
                <div className="flex">
                  <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">9</span>
                  <div>{'}'}</div>
                </div>
              </div>
            </div>

            {/* Right Pane: AI Assistant */}
            <div className="w-[40%] bg-[#16161e] flex flex-col border-l border-white/5">
              {/* Panel Header */}
              <div className="h-10 border-b border-white/5 flex items-center px-4 gap-2 text-[10px] font-bold text-slate-400 bg-[#1a1b26] uppercase tracking-wider">
                <div className="w-4 h-4 bg-violet-600 rounded-[3px] flex items-center justify-center text-white">
                  <Zap className="w-2.5 h-2.5 fill-current" />
                </div>
                AI Assistant
              </div>
              
              {/* Chat Messages */}
              <div className="flex-1 p-4 space-y-4">
                <div className="bg-[#232433] p-3 rounded-lg border border-white/5 shadow-sm relative">
                  {/* Little Triangle for Chat Bubble */}
                  <div className="absolute -left-1.5 top-3 w-3 h-3 bg-[#232433] border-l border-b border-white/5 transform rotate-45"></div>
                  <p className="text-[12px] text-slate-300 leading-relaxed font-medium">
                    I've identified a potential security flaw in the session handler. Should I implement the fix?
                  </p>
                </div>
                
                <div className="flex justify-end">
                  <button className="bg-gradient-to-r from-violet-600 to-indigo-600 text-white text-[12px] font-bold px-4 py-2 rounded-md shadow-lg shadow-violet-900/20 hover:brightness-110 transition-all">
                    Yes, proceed.
                  </button>
                </div>
              </div>

              {/* Input Area */}
              <div className="p-4 pt-2 mt-auto">
                <div className="bg-[#1a1b26] border border-white/10 rounded-lg p-2.5 flex items-center shadow-inner">
                  <input 
                    type="text" 
                    placeholder="Type a command..." 
                    className="w-full text-[12px] text-slate-400 placeholder:text-slate-600 outline-none px-1 bg-transparent font-medium"
                    disabled
                  />
                </div>
              </div>
            </div>

          </div>
        </div>
      </div>
    </section>
  );
}
