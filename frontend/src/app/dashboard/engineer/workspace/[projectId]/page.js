'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Full Page Conversation Workspace
//  Layout: [Chat] | [Terminal / FileViewer]
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect, useCallback, use } from 'react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { WorkspaceErrorBoundary } from '@/components/WorkspaceErrorBoundary';
import {
  ArrowLeft, Send, Terminal, Settings, X,
  Bot, User, Cpu, ArrowDown, Loader2,
  FileText, Copy, Check, Clock, GitBranch, Github,
  ChevronDown, ChevronRight, Zap, Code2, Play,
  FileCheck2, AlertCircle, TriangleAlert, Sparkles, Activity,
  ExternalLink, GitPullRequest
} from 'lucide-react';
import { useAgentSession } from '@/hooks/useAgentSession';
import {
  getConversation,
  getMessages,
  addMessage as saveMessage,
  updateConversation,
  getChatHistory,
  saveChatMessage,
} from '@/lib/conversations';
import TaskProgress from '@/components/TaskProgress';
import StopTaskButton from '@/components/StopTaskButton';

// ── Status Component ───────────────────────────────────────
function ConnectionStatus({ status, error }) {
  const config = {
    idle:       { color: 'text-slate-400 dark:text-slate-500', label: 'Idle' },
    connecting: { color: 'text-blue-600 dark:text-blue-400',  label: 'Connecting...' },
    preparing:  { color: 'text-amber-600 dark:text-amber-400', label: 'Preparing workspace...' },
    running:    { color: 'text-blue-600 dark:text-blue-400',  label: 'Agent working…' },
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
        status === 'connecting' || status === 'running' ? "bg-blue-500 animate-pulse" :
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
// CLEAN CHAT MODE: Only user, agent, system, and push_result messages.
// ThinkingBlock and ToolCard removed — events go to logs only.

// The main message bubble dispatcher
function MessageBubble({ msg, isLatest }) {
  // CLEAN CHAT MODE: skip thinking blocks and tool calls entirely
  if (msg.role === 'thinking' || msg.role === 'tool' || (msg.role === 'assistant' && msg.toolName)) {
    return null;
  }

  // User message
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end px-4 py-2">
        <div className="flex items-end gap-3 max-w-[85%]">
          <div className="flex flex-col items-end gap-1">
            <span className="text-[10px] uppercase font-bold tracking-widest text-slate-400 px-1">You</span>
            <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl rounded-br-sm px-4 py-3 shadow-sm">
              <p className="text-sm leading-relaxed whitespace-pre-wrap text-slate-800 dark:text-slate-200">{msg.content}</p>
            </div>
          </div>
          <div className="w-8 h-8 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center shrink-0 border border-slate-200 dark:border-slate-700 shadow-sm">
            <User className="w-4 h-4 text-slate-500" />
          </div>
        </div>
      </div>
    );
  }

  // Push result — minimal clean design
  if (msg.role === 'push_result') {
    return (
      <div className="flex items-start gap-3 px-4 py-2 animate-in fade-in duration-300">
        <div className="mt-0.5 text-emerald-500 shrink-0">
          <Check className="w-5 h-5" />
        </div>
        <div className="flex-1 space-y-1.5">
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">
            ✅ Changes pushed successfully
          </p>
          <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <GitBranch className="w-3.5 h-3.5" />
            <span className="font-mono">{msg.branch}</span>
            {msg.newBranch && (
              <span className="text-[10px] font-semibold text-blue-500">(new branch)</span>
            )}
          </div>
          {msg.content && (
            <pre className="font-mono text-[11px] text-slate-400 dark:text-slate-500 leading-relaxed whitespace-pre-wrap">
              {msg.content}
            </pre>
          )}
          {msg.prUrl && (
            <a
              href={msg.prUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline mt-1"
            >
              <GitPullRequest className="w-3.5 h-3.5" />
              Create Pull Request
              <ExternalLink className="w-3 h-3 opacity-60" />
            </a>
          )}
        </div>
      </div>
    );
  }

  // System status — redesigned for attractiveness
  if (msg.role === 'system') {
    const isWarning = msg.content.toLowerCase().includes('limit') || msg.content.toLowerCase().includes('warning');
    const isError = msg.content.toLowerCase().includes('error') || msg.content.toLowerCase().includes('failed');

    if (isWarning || isError) {
      return (
        <div className="px-4 py-2 flex justify-center animate-in fade-in zoom-in-95 duration-300">
          <div className={cn(
            "flex items-center gap-3 px-4 py-2.5 rounded-xl border shadow-sm max-w-[85%]",
            isError 
              ? "bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-900/50 text-red-700 dark:text-red-400" 
              : "bg-amber-50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900/50 text-amber-700 dark:text-amber-400"
          )}>
            {isError ? <AlertCircle className="w-4 h-4 shrink-0" /> : <TriangleAlert className="w-4 h-4 shrink-0" />}
            <span className="text-xs font-semibold leading-relaxed">
              {msg.content}
            </span>
          </div>
        </div>
      );
    }

    return (
      <div className="flex justify-center px-4 py-2 animate-in fade-in duration-500">
        <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-slate-100/80 dark:bg-slate-800/40 border border-slate-200/50 dark:border-slate-700/30 backdrop-blur-sm">
          <div className="w-1.5 h-1.5 rounded-full bg-slate-400 dark:bg-slate-600 animate-pulse" />
          <span className="text-[10px] uppercase font-bold tracking-widest text-slate-500 dark:text-slate-400">
            {msg.content}
          </span>
        </div>
      </div>
    );
  }

  // Agent message — clean, conversational, natural design
  const renderContent = (text) => {
    // Split into sections: steps, summary text, changed files
    const lines = text.split('\n');
    const elements = [];
    let inChangedFiles = false;
    let inCodeBlock = false;
    let codeLines = [];

    // Inline formatting helper — bold (**), inline code (`)
    function formatInline(str) {
      const parts = str.split(/(\*\*.*?\*\*|`[^`]+`)/g);
      return parts.map((part, j) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={j} className="font-semibold text-slate-800 dark:text-slate-100">{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return <code key={j} className="bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-[11px] font-mono text-blue-600 dark:text-blue-400">{part.slice(1, -1)}</code>;
        }
        return part;
      });
    }

    lines.forEach((line, i) => {
      // Code block handling
      if (line.trim().startsWith('```')) {
        if (inCodeBlock) {
          inCodeBlock = false;
          const code = codeLines.join('\n');
          codeLines = [];
          elements.push(
            <pre key={i} className="font-mono text-xs bg-slate-50 dark:bg-slate-800/60 rounded-lg px-3 py-2 my-2 text-slate-600 dark:text-slate-400 overflow-x-auto border border-slate-100 dark:border-slate-700/30">
              {code}
            </pre>
          );
          return;
        }
        inCodeBlock = true;
        return;
      }
      if (inCodeBlock) { codeLines.push(line); return; }

      // "Changed files:" header
      if (line.toLowerCase().includes('changed files:')) {
        inChangedFiles = true;
        elements.push(
          <div key={i} className="mt-3 mb-1.5 flex items-center gap-2">
            <FileText className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs uppercase font-bold tracking-wider text-slate-400 dark:text-slate-500">Changed files</span>
          </div>
        );
        return;
      }

      // File change lines (+ new, ~ modified, - deleted)
      if (inChangedFiles && line.trim()) {
        const trimmed = line.trim();
        let color = 'text-slate-500';
        let icon = '•';
        if (trimmed.startsWith('+') || trimmed.includes('(new)')) {
          color = 'text-emerald-500';
          icon = '+';
        } else if (trimmed.startsWith('~') || trimmed.includes('(modified)')) {
          color = 'text-amber-500';
          icon = '~';
        } else if (trimmed.startsWith('-') || trimmed.includes('(deleted)')) {
          color = 'text-red-400';
          icon = '−';
        }
        const fileName = trimmed.replace(/^[+~-]\s*/, '').replace(/\(new\)|\(modified\)|\(deleted\)/, '').trim();
        elements.push(
          <div key={i} className="flex items-center gap-2 py-0.5 pl-1">
            <span className={cn("font-mono text-xs font-bold w-3 text-center", color)}>{icon}</span>
            <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{fileName}</span>
          </div>
        );
        return;
      }

      // Step lines with ✅ — already completed, show subtly
      if (line.trim().startsWith('✅')) {
        elements.push(
          <div key={i} className="flex items-center gap-2 py-0.5 text-slate-500 dark:text-slate-400 text-sm">
            {line}
          </div>
        );
        return;
      }

      // Numbered list items
      const numberedMatch = line.match(/^\s*(\d+)\.\s+(.*)/);
      if (numberedMatch) {
        elements.push(
          <div key={i} className="flex items-start gap-2 py-0.5">
            <span className="shrink-0 text-slate-400 font-mono text-xs mt-0.5 w-4 text-right">{numberedMatch[1]}.</span>
            <span className="text-slate-700 dark:text-slate-300">{formatInline(numberedMatch[2])}</span>
          </div>
        );
        return;
      }

      // Bullets
      if (line.trim().startsWith('- ') || line.trim().startsWith('• ') || line.trim().startsWith('* ')) {
        const bulletContent = line.trim().replace(/^[-•*]\s*/, '');
        elements.push(
          <div key={i} className="flex items-start gap-2 py-0.5">
            <span className="shrink-0 mt-1 w-1.5 h-1.5 rounded-full bg-slate-400 dark:bg-slate-500" />
            <span className="text-slate-700 dark:text-slate-300">{formatInline(bulletContent)}</span>
          </div>
        );
        return;
      }

      // Empty line
      if (!line.trim()) {
        inChangedFiles = false; // end of changed files section
        elements.push(<div key={i} className="h-2" />);
        return;
      }

      // Normal text — conversation style
      elements.push(
        <div key={i} className="py-0.5 text-slate-700 dark:text-slate-300">{formatInline(line)}</div>
      );
    });

    return elements;
  };

  return (
    <div className="flex items-start gap-3 px-4 py-2 animate-in fade-in duration-300">
      <div className="mt-0.5 text-blue-500 shrink-0">
        <Bot className="w-5 h-5" />
      </div>
      <div className="flex-1 text-sm leading-relaxed">
        {renderContent(msg.content)}
      </div>
    </div>
  );
}

// ── Main Page Component ────────────────────────────────────
export default function ConversationPage({ params }) {
  return (
    <WorkspaceErrorBoundary>
      <ConversationPageInner params={params} />
    </WorkspaceErrorBoundary>
  );
}

function ConversationPageInner({ params }) {
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
          // Try unified chat_messages first (same table backend writes to).
          // Fall back to old messages table for legacy conversations.
          let msgs = await getChatHistory(conversationId);
          console.log('[Chat] chat_messages:', msgs.length, 'items');
          if (!msgs.length) {
            msgs = await getMessages(conversationId);
            console.log('[Chat] fallback messages:', msgs.length, 'items');
          }
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
  const [gitTokenLoaded, setGitTokenLoaded] = useState(false);
  useEffect(() => {
    if (!conversation) return;
    // Scratch sessions (no repo_provider) don't need a git token
    if (!conversation.repo_provider) {
      setGitTokenLoaded(true);
      return;
    }
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
      } finally {
        setGitTokenLoaded(true);
      }
    })();
  }, [conversation]);

  // ── Agent session hook ──────────────────────────────────
  // IMPORTANT: Don't pass the token until ALL data has loaded:
  //   1. Auth token (from /api/agent/token)
  //   2. Conversation metadata (from Supabase — has repoUrl, branch)
  //   3. Git token (from user_metadata — needed for cloning)
  // This prevents race conditions where the WebSocket connects before
  // the backend has the repo URL and git credentials.
  const effectiveToken = (convLoading || !gitTokenLoaded) ? '' : token;

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
    stopSession,
    pushToBranch,
    setInitialMessages,
    steps,
    phases,
  } = useAgentSession({
    projectId: conversationId,
    token: effectiveToken,
    repoUrl: conversation?.repo_url || '',
    gitToken,
    branch: conversation?.branch || 'main',
  });

  // ── Layout state ────────────────────────────────────────
  const [chatInput, setChatInput] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  // ── Hydrate chat history from Supabase into the hook ────
  useEffect(() => {
    if (savedMessages.length > 0 && setInitialMessages) {
      setInitialMessages(savedMessages);
    }
  }, [savedMessages, setInitialMessages]);

  // ── Frontend save (guaranteed backup) ────────────────────
  // Backend also saves to chat_messages, but those saves can fail
  // due to RLS/schema issues. Frontend saves to the simpler
  // 'messages' table as a reliable fallback.
  useEffect(() => {
    if (!conversation?.id || !messages || messages.length === 0) return;

    const newMessages = messages.slice(prevMessagesLenRef.current);
    prevMessagesLenRef.current = messages.length;

    for (const msg of newMessages) {
      if (msg.fromHistory) continue;

      if (msg.role === 'user' && msg.content) {
        saveMessage(conversation.id, { role: 'user', content: msg.content });
        saveChatMessage(conversationId, { role: 'user', content: msg.content });
      }
      if ((msg.role === 'agent' || msg.role === 'assistant') && msg.content) {
        saveMessage(conversation.id, { role: 'assistant', content: msg.content });
        saveChatMessage(conversationId, { role: 'assistant', content: msg.content });
      }
    }
  }, [messages, conversation?.id, conversationId]);

  // ── Update conversation status in DB ────────────────────
  const prevStatusRef = useRef(null);
  useEffect(() => {
    if (!conversationId || prevStatusRef.current === status) return;
    prevStatusRef.current = status;

    if (status === 'running') {
      updateConversation(conversationId, { status: 'active' });
    } else if (status === 'ready' && messages.length > 0) {
      // Only mark completed if there are messages (actual work was done)
      updateConversation(conversationId, { status: 'completed' });
    } else if (status === 'error') {
      updateConversation(conversationId, { status: 'error' });
    }
  }, [status, conversationId, messages.length]);

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

    // Save user message to both tables
    if (conversation?.id) {
      // Save to unified chat_messages
      saveChatMessage(conversationId, { role: 'user', content: text });
      // Save to legacy messages table
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
              <div className="flex items-center gap-2">
                <h1 className="text-sm font-bold text-slate-900 dark:text-slate-100 truncate max-w-[200px]">
                  {conversation?.title || 'New Conversation'}
                </h1>
                <span className="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 text-[9px] font-bold uppercase tracking-wider border border-blue-100 dark:border-blue-500/20">
                  {status || 'Idle'}
                </span>
              </div>
              <div className="flex items-center gap-3 mt-0.5 text-[10px] text-slate-400 dark:text-slate-500 font-medium">
                <span className="flex items-center gap-1">
                  <Github className="w-3 h-3" />
                  {conversation?.repo_name || 'local'}
                </span>
                <span className="flex items-center gap-1">
                  <GitBranch className="w-3 h-3 text-emerald-500" />
                  {conversation?.branch || 'main'}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <StopTaskButton 
              status={status} 
              websocket={{
                send: (data) => {
                  try {
                    // This relies on hook's stopSession internally generating a send
                    stopSession();
                  } catch(e) {}
                }
              }} 
              currentTaskId={sessionId} 
            />

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

            {/* Loading skeleton — shown while conversation loads from DB */}
            {convLoading && messages.length === 0 && (
              <div className="flex flex-col gap-6 py-12 animate-pulse">
                {/* Fake user message */}
                <div className="flex justify-end px-4">
                  <div className="w-48 h-10 rounded-2xl bg-slate-200 dark:bg-slate-800" />
                </div>
                {/* Fake agent message */}
                <div className="flex items-start gap-4 px-4">
                  <div className="w-8 h-8 rounded-xl bg-slate-200 dark:bg-slate-800 shrink-0" />
                  <div className="flex-1 space-y-2">
                    <div className="h-4 bg-slate-200 dark:bg-slate-800 rounded w-3/4" />
                    <div className="h-4 bg-slate-200 dark:bg-slate-800 rounded w-1/2" />
                    <div className="h-4 bg-slate-200 dark:bg-slate-800 rounded w-2/3" />
                  </div>
                </div>
                {/* Fake user message */}
                <div className="flex justify-end px-4">
                  <div className="w-64 h-10 rounded-2xl bg-slate-200 dark:bg-slate-800" />
                </div>
                {/* Fake agent message */}
                <div className="flex items-start gap-4 px-4">
                  <div className="w-8 h-8 rounded-xl bg-slate-200 dark:bg-slate-800 shrink-0" />
                  <div className="flex-1 space-y-2">
                    <div className="h-4 bg-slate-200 dark:bg-slate-800 rounded w-full" />
                    <div className="h-4 bg-slate-200 dark:bg-slate-800 rounded w-4/5" />
                  </div>
                </div>
              </div>
            )}

            {/* Welcome State — only after loading completes and no messages exist */}
            {!convLoading && messages.length === 0 && (
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

            {/* Structured progress steps from TaskProgress */}
            <TaskProgress phases={phases} status={status} />

            {/* Thinking indicator — shown while agent processes before first phase */}
            {phases.length === 0 && status === 'running' && (
              <div className="flex items-start gap-3 px-4 py-3 animate-in fade-in duration-300">
                <div className="mt-0.5 text-blue-500 shrink-0">
                  <Bot className="w-5 h-5" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl px-5 py-4 shadow-sm">
                    <div className="flex gap-1">
                      <div className="w-2 h-2 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                      <div className="w-2 h-2 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                      <div className="w-2 h-2 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                    <span className="text-sm text-slate-500 dark:text-slate-400 font-medium">
                      Thinking and analyzing your request...
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Preparing/connecting indicator (no steps yet) */}
            {steps.length === 0 && phases.length === 0 && (status === 'preparing' || status === 'connecting') && (
              <div className="flex items-center gap-3 px-4 py-2 animate-in fade-in duration-300">
                <div className="mt-0.5 text-blue-500 shrink-0">
                  <Bot className="w-5 h-5" />
                </div>
                <div className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 text-amber-500 animate-spin" />
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
              className={cn(
                "relative bg-white dark:bg-[#151b23] border rounded-2xl shadow-sm transition-all overflow-hidden",
                isPreparing
                  ? "border-amber-300 dark:border-amber-600/40"
                  : status === 'running'
                    ? "border-blue-300 dark:border-blue-600/40"
                    : "border-slate-300 dark:border-slate-700/60 focus-within:ring-4 focus-within:ring-blue-500/10 focus-within:border-blue-500 dark:focus-within:border-blue-500"
              )}
            >
              {/* Preparing overlay */}
              {isPreparing && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/80 dark:bg-[#151b23]/85 backdrop-blur-sm rounded-2xl">
                  <div className="flex items-center gap-3">
                    <div className="relative">
                      <div className="w-8 h-8 rounded-full border-[3px] border-amber-200 dark:border-amber-800/50 border-t-amber-500 dark:border-t-amber-400 animate-spin" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                        Setting up workspace
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Please wait — cloning repo and preparing environment…
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Running task overlay */}
              {status === 'running' && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/80 dark:bg-[#151b23]/85 backdrop-blur-sm rounded-2xl">
                  <div className="flex items-center gap-3">
                    <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                        Processing task…
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        The agent is working — please wait
                      </p>
                    </div>
                  </div>
                </div>
              )}

              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  isPreparing ? "Waiting for workspace…" :
                  status === 'running' ? "Agent is working…" :
                  "What do you want to build?"
                }
                disabled={isPreparing || status === 'running'}
                className={cn(
                  "w-full px-5 py-4 min-h-[56px] max-h-[200px] outline-none text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 resize-none font-medium leading-relaxed bg-transparent",
                  (isPreparing || status === 'running') && "opacity-40 cursor-not-allowed pointer-events-none"
                )}
                rows={1}
              />
              <div className="flex items-center justify-between px-4 pb-3">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-400 dark:text-slate-500">
                  <span className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-slate-100 dark:hover:bg-white/[0.04] cursor-pointer transition-colors">
                    <Settings className="w-3.5 h-3.5" />
                    Tools
                  </span>
                  {status === 'ready' && (
                    <button
                      type="button"
                      onClick={() => pushToBranch()}
                      className="flex items-center gap-1.5 px-2 py-1 rounded text-emerald-600 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 transition-colors"
                    >
                      <Zap className="w-3.5 h-3.5" />
                      Push to {conversation?.branch || 'main'}
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-slate-400 dark:text-slate-500 font-medium flex items-center gap-1.5">
                    {isPreparing ? (
                      <><Loader2 className="w-3.5 h-3.5 animate-spin text-amber-500" /> Preparing workspace…</>
                    ) : status === 'running' ? (
                      <><Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" /> Agent working…</>
                    ) : isReady ? (
                      <><Clock className="w-3.5 h-3.5" /> Ready for task</>
                    ) : (
                      <><Clock className="w-3.5 h-3.5" /> Waiting</>
                    )}
                  </span>
                  <button
                    type="submit"
                    disabled={!chatInput.trim() || isPreparing || status === 'running'}
                    className={cn(
                      "p-2.5 rounded-xl transition-all",
                      chatInput.trim() && !isPreparing && status !== 'running'
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
