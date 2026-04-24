'use client';

// ─────────────────────────────────────────────────────────
//  FileViewer — read-only code editor with line numbers
//  Base44 style: white background, breadcrumb bar, copy button
//  Extracted from workspace/[projectId]/page.js
// ─────────────────────────────────────────────────────────

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { FileText, Copy, Check, Loader2 } from 'lucide-react';

export default function FileViewer({ path, content, loading, onContentChange }) {
  const [copied, setCopied] = useState(false);
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
              <span className={i === arr.length - 1 ? 'text-slate-800 font-semibold' : 'text-slate-500'}>
                {part}
              </span>
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
            {copied ? (
              <Check className="w-4 h-4 text-[#dc5426]" />
            ) : (
              <Copy className="w-4 h-4" />
            )}
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
