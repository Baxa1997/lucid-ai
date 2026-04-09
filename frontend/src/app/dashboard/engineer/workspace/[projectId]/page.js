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
  ArrowLeft, ArrowRight, Send, Terminal, Settings, X,
  Bot, User, Cpu, ArrowDown, Loader2,
  FileText, Copy, Check, Clock, GitBranch, Github,
  ChevronDown, ChevronRight, Zap, Code2, Play,
  FileCheck2, AlertCircle, TriangleAlert, Sparkles, Activity,
  ExternalLink, GitPullRequest, FileImage, Eye,
  Video, Globe, Camera, Link2, Layers, Paperclip, Plus,
  FolderOpen, PanelLeftClose, PanelLeftOpen, Hammer, Monitor,
  Mic, Pencil, MessageCircle, Lightbulb, History, Diamond, MoreHorizontal,
  Wand2, Palette, RefreshCw, Maximize, Smartphone, Download, Search,
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
    cloning:    { color: 'text-violet-600 dark:text-violet-400', label: 'Cloning repository...' },
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
        status === 'cloning' ? "bg-violet-500 animate-pulse" :
        status === 'connecting' || status === 'running' ? "bg-blue-500 animate-pulse" :
        status === 'error' ? "bg-red-500" : "bg-slate-300 dark:bg-slate-600"
      )} />
      <span className={cn("text-xs font-semibold", current.color)}>
        {error || current.label}
      </span>
    </div>
  );
}

// ── File Viewer Panel ── White editor with line numbers (Base44 style)
function FileViewer({ path, content, loading, onContentChange }) {
  const [copied, setCopied] = useState(false);
  const fileName = path ? path.split('/').pop() : '';
  const breadcrumb = path || '';

  const handleCopy = () => {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const lines = content ? content.split('\n') : [];

  return (
    <div className="h-full flex flex-col bg-white font-mono">
      {/* Breadcrumb bar */}
      <div className="shrink-0 flex items-center justify-between px-4 py-2.5 bg-white border-b border-slate-200">
        <div className="flex items-center gap-1.5 text-slate-500 text-[13px] min-w-0">
          {breadcrumb.split('/').map((part, i, arr) => (
            <span key={i} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-slate-300">/</span>}
              <span className={i === arr.length - 1 ? 'text-slate-800 font-semibold' : 'text-slate-500'}>{part}</span>
            </span>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleCopy}
            disabled={loading || !content}
            className="p-1.5 text-slate-400 hover:text-slate-700 transition-colors disabled:opacity-30 rounded hover:bg-slate-100"
            title="Copy content"
          >
            {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Editor Content */}
      <div className="flex-1 overflow-auto custom-scrollbar">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />
          </div>
        ) : content ? (
          <div className="flex min-h-full">
            {/* Line numbers */}
            <div className="shrink-0 py-3 px-3 text-right select-none border-r border-slate-100 bg-slate-50/50">
              {lines.map((_, i) => (
                <div key={i} className="text-[12px] leading-[22px] text-slate-300 font-mono">
                  {i + 1}
                </div>
              ))}
            </div>
            {/* Code area — editable */}
            <div className="flex-1 min-w-0">
              <textarea
                value={content}
                onChange={(e) => onContentChange?.(e.target.value)}
                spellCheck={false}
                className="w-full min-h-full py-3 px-4 text-[13px] leading-[22px] text-slate-800 font-mono bg-transparent border-none outline-none resize-none"
                style={{ tabSize: 2 }}
              />
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
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

// Relative time helper (Base44 style: "a few seconds ago", "5 minutes ago", "10 hours ago")
function relativeTime(ts) {
  if (!ts) return '';
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 10) return 'a few seconds ago';
  if (diff < 60) return `${diff} seconds ago`;
  if (diff < 120) return 'a minute ago';
  if (diff < 3600) return `${Math.floor(diff / 60)} minutes ago`;
  if (diff < 7200) return 'an hour ago';
  if (diff < 86400) return `${Math.floor(diff / 3600)} hours ago`;
  if (diff < 172800) return 'yesterday';
  return `${Math.floor(diff / 86400)} days ago`;
}

// The main message bubble dispatcher
function MessageBubble({ msg, isLatest }) {
  const [copied, setCopied] = useState(false);

  // CLEAN CHAT MODE: skip thinking blocks and tool calls entirely
  if (msg.role === 'thinking' || msg.role === 'tool' || (msg.role === 'assistant' && msg.toolName)) {
    return null;
  }

  // User message — Base44 style: right-aligned, gray bubble, avatar right
  if (msg.role === 'user') {
    const cleanContent = msg.content?.replace(/\[LUCID_PROJECT\][\s\S]*?(?=\n\n(?:I need|Create)|\n)/i, '').replace(/\[LUCID_PROJECT\][\s\S]*?(?=\n\n|\n)/i, '').trim() || msg.content;
    
    const handleCopy = () => {
      navigator.clipboard.writeText(cleanContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    };

    return (
      <div className="flex items-start gap-2.5 px-4 py-3 w-full justify-end">
        <div className="max-w-[85%] flex flex-col items-end">
          <div className="bg-[#e8ecf1] dark:bg-slate-700/80 rounded-2xl rounded-tr-md px-4 py-3 relative group">
            <p className="text-[14px] leading-relaxed whitespace-pre-wrap text-slate-900 dark:text-slate-100">
              {cleanContent}
            </p>
            <div className="mt-2.5 flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
              <span>{relativeTime(msg.ts)}</span>
              <div className="flex items-center gap-1 ml-auto">
                <button onClick={handleCopy} className="p-0.5 hover:text-slate-600 dark:hover:text-slate-300 transition-colors" title="Copy">
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
                <button className="p-0.5 hover:text-slate-600 dark:hover:text-slate-300 transition-colors" title="Edit">
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <span className="text-slate-300 dark:text-slate-600 text-[11px] font-medium cursor-pointer hover:text-slate-500 dark:hover:text-slate-400 transition-colors">Revert</span>
              </div>
            </div>
          </div>
        </div>
        <div className="w-7 h-7 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center shrink-0 mt-1 overflow-hidden">
          <User className="w-4 h-4 text-slate-500" />
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

  // Agent message — Base44 style: transparent bg, no bubble, avatar left
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
          <div key={i} className="flex items-start gap-1.5 py-0.5 min-w-0">
            <span className="shrink-0 text-sm leading-5">✅</span>
            <span className="text-slate-500 dark:text-slate-400 text-sm break-words min-w-0 flex-1 leading-5">
              {line.trim().replace(/^✅\s*/, '')}
            </span>
          </div>
        );
        return;
      }

      // Numbered list items
      const numberedMatch = line.match(/^\s*(\d+)\.\s+(.*)/);
      if (numberedMatch) {
        elements.push(
          <div key={i} className="flex items-start gap-2 py-0.5 min-w-0">
            <span className="shrink-0 text-slate-400 font-mono text-xs mt-0.5 w-4 text-right">{numberedMatch[1]}.</span>
            <span className="text-slate-700 dark:text-slate-300 break-words min-w-0 flex-1">{formatInline(numberedMatch[2])}</span>
          </div>
        );
        return;
      }

      // Bullets
      if (line.trim().startsWith('- ') || line.trim().startsWith('• ') || line.trim().startsWith('* ')) {
        const bulletContent = line.trim().replace(/^[-•*]\s*/, '');
        elements.push(
          <div key={i} className="flex items-start gap-2 py-0.5 min-w-0">
            <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-slate-400 dark:bg-slate-500" />
            <span className="text-slate-700 dark:text-slate-300 break-words min-w-0 flex-1">{formatInline(bulletContent)}</span>
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
        <div key={i} className="py-0.5 text-slate-700 dark:text-slate-300 break-words">{formatInline(line)}</div>
      );
    });

    return elements;
  };

  return (
    <div className="flex items-start gap-3 px-4 py-4 w-full animate-in fade-in duration-300">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 mt-0.5 shadow-sm shadow-orange-500/20">
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-slate-800 dark:text-slate-200 text-[13px]">Lucid AI</span>
          <button className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 p-1 rounded hover:bg-slate-100 dark:hover:bg-white/[0.04] transition-colors">
            <MoreHorizontal className="w-4 h-4" />
          </button>
        </div>
        {msg.content && (
          <div className="text-[14px] leading-relaxed text-slate-700 dark:text-slate-300">
            {renderContent(msg.content)}
          </div>
        )}

        {/* ── File-write pills — Base44 style ── */}
        {msg.fileWrites?.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3">
            {msg.fileWrites.map((fw, i) => {
              const fname = fw.filename.split('/').pop();
              const label = fw.action === 'edit' ? 'Editing' : fw.action === 'multiedit' ? 'Editing' : 'Writing';
              return (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-100 dark:bg-slate-800 rounded-lg text-[12px] font-medium text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700/60"
                >
                  <FileText className="w-3 h-3 text-slate-400 shrink-0" />
                  {label} <span className="text-slate-400 dark:text-slate-500">{fname}</span>
                </span>
              );
            })}
          </div>
        )}

        <div className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
          {relativeTime(msg.ts)}
        </div>
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
  const [userName, setUserName] = useState('');

  useEffect(() => {
    fetch('/api/agent/token')
      .then((r) => r.json())
      .then((d) => { if (d.token) setToken(d.token); })
      .catch(() => {});
    // Get user name for header subtitle
    const supabase = getSupabaseBrowserClient();
    supabase.auth.getUser().then(({ data: { user } }) => {
      if (user) {
        const name = user.user_metadata?.full_name || user.user_metadata?.name || user.email?.split('@')[0] || '';
        setUserName(name.split(' ')[0]); // First name only
      }
    });
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
    setFiles,
    error,
    sendMessage,
    startSession,
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
  // repoInfo.vercelUrl must be in deps — otherwise the condition reads a stale capture
  // and can overwrite a deployUrl that arrived earlier.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewUrl, repoInfo.vercelUrl]);

  // ── Layout state ────────────────────────────────────────
  const [chatOpen, setChatOpen] = useState(true);
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
  const [editedFileContent, setEditedFileContent] = useState(null); // Track unsaved edits
  const fileInputRef = useRef(null);
  const videoInputRef = useRef(null);
  const toolsMenuRef = useRef(null);

  // ── Hydrate chat history from Supabase into the hook ────
  useEffect(() => {
    if (savedMessages.length > 0 && setInitialMessages) {
      setInitialMessages(savedMessages);
    }
  }, [savedMessages, setInitialMessages]);

  // ── Fetch file tree from GitLab when returning to an existing project ──
  // If the WS hasn't sent a file_tree yet but we have a repo_name, fetch from GitLab.
  const fileTreeFetched = useRef(false);
  useEffect(() => {
    if (fileTreeFetched.current) return;
    if (files.length > 0) { fileTreeFetched.current = true; return; }
    if (!conversation?.repo_name) return;

    fileTreeFetched.current = true;
    (async () => {
      try {
        const res = await fetch(
          `/api/gitlab/tree?repo=${encodeURIComponent(conversation.repo_name)}&ref=${encodeURIComponent(conversation.branch || 'main')}`
        );
        if (res.ok) {
          const data = await res.json();
          if (data.files && data.files.length > 0 && files.length === 0) {
            // Inject into the hook's files state
            if (typeof setFiles === 'function') setFiles(data.files);
          }
        }
      } catch (err) {
        console.warn('[workspace] GitLab tree fetch failed:', err);
      }
    })();
  }, [conversation?.repo_name, conversation?.branch, files.length]);

  // ── Auto-start wizard task when workspace becomes ready ──
  // NOTE: For wizard mode, the task is now sent in the WebSocket handshake
  // (via the `task` prop to useAgentSession). This effect is kept ONLY as a
  // safety net — the handshake path is the primary flow.
  const wizardAutoStarted = useRef(isWizardMode); // Already started if wizard mode
  useEffect(() => {
    if (wizardAutoStarted.current) return;

    // If wizard task was passed in handshake, just mark as started
    if (wizardTask) {
      wizardAutoStarted.current = true;
      return;
    }

    // Fallback: check sessionStorage — only runs if handshake missed the task
    if (status === 'ready') {
      try {
        const key = `wizard_prompt_${conversationId}`;
        const prompt = sessionStorage.getItem(key);
        if (prompt) {
          wizardAutoStarted.current = true;
          sessionStorage.removeItem(key);
          sessionStorage.removeItem(`wizard_meta_${conversationId}`);
          sessionStorage.removeItem(`wizard_desc_${conversationId}`);
          // Use startSession instead of sendMessage to avoid duplicate chat messages
          setTimeout(() => startSession(prompt), 300);
        }
      } catch (_) {}
    }
  }, [status, conversationId, startSession, wizardTask]);

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

      // User messages are already saved in handleSend — skip them here to
      // avoid writing duplicates (handleSend + this effect + backend = 3 copies).
      if (msg.role === 'user') continue;

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
  // rightPanel: 'preview' | 'dashboard' | 'code' | 'terminal'
  const [rightPanel, setRightPanel] = useState('preview');
  const [showFileExplorer, setShowFileExplorer] = useState(false);
  const [panelOverrideEnabled, setPanelOverrideEnabled] = useState(true);

  // ── Building screen latch ─────────────────────────────────
  // Prevents two flicker bugs:
  //   1. "Building" shown during `idle` for existing projects (cold reconnect)
  //   2. Flash to "Preview Not Available" during the brief `ready` window
  //      between workspace init and first `running` state for wizard projects.
  //
  // Rules:
  //   - Always starts true — the workspace is always connecting on page load
  //   - Debounce 1.2s before hiding — absorbs the preparing→ready→running gap
  //   - For wizard: stays true through the whole build
  //   - For existing: clears ~1.2s after status reaches 'ready' with no active task
  const [buildingActive, setBuildingActive] = useState(true);
  const buildingTimerRef = useRef(null);

  useEffect(() => {
    if (buildingTimerRef.current) {
      clearTimeout(buildingTimerRef.current);
      buildingTimerRef.current = null;
    }

    // "Actively building" means the agent is running, workspace is initializing,
    // OR at least one phase is still in progress. Completed phases (status === 'done')
    // do NOT count — they are never cleared from state, so checking phases.length > 0
    // would keep the building screen stuck forever after the task finishes.
    //
    // connecting/preparing applies to ALL modes (not just wizard) so the user
    // always sees a progress visual instead of "Preview Not Available" on load.
    const hasActivePhase = phases.some(p => p.status === 'active');
    const isActivelyBuilding =
      status === 'running' ||
      hasActivePhase ||
      status === 'connecting' ||
      status === 'cloning' ||
      status === 'preparing';

    if (isActivelyBuilding) {
      setBuildingActive(true);
    } else if (
      // Don't start the debounce-hide during initial idle — wait for at least
      // one real connection attempt before hiding the building screen.
      status !== 'idle'
    ) {
      // Debounce: absorbs the brief `ready` window between workspace init and task start
      buildingTimerRef.current = setTimeout(() => {
        setBuildingActive(false);
        buildingTimerRef.current = null;
      }, 1200);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, phases]);

  // Cleanup latch timer on unmount
  useEffect(() => () => {
    if (buildingTimerRef.current) clearTimeout(buildingTimerRef.current);
  }, []);

  // Auto-show right panel during generation — keep preview only if the user
  // has not selected a different panel manually.
  const prevStatusForPanel = useRef(null);
  useEffect(() => {
    if (prevStatusForPanel.current === status) return;
    prevStatusForPanel.current = status;

    // Show preview panel during initial workspace setup (cloning/preparing)
    // so the full-page building animation is immediately visible.
    if ((status === 'cloning' || status === 'preparing') && panelOverrideEnabled) {
      if (rightPanel !== 'code') setRightPanel('preview');
    }

    // When clone completes for an existing project (non-wizard), switch to Code
    // tab so the user can immediately see their cloned file tree.
    // Wizard projects skip this — they go straight to 'running'.
    if (status === 'ready' && !isWizardMode && panelOverrideEnabled) {
      setRightPanel('code');
    }

    // Show preview + file explorer when agent is actively running a task.
    if (status === 'running' && panelOverrideEnabled) {
      if (rightPanel !== 'code') setRightPanel('preview');
      setShowFileExplorer(true);
    }
  }, [status, rightPanel, panelOverrideEnabled]);

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
    // Prevent accidental horizontal scroll — reset it immediately
    if (e.target.scrollLeft !== 0) e.target.scrollLeft = 0;
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    const isNearBottom = distFromBottom < 100;
    setShowScrollBtn(distFromBottom > 100);
    userScrolledUpRef.current = !isNearBottom;
  };

  // ── File select: load content from backend ──────────────
  const handleFileSelect = useCallback(async (path) => {
    setSelectedFile(path);
    setEditedFileContent(null); // reset any unsaved edits
    setRightPanel('code');
    setFileLoading(true);
    setFileContent('');

    // ── Try 1: Read from active workspace session ──
    if (sessionId) {
      try {
        const res = await fetch(
          `/api/files/read?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`
        );
        const data = await res.json();
        if (res.ok && data.content) {
          setFileContent(data.content);
          setFileLoading(false);
          return;
        }
      } catch {
        // session API failed — try GitLab fallback below
      }
    }

    // ── Try 2: Read from GitLab repo ──
    const repoName = conversation?.repo_name;
    if (repoName) {
      try {
        const gitlabPath = encodeURIComponent(path);
        const res = await fetch(
          `/api/gitlab/file?repo=${encodeURIComponent(repoName)}&path=${gitlabPath}`
        );
        if (res.ok) {
          const data = await res.json();
          if (data.content) {
            // GitLab returns base64 encoded content
            const decoded = data.encoding === 'base64'
              ? atob(data.content)
              : data.content;
            setFileContent(decoded);
            setFileLoading(false);
            return;
          }
        }
      } catch {
        // GitLab fallback also failed
      }
    }

    // ── Fallback: file not available ──
    setFileContent(`// File content not available yet\n// Path: ${path}\n\n// The file will be viewable once the build completes\n// and code is pushed to the repository.`);
    setFileLoading(false);
  }, [sessionId, conversation?.repo_name]);

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
          TOP HEADER BAR — Base44 pixel-perfect
      ════════════════════════════════════════════════ */}
      <header className="shrink-0 h-[52px] bg-white dark:bg-[#161b22] border-b border-slate-200 dark:border-[#2d333b] flex items-center px-4 z-20">
        
        {/* Left: Back + Logo + Title + Clock + Sidebar Toggle */}
        <div className="flex items-center gap-2 min-w-0" style={{ flex: '0 0 auto' }}>
          <button
            onClick={() => window.history.back()}
            className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors shrink-0"
            title="Go Back"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/25">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <div className="flex flex-col justify-center min-w-0 mr-1">
            <span className="text-[13px] font-bold text-slate-900 dark:text-white leading-tight truncate max-w-[160px]">
              {conversation?.title || wizardDesc || 'New Project'}
            </span>
            <span className="text-[11px] text-slate-400 dark:text-slate-500 leading-tight truncate max-w-[160px]">
              {userName ? `${userName}'s Workspace W...` : 'AI Workspace'}
            </span>
          </div>
          <button className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors shrink-0">
            <History className="w-4 h-4" />
          </button>
          {/* Sidebar toggle — |← icon (Base44 places it in left group) */}
          <button
            onClick={() => setChatOpen(!chatOpen)}
            className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors shrink-0"
            title={chatOpen ? 'Hide Chat' : 'Show Chat'}
          >
            {chatOpen ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="3" y1="4" x2="3" y2="20"/><polyline points="11 8 7 12 11 16"/><line x1="7" y1="12" x2="21" y2="12"/></svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="3" y1="4" x2="3" y2="20"/><polyline points="13 8 17 12 13 16"/><line x1="7" y1="12" x2="17" y2="12"/></svg>
            )}
          </button>
        </div>

        {/* Center: Tab Switcher — Preview | Dashboard | Code + Split View */}
        <div className="flex-1 flex items-center justify-center gap-2">
          <div className="flex items-center bg-slate-100 dark:bg-[#21262d] rounded-xl p-[3px] border border-slate-200/80 dark:border-[#2d333b]">
            {[
              { key: 'preview', label: 'Preview' },
              { key: 'dashboard', label: 'Dashboard' },
              { key: 'code', label: 'Code' },
            ].map(tab => (
              <button
                key={tab.key}
                onClick={() => {
                  setRightPanel(tab.key);
                  setPanelOverrideEnabled(false);
                }}
                className={cn(
                  "flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[13px] font-semibold transition-all",
                  rightPanel === tab.key
                    ? "bg-white dark:bg-[#2d333b] text-slate-900 dark:text-white shadow-sm"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Split view icon */}
          <button
            className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors"
            title="Split View"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="12" y1="3" x2="12" y2="21"/></svg>
          </button>
        </div>

        {/* Right: Actions */}
        <div className="flex items-center gap-1" style={{ flex: '0 0 auto' }}>
          {/* Avatar Stack */}
          <div className="flex items-center -space-x-1 mr-1">
            <div className="w-7 h-7 rounded-full bg-emerald-100 dark:bg-emerald-900/30 border-2 border-white dark:border-[#161b22] flex items-center justify-center overflow-hidden shrink-0">
              <User className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
            </div>
            <div className="w-7 h-7 rounded-full bg-slate-100 dark:bg-slate-800 border-2 border-white dark:border-[#161b22] flex items-center justify-center z-10 text-slate-400 cursor-pointer shrink-0 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors">
              <Plus className="w-3 h-3" />
            </div>
          </div>

          <button className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
            <MoreHorizontal className="w-4 h-4" />
          </button>
          
          <button className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
            <Github className="w-4 h-4" />
          </button>

          <button className="flex items-center gap-1.5 px-3 py-1 rounded-full text-[12px] font-bold text-orange-500 bg-orange-50 dark:bg-orange-500/10 border border-orange-200 dark:border-orange-500/20 hover:bg-orange-100 dark:hover:bg-orange-500/20 transition-all ml-1">
            <Diamond className="w-3.5 h-3.5 fill-current" />
            30% off
          </button>

          <button
            onClick={() => setShowExportModal(true)}
            className="px-3 py-1 rounded-full text-[13px] font-semibold text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-all ml-0.5"
            title="Export Code"
          >
            Export
          </button>

          <button
            onClick={() => {
              if (repoInfo.vercelUrl) window.open(repoInfo.vercelUrl, '_blank');
            }}
            className={cn(
              "flex items-center px-4 py-1.5 rounded-full text-[13px] font-semibold transition-all ml-0.5",
              repoInfo.vercelUrl 
                ? "bg-slate-900 text-white hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-100 shadow-sm" 
                : "bg-slate-200 text-slate-400 dark:bg-slate-700 dark:text-slate-500 cursor-not-allowed"
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



      {/* ══ CHAT PANEL (left, collapsible with smooth animation) ══ */}
      <div
        className={cn(
          "shrink-0 flex flex-col min-w-0 border-r border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] transition-all duration-300 ease-in-out overflow-hidden",
          chatOpen ? "w-[380px]" : "w-0 border-r-0"
        )}
      >

        {/* Chat Stream */}
        <div
          className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-4 bg-[#f8f9fb] dark:bg-[#0d1117] custom-scrollbar"
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

            {/* Empty chat state — shown after workspace is ready with no history.
                Clean Base44-style: no splash card, just a subtle prompt. */}
            {!convLoading && messages.length === 0 && status === 'ready' && (
              <div className="flex flex-col items-center justify-center py-20 text-center px-6">
                <div className="w-10 h-10 rounded-2xl bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center mb-4 shadow-sm shadow-orange-500/20">
                  <Sparkles className="w-5 h-5 text-white" />
                </div>
                <p className="text-[14px] font-medium text-slate-500 dark:text-slate-400">
                  Workspace ready. What would you like to build?
                </p>
              </div>
            )}

            {/* Messages — OpenHands style */}
            {messages.map((msg, i) => (
              <div key={msg.id} className="animate-msg-in">
                <MessageBubble msg={msg} isLatest={i === messages.length - 1} />
              </div>
            ))}

            {/* Workspace setup card — cloning / preparing / ready (all-done) / early running */}
            {(() => {
              const isCloning   = status === 'cloning' || status === 'preparing' || (isWizardMode && status === 'connecting');
              const noAgentMsgs = messages.filter(m => !m.fromHistory && m.role === 'agent').length === 0;
              // Show "all done" step at ready, and keep showing during the brief window
              // when running has started but no task phases have arrived yet.
              const isReadyState   = status === 'ready' && noAgentMsgs;
              const isEarlyRunning = status === 'running' && phases.length === 0 && noAgentMsgs;
              if (!isCloning && !isReadyState && !isEarlyRunning) return null;

              const allDone = isReadyState || isEarlyRunning;
              const wsSteps = [
                { label: 'Connected to workspace', done: true, active: false },
                {
                  label: allDone
                    ? 'Repository cloned'
                    : status === 'cloning' ? 'Cloning repository…' : 'Preparing environment…',
                  done:   allDone,
                  active: !allDone,
                },
                { label: 'Ready for your task', done: allDone, active: false },
              ];

              return (
                <div className="px-4 py-3 animate-in fade-in duration-300">
                  <div className="flex items-start gap-3 p-4 rounded-2xl border border-slate-100 dark:border-[#2d333b] bg-white dark:bg-[#161b22] shadow-sm">
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/20 mt-0.5">
                      <Sparkles className="w-4 h-4 text-white" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 mb-2.5">
                        Setting up workspace
                      </p>
                      <div className="space-y-2">
                        {wsSteps.map((step, i) => (
                          <div key={i} className="flex items-center gap-2.5">
                            {step.done ? (
                              <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                            ) : step.active ? (
                              <Loader2 className="w-3.5 h-3.5 text-orange-500 animate-spin shrink-0" />
                            ) : (
                              <div className="w-3 h-3 rounded-full border-2 border-slate-200 dark:border-slate-700 shrink-0" />
                            )}
                            <span className={cn(
                              "text-[12px] leading-tight",
                              step.done   ? "text-slate-400 dark:text-slate-500" :
                              step.active ? "text-slate-700 dark:text-slate-200 font-medium" :
                                            "text-slate-400 dark:text-slate-600"
                            )}>
                              {step.label}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* Thinking indicator — only when agent is actively running a task */}
            {status === 'running' && (
              <div className="flex items-center gap-3 px-4 py-3 w-full animate-in fade-in duration-300">
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/20">
                  <Sparkles className="w-3.5 h-3.5 text-white" />
                </div>
                <span className="text-[13px] text-slate-500 dark:text-slate-400 flex items-center gap-0.5">
                  thinking…<span className="animate-pulse text-slate-800 dark:text-slate-200 font-light">▎</span>
                </span>
              </div>
            )}

            <div ref={chatEndRef} className="h-4" />
          </div>
        </div>

        {/* ── Suggestion Chips (Base44 style — pinned above input, never jumps) ── */}
        {messages.length > 0 && (
          <div className="shrink-0 px-4 py-2.5 bg-white dark:bg-[#0d1117] border-t border-slate-100 dark:border-[#1c2128] relative">
            {/* "Latest messages" button — floats above suggestions */}
            {showScrollBtn && (
              <div className="absolute -top-8 left-1/2 -translate-x-1/2 z-10">
                <button onClick={() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })}
                  className="bg-white dark:bg-[#161b22] text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-[#2d333b] px-3 py-1 rounded-full text-[11px] font-semibold shadow-md flex items-center gap-1.5 hover:bg-slate-50 dark:hover:bg-[#21262d] transition-colors">
                  <ArrowDown className="w-3 h-3" /> Latest messages
                </button>
              </div>
            )}
            <div className="flex items-center gap-2 mb-2">
              <Lightbulb className="w-3.5 h-3.5 text-slate-400" />
              <span className="text-[12px] font-medium text-slate-400 dark:text-slate-500">Suggestions</span>
            </div>
            <div className="flex items-center overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
              {['Add Admin Dashboard', 'Build Dedicated Menu', 'Improve Mobile Design'].map((s, i) => (
                <div key={s} className="flex items-center shrink-0">
                  {i > 0 && <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-3" />}
                  <button type="button" onClick={() => setChatInput(s)}
                    className="text-[13px] font-medium text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition-colors whitespace-nowrap"
                  >{s}</button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Bottom Input Bar (Base44 pixel-perfect) ── */}
        <div className="shrink-0 bg-white dark:bg-[#0d1117] px-3 pb-3">

          {attachedImages.length > 0 && (
            <div className="mb-2">
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
            {/* Input container — always enabled (Base44 style) */}
            <div className="border border-slate-200 dark:border-[#2d333b] rounded-2xl bg-white dark:bg-[#161b22] overflow-hidden">
              <textarea value={chatInput} onChange={(e) => setChatInput(e.target.value)} onKeyDown={handleKeyDown}
                placeholder="What would you like to change?"
                className="w-full px-4 pt-4 pb-8 min-h-[80px] max-h-[160px] outline-none text-[14px] text-slate-800 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 resize-none font-medium leading-relaxed bg-transparent"
                rows={2} />
            </div>

            {/* Toolbar below the input box */}
            <div className="flex items-center justify-between px-1 pt-2.5">
              <div className="flex items-center gap-1">
                {/* Settings gear */}
                <div className="relative" ref={toolsMenuRef}>
                  <button type="button" onClick={() => { setShowToolsMenu(!showToolsMenu); setShowFigmaInput(false); }}
                    className="p-2 text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors" title="Tools">
                    <Settings className="w-5 h-5" />
                  </button>
                  {showToolsMenu && (
                    <div className="absolute bottom-full left-0 mb-2 w-56 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl shadow-2xl overflow-hidden z-30 animate-in fade-in slide-in-from-bottom-2 duration-200">
                      <div className="py-1">
                        <button type="button" onClick={() => { fileInputRef.current?.click(); setShowToolsMenu(false); }} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left"><Paperclip className="w-4 h-4 text-slate-400" /><span className="font-medium">Add files or photos</span></button>
                        <button type="button" onClick={() => { videoInputRef.current?.click(); setShowToolsMenu(false); }} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left"><Video className="w-4 h-4 text-slate-400" /><span className="font-medium">Add video</span></button>
                        <button type="button" onClick={() => setShowFigmaInput(!showFigmaInput)} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left"><Layers className="w-4 h-4 text-slate-400" /><span className="font-medium">Paste Figma link</span></button>
                      </div>
                      <div className="border-t border-slate-100 dark:border-[#2d333b]" />
                      <div className="py-1">
                        <button type="button" onClick={() => setWebSearchEnabled(!webSearchEnabled)} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-left hover:bg-slate-50 dark:hover:bg-white/[0.04]"><Globe className={cn("w-4 h-4", webSearchEnabled ? "text-blue-500" : "text-slate-400")} /><span className={cn("flex-1 font-medium", webSearchEnabled ? "text-blue-600 dark:text-blue-400" : "text-slate-700 dark:text-slate-300")}>Web search</span>{webSearchEnabled && <Check className="w-4 h-4 text-blue-500" />}</button>
                      </div>
                    </div>
                  )}
                </div>

                {/* Plus button */}
                <button type="button" onClick={() => fileInputRef.current?.click()} className="p-2 text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors" title="Add files"><Plus className="w-5 h-5" /></button>

                {/* Separator */}
                <div className="h-5 w-px bg-slate-200 dark:bg-[#2d333b] mx-1" />

                {/* Discuss button — Base44 style (only Discuss, no separate Edit) */}
                <button type="button" onClick={() => setChatMode(chatMode === 'discuss' ? 'edit' : 'discuss')}
                  className={cn("flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-semibold transition-all border",
                    chatMode === 'discuss'
                      ? "bg-slate-100 dark:bg-[#21262d] text-slate-800 dark:text-white border-slate-200 dark:border-[#2d333b]"
                      : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300 border-transparent hover:bg-slate-50 dark:hover:bg-white/[0.04]"
                  )}>
                  <MessageCircle className="w-4 h-4" />Discuss
                </button>
              </div>

              <div className="flex items-center gap-1.5">
                {/* Mic button */}
                <button type="button" className="p-2 text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors" title="Voice input"><Mic className="w-5 h-5" /></button>

                {/* Send / Stop button — Base44 style: always filled dark */}
                {(status === 'running' || isPreparing) ? (
                  <button type="button" onClick={stopSession}
                    className="p-2.5 rounded-xl bg-slate-800 dark:bg-white text-white dark:text-slate-900 shadow-sm hover:bg-slate-900 dark:hover:bg-slate-100 transition-all hover:scale-105"
                    title="Stop processing">
                    <div className="w-4 h-4 flex items-center justify-center">
                      <div className="w-3 h-3 bg-current rounded-sm" />
                    </div>
                  </button>
                ) : (
                  <button type="submit" disabled={!chatInput.trim() && attachedImages.length === 0}
                    className={cn("p-2.5 rounded-xl transition-all shadow-sm",
                      (chatInput.trim() || attachedImages.length > 0)
                        ? "bg-slate-800 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-900 dark:hover:bg-slate-100 hover:scale-105"
                        : "bg-slate-200 dark:bg-[#21262d] text-slate-400 dark:text-slate-500 cursor-not-allowed"
                    )}>
                    <ArrowRight className="w-4 h-4" />
                  </button>
                )}
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

          {/* DASHBOARD tab — Build progress + task phases */}
          {rightPanel === 'dashboard' && (
            <div className="h-full overflow-y-auto bg-white dark:bg-[#0d1117] p-6 lg:p-10 custom-scrollbar">
              <div className="max-w-4xl mx-auto space-y-8">
                <div className="flex items-center gap-4 border-b border-slate-100 dark:border-[#1c2128] pb-6">
                  <div className="w-12 h-12 rounded-xl bg-blue-500 flex items-center justify-center shrink-0">
                    <Sparkles className="w-6 h-6 text-white" />
                  </div>
                  <div>
                    <h2 className="text-xl font-bold text-slate-800 dark:text-slate-100">
                      Building Your Project
                    </h2>
                    <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                      {status === 'cloning' ? 'Cloning your codebase...' : status === 'preparing' ? 'Preparing workspace...' : status === 'running' ? 'Agent actively working...' : phases.length > 0 ? 'Build in progress...' : 'Waiting to start.'}
                    </p>
                  </div>
                </div>
                <TaskProgress phases={phases} status={status} completionSummary={completionSummary} />
              </div>
            </div>
          )}

          {/* PREVIEW tab — Live iframe OR Building state OR Idle state */}
          {rightPanel === 'preview' && (
            <div className="h-full flex flex-col">
              {repoInfo.vercelUrl ? (
                <iframe
                  src={repoInfo.vercelUrl}
                  title="Live Preview"
                  className="flex-1 w-full border-0 bg-white"
                  sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
                />
              ) : (buildingActive || status === 'connecting' || status === 'cloning' || status === 'preparing') ? (() => {
                /* ── Derive dynamic text from current phase ── */
                const activePhase = phases.find(p => p.status === 'active');
                const maxDonePhase = phases.filter(p => p.status === 'done').reduce((max, p) => Math.max(max, p.phase || 0), 0);
                const currentPhaseNum = activePhase?.phase || maxDonePhase || 0;

                // Phase-aware label & icon
                let buildLabel =
                  status === 'connecting' ? 'Connecting...' :
                  status === 'cloning'    ? 'Cloning your codebase...' :
                  status === 'preparing'  ? 'Preparing workspace...' :
                  status === 'ready'      ? 'Workspace ready...' :
                  isWizardMode            ? 'Building your idea...' : 'Getting ready...';
                let buildSubtext =
                  status === 'cloning'   ? '📦 Fetching your repository files and setting up workspace' :
                  status === 'ready'     ? '✅ Workspace initialized successfully' :
                  isWizardMode           ? '💬 Brainstorm ideas in Discussion Mode at 0.3 credits per request' :
                  '🔧 Setting up your workspace and cloning the repository';
                let progressPct =
                  status === 'cloning'                             ? '8%' :
                  (status === 'connecting' || status === 'preparing') ? '5%' :
                  status === 'ready'                               ? '100%' : '20%';

                if (currentPhaseNum <= 1 && status === 'running') {
                  buildLabel = 'Setting things up...';
                  progressPct = '10%';
                } else if (currentPhaseNum === 2) {
                  buildLabel = 'Preparing workspace...';
                  progressPct = '15%';
                } else if (currentPhaseNum === 3) {
                  buildLabel = 'Analyzing your idea...';
                  progressPct = '20%';
                } else if (currentPhaseNum === 4 && activePhase?.title?.toLowerCase().includes('research')) {
                  buildLabel = 'Researching your idea...';
                  buildSubtext = '🔍 Analyzing top products in this domain for best practices';
                  progressPct = '35%';
                } else if (currentPhaseNum === 4) {
                  buildLabel = 'Research complete — planning code...';
                  buildSubtext = '📋 Creating implementation plan from research insights';
                  progressPct = '40%';
                } else if (currentPhaseNum === 5) {
                  buildLabel = 'Writing your code...';
                  buildSubtext = '⚡ AI is generating components, pages, and logic';
                  progressPct = '60%';
                } else if (currentPhaseNum === 6) {
                  buildLabel = 'Verifying build...';
                  buildSubtext = '🔧 Running build checks to ensure everything compiles';
                  progressPct = '80%';
                } else if (currentPhaseNum === 7) {
                  buildLabel = 'Publishing project...';
                  buildSubtext = '🚀 Committing and pushing code to repository';
                  progressPct = '90%';
                } else if (currentPhaseNum >= 8) {
                  buildLabel = 'Deploying...';
                  buildSubtext = '🌐 Setting up live preview on Vercel';
                  progressPct = '95%';
                }
                
                // If phase 4 research is done but phase 5 hasn't started yet
                const researchDone = phases.find(p => p.phase === 4 && p.status === 'done');
                const codingStarted = phases.find(p => p.phase === 5);
                if (researchDone && !codingStarted) {
                  buildLabel = 'Research complete — starting to code...';
                  buildSubtext = '✅ Domain analysis complete. Building your codebase now.';
                  progressPct = '45%';
                }

                return (
                /* ── Building state — Base44 "Building your idea..." ── */
                <div className="h-full flex flex-col items-center justify-center relative overflow-hidden bg-white dark:bg-[#0d1117]">
                  {/* Warm gradient glow at bottom */}
                  <div className="absolute bottom-0 left-0 right-0 h-[45%] bg-gradient-to-t from-orange-100/80 via-orange-50/40 to-transparent dark:from-orange-900/20 dark:via-orange-900/5 dark:to-transparent pointer-events-none" />
                  
                  {/* Orange circle logo with stripes */}
                  <div className="relative z-10 mb-6">
                    <div className="w-20 h-20 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shadow-lg shadow-orange-400/30">
                      <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
                        <rect x="8" y="10" width="24" height="3" rx="1.5" fill="white" opacity="0.9"/>
                        <rect x="8" y="16" width="24" height="3" rx="1.5" fill="white" opacity="0.7"/>
                        <rect x="8" y="22" width="24" height="3" rx="1.5" fill="white" opacity="0.5"/>
                        <rect x="12" y="28" width="16" height="3" rx="1.5" fill="white" opacity="0.3"/>
                      </svg>
                    </div>
                  </div>

                  {/* Dynamic building text */}
                  <h2 className="relative z-10 text-xl font-semibold text-slate-400 dark:text-slate-500 mb-8 transition-all duration-500">
                    {buildLabel}
                  </h2>

                  {/* Progress bar — reflects actual phase progress */}
                  <div className="relative z-10 w-40 h-1 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden mb-10">
                    <div 
                      className="h-full bg-gradient-to-r from-orange-400 to-orange-500 rounded-full transition-all duration-1000 ease-out" 
                      style={{ width: progressPct }} 
                    />
                  </div>

                  {/* Did you know tip — changes with phase */}
                  <div className="relative z-10 text-center">
                    <p className="text-[13px] font-medium text-orange-400 dark:text-orange-500 mb-1.5">Did you know?</p>
                    <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm transition-all duration-300">
                      {buildSubtext}
                    </p>
                  </div>
                </div>
                );
              })() : (
                /* ── Idle state — "Preview Not Available" (Base44 style) ── */
                <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                  <div className="w-16 h-16 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-5">
                    <Monitor className="w-8 h-8 text-slate-400 dark:text-slate-500" />
                  </div>
                  <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">Preview Not Available</h3>
                  <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm mb-6">
                    This project has not been deployed to Vercel yet, or the preview URL is missing.
                  </p>
                  <button
                    onClick={() => setRightPanel('code')}
                    className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm"
                  >
                    <Code2 className="w-4 h-4" />
                    View Source Code
                  </button>
                </div>
              )}
            </div>
          )}

          {/* CODE tab — Base44 style: single Code header on sidebar only */}
          {rightPanel === 'code' && (
            <div className="flex h-full w-full">
              {/* File sidebar */}
              <div className="w-[240px] shrink-0 border-r border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0f1118] flex flex-col">
                {/* Single Code header */}
                <div className="h-11 flex items-center justify-between px-4 border-b border-slate-200 dark:border-[#1c2128]">
                  <span className="text-[14px] font-bold text-slate-800 dark:text-white">Code</span>
                </div>
                {/* Files sub-header */}
                <div className="h-10 flex items-center justify-between px-3 border-b border-slate-100 dark:border-[#1c2128]">
                  <span className="text-[12px] font-bold text-slate-500 dark:text-slate-400">Code files</span>
                  <div className="flex items-center gap-0.5">
                    <button className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors" title="Search">
                      <Search className="w-3.5 h-3.5" />
                    </button>
                    <button className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors" title="Toggle">
                      <Code2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
                <div className="flex-1 overflow-hidden">
                  {(status === 'cloning' || status === 'preparing') && files.length === 0 ? (
                    /* Cloning loading state — shown until file_tree arrives */
                    <div className="flex flex-col items-center justify-center h-full gap-3 px-4 py-8">
                      <Loader2 className="w-5 h-5 text-orange-400 animate-spin" />
                      <div className="text-center space-y-1">
                        <p className="text-[12px] font-medium text-slate-500 dark:text-slate-400">
                          {status === 'cloning' ? 'Cloning repository…' : 'Preparing workspace…'}
                        </p>
                        <p className="text-[11px] text-slate-400 dark:text-slate-500">Files will appear here shortly</p>
                      </div>
                      {/* Skeleton rows */}
                      <div className="w-full mt-2 space-y-2 px-2 animate-pulse">
                        {[70, 55, 80, 45, 65, 50, 72].map((w, i) => (
                          <div key={i} className="flex items-center gap-2">
                            <div className="w-3.5 h-3.5 rounded bg-slate-200 dark:bg-slate-700 shrink-0" />
                            <div className={`h-2.5 rounded bg-slate-200 dark:bg-slate-700`} style={{ width: `${w}%` }} />
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                  <FileExplorer
                    files={files}
                    selectedFile={selectedFile}
                    onFileSelect={handleFileSelect}
                    projectName={conversation?.title || 'Project'}
                  />
                  )}
                </div>
              </div>
              {/* Editor area — no duplicate header, just action icons row */}
              <div className="flex-1 min-w-0 bg-white flex flex-col">
                <div className="flex-1 min-h-0">
                  <FileViewer
                    path={selectedFile}
                    content={editedFileContent !== null ? editedFileContent : fileContent}
                    loading={fileLoading}
                    onContentChange={(val) => setEditedFileContent(val)}
                  />
                </div>
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

