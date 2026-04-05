'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Full Page Conversation Workspace
//  3-Panel IDE Layout: [FileExplorer] | [Chat] | [Build/Preview/Code/Terminal]
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
  ExternalLink, GitPullRequest, FileImage, Eye,
  Video, Globe, Camera, Link2, Layers, Paperclip, Plus,
  FolderOpen, PanelLeftClose, PanelLeftOpen, Hammer, Monitor,
  Mic, Pencil, MessageCircle, Lightbulb, History, Diamond, MoreHorizontal,
  Wand2, Palette, RefreshCw, Maximize, Smartphone, Download,
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
import { getSupabaseBrowserClient } from '@/lib/supabase/client';
import TaskProgress from '@/components/TaskProgress';
import StopTaskButton from '@/components/StopTaskButton';
import ExportCodeModal from '@/components/ExportCodeModal';
import GenerationProgress from '@/components/GenerationProgress';
import FileExplorer from '@/components/agent/FileExplorer';
import BuildProgressPanel from '@/components/agent/BuildProgressPanel';

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
    const cleanContent = msg.content?.replace(/\[LUCID_PROJECT\][\s\S]*?(?=\n\n(?:I need|Create)|\n)/i, '').replace(/\[LUCID_PROJECT\][\s\S]*?(?=\n\n|\n)/i, '').trim() || msg.content;
    
    return (
      <div className="flex flex-col pl-4 pr-3 py-3 w-full">
        <div className="flex justify-end mb-1">
          <div className="w-6 h-6 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center shrink-0 overflow-hidden">
            <User className="w-4 h-4 text-slate-500" />
            {/* If we have a user image, we can use an img tag here */}
          </div>
        </div>
        <div className="bg-[#f0f4f9] dark:bg-slate-800/80 rounded-[20px] p-4 w-full relative">
          <p className="text-[13px] font-medium leading-relaxed whitespace-pre-wrap text-slate-800 dark:text-slate-200">
            {cleanContent}
          </p>
          <div className="mt-4 flex justify-between items-center text-[11px] text-slate-400 dark:text-slate-500 font-medium">
            <span>Just now</span>
            <button className="hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
              <Copy className="w-3.5 h-3.5" />
            </button>
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
    <div className="flex flex-col px-4 py-4 w-full animate-in fade-in duration-300 border-b border-slate-100 dark:border-[#1c2128]/50 last:border-0">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/20">
            <Sparkles className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="font-semibold text-slate-800 dark:text-slate-200 text-[13px]">Base44</span>
        </div>
        <button className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 p-1 rounded hover:bg-slate-100 dark:hover:bg-white/[0.04] transition-colors">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>
        </button>
      </div>
      <div className="text-[13px] leading-relaxed text-slate-700 dark:text-slate-300 pl-[34px]">
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

  // ── Detect wizard mode IMMEDIATELY (sync, before any async work) ──
  // If sessionStorage has a wizard_prompt, this is a brand-new project from
  // the wizard. We skip blocking on conversation/git-token loading and
  // connect the WebSocket immediately.
  const [isWizardMode] = useState(() => {
    try {
      return !!sessionStorage.getItem(`wizard_prompt_${decodeURIComponent(projectId || 'unknown')}`);
    } catch { return false; }
  });

  // Build the wizard prompt eagerly so we can pass it in the WS handshake
  // Also store the user-visible description (without [LUCID_PROJECT] header)
  const [wizardDesc] = useState(() => {
    try {
      const descKey = `wizard_desc_${decodeURIComponent(projectId || 'unknown')}`;
      return sessionStorage.getItem(descKey) || '';
    } catch { return ''; }
  });

  const [wizardTask] = useState(() => {
    try {
      const key = `wizard_prompt_${decodeURIComponent(projectId || 'unknown')}`;
      const prompt = sessionStorage.getItem(key);
      if (!prompt) return '';

      const cid = decodeURIComponent(projectId || 'unknown');
      const metaKey = `wizard_meta_${cid}`;
      const metaStr = sessionStorage.getItem(metaKey);
      const descKey = `wizard_desc_${cid}`;

      let finalPrompt = prompt;
      if (metaStr) {
        try {
          const meta = JSON.parse(metaStr);
          const origDesc = sessionStorage.getItem(descKey) || '';

          const parts = [
            `description=${origDesc || 'project'}`,
            `stack=${meta.stack || 'nextjs'}`,
            `backend=${meta.backend || 'none'}`,
          ];
          if (meta.projectType) parts.push(`project_type=${meta.projectType}`);
          if (meta.deployment)  parts.push(`deployment=${meta.deployment}`);
          if (meta.figmaUrl)    parts.push(`figma_url=${meta.figmaUrl}`);

          const header = `[LUCID_PROJECT] ${parts.join(' | ')}`;
          finalPrompt = `${header}\n\n${prompt}`;
        } catch (_) {}
      }

      // Clean up sessionStorage now that we've read it
      sessionStorage.removeItem(key);
      sessionStorage.removeItem(metaKey);
      sessionStorage.removeItem(descKey);

      return finalPrompt;
    } catch { return ''; }
  });

  // ── Conversation data from Supabase ─────────────────────
  const [conversation, setConversation] = useState(null);
  const [savedMessages, setSavedMessages] = useState([]);
  const [convLoading, setConvLoading] = useState(true);
  const prevMessagesLenRef = useRef(0);

  // Repo info from chat_sessions (platform vs user repos)
  const [repoInfo, setRepoInfo] = useState({
    platformRepoUrl: null,
    userRepoUrl: null,
    userRepoProvider: null,
    vercelUrl: null,
  });

  // Load conversation and messages on mount (with timeout)
  // For wizard mode, use a much shorter timeout since we don't need this data to connect.
  useEffect(() => {
    let cancelled = false;
    const timeoutMs = isWizardMode ? 1500 : 5000;
    const timeout = setTimeout(() => {
      if (!cancelled) setConvLoading(false);
    }, timeoutMs);

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

        // Load repo info from chat_sessions — always try, even without a conversation
        try {
          const sb = getSupabaseBrowserClient();
          const { data: sessions } = await sb
            .from('chat_sessions')
            .select('platform_repo_url, user_repo_url, user_repo_provider, vercel_url')
            .eq('project_id', conversationId)
            .order('created_at', { ascending: false })
            .limit(1);
          if (!cancelled && sessions?.[0]) {
            setRepoInfo({
              platformRepoUrl: sessions[0].platform_repo_url || null,
              userRepoUrl: sessions[0].user_repo_url || null,
              userRepoProvider: sessions[0].user_repo_provider || null,
              vercelUrl: sessions[0].vercel_url || null,
            });
          }
        } catch (e) {
          console.warn('[Workspace] Could not load repo info:', e);
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
  }, [conversationId, isWizardMode]);

  // ── Auth token for WebSocket ────────────────────────────
  const [token, setToken] = useState('');
  const [gitToken, setGitToken] = useState('');

  useEffect(() => {
    fetch('/api/agent/token')
      .then((r) => r.json())
      .then((d) => { if (d.token) setToken(d.token); })
      .catch(() => {});
  }, []);

  // Load git token from integrations — runs in PARALLEL, not blocked by conversation
  const [gitTokenLoaded, setGitTokenLoaded] = useState(false);
  useEffect(() => {
    // For wizard mode, mark as loaded immediately — backend uses its own token
    if (isWizardMode) {
      setGitTokenLoaded(true);
      // Still try to load in background for follow-up tasks
      (async () => {
        try {
          const { getIntegrations } = await import('@/lib/integrations');
          const intg = await getIntegrations();
          if (intg.github?.token) setGitToken(intg.github.token);
          else if (intg.gitlab?.token) setGitToken(intg.gitlab.token);
        } catch {}
      })();
      return;
    }

    // For non-wizard: wait for conversation, but also handle null (new conv)
    if (!conversation && !convLoading) {
      setGitTokenLoaded(true);
      return;
    }
    if (!conversation) return;

    (async () => {
      try {
        const { getIntegrations } = await import('@/lib/integrations');
        const intg = await getIntegrations();

        if (conversation.repo_provider === 'github' && intg.github?.token) {
          setGitToken(intg.github.token);
        } else if (conversation.repo_provider === 'gitlab' && intg.gitlab?.token) {
          setGitToken(intg.gitlab.token);
        } else if (!conversation.repo_provider) {
          if (intg.github?.token) setGitToken(intg.github.token);
          else if (intg.gitlab?.token) setGitToken(intg.gitlab.token);
        }
      } catch (err) {
        console.error('Failed to load git token:', err);
      } finally {
        setGitTokenLoaded(true);
      }
    })();
  }, [conversation, convLoading, isWizardMode]);

  // ── Agent session hook ──────────────────────────────────
  // For WIZARD projects: connect immediately with just the auth token.
  //   No repo URL or git token needed — backend handles everything.
  // For EXISTING projects: wait for conversation + git token before connecting.
  const effectiveToken = isWizardMode
    ? token  // Fast path: connect as soon as auth token is ready
    : (convLoading || !gitTokenLoaded) ? '' : token;

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
    completionSummary,
    deployUrl,
    previewUrl,
  } = useAgentSession({
    projectId: conversationId,
    token: effectiveToken,
    task: wizardTask,  // Pass wizard prompt in handshake for instant start
    repoUrl: conversation?.repo_url || '',
    gitToken,
    branch: conversation?.branch || 'main',
  });

  // ── Sync deployUrl / previewUrl from WebSocket to repoInfo (live update) ──
  useEffect(() => {
    if (deployUrl) {
      setRepoInfo(prev => ({ ...prev, vercelUrl: deployUrl }));
    }
  }, [deployUrl]);

  // Live Preview URL from dev server tunnel (takes priority during dev)
  useEffect(() => {
    if (previewUrl && !repoInfo.vercelUrl) {
      setRepoInfo(prev => ({ ...prev, vercelUrl: previewUrl }));
    }
  }, [previewUrl]);

  // ── Layout state ────────────────────────────────────────
  const [chatInput, setChatInput] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [showToolsMenu, setShowToolsMenu] = useState(false);
  const [attachedImages, setAttachedImages] = useState([]);
  const [previewImage, setPreviewImage] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);
  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  const [showFigmaInput, setShowFigmaInput] = useState(false);
  const [figmaUrl, setFigmaUrl] = useState('');
  const [showExportModal, setShowExportModal] = useState(false);
  const [chatMode, setChatMode] = useState('edit'); // 'edit' or 'discuss'
  const fileInputRef = useRef(null);
  const videoInputRef = useRef(null);
  const toolsMenuRef = useRef(null);

  // ── Hydrate chat history from Supabase into the hook ────
  useEffect(() => {
    if (savedMessages.length > 0 && setInitialMessages) {
      setInitialMessages(savedMessages);
    }
  }, [savedMessages, setInitialMessages]);

  // ── Auto-start wizard task when workspace becomes ready ──
  // NOTE: For wizard mode, the task is now sent in the WebSocket handshake
  // (via the `task` prop to useAgentSession). This effect is kept as a
  // fallback for edge cases where the handshake task wasn't received.
  const wizardAutoStarted = useRef(isWizardMode); // Already started if wizard mode
  useEffect(() => {
    if (status !== 'ready' || wizardAutoStarted.current) return;

    // Double-check: if wizard task was passed in handshake, skip
    if (wizardTask) {
      wizardAutoStarted.current = true;
      return;
    }

    // Fallback: check sessionStorage (shouldn't happen in normal flow)
    try {
      const key = `wizard_prompt_${conversationId}`;
      const prompt = sessionStorage.getItem(key);
      if (prompt) {
        wizardAutoStarted.current = true;
        sessionStorage.removeItem(key);
        sessionStorage.removeItem(`wizard_meta_${conversationId}`);
        sessionStorage.removeItem(`wizard_desc_${conversationId}`);
        setTimeout(() => sendMessage(prompt), 300);
      }
    } catch (_) {}
  }, [status, conversationId, sendMessage, wizardTask]);

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

  // ── 3-Panel IDE Layout State ─────────────────────────────
  // rightPanel: null | 'build' | 'preview' | 'code' | 'terminal'
  const [rightPanel, setRightPanel] = useState('preview');
  const [showFileExplorer, setShowFileExplorer] = useState(false);

  // Auto-show right panel with 'build' tab during generation
  const prevStatusForPanel = useRef(null);
  useEffect(() => {
    if (prevStatusForPanel.current === status) return;
    prevStatusForPanel.current = status;

    if (status === 'running' || status === 'preparing') {
      setRightPanel('build');
      setShowFileExplorer(true);
    }
  }, [status]);

  // Auto-switch to preview when build completes and vercelUrl is available
  useEffect(() => {
    if (repoInfo.vercelUrl && rightPanel === 'build') {
      setRightPanel('preview');
    }
  }, [repoInfo.vercelUrl, rightPanel]);

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
    if (!chatInput.trim() && attachedImages.length === 0) return;
    const text = chatInput.trim();
    sendMessage(text, attachedImages);
    setChatInput('');
    setAttachedImages([]);

    // Save user message to both tables
    if (conversation?.id && text) {
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

  // ── Image handling ──────────────────────────────────────
  const handleImageSelect = (e) => {
    const files = Array.from(e.target.files || []);
    files.forEach((file) => {
      if (!file.type.startsWith('image/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) =>
            img.file === file ? { ...img, data: ev.target.result } : img
          )
        );
      };
      reader.readAsDataURL(file);
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  const handleVideoSelect = (e) => {
    const files = Array.from(e.target.files || []);
    files.forEach((file) => {
      if (!file.type.startsWith('video/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size, type: 'video' }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) =>
            img.file === file ? { ...img, data: ev.target.result } : img
          )
        );
      };
      reader.readAsDataURL(file);
    });
    if (videoInputRef.current) videoInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  const handleFigmaSubmit = () => {
    if (!figmaUrl.trim()) return;
    setAttachedImages((prev) => {
      if (prev.length >= 5) return prev;
      return [...prev, { name: 'Figma Design', data: figmaUrl.trim(), size: 0, type: 'figma', url: figmaUrl.trim() }];
    });
    setFigmaUrl('');
    setShowFigmaInput(false);
    setShowToolsMenu(false);
  };

  const removeImage = (index) => {
    setAttachedImages((prev) => prev.filter((_, i) => i !== index));
  };

  const formatFileSize = (bytes) => {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  // Close tools menu on outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (toolsMenuRef.current && !toolsMenuRef.current.contains(e.target)) {
        setShowToolsMenu(false);
      }
    };
    if (showToolsMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showToolsMenu]);

  // ── Drag-and-drop handlers ──────────────────────────────
  const handleDragEnter = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (e.dataTransfer?.types?.includes('Files')) {
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragging(false);
    }
  }, []);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    dragCounterRef.current = 0;

    const files = Array.from(e.dataTransfer?.files || []);
    files.forEach((file) => {
      if (!file.type.startsWith('image/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) =>
            img.file === file ? { ...img, data: ev.target.result } : img
          )
        );
      };
      reader.readAsDataURL(file);
    });
  }, []);

  const handleChatScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    const isNearBottom = distFromBottom < 100;
    setShowScrollBtn(distFromBottom > 100);
    userScrolledUpRef.current = !isNearBottom;
  };

  // ── File select: load content from backend ──────────────
  const handleFileSelect = useCallback(async (path) => {
    setSelectedFile(path);
    setRightPanel('code');
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
      if (res.ok) {
        setFileContent(data.content || '');
      } else {
        const errorMsg = data.detail || data.error || 'Could not load file';
        setFileContent(`// Workspace Offline\n// Error: ${errorMsg}\n\n// The active development container for this project has spun down.\n// Your generated code is completely safe.\n\n// -> Click 'Export' in the top right to download the complete source code.\n// -> Or provide your VERCEL_TOKEN to the backend to enable Live Previews.`);
      }
    } catch {
      setFileContent('// Failed to load file content');
    } finally {
      setFileLoading(false);
    }
  }, [sessionId]);

  // ── Render ─────────────────────────────────────────────
  return (
    <div
      className="flex flex-col h-screen bg-[#f8f9fb] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >

      {/* Export Code Modal */}
      <ExportCodeModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
        projectSlug={
          conversation?.repo_name?.split('/').pop() ||
          (conversation?.title || 'project').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50) ||
          conversationId
        }
        projectId={conversationId}
      />


      {/* Full-page drag-and-drop overlay */}
      {isDragging && (
        <div className="fixed inset-0 z-[100] bg-blue-500/10 dark:bg-blue-500/15 backdrop-blur-[2px] border-2 border-dashed border-blue-400 dark:border-blue-500 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3 bg-white/90 dark:bg-slate-900/90 rounded-2xl px-8 py-6 shadow-xl border border-blue-200 dark:border-blue-800">
            <div className="w-14 h-14 rounded-2xl bg-blue-50 dark:bg-blue-500/15 border border-blue-200 dark:border-blue-700 flex items-center justify-center">
              <FileImage className="w-6 h-6 text-blue-500" />
            </div>
            <p className="text-sm font-bold text-slate-800 dark:text-slate-200">Drop images here</p>
            <p className="text-xs text-slate-400 dark:text-slate-500">PNG, JPG, GIF up to 5 files</p>
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════
          TOP HEADER BAR — Base44 style
      ════════════════════════════════════════════════ */}
      <header className="shrink-0 h-[60px] bg-white dark:bg-[#161b22] border-b border-slate-200 dark:border-[#2d333b] flex items-center px-4 z-20">
        
        {/* Left: Logo + Project Name + Sidebar Toggles */}
        <div className="flex items-center gap-4 flex-1">
          <div className="flex items-center gap-2">
            <button
              onClick={() => window.history.back()}
              className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 hover:bg-slate-100 dark:hover:bg-white/[0.06] text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
              title="Go Back"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/20 ml-1">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div className="flex flex-col justify-center">
              <div className="flex items-center gap-1.5">
                <span className="text-[13px] font-bold text-slate-800 dark:text-white leading-tight truncate max-w-[150px]">
                  {conversation?.title || 'Ember & Hearth'}
                </span>
              </div>
              <span className="text-[11px] font-medium text-slate-400 dark:text-slate-500 leading-tight truncate max-w-[150px]">
                Bakhriddin's Workspace Work...
              </span>
            </div>
          </div>

          <div className="flex items-center ml-2 border-l border-slate-200 dark:border-[#2d333b] pl-3 gap-1">
            <button className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
              <History className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Center: Tab Switcher (Preview | Dashboard | Code) */}
        <div className="flex justify-center flex-1">
          <div className="flex items-center bg-slate-100 dark:bg-[#21262d] rounded-[20px] p-[3px] border border-slate-200/60 dark:border-[#2d333b]">
            {[
              { key: 'preview', label: 'Preview', hasLive: !!repoInfo.vercelUrl },
              { key: 'code', label: 'Code', hasLive: false },
            ].map(tab => (
              <button
                key={tab.key}
                onClick={() => setRightPanel(rightPanel === tab.key ? 'preview' : tab.key)}
                className={cn(
                  "flex items-center gap-1.5 px-4 py-1.5 rounded-[18px] text-[13px] font-bold transition-all",
                  rightPanel === tab.key
                    ? "bg-white dark:bg-[#2d333b] text-slate-900 dark:text-white shadow-sm"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Right: Actions */}
        <div className="flex items-center justify-end flex-1 gap-1.5">
          {/* Avatar Stack */}
          <div className="flex items-center -space-x-1 mr-1">
            <div className="w-7 h-7 rounded-full bg-slate-200 border border-white dark:border-[#161b22] flex items-center justify-center overflow-hidden shrink-0">
              <User className="w-4 h-4 text-slate-500" />
            </div>
            <div className="w-7 h-7 rounded-full bg-slate-100 dark:bg-slate-800 border border-white dark:border-[#161b22] flex items-center justify-center z-10 text-slate-500 cursor-pointer shadow-sm shrink-0">
              <Plus className="w-3.5 h-3.5" />
            </div>
          </div>

          <button className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
            <MoreHorizontal className="w-4 h-4" />
          </button>
          
          <button className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors mr-1">
            <Github className="w-4 h-4" />
          </button>

          <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-[18px] text-[12px] font-bold text-orange-500 bg-orange-50 dark:bg-orange-500/10 border border-orange-200 dark:border-orange-500/20 hover:bg-orange-100 dark:hover:bg-orange-500/20 transition-all mr-1">
            <Diamond className="w-3.5 h-3.5 fill-current" />
            30% off
          </button>

          <button
            onClick={() => setShowExportModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-[18px] text-[13px] font-bold text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-all border border-transparent mr-0.5"
            title="Export Code"
          >
            Export
          </button>

          <button
            onClick={() => {
              if (repoInfo.vercelUrl) window.open(repoInfo.vercelUrl, '_blank');
            }}
            className={cn(
              "flex items-center px-4 py-1.5 rounded-[18px] text-[13px] font-bold shadow-sm transition-all",
              repoInfo.vercelUrl 
                ? "bg-slate-900 text-white hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-100" 
                : "bg-slate-200 text-slate-400 dark:bg-slate-800 dark:text-slate-500 cursor-not-allowed"
            )}
            title="Publish to Vercel"
          >
            Publish
          </button>
        </div>
      </header>

      {/* ════════════════════════════════════════════════
          BODY — 2-panel layout: Chat (left) + Preview/Code (right)
      ════════════════════════════════════════════════ */}
      <div className="flex-1 flex min-h-0 overflow-hidden">



      {/* ══ CHAT PANEL (left, 380px) ══ */}
      <div className="w-[380px] shrink-0 flex flex-col min-w-0 border-r border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117]">

        {/* Chat Stream */}
        <div
          className="flex-1 overflow-y-auto px-3 py-4 bg-[#f8f9fb] dark:bg-[#0d1117] custom-scrollbar"
          ref={chatContainerRef}
          onScroll={handleChatScroll}
        >
          <div className="space-y-4">

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

            {/* Welcome State — only after loading completes and no messages/phases exist */}
            {!convLoading && messages.length === 0 && phases.length === 0 && status !== 'running' && status !== 'preparing' && status !== 'connecting' && (
              <div className="flex flex-col items-center justify-center py-24 text-center">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-violet-500 to-blue-600 flex items-center justify-center mb-6 shadow-lg shadow-violet-500/20 mx-auto">
                  <Sparkles className="w-7 h-7 text-white" />
                </div>
                <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-100 mb-2">
                  What shall we build?
                </h2>
                <p className="text-sm text-slate-400 dark:text-slate-500 mb-8 max-w-md">
                  Describe your project below, or use the setup wizard for a guided experience.
                </p>
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => setShowWizard(true)}
                    className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-violet-600 to-blue-600 text-white rounded-xl text-sm font-bold hover:from-violet-700 hover:to-blue-700 shadow-sm shadow-violet-600/20 transition-all active:scale-[0.97]"
                  >
                    <Sparkles className="w-4 h-4" />
                    Open Project Wizard
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-3 max-w-lg mt-8">
                  {[
                    { icon: '🌐', label: 'Simple HTML & CSS site' },
                    { icon: '⚛', label: 'React dashboard app' },
                    { icon: '▲', label: 'Next.js landing page' },
                    { icon: '📦', label: 'REST API with Express' },
                  ].map((suggestion) => (
                    <button
                      key={suggestion.label}
                      onClick={() => setChatInput(suggestion.label)}
                      className="flex items-center gap-3 px-5 py-3 bg-white dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/60 rounded-xl text-sm font-medium text-slate-600 dark:text-slate-300 hover:border-violet-300 dark:hover:border-violet-500/50 hover:bg-violet-50 dark:hover:bg-violet-500/5 transition-all"
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


            {/* Thinking indicator — shown while agent processes before first phase */}
            {phases.length === 0 && status === 'running' && (
              <div className="flex flex-col px-4 py-4 w-full animate-in fade-in duration-300">
                <div className="flex items-center mb-3 gap-2.5">
                  <div className="w-6 h-6 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/20">
                    <Sparkles className="w-3.5 h-3.5 text-white" />
                  </div>
                  <span className="font-semibold text-slate-800 dark:text-slate-200 text-[13px]">Base44</span>
                </div>
                <div className="pl-[34px] flex items-center gap-3">
                  <div className="flex gap-1.5">
                    <div className="w-1.5 h-1.5 rounded-full bg-orange-500/80 animate-bounce" style={{ animationDelay: '0ms' }} />
                    <div className="w-1.5 h-1.5 rounded-full bg-orange-500/80 animate-bounce" style={{ animationDelay: '150ms' }} />
                    <div className="w-1.5 h-1.5 rounded-full bg-orange-500/80 animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                  <span className="text-[13px] text-slate-500 dark:text-slate-400">
                    Thinking and analyzing your request...
                  </span>
                </div>
              </div>
            )}

            {/* Preparing/connecting indicator (no steps yet) */}
            {steps.length === 0 && phases.length === 0 && (status === 'preparing' || status === 'connecting') && (
              <div className="flex flex-col px-4 py-4 w-full animate-in fade-in duration-300">
                <div className="flex items-center mb-3 gap-2.5">
                  <div className="w-6 h-6 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/20">
                    <Sparkles className="w-3.5 h-3.5 text-white" />
                  </div>
                  <span className="font-semibold text-slate-800 dark:text-slate-200 text-[13px]">Base44</span>
                </div>
                <div className="pl-[34px] flex items-center gap-3">
                  <Loader2 className="w-3.5 h-3.5 text-orange-500 animate-spin" />
                  <span className="text-[13px] text-slate-500 dark:text-slate-400">
                    {status === 'preparing' ? 'Preparing workspace…' : 'Connecting…'}
                  </span>
                </div>
              </div>
            )}

            <div ref={chatEndRef} className="h-4" />
          </div>
        </div>

        {/* ── Suggestion Chips (Base44 style) ── */}
        {messages.length > 0 && status !== 'running' && status !== 'preparing' && (
          <div className="shrink-0 px-3 py-2 border-t border-slate-100 dark:border-[#1c2128] bg-white dark:bg-[#0d1117]">
            <div className="flex items-center gap-1.5 mb-2">
              <Lightbulb className="w-3 h-3 text-slate-400" />
              <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500">Suggestions</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {['Add Admin Dashboard', 'Build Dedicated Menu', 'Improve Mobile Design'].map(s => (
                <button key={s} type="button" onClick={() => setChatInput(s)}
                  className="px-2.5 py-1 rounded-lg border border-slate-200 dark:border-[#2d333b] text-[11px] font-medium text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.04] hover:border-slate-300 dark:hover:border-[#444c56] transition-colors"
                >{s}</button>
              ))}
            </div>
          </div>
        )}

        {/* ── Bottom Input Bar (Base44 style) ── */}
        <div className="shrink-0 bg-white dark:bg-[#0d1117] border-t border-slate-200 dark:border-[#1c2128]">
          {showScrollBtn && (
            <div className="flex justify-center -mt-3 relative z-10">
              <button onClick={() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })}
                className="bg-slate-800/80 text-white px-3 py-1 rounded-full text-[10px] font-semibold backdrop-blur-md shadow-lg flex items-center gap-1.5 hover:bg-slate-900 transition-colors">
                <ArrowDown className="w-3 h-3" /> Latest messages
              </button>
            </div>
          )}

          {attachedImages.length > 0 && (
            <div className="px-3 pt-2">
              <div className="flex gap-2 flex-wrap">
                {attachedImages.map((img, i) => (
                  <div key={i} className="relative group flex items-center gap-2 bg-slate-50 dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-lg px-2 py-1.5">
                    <div className="relative w-8 h-8 rounded overflow-hidden shrink-0 bg-slate-200 dark:bg-[#21262d]">
                      {img.data ? <img src={img.data} alt={img.name} className="w-full h-full object-cover" /> : <Loader2 className="w-3 h-3 text-slate-400 animate-spin m-auto" />}
                    </div>
                    <span className="text-[10px] text-slate-500 truncate max-w-[60px]">{img.name}</span>
                    <button type="button" onClick={() => removeImage(i)} className="p-0.5 text-slate-400 hover:text-red-500 rounded transition-colors"><X className="w-3 h-3" /></button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <form onSubmit={handleSend} className="relative">
            {(isPreparing || status === 'running') && (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/80 dark:bg-[#0d1117]/85 backdrop-blur-sm">
                <div className="flex items-center gap-2">
                  <Loader2 className={cn("w-4 h-4 animate-spin", isPreparing ? "text-amber-500" : "text-blue-500")} />
                  <span className="text-[12px] font-medium text-slate-500">{isPreparing ? 'Preparing workspace…' : 'Agent working…'}</span>
                </div>
              </div>
            )}

            <div className="px-3 py-2">
              <textarea value={chatInput} onChange={(e) => setChatInput(e.target.value)} onKeyDown={handleKeyDown}
                placeholder={isPreparing ? "Waiting for workspace…" : status === 'running' ? "Agent is working…" : "What would you like to change?"}
                disabled={isPreparing || status === 'running'}
                className={cn("w-full px-0 py-1.5 min-h-[32px] max-h-[120px] outline-none text-[13px] text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 resize-none font-medium leading-relaxed bg-transparent",
                  (isPreparing || status === 'running') && "opacity-40 cursor-not-allowed pointer-events-none"
                )} rows={1} />
            </div>

            {/* Bottom toolbar: [⚙️] [+] [✏️ Edit] [💬 Discuss] ··· [🎙] [→] */}
            <div className="flex items-center justify-between px-3 pb-2.5">
              <div className="flex items-center gap-1">
                <div className="relative" ref={toolsMenuRef}>
                  <button type="button" onClick={() => { setShowToolsMenu(!showToolsMenu); setShowFigmaInput(false); }}
                    className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors" title="Tools">
                    <Settings className="w-4 h-4" />
                  </button>
                  {showToolsMenu && (
                    <div className="absolute bottom-full left-0 mb-2 w-56 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl shadow-2xl overflow-hidden z-30 animate-in fade-in slide-in-from-bottom-2 duration-200">
                      <div className="py-1">
                        <button type="button" onClick={() => { fileInputRef.current?.click(); setShowToolsMenu(false); }} className="w-full flex items-center gap-3 px-3 py-2 text-[12px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left"><Paperclip className="w-3.5 h-3.5 text-slate-400" /><span className="font-medium">Add files or photos</span></button>
                        <button type="button" onClick={() => { videoInputRef.current?.click(); setShowToolsMenu(false); }} className="w-full flex items-center gap-3 px-3 py-2 text-[12px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left"><Video className="w-3.5 h-3.5 text-slate-400" /><span className="font-medium">Add video</span></button>
                        <button type="button" onClick={() => setShowFigmaInput(!showFigmaInput)} className="w-full flex items-center gap-3 px-3 py-2 text-[12px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left"><Layers className="w-3.5 h-3.5 text-slate-400" /><span className="font-medium">Paste Figma link</span></button>
                      </div>
                      <div className="border-t border-slate-100 dark:border-[#2d333b]" />
                      <div className="py-1">
                        <button type="button" onClick={() => setWebSearchEnabled(!webSearchEnabled)} className="w-full flex items-center gap-3 px-3 py-2 text-[12px] text-left hover:bg-slate-50 dark:hover:bg-white/[0.04]"><Globe className={cn("w-3.5 h-3.5", webSearchEnabled ? "text-blue-500" : "text-slate-400")} /><span className={cn("flex-1 font-medium", webSearchEnabled ? "text-blue-600 dark:text-blue-400" : "text-slate-700 dark:text-slate-300")}>Web search</span>{webSearchEnabled && <Check className="w-3.5 h-3.5 text-blue-500" />}</button>
                      </div>
                    </div>
                  )}
                </div>
                <button type="button" onClick={() => fileInputRef.current?.click()} className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors" title="Add files"><Plus className="w-4 h-4" /></button>
                <div className="h-4 w-px bg-slate-200 dark:bg-[#2d333b] mx-0.5" />
                <button type="button" onClick={() => setChatMode('edit')} className={cn("flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-all", chatMode === 'edit' ? "bg-slate-100 dark:bg-[#21262d] text-slate-800 dark:text-white border border-slate-200 dark:border-[#2d333b]" : "text-slate-400 hover:text-slate-600 dark:hover:text-slate-300")}><Pencil className="w-3 h-3" />Edit</button>
                <button type="button" onClick={() => setChatMode('discuss')} className={cn("flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-all", chatMode === 'discuss' ? "bg-slate-100 dark:bg-[#21262d] text-slate-800 dark:text-white border border-slate-200 dark:border-[#2d333b]" : "text-slate-400 hover:text-slate-600 dark:hover:text-slate-300")}><MessageCircle className="w-3 h-3" />Discuss</button>
              </div>
              <div className="flex items-center gap-1">
                <button type="button" className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors" title="Voice input"><Mic className="w-4 h-4" /></button>
                <button type="submit" disabled={(!chatInput.trim() && attachedImages.length === 0) || isPreparing || status === 'running'}
                  className={cn("p-2 rounded-full transition-all", (chatInput.trim() || attachedImages.length > 0) && !isPreparing && status !== 'running' ? "bg-orange-500 text-white shadow-md shadow-orange-500/20 hover:bg-orange-600 hover:scale-105" : "bg-slate-100 dark:bg-[#21262d] text-slate-300 dark:text-slate-600 cursor-not-allowed")}>
                  <Send className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
            <input ref={fileInputRef} type="file" accept="image/*" multiple onChange={handleImageSelect} className="hidden" />
            <input ref={videoInputRef} type="file" accept="video/*" multiple onChange={handleVideoSelect} className="hidden" />
          </form>

          {previewImage && (
            <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 animate-in fade-in duration-200" onClick={() => setPreviewImage(null)}>
              <div className="relative max-w-[80vw] max-h-[80vh]" onClick={(e) => e.stopPropagation()}>
                <img src={previewImage.data} alt={previewImage.name} className="max-w-full max-h-[80vh] rounded-xl shadow-2xl" />
                <button onClick={() => setPreviewImage(null)} className="absolute -top-3 -right-3 w-8 h-8 bg-white text-slate-600 rounded-full flex items-center justify-center shadow-lg hover:bg-slate-100 transition-colors"><X className="w-4 h-4" /></button>
              </div>
            </div>
          )}

        </div>{/* end bottom input bar */}

      </div>{/* end chat panel */}

      {/* ════════════════════════════════════════════════
          RIGHT — Main Content Area (Preview/Code/Build/Terminal)
          Takes all remaining width — this is the primary panel
      ════════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col min-w-0 bg-[#f8f9fb] dark:bg-[#0d1117]">

        {/* Preview edit toolbar */}
        {rightPanel === 'preview' && repoInfo.vercelUrl && (
          <div className="shrink-0 h-14 flex items-center justify-between px-4 bg-white dark:bg-[#161b22] border-b border-slate-200 dark:border-[#2d333b]">
            
            {/* Left: Edit / Palette */}
            <div className="flex items-center gap-2 flex-1">
              <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-bold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors">
                <Wand2 className="w-3.5 h-3.5" />
                Edit
              </button>
              <button className="p-1.5 rounded-lg text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors">
                <Palette className="w-4 h-4" />
              </button>
            </div>

            {/* Center: URL Bar style */}
            <div className="flex justify-center flex-1">
              <div className="flex items-center w-full max-w-[360px] h-9 bg-[#f0f4f9] dark:bg-[#21262d] rounded-full px-3 border border-slate-200/50 dark:border-[#2d333b] hover:border-slate-300 dark:hover:border-slate-600 transition-colors">
                <button className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors">
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
                <div className="flex-1 text-center font-medium text-[13px] text-slate-700 dark:text-slate-200 px-2 cursor-text select-none">
                  /
                </div>
                <button className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors">
                  <ChevronDown className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Right: Device / Fullscreen / Exit */}
            <div className="flex items-center justify-end flex-1 gap-1">
              <button className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors">
                <Monitor className="w-4 h-4" />
                <ChevronDown className="w-3 h-3 ml-0.5" />
              </button>
              <div className="h-4 w-px bg-slate-200 dark:bg-[#2d333b] mx-1" />
              <button className="p-1.5 rounded-lg text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors">
                <Maximize className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* Panel content — fills remaining space */}
        <div className="flex-1 overflow-hidden">

          {/* BUILD / Dashboard tab */}
          {rightPanel === 'build' && (
            <BuildProgressPanel
              isVisible={true}
              onComplete={() => {
                if (repoInfo.vercelUrl) {
                  setTimeout(() => setRightPanel('preview'), 1500);
                }
              }}
            />
          )}

          {/* PREVIEW tab — Live iframe or Building Space */}
          {rightPanel === 'preview' && (
            <div className="h-full flex flex-col">
              {repoInfo.vercelUrl ? (
                <iframe
                  src={repoInfo.vercelUrl}
                  title="Live Preview"
                  className="flex-1 w-full border-0 bg-white"
                  sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
                />
              ) : status === 'running' || status === 'preparing' || phases.length > 0 ? (
                <div className="h-full overflow-y-auto bg-white dark:bg-[#0d1117] p-6 lg:p-10 custom-scrollbar">
                  <div className="max-w-4xl mx-auto space-y-8">
                    {/* Header */}
                    <div className="flex items-center gap-4 border-b border-slate-100 dark:border-[#1c2128] pb-6">
                      <div className="w-12 h-12 rounded-xl bg-blue-500 flex items-center justify-center shrink-0">
                        <Sparkles className="w-6 h-6 text-white" />
                      </div>
                      <div>
                        <h2 className="text-xl font-bold text-slate-800 dark:text-slate-100">
                          Building Your Project
                        </h2>
                        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                          {status === 'preparing' ? 'Preparing workspace...' : status === 'running' ? 'Agent actively working...' : 'Setting up the environment.'}
                        </p>
                      </div>
                    </div>
                    
                    {/* Progress Indicators */}
                    <TaskProgress phases={phases} status={status} completionSummary={completionSummary} />
                    <GenerationProgress isVisible={status === 'running' || status === 'preparing'} />
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-slate-50/50 dark:bg-[#0d1117]">
                  <div className="w-16 h-16 rounded-2xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] shadow-sm flex items-center justify-center mb-5">
                    <Monitor className="w-7 h-7 text-slate-400 dark:text-slate-500" />
                  </div>
                  <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">Preview Not Available</h3>
                  <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm mb-6">
                    This project has not been deployed to Vercel yet, or the preview URL is missing.
                  </p>
                  <button
                    onClick={() => setRightPanel('code')}
                    className="px-5 py-2.5 bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-bold rounded-[18px] hover:bg-slate-800 dark:hover:bg-slate-100 transition-all shadow-sm flex items-center gap-2"
                  >
                    <Code2 className="w-4 h-4" />
                    View Source Code
                  </button>
                </div>
              )}
            </div>
          )}

          {/* CODE tab */}
          {rightPanel === 'code' && (
            <div className="flex h-full w-full">
              <div className="w-[260px] shrink-0 border-r border-slate-200 dark:border-[#2d333b] bg-[#f8f9fb] dark:bg-[#0f1118] flex flex-col">
                <div className="h-10 flex items-center px-4 border-b border-slate-200 dark:border-[#1c2128]">
                  <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Code Files</span>
                </div>
                <div className="flex-1 overflow-hidden">
                  <FileExplorer
                    files={files}
                    selectedFile={selectedFile}
                    onFileSelect={handleFileSelect}
                    projectName={conversation?.title || 'Project'}
                  />
                </div>
              </div>
              <div className="flex-1 min-w-0 bg-white">
                <FileViewer
                  path={selectedFile}
                  content={fileContent}
                  loading={fileLoading}
                />
              </div>
            </div>
          )}

          {/* TERMINAL tab */}
          {rightPanel === 'terminal' && (
            <div className="h-full flex flex-col bg-[#1e1e2e]">
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
          )}

          {/* No panel selected — show welcome */}
          {!rightPanel && (
            <div className="h-full flex flex-col items-center justify-center text-center p-8 bg-[#f8f9fb] dark:bg-[#0d1117]">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center mb-5 shadow-lg shadow-orange-500/20">
                <Monitor className="w-7 h-7 text-white" />
              </div>
              <h3 className="text-[18px] font-bold text-slate-800 dark:text-white mb-2">Preview & Build</h3>
              <p className="text-[13px] text-slate-400 dark:text-slate-500 max-w-sm">
                Start a conversation to generate your app. The preview will appear here once deployed.
              </p>
            </div>
          )}

        </div>
      </div>{/* end right panel */}

      </div>{/* end body flex */}
    </div>
  );
}

