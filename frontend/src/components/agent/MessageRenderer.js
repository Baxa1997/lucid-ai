'use client';

// ─────────────────────────────────────────────────────────
//  MessageRenderer — Lightweight markdown + tool call renderer
//  No external deps. Handles bold, code, lists, code blocks.
// ─────────────────────────────────────────────────────────

import { useState } from 'react';
import { cn } from '@/lib/utils';
import {
  FileText, Terminal, Edit3, Search, ChevronDown, ChevronRight,
} from 'lucide-react';

/**
 * Parse and render markdown-like content with:
 * - **bold**, `inline code`, ```code blocks```
 * - Bullet lists (• or -)
 * - Collapsible tool call sections
 */
export function MessageRenderer({ content, toolCalls = [] }) {
  if (!content && toolCalls.length === 0) return null;

  return (
    <div className="space-y-2">
      {/* Tool calls */}
      {toolCalls.length > 0 && (
        <div className="space-y-1">
          {toolCalls.map((tool, i) => (
            <ToolCallBubble key={i} tool={tool} />
          ))}
        </div>
      )}

      {/* Text content */}
      {content && <MarkdownText text={content} />}
    </div>
  );
}

// ── Tool Call Bubble ─────────────────────────────────────

const TOOL_ICONS = {
  read: FileText,
  write: Edit3,
  edit: Edit3,
  bash: Terminal,
  grep: Search,
  glob: Search,
  ls: Search,
  multiedit: Edit3,
};

const TOOL_COLORS = {
  read: 'bg-sky-50 border-sky-200 text-sky-700',
  write: 'bg-violet-50 border-violet-200 text-violet-700',
  edit: 'bg-violet-50 border-violet-200 text-violet-700',
  bash: 'bg-emerald-50 border-emerald-200 text-emerald-700',
  grep: 'bg-amber-50 border-amber-200 text-amber-700',
  glob: 'bg-amber-50 border-amber-200 text-amber-700',
  ls: 'bg-amber-50 border-amber-200 text-amber-700',
  multiedit: 'bg-violet-50 border-violet-200 text-violet-700',
};

const TOOL_LABELS = {
  read: 'Read',
  write: 'Write',
  edit: 'Edit',
  bash: 'Run command',
  grep: 'Search',
  glob: 'Find files',
  ls: 'List files',
  multiedit: 'Multi-edit',
};

function ToolCallBubble({ tool }) {
  const [expanded, setExpanded] = useState(false);
  const toolType = tool.type || 'read';
  const Icon = TOOL_ICONS[toolType] || FileText;
  const colors = TOOL_COLORS[toolType] || 'bg-slate-50 border-slate-200 text-slate-600';
  const label = TOOL_LABELS[toolType] || toolType;

  return (
    <div className={cn(
      "rounded-lg border px-3 py-1.5 text-xs transition-all",
      colors,
    )}>
      <button
        onClick={() => tool.detail && setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left"
      >
        <Icon className="w-3.5 h-3.5 shrink-0" />
        <span className="font-semibold">{label}</span>
        {tool.target && (
          <span className="font-mono opacity-70 truncate">{tool.target}</span>
        )}
        {tool.detail && (
          expanded
            ? <ChevronDown className="w-3 h-3 ml-auto shrink-0 opacity-50" />
            : <ChevronRight className="w-3 h-3 ml-auto shrink-0 opacity-50" />
        )}
      </button>
      {expanded && tool.detail && (
        <pre className="mt-2 p-2 bg-white/60 rounded text-[11px] font-mono whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto border border-current/10">
          {tool.detail}
        </pre>
      )}
    </div>
  );
}

// ── Markdown Text Renderer ───────────────────────────────

function MarkdownText({ text }) {
  // Split into code blocks and regular text
  const parts = text.split(/(```[\s\S]*?```)/g);

  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {parts.map((part, i) => {
        // Code block
        if (part.startsWith('```') && part.endsWith('```')) {
          const inner = part.slice(3, -3);
          const firstNewline = inner.indexOf('\n');
          const lang = firstNewline > 0 ? inner.slice(0, firstNewline).trim() : '';
          const code = firstNewline > 0 ? inner.slice(firstNewline + 1) : inner;

          return (
            <div key={i} className="relative group">
              {lang && (
                <div className="absolute top-0 right-0 px-2 py-0.5 text-[10px] font-mono text-slate-400 bg-slate-800 rounded-bl-md rounded-tr-lg">
                  {lang}
                </div>
              )}
              <pre className="bg-slate-900 text-slate-200 rounded-xl px-4 py-3 text-[12px] font-mono overflow-x-auto leading-relaxed">
                <code>{code}</code>
              </pre>
            </div>
          );
        }

        // Regular text with inline formatting
        return <InlineText key={i} text={part} />;
      })}
    </div>
  );
}

function InlineText({ text }) {
  const lines = text.split('\n');

  return (
    <>
      {lines.map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return <br key={i} />;

        // Bullet list
        if (trimmed.startsWith('• ') || trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={i} className="flex items-start gap-2 pl-1">
              <span className="text-blue-400 mt-1 text-xs">•</span>
              <span>{renderInline(trimmed.slice(2))}</span>
            </div>
          );
        }

        // Numbered list
        const numMatch = trimmed.match(/^(\d+)\.\s+(.+)/);
        if (numMatch) {
          return (
            <div key={i} className="flex items-start gap-2 pl-1">
              <span className="text-blue-400 text-xs font-semibold mt-0.5 min-w-[16px]">{numMatch[1]}.</span>
              <span>{renderInline(numMatch[2])}</span>
            </div>
          );
        }

        return <p key={i}>{renderInline(trimmed)}</p>;
      })}
    </>
  );
}

function renderInline(text) {
  // Process **bold** and `inline code`
  const parts = [];
  let remaining = text;
  let key = 0;

  while (remaining.length > 0) {
    // **bold**
    const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
    // `inline code`
    const codeMatch = remaining.match(/`([^`]+)`/);

    let match = null;
    let matchType = null;

    if (boldMatch && (!codeMatch || boldMatch.index <= codeMatch.index)) {
      match = boldMatch;
      matchType = 'bold';
    } else if (codeMatch) {
      match = codeMatch;
      matchType = 'code';
    }

    if (!match) {
      parts.push(<span key={key++}>{remaining}</span>);
      break;
    }

    // Text before match
    if (match.index > 0) {
      parts.push(<span key={key++}>{remaining.slice(0, match.index)}</span>);
    }

    // The match itself
    if (matchType === 'bold') {
      parts.push(<strong key={key++} className="font-semibold">{match[1]}</strong>);
    } else {
      parts.push(
        <code key={key++} className="px-1.5 py-0.5 bg-slate-100 border border-slate-200 rounded text-[12px] font-mono text-pink-600">
          {match[1]}
        </code>
      );
    }

    remaining = remaining.slice(match.index + match[0].length);
  }

  return parts;
}

export default MessageRenderer;
