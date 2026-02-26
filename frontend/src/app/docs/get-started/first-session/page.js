'use client';

import { useState } from 'react';
import { ArrowRight, ArrowLeft, MessageSquare, Bot, Info, Lightbulb, ChevronRight, LayoutGrid, Link2, BookOpen, Database, Plug } from 'lucide-react';
import Link from 'next/link';

function InfoCallout({ children }) {
  return (
    <div className="flex gap-3 p-4 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800/50 rounded-lg my-6">
      <Info className="w-5 h-5 text-blue-500 dark:text-blue-400 flex-shrink-0 mt-0.5" />
      <div className="text-[15px] text-blue-900 dark:text-blue-200 leading-relaxed">{children}</div>
    </div>
  );
}

function TipCallout({ children }) {
  return (
    <div className="flex gap-3 p-4 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800/50 rounded-lg my-6">
      <Lightbulb className="w-5 h-5 text-emerald-500 dark:text-emerald-400 flex-shrink-0 mt-0.5" />
      <div className="text-[15px] text-emerald-900 dark:text-emerald-200 leading-relaxed">{children}</div>
    </div>
  );
}

function ScreenshotMockup({ title, children }) {
  return (
    <div className="relative bg-[#0f172a] rounded-xl my-6 overflow-hidden shadow-lg border border-slate-700/50">
      <div className="h-8 bg-[#1e293b] flex items-center px-3 gap-2 border-b border-slate-700/50">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-rose-500/70" />
          <div className="w-2.5 h-2.5 rounded-full bg-amber-500/70" />
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-500/70" />
        </div>
        {title && (
          <span className="text-[11px] text-slate-400 ml-2 font-mono">{title}</span>
        )}
      </div>
      <div className="p-0">
        {children}
      </div>
    </div>
  );
}

function AccordionItem({ icon: Icon, title, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-3 w-full px-5 py-4 text-left hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
      >
        <ChevronRight className={`w-4 h-4 text-slate-400 dark:text-slate-500 transition-transform ${open ? 'rotate-90' : ''}`} />
        {Icon && <Icon className="w-5 h-5 text-slate-500 dark:text-slate-400" />}
        <span className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">{title}</span>
      </button>
      {open && (
        <div className="px-5 pb-4 text-[14px] text-slate-600 dark:text-slate-400 leading-relaxed border-t border-slate-100 dark:border-slate-700 pt-3">
          {children}
        </div>
      )}
    </div>
  );
}

export default function FirstSessionPage() {
  return (
    <div className="docs-content">
      {/* Breadcrumb */}
      <div className="mb-4">
        <span className="text-[14px] font-semibold text-blue-600 dark:text-blue-400">Get Started</span>
      </div>

      {/* Main Heading */}
      <h1 className="text-[32px] font-bold text-slate-900 dark:text-slate-100 mb-2 tracking-tight leading-[1.2]">
        Your First Session
      </h1>

      {/* Subtitle */}
      <p className="text-[17px] text-slate-500 dark:text-slate-400 mb-6">
        Start your first session and see what Lucid AI can do
      </p>

      {/* Info Callout */}
      <InfoCallout>
        Before you start your first session, make sure you've{' '}
        <Link href="#" className="font-semibold underline text-blue-900 dark:text-blue-300 hover:text-blue-700 dark:hover:text-blue-200">indexed</Link>{' '}
        and{' '}
        <Link href="#" className="font-semibold underline text-blue-900 dark:text-blue-300 hover:text-blue-700 dark:hover:text-blue-200">set up</Link>{' '}
        your repositories. These are the foundational steps that help Lucid AI understand and work with your codebase.
      </InfoCallout>

      {/* Intro paragraph */}
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-8">
        Now that you're all set up, kick off your first Lucid AI session! This guide will walk you through 
        the new session interface and help you understand the best ways to interact with Lucid AI.
      </p>

      {/* Understanding the Session Page */}
      <h2 id="understanding-the-session-page" className="text-[24px] font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tight">
        Understanding the Lucid AI Session Page
      </h2>

      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
        When you start a new session, you'll see two primary modes: <strong className="text-slate-900 dark:text-slate-100">Ask</strong> and <strong className="text-slate-900 dark:text-slate-100">Agent</strong>.
      </p>

      <InfoCallout>
        Unless you already have a fully scoped plan, we recommend starting with Ask to work with Lucid AI 
        to understand the problem better before prompting agent mode to execute.
      </InfoCallout>

      {/* Ask Mode */}
      <h3 id="ask-mode" className="text-[20px] font-bold text-slate-900 dark:text-slate-100 mb-3 tracking-tight">
        Ask Mode
      </h3>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
        Ask mode is for code research, browsing documentation, and planning. Use it to:
      </p>
      <ul className="space-y-2 text-[15px] text-slate-600 dark:text-slate-400 mb-6 list-disc pl-6">
        <li>Ask questions about how your code works</li>
        <li>Explore architecture and dependencies</li>
        <li>Plan and scope tasks before implementation</li>
        <li>Generate context-rich prompts for Agent sessions</li>
      </ul>

      {/* Ask Mode Screenshot */}
      <ScreenshotMockup title="Lucid AI — Ask Session">
        <div className="flex h-[320px]">
          {/* Left sidebar - history */}
          <div className="w-[200px] bg-[#0f172a] border-r border-slate-700/50 p-3 flex flex-col">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-5 h-5 rounded bg-violet-600 flex items-center justify-center">
                <span className="text-[10px] text-white font-bold">L</span>
              </div>
              <span className="text-[11px] text-slate-300 font-medium">Sessions</span>
            </div>
            <div className="flex items-center gap-2 mb-3 bg-blue-600/20 border border-blue-500/30 rounded px-2 py-1.5">
              <div className="w-3 h-3 rounded bg-blue-500/50" />
              <span className="text-[10px] text-blue-300">Ask</span>
            </div>
            <div className="space-y-2 mt-2">
              {['How does the data flow through...', 'What is the stack for this repo', 'Are there any refactors we can consi...', 'What are the steps to migrate...'].map((q, i) => (
                <div key={i} className="text-[10px] text-slate-500 truncate">{q}</div>
              ))}
            </div>
          </div>
          {/* Main chat area */}
          <div className="flex-1 bg-[#0f172a] p-4 flex flex-col">
            <div className="flex items-center gap-3 mb-4 pb-3 border-b border-slate-700/50">
              <input
                readOnly
                placeholder="how does the data flow through the application"
                className="flex-1 bg-[#1e293b] text-[12px] text-slate-400 px-3 py-2 rounded-md border border-slate-700/50"
              />
            </div>
            <div className="flex-1 overflow-hidden">
              <div className="text-[11px] text-slate-500 mb-2">Thinking process (21 tools used)</div>
              <div className="text-[12px] text-slate-300 mb-3">
                Now I have a thorough understanding of the data flow. Here's the complete picture:
              </div>
              <div className="text-[13px] text-white font-semibold mb-2">Data Flow Through the Application</div>
              <div className="text-[11px] text-slate-400 leading-relaxed">
                This is a full-stack payment application with a clear layered architecture. Data flows through four main layers: UI Components → XState Machines → HTTP Client → Express Backend → lowdb JSON Database.
              </div>
            </div>
          </div>
          {/* Right panel - code */}
          <div className="w-[240px] bg-[#0f172a] border-l border-slate-700/50 p-3">
            <div className="text-[10px] text-slate-500 mb-2 flex items-center gap-1.5">
              <span>▸ Code snippets</span>
            </div>
            <div className="text-[10px] text-slate-500 mb-1 font-mono">{'▸ index.tsx  cypress-realworld-app > src/'}</div>
            <div className="font-mono text-[10px] leading-relaxed space-y-0.5">
              <div className="text-slate-500">const root = createRoot</div>
              <div className="text-emerald-400">{'  root.render('}</div>
              <div className="text-slate-400">{'    <Router history={history}>'}</div>
              <div className="text-purple-400">{'      <StyledEngineProvider>'}</div>
              <div className="text-blue-400">{'        <ThemeProvider>'}</div>
              <div className="text-slate-400">{'          <App />'}</div>
              <div className="text-blue-400">{'        </ThemeProvider>'}</div>
              <div className="text-purple-400">{'      </StyledEngineProvider>'}</div>
              <div className="text-slate-400">{'    </Router>'}</div>
            </div>
          </div>
        </div>
      </ScreenshotMockup>

      {/* Triggering Ask Mode */}
      <h4 id="triggering-ask-mode" className="text-[17px] font-bold text-slate-900 dark:text-slate-100 mb-3 tracking-tight">
        Triggering Ask Mode
      </h4>

      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
        From the main page, type a query in the Ask input and press Enter. See the{' '}
        <Link href="#" className="text-blue-600 dark:text-blue-400 font-semibold underline hover:text-blue-800 dark:hover:text-blue-300">Ask Lucid AI guide</Link>{' '}
        for more details.
      </p>

      {/* Triggering Ask Mode Screenshot */}
      <ScreenshotMockup title="Lucid AI — New Session">
        <div className="h-[200px] bg-[#0f172a] flex flex-col items-center justify-center">
          <div className="flex items-center gap-2 mb-4">
            <div className="flex items-center gap-1.5 bg-[#1e293b] rounded-md px-3 py-1.5 border border-slate-700/50">
              <div className="w-3 h-3 rounded bg-blue-500/50" />
              <span className="text-[11px] text-slate-300">Ask</span>
            </div>
            <span className="text-[10px] text-slate-500">+</span>
          </div>
          <div className="w-[300px] bg-[#1e293b] rounded-md px-3 py-2 border border-slate-700/50">
            <span className="text-[12px] text-slate-500">Ask Lucid AI questions about your code</span>
          </div>
        </div>
      </ScreenshotMockup>

      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-8">
        For Ask mode from a DeepWiki page, type a query in the chat input at the bottom of the page 
        and click Ask. This will automatically scope Lucid AI's knowledge to that repository specifically.
      </p>

      {/* Agent Mode */}
      <h3 id="agent-mode" className="text-[20px] font-bold text-slate-900 dark:text-slate-100 mb-3 tracking-tight">
        Agent Mode
      </h3>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
        Agent mode is Lucid AI's full autonomous mode where it can write code, run commands, browse 
        the web, and complete complex tasks end-to-end. Use Agent mode when you're ready to:
      </p>
      <ul className="space-y-2 text-[15px] text-slate-600 dark:text-slate-400 mb-6 list-disc pl-6">
        <li>Implement features or fix bugs</li>
        <li>Create pull requests</li>
        <li>Run tests and debug issues</li>
        <li>Perform multi-step tasks that require code changes</li>
      </ul>

      {/* Triggering Agent Mode */}
      <h4 id="triggering-agent-mode" className="text-[17px] font-bold text-slate-900 dark:text-slate-100 mb-3 tracking-tight">
        Triggering Agent Mode
      </h4>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
        You can trigger Agent mode from the main page or from an Ask Lucid AI session.
      </p>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
        For tasks that are not fully scoped, we recommend:
      </p>
      <ul className="space-y-2 text-[15px] text-slate-600 dark:text-slate-400 mb-6 list-disc pl-6">
        <li>Start with <strong className="text-slate-900 dark:text-slate-100">Ask mode</strong> to plan out the task</li>
        <li>Construct a Lucid AI Prompt, which will draw from your Ask session to create a scoped plan</li>
        <li>Click <strong className="text-slate-900 dark:text-slate-100">Send to Lucid AI</strong> to move to Agent mode and execute the task</li>
      </ul>

      {/* Agent Mode Screenshot */}
      <ScreenshotMockup title="Lucid AI — Agent Prompt Builder">
        <div className="h-[280px] bg-[#0f172a] flex flex-col">
          <div className="flex-1 p-4">
            <div className="text-[12px] text-slate-400 mb-3 font-medium">Background Context</div>
            <div className="text-[11px] text-slate-500 leading-relaxed mb-4">
              The COBOL codebase currently has two serialization implementations:<br/>
              1. JSON Generation in json_generate/json_generate.cbl (lines 42-54)<br/>
              2. XML Generation in xml_generate/xml_generate.cbl (lines 41-56)
            </div>
            <div className="text-[12px] text-slate-400 mb-2 font-medium">Key COBOL Data Structures to Migrate</div>
            <div className="text-[11px] text-slate-500 leading-relaxed">
              The COBOL code uses data structures like this (from json_generate/json_generate.cbl, lines 27-33):<br/>
              • Record structures with fields like CUSTOMER-NAME, CUSTOMER-ID, CUSTOMER-BALANCE<br/>
              • 88-level conditions (boolean flags) for status indicators
            </div>
          </div>
          <div className="flex items-center justify-between px-4 py-2 border-t border-slate-700/50 bg-[#1e293b]">
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-slate-500">Ask a follow-up question or construct a Lucid AI prompt...</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-emerald-400 flex items-center gap-1">⚡ Fast ▾</span>
              <button className="text-[10px] bg-slate-600 text-slate-300 px-2.5 py-1 rounded">Construct Lucid AI Prompt...</button>
              <button className="text-[10px] bg-blue-600 text-white px-2.5 py-1 rounded">Ask →</button>
            </div>
          </div>
        </div>
      </ScreenshotMockup>

      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
        For Agent mode from the main page, toggle to Agent mode and select the 
        repository/repositories you want to work with.
      </p>

      {/* Agent mode main page screenshot */}
      <ScreenshotMockup title="Lucid AI — Sessions">
        <div className="h-[220px] bg-[#0f172a] flex flex-col items-center justify-center">
          <h3 className="text-[18px] text-white font-semibold mb-4">What do you want to get done?</h3>
          <div className="w-[400px] bg-[#1e293b] rounded-lg border border-slate-700/50 overflow-hidden">
            <div className="px-3 py-3">
              <span className="text-[12px] text-slate-500">Ask Lucid AI to build features, fix bugs, or work on your code...</span>
            </div>
            <div className="flex items-center gap-3 px-3 py-2 border-t border-slate-700/50">
              <span className="text-[10px] text-slate-500">+ 65 repos ▾</span>
              <span className="text-[10px] text-slate-500">23 MCPs</span>
              <span className="text-[10px] text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded font-medium">Agent</span>
            </div>
          </div>
        </div>
      </ScreenshotMockup>

      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
        When starting an Agent session, you'll configure a few options: selecting a Repository and 
        selecting an Agent.
      </p>

      {/* Selecting a Repository */}
      <h4 id="selecting-a-repository" className="text-[17px] font-bold text-slate-900 dark:text-slate-100 mb-3 tracking-tight">
        Selecting a Repository
      </h4>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
        Select the repository you want Lucid AI to work with. Click the repository selector to see all 
        repositories that have been{' '}
        <Link href="#" className="text-blue-600 dark:text-blue-400 font-semibold underline hover:text-blue-800 dark:hover:text-blue-300">added to Lucid AI's machine</Link>.
      </p>

      {/* Repo selector screenshot */}
      <ScreenshotMockup title="Lucid AI — Repository Selector">
        <div className="h-[240px] bg-[#0f172a] flex items-start justify-center pt-4">
          <div className="w-[240px] bg-[#1e293b] rounded-lg border border-slate-700/50 overflow-hidden">
            <div className="px-3 py-2 border-b border-slate-700/50">
              <div className="flex items-center gap-2 bg-[#0f172a] rounded px-2 py-1.5 border border-slate-700/50">
                <span className="text-[11px] text-slate-500">🔍 Search for a repo</span>
              </div>
            </div>
            <div className="divide-y divide-slate-700/30">
              {['lucid-ai-frontend', 'lucid-ai-backend', 'lucid-ai-docs', 'lucid-ai-cli', 'lucid-ai-sdk', 'design-system'].map((repo, i) => (
                <div key={repo} className="flex items-center gap-2 px-3 py-2 hover:bg-slate-700/30 cursor-pointer">
                  <div className={`w-3 h-3 rounded-sm border ${i === 2 ? 'bg-emerald-500 border-emerald-500' : 'border-slate-600'}`} />
                  <span className="text-[11px] text-slate-400">{repo}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </ScreenshotMockup>

      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
        When you select a repository, Lucid AI:
      </p>
      <ul className="space-y-2 text-[15px] text-slate-600 dark:text-slate-400 mb-6 list-disc pl-6">
        <li>Has access to your codebase and can make changes</li>
        <li>Uses the correct branch as a starting point</li>
        <li>Can create pull requests to the right repository</li>
      </ul>

      {/* Selecting an Agent */}
      <h4 id="selecting-an-agent" className="text-[17px] font-bold text-slate-900 dark:text-slate-100 mb-3 tracking-tight">
        Selecting an Agent
      </h4>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
        You can choose which agent configuration Lucid AI uses for your session. Different agents may 
        have different capabilities or be optimized for specific types of tasks.
      </p>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
        Currently, we have a default agent that works well for most tasks, and a data analyst agent 
        named Dana that is optimized for data analysis tasks.
      </p>

      {/* Agent selector screenshot */}
      <ScreenshotMockup title="Lucid AI — Agent Selector">
        <div className="h-[200px] bg-[#0f172a] flex flex-col items-center justify-center">
          <h3 className="text-[16px] text-white font-semibold mb-4">What do you want to get done?</h3>
          <div className="w-[380px] bg-[#1e293b] rounded-lg border border-slate-700/50">
            <div className="px-3 py-3">
              <span className="text-[12px] text-slate-500">Ask Lucid AI to build features, fix bugs, or work on your code...</span>
            </div>
            <div className="flex items-center gap-3 px-3 py-2 border-t border-slate-700/50">
              <span className="text-[10px] text-slate-500">+ 65 repos ▾</span>
              <span className="text-[10px] text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded font-medium">Agent</span>
            </div>
            <div className="border-t border-slate-700/50 p-2">
              <div className="flex items-center gap-2">
                <div className="text-[10px] text-blue-400 bg-blue-500/10 px-2 py-1 rounded">Advanced →</div>
                <div className="flex items-center gap-2 bg-[#0f172a] rounded px-3 py-1.5 border border-slate-700/50">
                  <Bot className="w-3 h-3 text-emerald-400" />
                  <div>
                    <span className="text-[11px] text-slate-300 font-medium">Agent</span>
                    <span className="text-[10px] text-slate-500 ml-2">Fast and good at long-horizon planning.</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </ScreenshotMockup>

      {/* Using @ Mentions */}
      <h2 id="using-mentions" className="text-[24px] font-bold text-slate-900 dark:text-slate-100 mb-4 mt-10 tracking-tight">
        Using @ Mentions
      </h2>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
        When you type{' '}
        <code className="bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-[14px] text-slate-800 dark:text-slate-200 font-mono">@</code>{' '}
        in the chat input, you'll see a dropdown of available mentions:
      </p>
      <ul className="space-y-3 text-[15px] text-slate-600 dark:text-slate-400 mb-6 list-disc pl-6">
        <li><strong className="text-slate-900 dark:text-slate-100">@Repos</strong> - Reference a specific repository</li>
        <li><strong className="text-slate-900 dark:text-slate-100">@Files</strong> - Reference a specific file in your codebase</li>
        <li><Link href="#" className="text-blue-600 dark:text-blue-400 font-semibold underline hover:text-blue-800 dark:hover:text-blue-300">@Macros</Link> - Reference a macro for a Knowledge entry</li>
        <li><Link href="#" className="text-blue-600 dark:text-blue-400 font-semibold underline hover:text-blue-800 dark:hover:text-blue-300">@Playbooks</Link> - Reference a team or community playbook, which are detailed prompt templates that can be used to guide Lucid AI's behavior.</li>
        <li><Link href="#" className="text-blue-600 dark:text-blue-400 font-semibold underline hover:text-blue-800 dark:hover:text-blue-300">@Secrets</Link> - Reference a specific secret (e.g. API keys, credentials, etc.) from Lucid AI's session manager</li>
      </ul>

      {/* @ Mentions Screenshot */}
      <ScreenshotMockup title="Lucid AI — @ Mentions">
        <div className="h-[200px] bg-[#0f172a] flex flex-col items-center justify-center">
          <h3 className="text-[16px] text-white font-semibold mb-4">What do you want to get done?</h3>
          <div className="w-[380px]">
            <div className="bg-[#1e293b] rounded-lg border border-slate-700/50 px-3 py-2.5 mb-2">
              <span className="text-[13px] text-white font-mono">@</span>
              <span className="text-[12px] text-slate-500 ml-1">|</span>
            </div>
            <div className="bg-[#1e293b] rounded-lg border border-slate-700/50 overflow-hidden">
              <div className="flex items-center gap-2.5 px-3 py-2.5 bg-blue-600/10 border-l-2 border-blue-500">
                <span className="text-[12px] text-blue-400">📁 Repos</span>
                <span className="text-[10px] text-slate-500">All repositories added to Lucid AI's machine</span>
              </div>
              <div className="flex items-center gap-2.5 px-3 py-2.5">
                <span className="text-[12px] text-slate-400">📄 Files</span>
              </div>
            </div>
          </div>
        </div>
      </ScreenshotMockup>

      {/* Scoping Your First Session */}
      <h2 id="scoping-your-first-session" className="text-[24px] font-bold text-slate-900 dark:text-slate-100 mb-4 mt-10 tracking-tight">
        Scoping Your First Session
      </h2>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-4">
        Start with tasks that have <strong className="text-slate-900 dark:text-slate-100">clear success criteria</strong> and{' '}
        <strong className="text-slate-900 dark:text-slate-100">provide Lucid AI with the context it needs</strong> — just as you would when 
        handing off work to a teammate. As you get comfortable, try progressively more complex tasks. 
        We've seen users work with Lucid AI on everything from fixing small bugs to targeted refactors to 
        large-scale migrations and building entire features from scratch.
      </p>
      <TipCallout>
        As a rule of thumb: if a task would take you three hours or less, Lucid AI can most likely do it. 
        For larger projects, break them into focused sessions and run them in parallel with{' '}
        <Link href="#" className="text-emerald-800 dark:text-emerald-300 font-semibold underline hover:text-emerald-700 dark:hover:text-emerald-200">batch sessions</Link>.
      </TipCallout>

      {/* First-time Prompt Ideas */}
      <h2 id="first-time-prompt-ideas" className="text-[24px] font-bold text-slate-900 dark:text-slate-100 mb-4 mt-10 tracking-tight">
        First-time Prompt Ideas
      </h2>
      <div className="space-y-2 mb-6">
        <AccordionItem icon={Link2} title="Adding a new API endpoint">
          <p className="mb-2">Try prompts like:</p>
          <ul className="space-y-1 list-disc pl-4">
            <li>"Add a GET /api/users/:id endpoint that returns user profile data"</li>
            <li>"Create a REST endpoint for uploading images with validation"</li>
            <li>"Add pagination to the /api/posts endpoint"</li>
          </ul>
        </AccordionItem>
        <AccordionItem icon={LayoutGrid} title="Small frontend features">
          <p className="mb-2">Try prompts like:</p>
          <ul className="space-y-1 list-disc pl-4">
            <li>"Add a dark mode toggle to the settings page"</li>
            <li>"Create a responsive navbar with a mobile hamburger menu"</li>
            <li>"Add a toast notification system using React"</li>
          </ul>
        </AccordionItem>
        <AccordionItem title="Write unit tests">
          <p className="mb-2">Try prompts like:</p>
          <ul className="space-y-1 list-disc pl-4">
            <li>"Write unit tests for the authentication service"</li>
            <li>"Add integration tests for the checkout flow"</li>
            <li>"Increase test coverage for the utils/ directory"</li>
          </ul>
        </AccordionItem>
        <AccordionItem title="Migrating or refactoring existing code">
          <p className="mb-2">Try prompts like:</p>
          <ul className="space-y-1 list-disc pl-4">
            <li>"Migrate the class components in /components to functional components with hooks"</li>
            <li>"Refactor the database queries to use the repository pattern"</li>
          </ul>
        </AccordionItem>
        <AccordionItem title="Updating APIs or database queries">
          <p className="mb-2">Try prompts like:</p>
          <ul className="space-y-1 list-disc pl-4">
            <li>"Update the user model to include an email verification field"</li>
            <li>"Optimize the slow database queries in the analytics module"</li>
          </ul>
        </AccordionItem>
        <AccordionItem title="Create a quick PR">
          <p>We recommend using this prompt in a Playbook for repeatable tasks.</p>
        </AccordionItem>
      </div>

      {/* Browse Use Cases Card */}
      <Link href="#" className="block p-5 border border-slate-200 dark:border-slate-700 rounded-xl hover:border-blue-200 dark:hover:border-blue-800 hover:bg-blue-50/30 dark:hover:bg-blue-950/30 transition-all group mb-10">
        <div className="flex items-start gap-3">
          <LayoutGrid className="w-6 h-6 text-slate-600 dark:text-slate-400 flex-shrink-0 mt-0.5" />
          <div>
            <h4 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 group-hover:text-blue-700 dark:group-hover:text-blue-400 transition-colors">Browse Use Cases</h4>
            <p className="text-[13px] text-slate-500 dark:text-slate-400 mt-1">
              Explore practical examples across engineering workflows — each includes prompts you can try immediately.
            </p>
          </div>
        </div>
      </Link>

      {/* After Your Session */}
      <h2 id="after-your-session" className="text-[24px] font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tight">
        After Your Session
      </h2>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-10">
        Once Lucid AI finishes, check out{' '}
        <Link href="#" className="text-blue-600 dark:text-blue-400 font-semibold underline hover:text-blue-800 dark:hover:text-blue-300">Session Insights</Link>{' '}
        — you'll get a timeline of what happened, actionable feedback, and an improved prompt you can 
        use for similar tasks in the future.
      </p>

      {/* Next Steps */}
      <h2 id="next-steps" className="text-[24px] font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tight">
        Next Steps
      </h2>
      <p className="text-[15px] text-slate-600 dark:text-slate-400 leading-relaxed mb-5">
        Once you're comfortable with basic sessions, explore these resources to get more out of Lucid AI:
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-10">
        <Link href="#" className="block p-5 border border-slate-200 dark:border-slate-700 rounded-xl hover:border-blue-200 dark:hover:border-blue-800 hover:bg-blue-50/30 dark:hover:bg-blue-950/30 transition-all group">
          <Plug className="w-5 h-5 text-slate-500 dark:text-slate-400 mb-3" />
          <h3 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 mb-1 group-hover:text-blue-700 dark:group-hover:text-blue-400 transition-colors">Integrations</h3>
          <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">Connect Lucid AI to your existing tools like GitHub, Slack, Jira, and more.</p>
        </Link>
        <Link href="#" className="block p-5 border border-slate-200 dark:border-slate-700 rounded-xl hover:border-blue-200 dark:hover:border-blue-800 hover:bg-blue-50/30 dark:hover:bg-blue-950/30 transition-all group">
          <BookOpen className="w-5 h-5 text-slate-500 dark:text-slate-400 mb-3" />
          <h3 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 mb-1 group-hover:text-blue-700 dark:group-hover:text-blue-400 transition-colors">Playbooks</h3>
          <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">Learn how to use Playbooks to implement tasks.</p>
        </Link>
        <Link href="#" className="block p-5 border border-slate-200 dark:border-slate-700 rounded-xl hover:border-blue-200 dark:hover:border-blue-800 hover:bg-blue-50/30 dark:hover:bg-blue-950/30 transition-all group">
          <Database className="w-5 h-5 text-slate-500 dark:text-slate-400 mb-3" />
          <h3 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 mb-1 group-hover:text-blue-700 dark:group-hover:text-blue-400 transition-colors">Knowledge</h3>
          <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">Add knowledge to help Lucid AI understand your team's practices.</p>
        </Link>
      </div>

      {/* Footer Navigation */}
      <div className="mt-16 pt-8 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between">
        <Link 
          href="/docs/get-started/lucid-ai-intro" 
          className="group flex flex-col items-start gap-0.5 px-4 py-3 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
        >
          <span className="text-[12px] font-medium text-slate-400 dark:text-slate-500">Previous</span>
          <div className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400 font-semibold text-[14px]">
            <ArrowLeft className="w-4 h-4 transition-transform group-hover:-translate-x-1" />
            Introducing Lucid AI
          </div>
        </Link>
        <Link 
          href="#" 
          className="group flex flex-col items-end gap-0.5 px-4 py-3 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
        >
          <span className="text-[12px] font-medium text-slate-400 dark:text-slate-500">Next</span>
          <div className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400 font-semibold text-[14px]">
            Tutorial Library
            <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
          </div>
        </Link>
      </div>
    </div>
  );
}
