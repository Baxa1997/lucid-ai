'use client';

// ─────────────────────────────────────────────────────────
//  u-code — AgentWorkspace Component
//  Split-pane: Chat (left) + Terminal/Log (right)
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useAgentSession } from '@/hooks/useAgentSession';
import {
  Send, Terminal, MessageSquare, Circle, X, StopCircle,
  Maximize2, Minimize2, AlertCircle, CheckCircle2,
  Bot, User, Cpu, ChevronDown,
} from 'lucide-react';

/**
 * AgentWorkspace — The main AI agent interaction interface.
 *
 * Left panel: Chat with the AI agent
 * Right panel: Terminal/log output with auto-scroll
 *
 * Props:
 * @param {string} projectId    - Project to work on
 * @param {string} task         - Initial task for the agent
 * @param {string} projectName  - Display name
 * @param {Function} onClose    - Called when workspace is closed
 */
export default function AgentWorkspace({
  projectId,
  task,
  projectName = 'Project',
  onClose,
}) {
  const {
    state,
    sessionId,
    logs,
    chatMessages,
    error,
    sendMessage,
    sendCommand,
    stopSession,
  } = useAgentSession({ projectId, task, autoStart: true });

  const [chatInput, setChatInput] = useState('');
  const [terminalInput, setTerminalInput] = useState('');
  const [terminalExpanded, setTerminalExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState('terminal'); // terminal | logs

  const chatEndRef = useRef(null);
  const terminalEndRef = useRef(null);

  // ── Auto-scroll chat ──────────────────────────────────
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  // ── Auto-scroll terminal ──────────────────────────────
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // ── Send chat message ─────────────────────────────────
  const handleChatSend = (e) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    sendMessage(chatInput.trim());
    setChatInput('');
  };

  // ── Send terminal command ─────────────────────────────
  const handleTerminalSend = (e) => {
    e.preventDefault();
    if (!terminalInput.trim()) return;
    sendCommand(terminalInput.trim());
    setTerminalInput('');
  };

  // ── Status indicator ──────────────────────────────────
  const statusConfig = {
    idle:       { color: 'text-slate-400', bg: 'bg-slate-400', label: 'Idle' },
    starting:   { color: 'text-amber-500', bg: 'bg-amber-500', label: 'Starting...' },
    connecting: { color: 'text-blue-500',  bg: 'bg-blue-500',  label: 'Connecting...' },
    connected:  { color: 'text-green-500', bg: 'bg-green-500', label: 'Connected' },
    error:      { color: 'text-red-500',   bg: 'bg-red-500',   label: 'Error' },
    stopped:    { color: 'text-slate-400', bg: 'bg-slate-400', label: 'Stopped' },
  };

  const currentStatus = statusConfig[state] || statusConfig.idle;

  // ═══════════════════════════════════════════════════════
  //  Render
  // ═══════════════════════════════════════════════════════

  return (
    <div className="h-full flex flex-col bg-[#f0f4f9]">

      {/* ── Top Bar ────────────────────────────────────── */}
      <div className="shrink-0 flex items-center justify-between px-5 py-3 bg-white border-b border-slate-200">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-blue-50 border border-blue-100 flex items-center justify-center">
            <Cpu className="w-4 h-4 text-blue-600" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-slate-900">{projectName}</h2>
            <div className="flex items-center gap-2">
              <div className={cn("w-2 h-2 rounded-full", currentStatus.bg, state === 'connected' && 'animate-pulse')} />
              <span className={cn("text-[11px] font-medium", currentStatus.color)}>
                {currentStatus.label}
              </span>
              {sessionId && (
                <span className="text-[10px] text-slate-300 font-mono">
                  {sessionId.slice(0, 8)}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {state === 'connected' && (
            <button
              onClick={stopSession}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-all"
            >
              <StopCircle className="w-3.5 h-3.5" />
              Stop
            </button>
          )}
          {onClose && (
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-all"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* ── Error Banner ──────────────────────────────── */}
      {error && (
        <div className="shrink-0 flex items-center gap-3 px-5 py-2.5 bg-red-50 border-b border-red-200">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
          <p className="text-xs text-red-600 flex-1">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="text-xs font-semibold text-red-700 hover:underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Split Pane ────────────────────────────────── */}
      <div className={cn(
        "flex-1 flex overflow-hidden",
        terminalExpanded ? "flex-col" : "flex-row"
      )}>

        {/* ════════════════════════════════════════════════
            LEFT — Chat Panel
        ════════════════════════════════════════════════ */}
        {!terminalExpanded && (
          <div className="flex-1 flex flex-col min-w-0 border-r border-slate-200">

            {/* Chat Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
              {chatMessages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <div className="w-14 h-14 rounded-2xl bg-blue-50 border border-blue-100 flex items-center justify-center mb-4">
                    <Bot className="w-6 h-6 text-blue-400" />
                  </div>
                  <p className="text-sm font-medium text-slate-400">
                    {state === 'starting' || state === 'connecting'
                      ? 'Connecting to agent...'
                      : 'Chat with the AI agent'}
                  </p>
                  <p className="text-xs text-slate-300 mt-1">
                    Messages will appear here once the session is active.
                  </p>
                </div>
              ) : (
                chatMessages.map((msg) => (
                  <div
                    key={msg.id}
                    className={cn(
                      "flex gap-3",
                      msg.role === 'user' ? "justify-end" : "justify-start"
                    )}
                  >
                    {msg.role !== 'user' && (
                      <div className={cn(
                        "w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5",
                        msg.role === 'agent'
                          ? "bg-blue-50 border border-blue-100"
                          : "bg-slate-100 border border-slate-200"
                      )}>
                        {msg.role === 'agent'
                          ? <Bot className="w-3.5 h-3.5 text-blue-500" />
                          : <Cpu className="w-3.5 h-3.5 text-slate-400" />}
                      </div>
                    )}

                    <div className={cn(
                      "max-w-[75%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed",
                      msg.role === 'user'
                        ? "bg-blue-600 text-white rounded-br-md"
                        : msg.role === 'agent'
                        ? "bg-white border border-slate-200 text-slate-700 rounded-bl-md shadow-sm"
                        : "bg-slate-100 text-slate-500 text-xs italic rounded-bl-md"
                    )}>
                      {msg.content}
                    </div>

                    {msg.role === 'user' && (
                      <div className="w-7 h-7 rounded-lg bg-slate-700 flex items-center justify-center shrink-0 mt-0.5">
                        <User className="w-3.5 h-3.5 text-white" />
                      </div>
                    )}
                  </div>
                ))
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Chat Input */}
            <form onSubmit={handleChatSend} className="shrink-0 px-4 py-3 border-t border-slate-200 bg-white">
              <div className="flex items-center gap-2">
                <input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  placeholder={state === 'connected' ? 'Send a message to the agent...' : 'Waiting for connection...'}
                  disabled={state !== 'connected'}
                  className="flex-1 px-4 py-2.5 text-sm bg-slate-50 border border-slate-200 rounded-xl outline-none focus:border-blue-300 focus:ring-2 focus:ring-blue-50 text-slate-700 placeholder:text-slate-300 disabled:opacity-50 transition-all"
                />
                <button
                  type="submit"
                  disabled={!chatInput.trim() || state !== 'connected'}
                  className={cn(
                    "p-2.5 rounded-xl transition-all",
                    chatInput.trim() && state === 'connected'
                      ? "bg-blue-600 text-white hover:bg-blue-700 shadow-sm"
                      : "bg-slate-100 text-slate-300 cursor-not-allowed"
                  )}
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </form>
          </div>
        )}

        {/* ════════════════════════════════════════════════
            RIGHT — Terminal / Logs Panel
        ════════════════════════════════════════════════ */}
        <div className={cn(
          "flex flex-col bg-[#1a1b26]",
          terminalExpanded ? "flex-1" : "w-[45%] min-w-[360px]"
        )}>

          {/* Terminal Header */}
          <div className="shrink-0 flex items-center justify-between px-4 py-2.5 bg-[#16171f] border-b border-[#2a2b3d]">
            <div className="flex items-center gap-3">
              {/* Tab: Terminal */}
              <button
                onClick={() => setActiveTab('terminal')}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all",
                  activeTab === 'terminal'
                    ? "bg-[#2a2b3d] text-green-400"
                    : "text-slate-500 hover:text-slate-400"
                )}
              >
                <Terminal className="w-3.5 h-3.5" />
                Terminal
              </button>
              {/* Tab: Logs */}
              <button
                onClick={() => setActiveTab('logs')}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all",
                  activeTab === 'logs'
                    ? "bg-[#2a2b3d] text-blue-400"
                    : "text-slate-500 hover:text-slate-400"
                )}
              >
                <MessageSquare className="w-3.5 h-3.5" />
                Logs
                {logs.length > 0 && (
                  <span className="ml-1 px-1.5 py-0.5 bg-[#2a2b3d] rounded text-[10px] text-slate-400">
                    {logs.length}
                  </span>
                )}
              </button>
            </div>

            <button
              onClick={() => setTerminalExpanded(!terminalExpanded)}
              className="p-1 text-slate-500 hover:text-slate-300 transition-colors"
            >
              {terminalExpanded
                ? <Minimize2 className="w-3.5 h-3.5" />
                : <Maximize2 className="w-3.5 h-3.5" />}
            </button>
          </div>

          {/* Terminal Content */}
          <div className="flex-1 overflow-y-auto px-4 py-3 font-mono text-[13px] leading-relaxed">
            {logs.length === 0 ? (
              <div className="flex items-center gap-2 text-slate-600">
                <Circle className="w-2.5 h-2.5 text-slate-600" />
                <span>Waiting for output...</span>
              </div>
            ) : (
              logs
                .filter(log => activeTab === 'logs' || log.type !== 'user')
                .map((log) => (
                  <div key={log.id} className="flex items-start gap-2 mb-1 group">
                    {/* Timestamp */}
                    <span className="text-[10px] text-slate-700 tabular-nums shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                      {new Date(log.timestamp).toLocaleTimeString('en-US', {
                        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
                      })}
                    </span>
                    {/* Content */}
                    <span className={cn(
                      "whitespace-pre-wrap break-all",
                      log.type === 'error' && 'text-red-400',
                      log.type === 'cmd_output' && 'text-green-400',
                      log.type === 'agent_message' && 'text-blue-400',
                      log.type === 'system' && 'text-slate-500',
                      log.type === 'user' && 'text-yellow-400',
                      log.type === 'file_write' && 'text-purple-400',
                      !['error', 'cmd_output', 'agent_message', 'system', 'user', 'file_write'].includes(log.type) && 'text-slate-400',
                    )}>
                      {log.content}
                    </span>
                  </div>
                ))
            )}
            <div ref={terminalEndRef} />
          </div>

          {/* Terminal Input */}
          {activeTab === 'terminal' && (
            <form onSubmit={handleTerminalSend} className="shrink-0 px-4 py-2.5 border-t border-[#2a2b3d]">
              <div className="flex items-center gap-2">
                <span className="text-green-500 text-xs font-mono shrink-0">$</span>
                <input
                  value={terminalInput}
                  onChange={(e) => setTerminalInput(e.target.value)}
                  placeholder="Run a command..."
                  disabled={state !== 'connected'}
                  className="flex-1 bg-transparent text-sm text-green-300 placeholder:text-slate-600 outline-none font-mono disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={!terminalInput.trim() || state !== 'connected'}
                  className="p-1 text-green-500 hover:text-green-400 disabled:text-slate-600 transition-colors"
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
