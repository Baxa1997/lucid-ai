'use client';

import { 
  BookOpen, GitBranch, Plug, FolderGit2, 
  Play, MessageSquare, FileText, ArrowRight,
  CheckCircle2, Zap, Settings, Terminal
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useState } from 'react';

const sections = [
  { id: 'getting-started', label: 'Getting Started' },
  { id: 'connect-integration', label: 'Connect Integration' },
  { id: 'select-project', label: 'Select Project & Branch' },
  { id: 'give-task', label: 'Give a Task' },
  { id: 'review-process', label: 'Review & Process' },
  { id: 'documentation', label: 'How Documentation Works' },
];

export default function UsageDocsPage() {
  const [activeSection, setActiveSection] = useState('getting-started');

  return (
    <div className="h-full flex bg-[#f5f7fa] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">

      {/* ── Sidebar TOC ── */}
      <aside className="w-[220px] shrink-0 border-r border-slate-200 dark:border-slate-800/60 bg-white dark:bg-[#0d1117] overflow-y-auto py-8 px-4">
        <div className="flex items-center gap-2 px-3 mb-6">
          <BookOpen className="w-4 h-4 text-blue-600 dark:text-blue-400" />
          <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.1em]">
            Usage Guide
          </span>
        </div>
        <nav className="space-y-0.5">
          {sections.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              onClick={(e) => {
                e.preventDefault();
                setActiveSection(section.id);
                document.getElementById(section.id)?.scrollIntoView({ behavior: 'smooth' });
              }}
              className={cn(
                "block px-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150",
                activeSection === section.id
                  ? "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400"
                  : "text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.03] hover:text-slate-700 dark:hover:text-slate-300"
              )}
            >
              {section.label}
            </a>
          ))}
        </nav>
      </aside>

      {/* ── Main Content ── */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-10 py-12">

          {/* Title */}
          <div className="mb-12">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-600/20">
                <Zap className="w-5 h-5 text-white fill-current" />
              </div>
              <div>
                <h1 className="text-3xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight">
                  Lucid AI — Usage Guide
                </h1>
                <p className="text-sm text-slate-400 dark:text-slate-500 font-medium mt-0.5">
                  Everything you need to start using the AI Software Engineer
                </p>
              </div>
            </div>
            <div className="h-px bg-gradient-to-r from-blue-500/30 via-slate-200 dark:via-slate-800 to-transparent mt-6" />
          </div>

          {/* ═══════════════════════════════════════════════
              SECTION 1: Getting Started
          ═══════════════════════════════════════════════ */}
          <section id="getting-started" className="mb-16 scroll-mt-8">
            <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-4 flex items-center gap-3">
              <Play className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              Getting Started
            </h2>
            <p className="text-[15px] text-slate-600 dark:text-slate-300 leading-relaxed mb-6">
              Lucid AI is an autonomous software engineering agent that can write code, run commands, 
              debug issues, and manage your projects — all through a simple chat interface. Here's 
              how to get up and running in minutes.
            </p>

            <div className="bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 p-6 mb-6">
              <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-4">Quick Start Workflow</h3>
              <div className="space-y-3">
                {[
                  { step: '1', text: 'Connect your GitHub or GitLab integration', icon: Plug },
                  { step: '2', text: 'Select a repository and branch to work on', icon: FolderGit2 },
                  { step: '3', text: 'Describe your task in natural language', icon: MessageSquare },
                  { step: '4', text: 'Review the AI\'s changes and approve', icon: CheckCircle2 },
                ].map((item) => (
                  <div key={item.step} className="flex items-start gap-4">
                    <div className="w-7 h-7 rounded-lg bg-blue-50 dark:bg-blue-500/10 border border-blue-100 dark:border-blue-500/20 flex items-center justify-center shrink-0 mt-0.5">
                      <span className="text-xs font-bold text-blue-700 dark:text-blue-400">{item.step}</span>
                    </div>
                    <div className="flex items-center gap-2 text-[14px] text-slate-600 dark:text-slate-300 font-medium pt-1">
                      <item.icon className="w-4 h-4 text-slate-400 dark:text-slate-500 shrink-0" />
                      {item.text}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex items-start gap-3 p-4 bg-blue-50 dark:bg-blue-500/5 border border-blue-200 dark:border-blue-500/20 rounded-xl">
              <Zap className="w-4 h-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
              <p className="text-sm text-blue-800 dark:text-blue-300 leading-relaxed">
                <strong>Tip:</strong> You can also start a scratch session without connecting a repository. 
                Just click "Start from Scratch" on the main dashboard to experiment freely.
              </p>
            </div>
          </section>

          {/* ═══════════════════════════════════════════════
              SECTION 2: Connect Integration
          ═══════════════════════════════════════════════ */}
          <section id="connect-integration" className="mb-16 scroll-mt-8">
            <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-4 flex items-center gap-3">
              <Plug className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
              Connect Integration
            </h2>
            <p className="text-[15px] text-slate-600 dark:text-slate-300 leading-relaxed mb-6">
              Before working on a real project, you need to connect your Git provider. 
              Lucid AI supports <strong>GitHub</strong> and <strong>GitLab</strong>.
            </p>

            <div className="bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 p-6 space-y-5">
              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-2">Step 1: Navigate to Integrations</h3>
                <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                  Go to <code className="px-1.5 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded text-blue-600 dark:text-blue-400 text-xs font-mono">Integrations</code> in the sidebar. You'll see available Git providers listed as cards.
                </p>
              </div>

              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-2">Step 2: Authorize Access</h3>
                <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                  Click <strong>"Connect"</strong> on your preferred provider. You'll be redirected to authorize 
                  Lucid AI to access your repositories. We only request the minimum permissions needed.
                </p>
              </div>

              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-2">Step 3: Verify Connection</h3>
                <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                  Once authorized, the integration card will show a green <strong>"Connected"</strong> badge. 
                  Your repositories will now appear in the project selector on the dashboard.
                </p>
              </div>
            </div>
          </section>

          {/* ═══════════════════════════════════════════════
              SECTION 3: Select Project & Branch
          ═══════════════════════════════════════════════ */}
          <section id="select-project" className="mb-16 scroll-mt-8">
            <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-4 flex items-center gap-3">
              <FolderGit2 className="w-5 h-5 text-violet-600 dark:text-violet-400" />
              Select Project & Branch
            </h2>
            <p className="text-[15px] text-slate-600 dark:text-slate-300 leading-relaxed mb-6">
              After connecting an integration, choose which repository and branch the AI should work on.
            </p>

            <div className="bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 p-6">
              <div className="space-y-5">
                <div className="flex items-start gap-4">
                  <div className="w-8 h-8 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center shrink-0">
                    <GitBranch className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-1">Select Provider</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                      Choose between GitHub or GitLab from the provider dropdown on the "Open Repository" card.
                    </p>
                  </div>
                </div>

                <div className="flex items-start gap-4">
                  <div className="w-8 h-8 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center shrink-0">
                    <FolderGit2 className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-1">Pick a Repository</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                      Search and select from your connected repositories. The list auto-populates 
                      from your Git provider.
                    </p>
                  </div>
                </div>

                <div className="flex items-start gap-4">
                  <div className="w-8 h-8 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center shrink-0">
                    <GitBranch className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-1">Choose a Branch</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                      Select the branch you want the AI to work on. We recommend using a 
                      <code className="px-1.5 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded text-xs font-mono ml-1">feature/*</code> or 
                      <code className="px-1.5 py-0.5 bg-slate-100 dark:bg-white/[0.06] rounded text-xs font-mono ml-1">develop</code> branch 
                      to keep your main branch clean.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <div className="flex items-start gap-3 p-4 mt-4 bg-amber-50 dark:bg-amber-500/5 border border-amber-200 dark:border-amber-500/20 rounded-xl">
              <Settings className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <p className="text-sm text-amber-800 dark:text-amber-300 leading-relaxed">
                <strong>AI Model:</strong> You can also select which AI model to use (Gemini or Claude) — 
                each has different strengths. Claude is great for reasoning, Gemini excels with large files.
              </p>
            </div>
          </section>

          {/* ═══════════════════════════════════════════════
              SECTION 4: Give a Task
          ═══════════════════════════════════════════════ */}
          <section id="give-task" className="mb-16 scroll-mt-8">
            <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-4 flex items-center gap-3">
              <MessageSquare className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              Give a Task
            </h2>
            <p className="text-[15px] text-slate-600 dark:text-slate-300 leading-relaxed mb-6">
              Once your session is launched, you'll enter the workspace chat view. Simply describe 
              what you want the AI to do in natural language.
            </p>

            <div className="bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 p-6 mb-6">
              <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-4">Example Tasks</h3>
              <div className="grid grid-cols-1 gap-3">
                {[
                  { emoji: '🔧', task: '"Add a login page with email and password fields"' },
                  { emoji: '🐛', task: '"Fix the bug in the API route that causes a 500 error"' },
                  { emoji: '📦', task: '"Install and configure Tailwind CSS with dark mode support"' },
                  { emoji: '♻️', task: '"Refactor the user service to use async/await instead of callbacks"' },
                  { emoji: '🧪', task: '"Write unit tests for the authentication module"' },
                  { emoji: '📝', task: '"Update the README with installation instructions"' },
                ].map((item) => (
                  <div key={item.task} className="flex items-start gap-3 px-4 py-3 bg-slate-50 dark:bg-white/[0.02] rounded-xl border border-slate-100 dark:border-slate-700/30">
                    <span className="text-base shrink-0">{item.emoji}</span>
                    <span className="text-sm text-slate-600 dark:text-slate-300 font-medium italic">{item.task}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex items-start gap-3 p-4 bg-emerald-50 dark:bg-emerald-500/5 border border-emerald-200 dark:border-emerald-500/20 rounded-xl">
              <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5" />
              <p className="text-sm text-emerald-800 dark:text-emerald-300 leading-relaxed">
                <strong>Be specific:</strong> The more detailed your task description, the better the AI 
                will perform. Include file names, expected behavior, and any constraints.
              </p>
            </div>
          </section>

          {/* ═══════════════════════════════════════════════
              SECTION 5: Review & Process
          ═══════════════════════════════════════════════ */}
          <section id="review-process" className="mb-16 scroll-mt-8">
            <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-4 flex items-center gap-3">
              <Terminal className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
              Review & Process
            </h2>
            <p className="text-[15px] text-slate-600 dark:text-slate-300 leading-relaxed mb-6">
              After you submit a task, the AI agent will work autonomously. Here's what happens:
            </p>

            <div className="space-y-4">
              {[
                {
                  title: 'Planning',
                  desc: 'The agent analyzes your request and creates an execution plan.',
                  color: 'blue',
                },
                {
                  title: 'Coding',
                  desc: 'It writes or modifies files in a sandboxed Docker container, ensuring your actual code is safe.',
                  color: 'violet',
                },
                {
                  title: 'Running Commands',
                  desc: 'The agent can install packages, run builds, execute tests — all visible in the terminal panel.',
                  color: 'emerald',
                },
                {
                  title: 'Results',
                  desc: 'Once complete, you can review all changes, view modified files, and chat further to refine.',
                  color: 'amber',
                },
              ].map((phase) => (
                <div key={phase.title} className="flex items-start gap-4 bg-white dark:bg-[#151b23] rounded-xl border border-slate-200 dark:border-slate-700/50 p-5">
                  <div className={cn(
                    "w-3 h-3 rounded-full shrink-0 mt-1.5",
                    phase.color === 'blue' ? 'bg-blue-500' :
                    phase.color === 'violet' ? 'bg-violet-500' :
                    phase.color === 'emerald' ? 'bg-emerald-500' : 'bg-amber-500'
                  )} />
                  <div>
                    <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-1">{phase.title}</h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">{phase.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* ═══════════════════════════════════════════════
              SECTION 6: How Documentation Works
          ═══════════════════════════════════════════════ */}
          <section id="documentation" className="mb-16 scroll-mt-8">
            <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-4 flex items-center gap-3">
              <FileText className="w-5 h-5 text-slate-600 dark:text-slate-400" />
              How Documentation Works
            </h2>
            <p className="text-[15px] text-slate-600 dark:text-slate-300 leading-relaxed mb-6">
              Lucid AI includes a built-in documentation system designed to help you understand 
              the platform, its capabilities, and best practices.
            </p>

            <div className="bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 p-6 space-y-5">
              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-2">📖 Usage Docs (this page)</h3>
                <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                  Task-oriented guides for common workflows: connecting integrations, launching sessions, and giving tasks.
                </p>
              </div>

              <div className="h-px bg-slate-100 dark:bg-slate-700/30" />

              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-2">📚 Documentation (sidebar)</h3>
                <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                  The <strong>Documentation</strong> section in the sidebar contains deeper technical 
                  reference material — API details, configuration options, and architecture overview.
                </p>
              </div>

              <div className="h-px bg-slate-100 dark:bg-slate-700/30" />

              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-2">💬 In-Chat Help</h3>
                <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
                  You can always ask the AI agent itself for help during a session. Try:
                  <code className="block mt-2 px-4 py-2 bg-slate-50 dark:bg-white/[0.03] rounded-lg text-xs font-mono text-slate-600 dark:text-slate-400 border border-slate-100 dark:border-slate-700/30">
                    "How do I set up environment variables for my project?"
                  </code>
                </p>
              </div>
            </div>
          </section>

          {/* Footer */}
          <div className="text-center pb-8">
            <div className="h-px bg-gradient-to-r from-transparent via-slate-200 dark:via-slate-800 to-transparent mb-6" />
            <p className="text-xs text-slate-400 dark:text-slate-500 font-medium">
              Need more help? Reach out to our team or open a conversation with the AI agent.
            </p>
          </div>

        </div>
      </main>
    </div>
  );
}
