'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Full Page Conversation Workspace
//  Focus strictly on Chat + Terminal (No File Tree/Editor)
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect, use } from 'react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  ArrowLeft, Send, Terminal, Settings, Minimize2,
  Bot, User, Cpu, ArrowDown, Loader2
} from 'lucide-react';
import { useAgentSession } from '@/hooks/useAgentSession';

// ── Status Component ───────────────────────────────────────
function ConnectionStatus({ status, error }) {
  const config = {
    idle:       { color: 'text-slate-400', label: 'Idle' },
    connecting: { color: 'text-blue-600',  label: 'Connecting...' },
    connected:  { color: 'text-emerald-600', label: 'Connected' },
    error:      { color: 'text-red-600',   label: 'Error' },
  };
  const current = config[status] || config.idle;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-white border border-slate-200 rounded-full shadow-sm">
      <div className={cn("w-2 h-2 rounded-full",
        status === 'connected' ? "bg-emerald-500" :
        status === 'connecting' ? "bg-blue-500 animate-pulse" :
        status === 'error' ? "bg-red-500" : "bg-slate-300"
      )} />
      <span className={cn("text-xs font-semibold", current.color)}>
        {error || current.label}
      </span>
    </div>
  );
}

// ── Main Page Component ────────────────────────────────────
export default function ConversationPage({ params }) {
  const { projectId } = use(params);
  const router = useRouter();
  const decodedProjectId = decodeURIComponent(projectId || 'unknown');

  // ── Hook ───────────────────────────────────────────────
  const {
    status,
    messages,
    terminalLogs,
    error,
    sendMessage,
  } = useAgentSession({
    projectId: decodedProjectId,
    token: '',
  });

  // ── State ──────────────────────────────────────────────
  const [chatInput, setChatInput] = useState('');
  const [showTerminal, setShowTerminal] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  const chatEndRef = useRef(null);
  const logsEndRef = useRef(null);
  const chatContainerRef = useRef(null);

  // ── Auto-scroll ────────────────────────────────────────
  useEffect(() => {
    if (!showScrollBtn) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, showScrollBtn]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [terminalLogs]);

  // ── Handlers ───────────────────────────────────────────
  const handleSend = (e) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    sendMessage(chatInput.trim());
    setChatInput('');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  };

  const handleChatScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    setShowScrollBtn(scrollHeight - scrollTop - clientHeight > 100);
  };

  // ── Render ─────────────────────────────────────────────
  return (
    <div className="flex h-screen bg-[#f5f7fa] overflow-hidden">
      
      {/* ════════════════════════════════════════════════
          MAIN CHAT AREA (Centers the conversation)
      ════════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        
        {/* Header */}
        <header className="shrink-0 h-16 bg-white/80 backdrop-blur-md border-b border-slate-200 flex items-center justify-between px-6 z-10">
          <div className="flex items-center gap-4">
            <button 
              onClick={() => router.push('/dashboard/engineer')}
              className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-full transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-slate-900">{decodedProjectId}</h1>
                <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 text-[10px] font-bold uppercase tracking-wide">
                  Gemini 3 Flash
                </span>
              </div>
              <p className="text-xs text-slate-500 font-medium">AI Engineering Session</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <ConnectionStatus status={status} error={error} />
            <button
              onClick={() => setShowTerminal(!showTerminal)}
              className={cn(
                "p-2 rounded-lg transition-all border",
                showTerminal 
                  ? "bg-slate-100 text-slate-900 border-slate-300"
                  : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50"
              )}
              title="Toggle Terminal"
            >
              <Terminal className="w-4 h-4" />
            </button>
            <button 
              onClick={() => router.push('/dashboard/engineer/settings')}
              className="p-2 text-slate-400 hover:text-slate-900 transition-colors"
            >
              <Settings className="w-5 h-5" />
            </button>
          </div>
        </header>

        {/* Chat Stream */}
        <div 
          className="flex-1 overflow-y-auto px-4 md:px-0 py-6"
          ref={chatContainerRef}
          onScroll={handleChatScroll}
        >
          <div className="max-w-3xl mx-auto space-y-6">
            
            {/* Welcome State */}
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center py-20 text-center opacity-0 animate-in fade-in slide-in-from-bottom-4 duration-700 fill-mode-forwards">
                <div className="w-20 h-20 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-3xl flex items-center justify-center shadow-xl shadow-blue-500/20 mb-8">
                  <Bot className="w-10 h-10 text-white" />
                </div>
                <h2 className="text-2xl font-bold text-slate-900 mb-3">
                  How can I help you build today?
                </h2>
                <p className="text-slate-500 max-w-md leading-relaxed mb-8">
                  I can write code, run commands, and debug your application. 
                  Just describe your task to get started.
                </p>
                <div className="flex flex-wrap gap-2 justify-center">
                  {[
                    "Create a login form",
                    "Debug the API route",
                    "Install Tailwind CSS",
                    "Refactor the sidebar"
                  ].map(suggestion => (
                    <button
                      key={suggestion}
                      onClick={() => setChatInput(suggestion)}
                      className="px-4 py-2 bg-white border border-slate-200 rounded-full text-sm font-medium text-slate-600 hover:border-blue-300 hover:text-blue-600 hover:shadow-sm transition-all"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Messages */}
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={cn(
                  "flex gap-4 group px-4",
                  msg.type === 'user' ? "flex-row-reverse" : "flex-row"
                )}
              >
                {/* Avatar */}
                <div className={cn(
                  "w-9 h-9 rounded-xl flex items-center justify-center shrink-0 shadow-sm",
                  msg.type === 'user' 
                    ? "bg-slate-900 text-white" 
                    : msg.type === 'agent'
                    ? "bg-blue-600 text-white"
                    : "bg-slate-100 text-slate-400"
                )}>
                  {msg.type === 'user' ? <User className="w-5 h-5" /> : 
                   msg.type === 'agent' ? <Bot className="w-5 h-5" /> : 
                   <Cpu className="w-5 h-5" />}
                </div>

                {/* Content */}
                <div className={cn(
                  "flex-1 max-w-[85%] rounded-2xl p-4 shadow-sm",
                  msg.type === 'user'
                    ? "bg-white border border-slate-200 text-slate-700"
                    : msg.type === 'agent'
                    ? "bg-white border border-blue-100 text-slate-800"
                    : "bg-slate-50 border border-slate-100 text-slate-500 text-sm italic"
                )}>
                  <div className="whitespace-pre-wrap text-sm leading-6 font-sans">
                    {msg.content}
                  </div>
                </div>
              </div>
            ))}
            
            {/* Loading Indicator */}
            {(status === 'working' || status === 'connecting') && (
              <div className="flex items-center gap-3 px-4 max-w-3xl mx-auto">
                <div className="w-9 h-9 rounded-xl bg-white border border-slate-200 flex items-center justify-center shadow-sm">
                   <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
                </div>
                <span className="text-sm font-medium text-slate-400 animate-pulse">
                  Agent is thinking...
                </span>
              </div>
            )}

            <div ref={chatEndRef} className="h-4" />
          </div>
        </div>

        {/* Input Area */}
        <div className="shrink-0 p-6 bg-[#f5f7fa]">
          <div className="max-w-3xl mx-auto relative">
            {showScrollBtn && (
              <button
                onClick={() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })}
                className="absolute -top-12 left-1/2 -translate-x-1/2 bg-slate-900/80 text-white px-4 py-1.5 rounded-full text-xs font-semibold backdrop-blur-md shadow-lg flex items-center gap-2 hover:bg-slate-900 transition-colors z-20"
              >
                <ArrowDown className="w-3 h-3" />
                Scroll to latest
              </button>
            )}

            <form 
              onSubmit={handleSend}
              className="relative bg-white border border-slate-300 rounded-2xl shadow-sm focus-within:ring-4 focus-within:ring-blue-500/10 focus-within:border-blue-500 transition-all overflow-hidden"
            >
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask the agent to make changes..."
                className="w-full px-5 py-4 min-h-[60px] max-h-[200px] outline-none text-slate-900 placeholder:text-slate-400 resize-none font-medium leading-relaxed"
                rows={1}
              />
              <div className="flex items-center justify-between px-3 pb-3">
                <div className="flex items-center gap-1 text-xs font-semibold text-slate-400">
                  <span className="px-2 py-1 rounded hover:bg-slate-100 cursor-pointer transition-colors">+ Add Context</span>
                </div>
                <button
                  type="submit"
                  disabled={!chatInput.trim()}
                  className={cn(
                    "p-2.5 rounded-xl transition-all",
                    chatInput.trim()
                      ? "bg-blue-600 text-white shadow-md shadow-blue-600/20 hover:bg-blue-700 hover:scale-105"
                      : "bg-slate-100 text-slate-300 cursor-not-allowed"
                  )}
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </form>
            <p className="text-center text-[11px] font-medium text-slate-400 mt-3">
              Press <kbd className="font-sans px-1 py-0.5 bg-white border border-slate-200 rounded text-slate-500">Enter</kbd> to send, <kbd className="font-sans px-1 py-0.5 bg-white border border-slate-200 rounded text-slate-500">Shift + Enter</kbd> for new line
            </p>
          </div>
        </div>

      </div>

      {/* ════════════════════════════════════════════════
          RIGHT — Terminal Sidebar (Collapsible)
      ════════════════════════════════════════════════ */}
      {showTerminal && (
        <div className="w-[400px] bg-[#1e1e2e] flex flex-col border-l border-slate-800 shadow-2xl shrink-0 transition-all">
          <div className="h-12 flex items-center justify-between px-4 border-b border-slate-800 bg-[#181825]">
            <div className="flex items-center gap-2 text-slate-400">
              <Terminal className="w-4 h-4 text-emerald-500" />
              <span className="text-xs font-bold uppercase tracking-wider text-slate-300">Terminal Output</span>
            </div>
            <div className="flex items-center gap-2">
              <button 
                onClick={() => setShowTerminal(false)}
                className="p-1.5 text-slate-500 hover:text-white transition-colors"
                title="Hide Terminal"
              >
                <Minimize2 className="w-4 h-4" />
              </button>
            </div>
          </div>
          
          <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed custom-scrollbar">
            {terminalLogs.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-slate-600 space-y-3">
                 <Terminal className="w-8 h-8 opacity-20" />
                 <p>Ready to execute commands...</p>
                 <span className="text-[10px] bg-slate-800/50 px-2 py-1 rounded text-slate-500">
                    Waiting for agent
                 </span>
              </div>
            ) : (
              terminalLogs
                .filter(log => log.type !== 'user') // Filter out user messages from log view
                .map((log) => (
               <div key={log.id} className="mb-2 break-all group">
                 {/* Timestamp */}
                 <span className="block text-[10px] text-slate-600 mb-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    {new Date(log.ts).toLocaleTimeString()}
                 </span>
                 <span className={cn(
                   "whitespace-pre-wrap",
                   log.content.includes('[ERROR]') ? "text-red-400" :
                   log.content.startsWith('$') ? "text-emerald-400 font-bold" :
                   "text-slate-300"
                 )}>
                   {log.content}
                 </span>
               </div>
              ))
            )}
            <div ref={logsEndRef} />
          </div>

          <div className="p-3 bg-[#181825] border-t border-slate-800">
             <div className="flex items-center gap-2 px-3 py-2 bg-[#1e1e2e] rounded-lg border border-slate-700 focus-within:border-emerald-500/50 focus-within:ring-1 focus-within:ring-emerald-500/20 transition-all">
                <span className="text-emerald-500 font-mono">$</span>
                <input 
                  type="text"
                  placeholder="Run command..." 
                  className="flex-1 bg-transparent border-none outline-none text-emerald-100 text-xs font-mono placeholder:text-slate-600"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && e.currentTarget.value.trim()) {
                      sendMessage(e.currentTarget.value.trim());
                      e.currentTarget.value = '';
                    }
                  }}
                />
             </div>
          </div>
        </div>
      )}
    </div>
  );
}
