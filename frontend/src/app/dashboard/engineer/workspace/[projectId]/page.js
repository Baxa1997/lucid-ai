'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Full Page Conversation Workspace
//  Layout: [FileExplorer] | [Chat] | [Terminal / FileViewer]
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect, useCallback, use } from 'react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  ArrowLeft, Send, Terminal, Settings, Minimize2,
  Bot, User, Cpu, ArrowDown, Loader2,
  FolderOpen, X, FileText, Copy, Check,
} from 'lucide-react';
import { useAgentSession } from '@/hooks/useAgentSession';
import FileExplorer from '@/components/agent/FileExplorer';

// ── Status Component ───────────────────────────────────────
function ConnectionStatus({ status, error }) {
  const config = {
    idle:       { color: 'text-slate-400', label: 'Idle' },
    connecting: { color: 'text-blue-600',  label: 'Connecting...' },
    connected:  { color: 'text-emerald-600', label: 'Connected' },
    error:      { color: 'text-red-600',   label: 'Error' },
    stopped:    { color: 'text-slate-400', label: 'Stopped' },
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

// ── File Viewer Panel ──────────────────────────────────────
function FileViewer({ path, content, loading }) {
  const [copied, setCopied] = useState(false);
  const fileName = path ? path.split('/').pop() : '';

  const handleCopy = () => {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="h-full flex flex-col bg-[#1e1e2e] font-mono">
      {/* File name bar */}
      <div className="shrink-0 flex items-center justify-between px-4 py-2 bg-[#181825] border-b border-slate-800">
        <div className="flex items-center gap-2 text-slate-400">
          <FileText className="w-3.5 h-3.5 text-blue-400" />
          <span className="text-xs text-slate-300 font-medium truncate max-w-[200px]">
            {fileName || 'File'}
          </span>
        </div>
        <button
          onClick={handleCopy}
          disabled={loading || !content}
          className="p-1.5 text-slate-500 hover:text-white transition-colors disabled:opacity-30"
          title="Copy content"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4 custom-scrollbar">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />
          </div>
        ) : content ? (
          <pre className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap break-all">
            {content}
          </pre>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-slate-600 gap-2">
            <FileText className="w-8 h-8 opacity-20" />
            <p className="text-xs">Select a file to view its contents</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Page Component ────────────────────────────────────
export default function ConversationPage({ params }) {
  const { projectId } = use(params);
  const router = useRouter();
  const decodedProjectId = decodeURIComponent(projectId || 'unknown');

  // ── Auth token for WebSocket ────────────────────────────
  const [token, setToken] = useState('');

  useEffect(() => {
    fetch('/api/agent/token')
      .then((r) => r.json())
      .then((d) => { if (d.token) setToken(d.token); })
      .catch(() => {});
  }, []);

  // ── Agent session hook ──────────────────────────────────
  const {
    status,
    sessionId,
    messages,
    terminalLogs,
    files,
    error,
    sendMessage,
  } = useAgentSession({
    projectId: decodedProjectId,
    token,
  });

  // ── Layout state ────────────────────────────────────────
  const [chatInput, setChatInput] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [showExplorer, setShowExplorer] = useState(true);

  // rightPanel: null | 'terminal' | 'file'
  const [rightPanel, setRightPanel] = useState(null);

  // ── File viewer state ───────────────────────────────────
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState('');
  const [fileLoading, setFileLoading] = useState(false);

  const chatEndRef = useRef(null);
  const logsEndRef = useRef(null);
  const chatContainerRef = useRef(null);

  // ── Auto-scroll chat ────────────────────────────────────
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

  // ── File select: load content from backend ──────────────
  const handleFileSelect = useCallback(async (path) => {
    setSelectedFile(path);
    setRightPanel('file');
    setFileLoading(true);
    setFileContent('');

    if (!sessionId) {
      setFileContent('// Session not connected — start a task first.');
      setFileLoading(false);
      return;
    }

    try {
      const res = await fetch(
        `/api/files/read?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`
      );
      const data = await res.json();
      setFileContent(res.ok ? (data.content || '') : `// Error: ${data.error || 'Could not load file'}`);
    } catch {
      setFileContent('// Failed to load file content');
    } finally {
      setFileLoading(false);
    }
  }, [sessionId]);

  // ── Render ─────────────────────────────────────────────
  return (
    <div className="flex h-screen bg-[#f5f7fa] overflow-hidden">

      {/* ════════════════════════════════════════════════
          LEFT — File Explorer Sidebar
      ════════════════════════════════════════════════ */}
      {showExplorer && (
        <div className="w-60 shrink-0 border-r border-slate-200 overflow-hidden">
          <FileExplorer
            files={files}
            selectedFile={selectedFile}
            onFileSelect={handleFileSelect}
            projectName={decodedProjectId}
          />
        </div>
      )}

      {/* ════════════════════════════════════════════════
          CENTER — Chat Area
      ════════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col min-w-0 relative">

        {/* Header */}
        <header className="shrink-0 h-16 bg-white/80 backdrop-blur-md border-b border-slate-200 flex items-center justify-between px-6 z-10">
          <div className="flex items-center gap-3">
            <button
              onClick={() => router.push('/dashboard/engineer')}
              className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-full transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-slate-900 truncate max-w-[200px]">{decodedProjectId}</h1>
                <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 text-[10px] font-bold uppercase tracking-wide">
                  AI Session
                </span>
              </div>
              <p className="text-xs text-slate-500 font-medium">AI Engineering Session</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <ConnectionStatus status={status} error={error} />

            {/* Explorer toggle */}
            <button
              onClick={() => setShowExplorer(!showExplorer)}
              className={cn(
                "p-2 rounded-lg transition-all border",
                showExplorer
                  ? "bg-slate-100 text-slate-900 border-slate-300"
                  : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50"
              )}
              title="Toggle File Explorer"
            >
              <FolderOpen className="w-4 h-4" />
            </button>

            {/* Terminal toggle */}
            <button
              onClick={() => setRightPanel(rightPanel === 'terminal' ? null : 'terminal')}
              className={cn(
                "p-2 rounded-lg transition-all border",
                rightPanel === 'terminal'
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
                  msg.role === 'user' ? "flex-row-reverse" : "flex-row"
                )}
              >
                {/* Avatar */}
                <div className={cn(
                  "w-9 h-9 rounded-xl flex items-center justify-center shrink-0 shadow-sm",
                  msg.role === 'user'
                    ? "bg-slate-900 text-white"
                    : msg.role === 'agent'
                    ? "bg-blue-600 text-white"
                    : "bg-slate-100 text-slate-400"
                )}>
                  {msg.role === 'user' ? <User className="w-5 h-5" /> :
                   msg.role === 'agent' ? <Bot className="w-5 h-5" /> :
                   <Cpu className="w-5 h-5" />}
                </div>

                {/* Content */}
                <div className={cn(
                  "flex-1 max-w-[85%] rounded-2xl p-4 shadow-sm",
                  msg.role === 'user'
                    ? "bg-white border border-slate-200 text-slate-700"
                    : msg.role === 'agent'
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
              Press <kbd className="font-sans px-1 py-0.5 bg-white border border-slate-200 rounded text-slate-500">Enter</kbd> to send,{' '}
              <kbd className="font-sans px-1 py-0.5 bg-white border border-slate-200 rounded text-slate-500">Shift + Enter</kbd> for new line
            </p>
          </div>
        </div>

      </div>

      {/* ════════════════════════════════════════════════
          RIGHT — Terminal OR File Viewer Panel
      ════════════════════════════════════════════════ */}
      {rightPanel && (
        <div className="w-[400px] bg-[#1e1e2e] flex flex-col border-l border-slate-800 shadow-2xl shrink-0">

          {/* Panel header with tab buttons */}
          <div className="h-12 flex items-center justify-between px-4 border-b border-slate-800 bg-[#181825]">
            <div className="flex items-center gap-1">
              <button
                onClick={() => setRightPanel('terminal')}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-bold transition-colors",
                  rightPanel === 'terminal'
                    ? "bg-slate-700 text-white"
                    : "text-slate-500 hover:text-slate-300"
                )}
              >
                <Terminal className="w-3.5 h-3.5 text-emerald-500" />
                Terminal
              </button>
              {selectedFile && (
                <button
                  onClick={() => setRightPanel('file')}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-bold transition-colors",
                    rightPanel === 'file'
                      ? "bg-slate-700 text-white"
                      : "text-slate-500 hover:text-slate-300"
                  )}
                >
                  <FileText className="w-3.5 h-3.5 text-blue-400" />
                  {selectedFile.split('/').pop()}
                </button>
              )}
            </div>
            <button
              onClick={() => setRightPanel(null)}
              className="p-1.5 text-slate-500 hover:text-white transition-colors"
              title="Close panel"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Panel content */}
          <div className="flex-1 overflow-hidden">
            {rightPanel === 'terminal' ? (
              <div className="h-full flex flex-col">
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
                      .filter(log => log.type !== 'user')
                      .map((log) => (
                        <div key={log.id} className="mb-2 break-all group">
                          <span className={cn(
                            "whitespace-pre-wrap",
                            log.type === 'error' || log.content?.includes('[ERROR]') ? "text-red-400" :
                            log.content?.startsWith('$') ? "text-emerald-400 font-bold" :
                            log.type === 'file_write' ? "text-amber-400" :
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
            ) : (
              <FileViewer
                path={selectedFile}
                content={fileContent}
                loading={fileLoading}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
