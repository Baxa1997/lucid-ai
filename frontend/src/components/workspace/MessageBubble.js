'use client';

// ─────────────────────────────────────────────────────────
//  MessageBubble — Base44-style chat message renderer.
//  Handles: user, agent, system, push_result, plan roles.
//  Extracted from workspace/[projectId]/page.js
// ─────────────────────────────────────────────────────────

import { useState, useEffect } from 'react';
import { cn } from '@/lib/utils';
import {
  Sparkles, FileText, FileCheck2, Copy, Check, Pencil,
  GitBranch, GitPullRequest, ExternalLink, AlertCircle, TriangleAlert,
} from 'lucide-react';

// ── Relative time helper (Base44 style) ──────────────────
export function relativeTime(ts) {
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

// Module-level Set: persists across re-renders, never re-animates finished messages
const _animatedMsgIds = new Set();

// ── PlanBubble — structured plan card ────────────────────
function PlanBubble({ msg }) {
  const { planData, fileWrites = [] } = msg;
  if (!planData) return null;

  const introText = (planData.intro || '').replace(/\*\*(.*?)\*\*/g, '$1');

  return (
    <div className="flex items-start gap-3 px-4 py-3 animate-in fade-in duration-300">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 mt-0.5 shadow-sm shadow-orange-500/20">
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="flex-1 min-w-0">
        {introText && (
          <p className="text-[14px] text-slate-700 dark:text-slate-200 mb-3 leading-relaxed">
            {introText}
          </p>
        )}

        <div className="border border-slate-100 dark:border-[#2d333b] rounded-xl overflow-hidden mb-2.5">
          <div className="px-4 py-2 bg-slate-50 dark:bg-[#161b22] border-b border-slate-100 dark:border-[#2d333b]">
            <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Plan</span>
          </div>
          <div className="px-4 py-3 bg-white dark:bg-[#0d1117] space-y-3.5">
            {planData.features?.length > 0 && (
              <div>
                <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5">Key Features</p>
                <div className="space-y-1">
                  {planData.features.map((f, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-slate-300 dark:text-slate-600 text-[12px] mt-0.5 shrink-0">•</span>
                      <span className="text-[13px] text-slate-600 dark:text-slate-300 leading-snug">{f}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {planData.design && (
              <p className="text-[13px] text-slate-400 dark:text-slate-500 italic">{planData.design}</p>
            )}
            {planData.entities?.length > 0 && (
              <div>
                <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5">Entities</p>
                <div className="space-y-1">
                  {planData.entities.map((e, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-slate-300 dark:text-slate-600 text-[12px] mt-0.5 shrink-0">•</span>
                      <span className="text-[13px] leading-snug">
                        <span className="font-medium text-slate-700 dark:text-slate-200">{e.name}</span>
                        {e.fields && <span className="text-slate-400 dark:text-slate-500"> — {e.fields}</span>}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {planData.pages?.length > 0 && (
              <div>
                <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5">Pages & Components</p>
                <div className="space-y-1">
                  {planData.pages.map((p, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-slate-300 dark:text-slate-600 text-[12px] mt-0.5 shrink-0">•</span>
                      <span className="text-[13px] text-slate-600 dark:text-slate-300 leading-snug">{p.name}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <p className="text-[13px] text-slate-500 dark:text-slate-400 mb-2">Let me build this now.</p>

        {fileWrites.length > 0 && (
          <div className="space-y-1 mb-1">
            {fileWrites.map((fw, i) => (
              <div key={i} className="flex items-center gap-2 text-[12px] text-slate-500 dark:text-slate-400 animate-in fade-in duration-200">
                <FileCheck2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                <span>Wrote <span className="font-mono text-slate-600 dark:text-slate-300">{fw.filename}</span></span>
              </div>
            ))}
          </div>
        )}

        <div className="mt-1.5 text-[11px] text-slate-400 dark:text-slate-500">{relativeTime(msg.ts)}</div>
      </div>
    </div>
  );
}

// ── Inline content renderer for agent messages ────────────
function renderAgentContent(text) {
  const lines = text.split('\n');
  const elements = [];
  let inChangedFiles = false;
  let inCodeBlock = false;
  let codeLines = [];

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
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        inCodeBlock = false;
        const code = codeLines.join('\n');
        codeLines = [];
        elements.push(
          <pre key={i} className="font-mono text-xs bg-slate-50 dark:bg-slate-800/60 rounded-lg px-3 py-2 my-2 text-slate-600 dark:text-slate-400 overflow-x-auto border border-slate-100 dark:border-slate-700/30">
            {code}
          </pre>,
        );
        return;
      }
      inCodeBlock = true;
      return;
    }
    if (inCodeBlock) { codeLines.push(line); return; }

    if (line.toLowerCase().includes('changed files:')) {
      inChangedFiles = true;
      elements.push(
        <div key={i} className="mt-3 mb-1.5 flex items-center gap-2">
          <FileText className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-xs uppercase font-bold tracking-wider text-slate-400 dark:text-slate-500">Changed files</span>
        </div>,
      );
      return;
    }

    if (inChangedFiles && line.trim()) {
      const trimmed = line.trim();
      let color = 'text-slate-500'; let icon = '•';
      if (trimmed.startsWith('+') || trimmed.includes('(new)'))      { color = 'text-emerald-500'; icon = '+'; }
      else if (trimmed.startsWith('~') || trimmed.includes('(modified)')) { color = 'text-amber-500';  icon = '~'; }
      else if (trimmed.startsWith('-') || trimmed.includes('(deleted)'))  { color = 'text-red-400';    icon = '−'; }
      const fileName = trimmed.replace(/^[+~-]\s*/, '').replace(/\(new\)|\(modified\)|\(deleted\)/, '').trim();
      elements.push(
        <div key={i} className="flex items-center gap-2 py-0.5 pl-1">
          <span className={cn('font-mono text-xs font-bold w-3 text-center', color)}>{icon}</span>
          <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{fileName}</span>
        </div>,
      );
      return;
    }

    if (line.trim().startsWith('✅')) {
      elements.push(
        <div key={i} className="flex items-start gap-1.5 py-0.5 min-w-0">
          <span className="shrink-0 text-sm leading-5">✅</span>
          <span className="text-slate-500 dark:text-slate-400 text-sm break-words min-w-0 flex-1 leading-5">{line.trim().replace(/^✅\s*/, '')}</span>
        </div>,
      );
      return;
    }

    const numberedMatch = line.match(/^\s*(\d+)\.\s+(.*)/);
    if (numberedMatch) {
      elements.push(
        <div key={i} className="flex items-start gap-2 py-0.5 min-w-0">
          <span className="shrink-0 text-slate-400 font-mono text-xs mt-0.5 w-4 text-right">{numberedMatch[1]}.</span>
          <span className="text-slate-700 dark:text-slate-300 break-words min-w-0 flex-1">{formatInline(numberedMatch[2])}</span>
        </div>,
      );
      return;
    }

    if (line.trim().startsWith('- ') || line.trim().startsWith('• ') || line.trim().startsWith('* ')) {
      const bulletContent = line.trim().replace(/^[-•*]\s*/, '');
      elements.push(
        <div key={i} className="flex items-start gap-2 py-0.5 min-w-0">
          <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-slate-400 dark:bg-slate-500" />
          <span className="text-slate-700 dark:text-slate-300 break-words min-w-0 flex-1">{formatInline(bulletContent)}</span>
        </div>,
      );
      return;
    }

    if (!line.trim()) {
      inChangedFiles = false;
      elements.push(<div key={i} className="h-2" />);
      return;
    }

    elements.push(
      <div key={i} className="py-0.5 text-slate-700 dark:text-slate-300 break-words">
        {formatInline(line)}
      </div>,
    );
  });

  return elements;
}

// ── Main MessageBubble dispatcher ─────────────────────────
export default function MessageBubble({ msg, isLatest }) {
  const [copied, setCopied] = useState(false);

  const needsAnimate =
    msg.role === 'agent' &&
    !msg.fromHistory &&
    !msg.messageType &&
    Boolean(msg.content) &&
    !_animatedMsgIds.has(msg.id);

  const [displayedText, setDisplayedText] = useState(() => (needsAnimate ? '' : msg.content || ''));
  const [isAnimating, setIsAnimating] = useState(needsAnimate);

  useEffect(() => {
    if (!needsAnimate) return;
    const fullText = msg.content || '';
    if (!fullText) { setIsAnimating(false); _animatedMsgIds.add(msg.id); return; }
    let i = 0;
    const CHARS_PER_TICK = 4;
    const tick = setInterval(() => {
      i = Math.min(i + CHARS_PER_TICK, fullText.length);
      setDisplayedText(fullText.slice(0, i));
      if (i >= fullText.length) {
        clearInterval(tick);
        setIsAnimating(false);
        _animatedMsgIds.add(msg.id);
      }
    }, 16);
    return () => clearInterval(tick);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Skip thinking/tool messages entirely
  if (msg.role === 'thinking' || msg.role === 'tool' || (msg.role === 'assistant' && msg.toolName)) return null;

  // Structured plan card
  if (msg.messageType === 'plan') return <PlanBubble msg={msg} />;

  // ── User message ──────────────────────────────────────
  if (msg.role === 'user') {
    const cleanContent =
      msg.content
        ?.replace(/\[LUCID_PROJECT\][\s\S]*?(?=\n\n(?:I need|Create)|\n)/i, '')
        .replace(/\[LUCID_PROJECT\][\s\S]*?(?=\n\n|\n)/i, '')
        .trim() || msg.content;

    const handleCopy = () => {
      navigator.clipboard.writeText(cleanContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    };

    return (
      <div className="flex items-start gap-2.5 px-3 py-2 w-full justify-end">
        <div className="max-w-[88%] flex flex-col items-end">
          <div className="bg-[#f1f2f6] dark:bg-slate-700/80 rounded-xl px-3 py-2.5 relative group">
            <p className="text-[13px] leading-relaxed whitespace-pre-wrap text-[#1f2937] dark:text-slate-100">
              {cleanContent}
            </p>
            <div className="mt-2 flex items-center gap-1.5 text-[10px] text-slate-400 dark:text-slate-500">
              <span>{relativeTime(msg.ts)}</span>
              <div className="flex items-center gap-0.5 ml-auto">
                <button onClick={handleCopy} className="p-0.5 hover:text-slate-600 dark:hover:text-slate-300 transition-colors" title="Copy">
                  {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                </button>
                <button className="p-0.5 hover:text-slate-600 dark:hover:text-slate-300 transition-colors" title="Edit">
                  <Pencil className="w-3 h-3" />
                </button>
                <span className="text-slate-300 dark:text-slate-600 text-[10px] font-medium cursor-pointer hover:text-slate-500 dark:hover:text-slate-400 transition-colors">
                  Revert
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── Push result ───────────────────────────────────────
  if (msg.role === 'push_result') {
    return (
      <div className="flex items-start gap-3 px-4 py-2 animate-in fade-in duration-300">
        <div className="mt-0.5 text-emerald-500 shrink-0">
          <Check className="w-5 h-5" />
        </div>
        <div className="flex-1 space-y-1.5">
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">✅ Changes pushed successfully</p>
          <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <GitBranch className="w-3.5 h-3.5" />
            <span className="font-mono">{msg.branch}</span>
            {msg.newBranch && <span className="text-[10px] font-semibold text-blue-500">(new branch)</span>}
          </div>
          {msg.content && (
            <pre className="font-mono text-[11px] text-slate-400 dark:text-slate-500 leading-relaxed whitespace-pre-wrap">
              {msg.content}
            </pre>
          )}
          {msg.prUrl && (
            <a href={msg.prUrl} target="_blank" rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline mt-1">
              <GitPullRequest className="w-3.5 h-3.5" />
              Create Pull Request
              <ExternalLink className="w-3 h-3 opacity-60" />
            </a>
          )}
        </div>
      </div>
    );
  }

  // ── System message ────────────────────────────────────
  if (msg.role === 'system') {
    const isWarning = msg.content.toLowerCase().includes('limit') || msg.content.toLowerCase().includes('warning');
    const isError   = msg.content.toLowerCase().includes('error') || msg.content.toLowerCase().includes('failed');

    if (isWarning || isError) {
      return (
        <div className="px-4 py-2 flex justify-center animate-in fade-in zoom-in-95 duration-300">
          <div className={cn(
            'flex items-center gap-3 px-4 py-2.5 rounded-xl border shadow-sm max-w-[85%]',
            isError
              ? 'bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-900/50 text-red-700 dark:text-red-400'
              : 'bg-amber-50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900/50 text-amber-700 dark:text-amber-400',
          )}>
            {isError ? <AlertCircle className="w-4 h-4 shrink-0" /> : <TriangleAlert className="w-4 h-4 shrink-0" />}
            <span className="text-xs font-semibold leading-relaxed">{msg.content}</span>
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

  // ── Agent message — Base44 style: transparent, avatar left ──
  return (
    <div className="flex items-start gap-3 px-4 py-4 w-full animate-in fade-in duration-300">
      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center shrink-0 mt-0.5 shadow-sm shadow-orange-500/20">
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-slate-800 dark:text-slate-200 text-[13px]">Lucid AI</span>
        </div>

        {(displayedText || msg.content) && (
          <div className="text-[13px] leading-relaxed text-slate-700 dark:text-slate-300">
            {renderAgentContent(isAnimating ? displayedText : msg.content || '')}
            {isAnimating && (
              <span className="inline-block w-[2px] h-[1em] bg-slate-500 dark:bg-slate-400 ml-0.5 align-middle animate-blink" />
            )}
          </div>
        )}

        {/* File-write rows */}
        {msg.fileWrites?.length > 0 && (
          <div className="space-y-1 mt-2">
            {msg.fileWrites.map((fw, i) => {
              const shortName = fw.filename.split('/').pop();
              const isEdit = fw.action === 'edit' || fw.action === 'multiedit';
              return (
                <div key={i} className="flex items-center gap-2 text-[13px]">
                  <FileText className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                  <span className="text-slate-500 dark:text-slate-400">{isEdit ? 'Edited' : 'Wrote'}</span>
                  <span className="inline-flex items-center px-2 py-0.5 bg-[#f1f3f5] dark:bg-slate-700/80 rounded text-[12px] font-medium text-slate-700 dark:text-slate-200 border border-slate-200/80 dark:border-slate-600/50">
                    {shortName}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">{relativeTime(msg.ts)}</div>
      </div>
    </div>
  );
}
