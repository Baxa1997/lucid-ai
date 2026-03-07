'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Full Page Conversation Workspace
//  Layout: [Chat] | [Terminal / FileViewer]
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect, useCallback, use } from 'react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  ArrowLeft, Send, Terminal, Settings, X,
  Bot, User, Cpu, ArrowDown, Loader2,
  FileText, Copy, Check, Clock, GitBranch, Github,
  ChevronDown, ChevronRight, Zap, Code2, Play,
} from 'lucide-react';
import { useAgentSession } from '@/hooks/useAgentSession';
import {
  getConversation,
  getMessages,
  addMessage as saveMessage,
  updateConversation,
} from '@/lib/conversations';

// ── Status Component ───────────────────────────────────────
function ConnectionStatus({ status, error }) {
  const config = {
    idle:       { color: 'text-slate-400 dark:text-slate-500', label: 'Idle' },
    connecting: { color: 'text-blue-600 dark:text-blue-400',  label: 'Connecting...' },
    preparing:  { color: 'text-amber-600 dark:text-amber-400', label: 'Preparing workspace...' },
    ready:      { color: 'text-emerald-600 dark:text-emerald-400', label: 'Ready' },
    connected:  { color: 'text-emerald-600 dark:text-emerald-400', label: 'Connected' },
    error:      { color: 'text-red-600 dark:text-red-400',   label: 'Error' },
    stopped:    { color: 'text-slate-400 dark:text-slate-500', label: 'Stopped' },
  };
  const current = config[status] || config.idle;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full shadow-sm">
      <div className={cn("w-2 h-2 rounded-full",
        status === 'ready' || status === 'connected' ? "bg-emerald-500" :
        status === 'preparing' ? "bg-amber-500 animate-pulse" :
        status === 'connecting' ? "bg-blue-500 animate-pulse" :
        status === 'error' ? "bg-red-500" : "bg-slate-300 dark:bg-slate-600"
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

// ── Message Bubble Components ──────────────────────────────

// Collapsible thinking block (like OpenHands)
function ThinkingBlock({ content, isActive }) {
  const [expanded, setExpanded] = useState(false);
  const preview = content.split('\n')[0].slice(0, 80);

  return (
    <div className="border border-blue-200/70 dark:border-blue-500/20 rounded-xl overflow-hidden bg-blue-50/40 dark:bg-blue-950/20">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-blue-50/60 dark:hover:bg-blue-900/15 transition-colors"
      >
        <div className="flex items-center gap-1.5 shrink-0">
          {isActive ? (
            <span className="flex gap-0.5">
              <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'0ms'}} />
              <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'120ms'}} />
              <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'240ms'}} />
            </span>
          ) : (
            <Cpu className="w-3.5 h-3.5 text-blue-500 dark:text-blue-400" />
          )}
          <span className="text-[11px] font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400">
            {isActive ? 'Thinking…' : 'Thought'}
          </span>
        </div>
        <span className="flex-1 text-xs text-slate-500 dark:text-slate-400 truncate">
          {!expanded && preview}
        </span>
        <span className="shrink-0 text-blue-400 dark:text-blue-500">
          {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </span>
      </button>
      {expanded && (
        <div className="px-4 pb-3 pt-1 border-t border-blue-200/40 dark:border-blue-500/15">
          <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed whitespace-pre-wrap font-mono">
            {content}
          </p>
        </div>
      )}
    </div>
  );
}

// Tool call step card — human-readable
function ToolCard({ content, toolName, isLatest }) {
  const [expanded, setExpanded] = useState(false);

  // Parse the content to get a human-readable label
  let label = '';
  let detail = '';
  const raw = content.trim();

  if (raw.startsWith('Running:')) {
    const cmd = raw.replace(/^Running:\s*`?/, '').replace(/`$/, '').trim();
    label = 'Ran command';
    detail = cmd.split('\n')[0].slice(0, 100);
  } else if (raw.startsWith('Editing file:')) {
    const path = raw.replace(/^Editing file:\s*/, '').trim();
    const fileName = path.split('/').pop();
    label = 'Edited';
    detail = fileName || path;
  } else if (raw.startsWith('File:')) {
    const path = raw.replace(/^File:\s*/, '').trim();
    const fileName = path.split('/').pop();
    label = 'Modified';
    detail = fileName || path;
  } else if (toolName === 'file_editor' || toolName === 'str_replace_editor') {
    label = 'Modified file';
    detail = raw.split('\n')[0].slice(0, 80);
  } else if (toolName === 'terminal') {
    label = 'Ran command';
    detail = raw.split('\n')[0].slice(0, 100);
  } else {
    label = 'Action';
    detail = raw.split('\n')[0].slice(0, 100);
  }

  const hasMore = content.length > (detail.length + label.length + 20);

  return (
    <div className="flex items-center gap-2 py-1.5 group">
      {/* Status icon */}
      <div className="shrink-0">
        {isLatest ? (
          <span className="flex gap-0.5">
            <span className="w-1 h-1 bg-blue-400 rounded-full animate-bounce" style={{animationDelay:'0ms'}} />
            <span className="w-1 h-1 bg-blue-400 rounded-full animate-bounce" style={{animationDelay:'120ms'}} />
            <span className="w-1 h-1 bg-blue-400 rounded-full animate-bounce" style={{animationDelay:'240ms'}} />
          </span>
        ) : (
          <Check className="w-3.5 h-3.5 text-emerald-500" />
        )}
      </div>

      {/* Label + detail */}
      <button
        onClick={() => hasMore && setExpanded(e => !e)}
        className={cn(
          "flex items-center gap-1.5 text-xs text-left min-w-0",
          hasMore && "cursor-pointer hover:opacity-80"
        )}
      >
        <span className="text-slate-500 dark:text-slate-400 shrink-0">{label}</span>
        <code className="text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-[11px] truncate max-w-[400px]">
          {detail}
        </code>
        {hasMore && (
          expanded
            ? <ChevronDown className="w-3 h-3 text-slate-400 shrink-0" />
            : <ChevronRight className="w-3 h-3 text-slate-400 shrink-0" />
        )}
      </button>

      {/* Expanded output */}
      {expanded && (
        <div className="absolute left-0 right-0 mt-8 mx-4 p-3 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg z-10">
          <pre className="text-[11px] text-slate-600 dark:text-slate-400 leading-relaxed whitespace-pre-wrap font-mono overflow-x-auto max-h-40 overflow-y-auto">
            {content}
          </pre>
        </div>
      )}
    </div>
  );
}

// The main message bubble dispatcher
function MessageBubble({ msg, isLatest }) {
  // User message
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end px-4">
        <div className="max-w-[80%] flex items-end gap-3">
          <div className="bg-slate-900 dark:bg-blue-600 text-white rounded-2xl rounded-br-sm px-4 py-3 shadow-sm">
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
          </div>
          <div className="w-7 h-7 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center shrink-0 mb-0.5">
            <User className="w-3.5 h-3.5 text-slate-600 dark:text-slate-300" />
          </div>
        </div>
      </div>
    );
  }

  // Thinking block
  if (msg.role === 'thinking') {
    return (
      <div className="flex items-start gap-3 px-4">
        <div className="w-7 h-7 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center shrink-0 mt-0.5">
          <Cpu className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
        </div>
        <div className="flex-1 max-w-[90%]">
          <ThinkingBlock content={msg.content} isActive={isLatest} />
        </div>
      </div>
    );
  }

  // Tool action — inline step with no border, just a check + description
  if (msg.role === 'tool') {
    return (
      <div className="flex items-start gap-3 px-4 relative">
        <div className="w-7 shrink-0" /> {/* Spacer to align with avatar column */}
        <div className="flex-1 max-w-[90%]">
          <ToolCard content={msg.content} toolName={msg.toolName || ''} isLatest={isLatest} />
        </div>
      </div>
    );
  }

  // System status
  if (msg.role === 'system') {
    return (
      <div className="flex justify-center px-4">
        <span className="text-[11px] text-slate-400 dark:text-slate-500 bg-slate-100 dark:bg-slate-800/60 px-3 py-1 rounded-full">
          {msg.content}
        </span>
      </div>
    );
  }

  // Agent message — clean, no borders, well-structured
  // Parse markdown-like content into rich elements
  const renderContent = (text) => {
    const lines = text.split('\n');
    let inCodeBlock = false;
    let codeLines = [];

    return lines.map((line, i) => {
      // Code block start/end
      if (line.trim().startsWith('```')) {
        if (inCodeBlock) {
          inCodeBlock = false;
          const code = codeLines.join('\n');
          codeLines = [];
          return (
            <pre key={i} className="font-mono text-xs bg-slate-100 dark:bg-slate-800/80 rounded-lg px-3 py-2 my-1.5 text-slate-600 dark:text-slate-400 overflow-x-auto">
              {code}
            </pre>
          );
        }
        inCodeBlock = true;
        return null;
      }

      if (inCodeBlock) {
        codeLines.push(line);
        return null;
      }

      // Inline formatting helper
      const formatInline = (str) => {
        // Bold + inline code
        const parts = str.split(/(\*\*.*?\*\*|`[^`]+`)/g);
        return parts.map((part, j) => {
          if (part.startsWith('**') && part.endsWith('**')) {
            return <strong key={j} className="font-semibold">{part.slice(2, -2)}</strong>;
          }
          if (part.startsWith('`') && part.endsWith('`')) {
            return <code key={j} className="bg-slate-200/60 dark:bg-slate-700/60 px-1 py-0.5 rounded text-[12px] font-mono">{part.slice(1, -1)}</code>;
          }
          return part;
        });
      };

      // Numbered list items
      const numberedMatch = line.match(/^\s*(\d+)\.\s+(.*)/);
      if (numberedMatch) {
        return (
          <div key={i} className="flex items-start gap-2.5 py-1">
            <span className="w-5 h-5 rounded-full bg-emerald-100 dark:bg-emerald-500/15 flex items-center justify-center shrink-0 mt-0.5">
              <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
            </span>
            <span className="flex-1">{formatInline(numberedMatch[2])}</span>
          </div>
        );
      }

      // Bullet points
      if (line.trim().startsWith('- ') || line.trim().startsWith('• ')) {
        const bulletContent = line.trim().replace(/^[-•]\s*/, '');
        return (
          <div key={i} className="flex items-start gap-2 py-0.5 pl-2">
            <span className="text-emerald-500 mt-1 shrink-0">•</span>
            <span>{formatInline(bulletContent)}</span>
          </div>
        );
      }

      if (!line.trim()) return <div key={i} className="h-1.5" />;
      return <div key={i} className="py-0.5">{formatInline(line)}</div>;
    });
  };

  return (
    <div className="flex items-start gap-3 px-4">
      <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center shrink-0 mt-0.5">
        <Bot className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="flex-1 max-w-[90%] text-sm text-slate-700 dark:text-slate-300 leading-relaxed">
        {renderContent(msg.content)}
      </div>
    </div>
  );
}

// ── Main Page Component ────────────────────────────────────
export default function ConversationPage({ params }) {
  const { projectId } = use(params);
  const router = useRouter();
  const conversationId = decodeURIComponent(projectId || 'unknown');

  // ── Conversation data from Supabase ─────────────────────
  const [conversation, setConversation] = useState(null);
  const [savedMessages, setSavedMessages] = useState([]);
  const [convLoading, setConvLoading] = useState(true);
  const prevMessagesLenRef = useRef(0);

  // Load conversation and messages on mount (with timeout)
  useEffect(() => {
    let cancelled = false;
    const timeout = setTimeout(() => {
      if (!cancelled) setConvLoading(false);
    }, 5000); // 5s safety timeout

    (async () => {
      try {
        const conv = await getConversation(conversationId);
        if (!cancelled && conv) {
          setConversation(conv);
          const msgs = await getMessages(conversationId);
          if (!cancelled) setSavedMessages(msgs);
        }
      } catch (err) {
        console.error('Failed to load conversation:', err);
      } finally {
        if (!cancelled) {
          clearTimeout(timeout);
          setConvLoading(false);
        }
      }
    })();

    return () => { cancelled = true; clearTimeout(timeout); };
  }, [conversationId]);

  // ── Auth token for WebSocket ────────────────────────────
  const [token, setToken] = useState('');
  const [gitToken, setGitToken] = useState('');

  useEffect(() => {
    fetch('/api/agent/token')
      .then((r) => r.json())
      .then((d) => { if (d.token) setToken(d.token); })
      .catch(() => {});
  }, []);

  // Load git token from integrations when conversation is available
  useEffect(() => {
    if (!conversation) return;
    (async () => {
      try {
        const { getIntegrations } = await import('@/lib/integrations');
        const intg = await getIntegrations();
        if (conversation.repo_provider === 'github' && intg.github?.token) {
          setGitToken(intg.github.token);
        } else if (conversation.repo_provider === 'gitlab' && intg.gitlab?.token) {
          setGitToken(intg.gitlab.token);
        }
      } catch (err) {
        console.error('Failed to load git token:', err);
      }
    })();
  }, [conversation]);

  // ── Agent session hook ──────────────────────────────────
  const {
    status,
    sessionId,
    messages,
    terminalLogs,
    files,
    error,
    sendMessage,
    isReady,
    isPreparing,
  } = useAgentSession({
    projectId: conversation?.repo_name || conversationId,
    token,
    repoUrl: conversation?.repo_url || '',
    gitToken,
    branch: conversation?.branch || 'main',
  });

  // ── Layout state ────────────────────────────────────────
  const [chatInput, setChatInput] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  // Save assistant messages to Supabase as they arrive
  useEffect(() => {
    if (!conversation?.id || !messages || messages.length === 0) return;

    const newMessages = messages.slice(prevMessagesLenRef.current);
    prevMessagesLenRef.current = messages.length;

    for (const msg of newMessages) {
      if (msg.role === 'assistant' && msg.content) {
        saveMessage(conversation.id, { role: 'assistant', content: msg.content });
      }
    }
  }, [messages, conversation?.id]);

  // rightPanel: null | 'terminal' | 'file'
  const [rightPanel, setRightPanel] = useState(null);

  // ── File viewer state ───────────────────────────────────
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState('');
  const [fileLoading, setFileLoading] = useState(false);

  const chatEndRef = useRef(null);
  const logsEndRef = useRef(null);
  const chatContainerRef = useRef(null);
  const userScrolledUpRef = useRef(false);

  // ── Smart auto-scroll: only scroll if user is near the bottom ──
  useEffect(() => {
    if (userScrolledUpRef.current) return; // user scrolled up — don't force scroll
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [terminalLogs]);

  // ── Handlers ───────────────────────────────────────────
  const handleSend = async (e) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    const text = chatInput.trim();
    sendMessage(text);
    setChatInput('');

    // Save user message to Supabase
    if (conversation?.id) {
      await saveMessage(conversation.id, { role: 'user', content: text });

      // Auto-generate title from first message via Gemini
      if (conversation.title === 'New Conversation') {
        // Don't block the chat — generate title in background
        (async () => {
          try {
            const res = await fetch('/api/generate-title', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                message: text,
                repoName: conversation.repo_name || '',
              }),
            });
            const { title } = await res.json();
            if (title && title !== 'New Conversation') {
              await updateConversation(conversation.id, { title });
              setConversation(prev => ({ ...prev, title }));
            }
          } catch {
            // Fallback: use truncated message
            const fallback = text.length > 50 ? text.slice(0, 50).replace(/\s+\S*$/, '…') : text;
            await updateConversation(conversation.id, { title: fallback });
            setConversation(prev => ({ ...prev, title: fallback }));
          }
        })();
      }
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  };

  const handleChatScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    const isNearBottom = distFromBottom < 100;
    setShowScrollBtn(distFromBottom > 100);
    // Track if user scrolled up intentionally
    userScrolledUpRef.current = !isNearBottom;
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
    <div className="flex h-screen bg-[#f5f7fa] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">

      {/* ════════════════════════════════════════════════
          CENTER — Chat Area
      ════════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col min-w-0 relative">

        {/* Header */}
        <header className="shrink-0 h-16 bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border-b border-slate-200 dark:border-slate-800 flex items-center justify-between px-6 z-10 transition-colors duration-200">
          <div className="flex items-center gap-3">
            <button
              onClick={() => router.push('/dashboard/engineer')}
              className="p-2 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-full transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-lg font-bold text-slate-900 dark:text-slate-100 truncate max-w-[300px]">
                {conversation?.title || 'New Conversation'}
              </h1>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="px-2 py-0.5 rounded-md bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 text-[10px] font-bold uppercase tracking-wide border border-blue-100 dark:border-blue-500/20">
                  {status || 'Idle'}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <ConnectionStatus status={status} error={error} />

            {/* Terminal toggle */}
            <button
              onClick={() => setRightPanel(rightPanel === 'terminal' ? null : 'terminal')}
              className={cn(
                "p-2 rounded-lg transition-all border",
                rightPanel === 'terminal'
                  ? "bg-slate-100 dark:bg-slate-800 text-slate-900 dark:text-slate-100 border-slate-300 dark:border-slate-600"
                  : "bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800"
              )}
              title="Toggle Terminal"
            >
              <Terminal className="w-4 h-4" />
            </button>

            <button
              onClick={() => router.push('/dashboard/engineer/settings')}
              className="p-2 text-slate-400 dark:text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 transition-colors"
            >
              <Settings className="w-5 h-5" />
            </button>
          </div>
        </header>

        {/* Chat Stream */}
        <div
          className="flex-1 overflow-y-auto px-4 md:px-0 py-6 bg-[#f5f7fa] dark:bg-[#0d1117] transition-colors duration-200 custom-scrollbar"
          ref={chatContainerRef}
          onScroll={handleChatScroll}
        >
          <div className="max-w-3xl mx-auto space-y-6">

            {/* Welcome State */}
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center py-24 text-center">
                <div className="text-6xl mb-6">🔨</div>
                <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-8">
                  Let&apos;s start building!
                </h2>
                <div className="grid grid-cols-2 gap-3 max-w-lg">
                  {[
                    { icon: '🔄', label: 'Increase test coverage' },
                    { icon: '🔀', label: 'Auto-merge PRs' },
                    { icon: '📄', label: 'Fix README' },
                    { icon: '📦', label: 'Clean dependencies' },
                  ].map((suggestion) => (
                    <button
                      key={suggestion.label}
                      onClick={() => setChatInput(suggestion.label)}
                      className="flex items-center gap-3 px-5 py-3 bg-white/5 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/60 rounded-xl text-sm font-medium text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-500 hover:bg-white/10 dark:hover:bg-white/[0.07] transition-all"
                    >
                      <span className="text-base">{suggestion.icon}</span>
                      {suggestion.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Messages — OpenHands style */}
            {messages.map((msg, i) => (
              <div key={msg.id} className="animate-msg-in">
                <MessageBubble msg={msg} isLatest={i === messages.length - 1} />
              </div>
            ))}

            {/* Agent working indicator */}
            {(status === 'preparing' || status === 'connecting') && (
              <div className="flex items-center gap-3 px-4">
                <div className="w-8 h-8 rounded-xl bg-blue-600 flex items-center justify-center shadow-sm shrink-0">
                  <Bot className="w-4 h-4 text-white" />
                </div>
                <div className="flex items-center gap-2 px-4 py-2.5 bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 rounded-2xl shadow-sm">
                  <div className="flex gap-1">
                    <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'0ms'}} />
                    <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'150ms'}} />
                    <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'300ms'}} />
                  </div>
                  <span className="text-sm text-slate-500 dark:text-slate-400">
                    {status === 'preparing' ? 'Preparing workspace…' : 'Connecting…'}
                  </span>
                </div>
              </div>
            )}

            <div ref={chatEndRef} className="h-4" />
          </div>
        </div>

        {/* Input Area */}
        <div className="shrink-0 px-6 pb-4 pt-2 bg-[#f5f7fa] dark:bg-[#0d1117] transition-colors duration-200">
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
              className="relative bg-white dark:bg-[#151b23] border border-slate-300 dark:border-slate-700/60 rounded-2xl shadow-sm focus-within:ring-4 focus-within:ring-blue-500/10 focus-within:border-blue-500 dark:focus-within:border-blue-500 transition-all overflow-hidden"
            >
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={isPreparing ? "Preparing workspace…" : "What do you want to build?"}
                disabled={isPreparing}
                className={cn(
                  "w-full px-5 py-4 min-h-[56px] max-h-[200px] outline-none text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 resize-none font-medium leading-relaxed bg-transparent",
                  isPreparing && "opacity-50 cursor-not-allowed"
                )}
                rows={1}
              />
              <div className="flex items-center justify-between px-4 pb-3">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-400 dark:text-slate-500">
                  <span className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-slate-100 dark:hover:bg-white/[0.04] cursor-pointer transition-colors">
                    <Settings className="w-3.5 h-3.5" />
                    Tools
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-slate-400 dark:text-slate-500 font-medium flex items-center gap-1.5">
                    {isPreparing ? (
                      <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Preparing workspace…</>
                    ) : isReady ? (
                      <><Clock className="w-3.5 h-3.5" /> Ready for task</>
                    ) : (
                      <><Clock className="w-3.5 h-3.5" /> Waiting</>
                    )}
                  </span>
                  <button
                    type="submit"
                    disabled={!chatInput.trim() || isPreparing}
                    className={cn(
                      "p-2.5 rounded-xl transition-all",
                      chatInput.trim() && !isPreparing
                        ? "bg-blue-600 text-white shadow-md shadow-blue-600/20 hover:bg-blue-700 hover:scale-105"
                        : "bg-slate-100 dark:bg-white/[0.06] text-slate-300 dark:text-slate-600 cursor-not-allowed"
                    )}
                  >
                    <Send className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </form>

            {/* Bottom repo/branch bar */}
            <div className="flex items-center gap-3 mt-3">
              <span className="flex items-center gap-1.5 px-3 py-1.5 bg-white/5 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 rounded-lg text-xs font-semibold text-slate-400 dark:text-slate-500">
                {conversation?.repo_provider === 'github' ? <Github className="w-3 h-3" /> : <GitBranch className="w-3 h-3" />}
                {conversation?.repo_name || 'No Repo Connected'}
              </span>
              <span className="flex items-center gap-1.5 px-3 py-1.5 bg-white/5 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 rounded-lg text-xs font-semibold text-slate-400 dark:text-slate-500">
                <GitBranch className="w-3 h-3" />
                {conversation?.branch || 'No Branch'}
              </span>
            </div>
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
