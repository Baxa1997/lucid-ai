'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — AgentPanel (Light Mode)
//  Chat + Terminal tabs for the Agent Workspace
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';
import {
  Send, Terminal, MessageSquare, Bot, User, Cpu,
  StopCircle, Trash2, Circle, ArrowDown,
} from 'lucide-react';

/**
 * AgentPanel — Right-side panel with Chat and Terminal tabs (Light Mode).
 */
export default function AgentPanel({
  state = 'idle',
  chatMessages = [],
  logs = [],
  onSendMessage,
  onSendCommand,
  onStop,
  onClearLogs,
  error,
}) {
  const [activeTab, setActiveTab] = useState('chat');
  const [chatInput, setChatInput] = useState('');
  const [terminalInput, setTerminalInput] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  const chatEndRef = useRef(null);
  const terminalEndRef = useRef(null);
  const chatContainerRef = useRef(null);

  // ── Auto-scroll chat ──────────────────────────────────
  useEffect(() => {
    if (!showScrollBtn) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, showScrollBtn]);

  // ── Auto-scroll terminal ──────────────────────────────
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // ── Track scroll position ─────────────────────────────
  const handleChatScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
    setShowScrollBtn(!isNearBottom);
  };

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    setShowScrollBtn(false);
  };

  // ── Send chat message ─────────────────────────────────
  const handleChatSend = (e) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    onSendMessage?.(chatInput.trim());
    setChatInput('');
  };

  // ── Send terminal command ─────────────────────────────
  const handleTerminalSend = (e) => {
    e.preventDefault();
    if (!terminalInput.trim()) return;
    onSendCommand?.(terminalInput.trim());
    setTerminalInput('');
  };

  // ── Chat keyboard handler ─────────────────────────────
  const handleChatKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleChatSend(e);
    }
  };

  // ── Status badge ──────────────────────────────────────
  const statusConfig = {
    idle:       { dot: 'bg-slate-400', text: 'text-slate-500', label: 'Idle' },
    connecting: { dot: 'bg-amber-500 animate-pulse', text: 'text-amber-600', label: 'Connecting...' },
    connected:  { dot: 'bg-emerald-500', text: 'text-emerald-600', label: 'Connected' },
    working:    { dot: 'bg-blue-500 animate-pulse', text: 'text-blue-600', label: 'Working...' },
    error:      { dot: 'bg-red-500', text: 'text-red-600', label: 'Error' },
    stopped:    { dot: 'bg-slate-400', text: 'text-slate-500', label: 'Stopped' },
  };
  const currentStatus = statusConfig[state] || statusConfig.idle;

  // ── Log type colors ───────────────────────────────────
  const logColor = (type) => {
    const colors = {
      error: 'text-red-600',
      cmd_output: 'text-emerald-700',
      agent_message: 'text-blue-700',
      system: 'text-slate-400',
      user: 'text-amber-700',
      file_write: 'text-purple-700',
    };
    return colors[type] || 'text-slate-600';
  };

  return (
    <div className="h-full flex flex-col bg-white border-l border-slate-200">

      {/* ── Header ──────────────────────────────────────── */}
      <div className="shrink-0 flex items-center justify-between px-3 py-2 border-b border-slate-200 bg-slate-50/80">
        {/* Tabs */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => setActiveTab('chat')}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all",
              activeTab === 'chat'
                ? "bg-blue-50 text-blue-600 border border-blue-200"
                : "text-slate-400 hover:text-slate-600 hover:bg-slate-100"
            )}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            Chat
            {chatMessages.length > 0 && (
              <span className="ml-0.5 px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10px] tabular-nums text-slate-500">
                {chatMessages.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setActiveTab('terminal')}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all",
              activeTab === 'terminal'
                ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                : "text-slate-400 hover:text-slate-600 hover:bg-slate-100"
            )}
          >
            <Terminal className="w-3.5 h-3.5" />
            Terminal
            {logs.length > 0 && (
              <span className="ml-0.5 px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10px] tabular-nums text-slate-500">
                {logs.length}
              </span>
            )}
          </button>
        </div>

        {/* Status + Actions */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5">
            <div className={cn("w-2 h-2 rounded-full", currentStatus.dot)} />
            <span className={cn("text-[10px] font-bold uppercase tracking-wider", currentStatus.text)}>
              {currentStatus.label}
            </span>
          </div>

          {(state === 'connected' || state === 'working') && (
            <button
              onClick={onStop}
              className="p-1 text-red-500 hover:text-red-700 hover:bg-red-50 rounded transition-colors"
              title="Stop session"
            >
              <StopCircle className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* ── Error Banner ────────────────────────────────── */}
      {error && (
        <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-red-50 border-b border-red-200">
          <Circle className="w-2 h-2 text-red-500 fill-red-500" />
          <p className="text-[11px] text-red-600 font-medium flex-1 truncate">{error}</p>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          CHAT TAB
      ═══════════════════════════════════════════════════ */}
      {activeTab === 'chat' && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Messages */}
          <div
            ref={chatContainerRef}
            onScroll={handleChatScroll}
            className="flex-1 overflow-y-auto px-4 py-4 space-y-4 bg-[#f5f7fa]"
          >
            {chatMessages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center px-6">
                <div className="w-14 h-14 rounded-2xl bg-blue-50 border border-blue-200 flex items-center justify-center mb-4">
                  <Bot className="w-6 h-6 text-blue-600" />
                </div>
                <p className="text-sm font-semibold text-slate-700">
                  {state === 'connecting' || state === 'working'
                    ? 'Connecting to agent...'
                    : 'Start a conversation'}
                </p>
                <p className="text-xs text-slate-400 mt-1.5 max-w-[260px] leading-relaxed">
                  Describe what you want to build, fix, or improve. The agent will analyze and execute.
                </p>
              </div>
            ) : (
              chatMessages.map((msg) => (
                <div
                  key={msg.id}
                  className={cn(
                    "flex gap-3",
                    msg.type === 'user' ? "justify-end" : "justify-start"
                  )}
                >
                  {/* Avatar */}
                  {msg.type !== 'user' && (
                    <div className={cn(
                      "w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 border",
                      msg.type === 'agent'
                        ? "bg-blue-50 border-blue-200"
                        : "bg-slate-50 border-slate-200"
                    )}>
                      {msg.type === 'agent'
                        ? <Bot className="w-3.5 h-3.5 text-blue-600" />
                        : <Cpu className="w-3.5 h-3.5 text-slate-400" />}
                    </div>
                  )}

                  {/* Bubble */}
                  <div className={cn(
                    "max-w-[85%] px-3.5 py-2.5 rounded-2xl text-sm leading-relaxed",
                    msg.type === 'user'
                      ? "bg-blue-600 text-white rounded-br-md shadow-sm shadow-blue-600/20"
                      : msg.type === 'agent'
                      ? "bg-white border border-slate-200 text-slate-700 rounded-bl-md shadow-sm"
                      : "bg-slate-100 text-slate-500 text-xs italic rounded-bl-md"
                  )}>
                    {msg.content}
                  </div>

                  {/* User Avatar */}
                  {msg.type === 'user' && (
                    <div className="w-7 h-7 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0 mt-0.5">
                      <User className="w-3.5 h-3.5 text-slate-500" />
                    </div>
                  )}
                </div>
              ))
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Scroll-to-bottom FAB */}
          {showScrollBtn && (
            <div className="absolute bottom-20 left-1/2 -translate-x-1/2 z-10">
              <button
                onClick={scrollToBottom}
                className="p-2 bg-white border border-slate-200 rounded-full shadow-lg hover:bg-slate-50 transition-colors"
              >
                <ArrowDown className="w-4 h-4 text-slate-600" />
              </button>
            </div>
          )}

          {/* Chat Input */}
          <form onSubmit={handleChatSend} className="shrink-0 px-3 py-3 border-t border-slate-200 bg-white">
            <div className="flex items-end gap-2">
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={handleChatKeyDown}
                placeholder={
                  state === 'connected' || state === 'working'
                    ? 'Send a message...'
                    : state === 'idle'
                    ? 'Type a task to start...'
                    : 'Waiting for connection...'
                }
                rows={1}
                className="flex-1 px-3.5 py-2.5 text-sm bg-slate-50 border border-slate-200 rounded-xl outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 text-slate-800 placeholder:text-slate-400 resize-none min-h-[40px] max-h-[120px] transition-all"
              />
              <button
                type="submit"
                disabled={!chatInput.trim()}
                className={cn(
                  "p-2.5 rounded-xl transition-all shrink-0",
                  chatInput.trim()
                    ? "bg-blue-600 text-white hover:bg-blue-700 shadow-sm shadow-blue-600/20"
                    : "bg-slate-100 text-slate-400 cursor-not-allowed"
                )}
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
            <div className="flex items-center justify-between mt-1.5 px-1">
              <span className="text-[10px] text-slate-400">↵ Send · ⇧↵ New Line</span>
              <span className="text-[10px] text-slate-400 font-mono">gemini-3-flash-preview</span>
            </div>
          </form>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          TERMINAL TAB
      ═══════════════════════════════════════════════════ */}
      {activeTab === 'terminal' && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Terminal toolbar */}
          <div className="shrink-0 flex items-center justify-between px-4 py-1.5 border-b border-slate-200 bg-slate-50/80">
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-400" />
              <div className="w-2.5 h-2.5 rounded-full bg-amber-400" />
              <div className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
            </div>
            <button
              onClick={onClearLogs}
              className="p-1 text-slate-400 hover:text-slate-600 transition-colors"
              title="Clear terminal"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </div>

          {/* Terminal output */}
          <div className="flex-1 overflow-y-auto px-4 py-3 font-mono text-[13px] leading-relaxed bg-slate-900 text-slate-300">
            {logs.length === 0 ? (
              <div className="flex items-center gap-2 text-slate-500">
                <Circle className="w-2 h-2 text-slate-600" />
                <span>Waiting for output...</span>
              </div>
            ) : (
              logs.map((log) => (
                <div key={log.id} className="flex items-start gap-2 mb-0.5 group">
                  {/* Timestamp — shown on hover */}
                  <span className="text-[10px] text-slate-600 tabular-nums shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity select-none">
                    {new Date(log.ts).toLocaleTimeString('en-US', {
                      hour12: false,
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                    })}
                  </span>
                  {/* Content */}
                  <span className="whitespace-pre-wrap break-all text-slate-300">
                    {log.content}
                  </span>
                </div>
              ))
            )}
            <div ref={terminalEndRef} />
          </div>

          {/* Terminal input */}
          <form onSubmit={handleTerminalSend} className="shrink-0 px-4 py-2.5 border-t border-slate-200 bg-slate-900">
            <div className="flex items-center gap-2">
              <span className="text-emerald-400 text-sm font-mono shrink-0">$</span>
              <input
                value={terminalInput}
                onChange={(e) => setTerminalInput(e.target.value)}
                placeholder="Run a command..."
                disabled={state !== 'connected' && state !== 'working'}
                className="flex-1 bg-transparent text-sm text-emerald-300 placeholder:text-slate-600 outline-none font-mono disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={!terminalInput.trim() || (state !== 'connected' && state !== 'working')}
                className="p-1 text-emerald-400 hover:text-emerald-300 disabled:text-slate-600 transition-colors"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
