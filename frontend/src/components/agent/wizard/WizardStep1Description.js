'use client';

// ─────────────────────────────────────────────────────────
//  WizardStep1Description — Describe your project
// ─────────────────────────────────────────────────────────

import { useRef } from 'react';
import { cn } from '@/lib/utils';
import { FileText, CloudUpload, Trash2 } from 'lucide-react';

const ACCEPTED_FILE_TYPES = '.pdf,.docx,.doc,.txt';

export default function WizardStep1Description({ description, descriptionFile, onUpdate }) {
  const fileInputRef = useRef(null);

  const handleFileUpload = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    onUpdate({ descriptionFile: { name: f.name, file: f } });
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const f = e.dataTransfer?.files?.[0];
    if (!f) return;
    const ext = f.name.split('.').pop().toLowerCase();
    if (['pdf', 'docx', 'doc', 'txt'].includes(ext)) onUpdate({ descriptionFile: { name: f.name, file: f } });
  };

  const handleDragOver = (e) => { e.preventDefault(); e.stopPropagation(); };

  const handleKeyDown = (e) => {
    if (e.key !== 'Enter') return;
    const ta = e.target;
    const val = ta.value;
    const pos = ta.selectionStart;
    const beforeCursor = val.slice(0, pos);
    const currentLine = beforeCursor.split('\n').pop() || '';

    const numMatch = currentLine.match(/^(\s*)(\d+)\.\s(.*)$/);
    if (numMatch) {
      const [, indent, num, content] = numMatch;
      if (!content.trim()) {
        e.preventDefault();
        const lineStart = pos - currentLine.length;
        const newVal = val.slice(0, lineStart) + '\n' + val.slice(pos);
        onUpdate({ description: newVal });
        setTimeout(() => { ta.selectionStart = ta.selectionEnd = lineStart + 1; }, 0);
        return;
      }
      e.preventDefault();
      const next = `\n${indent}${parseInt(num) + 1}. `;
      const newVal = val.slice(0, pos) + next + val.slice(pos);
      onUpdate({ description: newVal });
      setTimeout(() => { ta.selectionStart = ta.selectionEnd = pos + next.length; }, 0);
      return;
    }

    const bulletMatch = currentLine.match(/^(\s*)([-•])\s(.*)$/);
    if (bulletMatch) {
      const [, indent, bullet, content] = bulletMatch;
      if (!content.trim()) {
        e.preventDefault();
        const lineStart = pos - currentLine.length;
        const newVal = val.slice(0, lineStart) + '\n' + val.slice(pos);
        onUpdate({ description: newVal });
        setTimeout(() => { ta.selectionStart = ta.selectionEnd = lineStart + 1; }, 0);
        return;
      }
      e.preventDefault();
      const next = `\n${indent}${bullet} `;
      const newVal = val.slice(0, pos) + next + val.slice(pos);
      onUpdate({ description: newVal });
      setTimeout(() => { ta.selectionStart = ta.selectionEnd = pos + next.length; }, 0);
    }
  };

  return (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[600px]">
        <div className="text-center mb-6">
          <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Describe your project</h2>
          <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Tell us what you want to build. Be as detailed as you like.</p>
        </div>

        <div className="bg-white dark:bg-slate-800/60 rounded-2xl border border-slate-200 dark:border-slate-700/80 shadow-sm overflow-hidden">
          <textarea
            value={description}
            onChange={(e) => onUpdate({ description: e.target.value })}
            onKeyDown={handleKeyDown}
            placeholder="Describe your project — features, target users, design preferences, technical requirements..."
            rows={8}
            className="w-full px-5 py-4 text-[13px] leading-relaxed bg-transparent text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 outline-none resize-none border-b border-slate-100 dark:border-slate-700/50"
          />

          {/* File upload strip */}
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              'flex items-center gap-3 px-5 py-3 cursor-pointer transition-colors',
              descriptionFile ? 'bg-emerald-50/50 dark:bg-emerald-500/5' : 'hover:bg-slate-50 dark:hover:bg-slate-700/30',
            )}
          >
            {descriptionFile ? (
              <>
                <div className="w-8 h-8 rounded-lg bg-emerald-100 dark:bg-emerald-500/20 flex items-center justify-center shrink-0">
                  <FileText className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] font-medium text-emerald-700 dark:text-emerald-400 truncate">{descriptionFile.name}</p>
                  <p className="text-[10px] text-emerald-500">Uploaded successfully</p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); onUpdate({ descriptionFile: null }); }}
                  className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-colors shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </>
            ) : (
              <>
                <div className="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-700/80 flex items-center justify-center shrink-0">
                  <CloudUpload className="w-4 h-4 text-slate-400 dark:text-slate-500" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] font-medium text-slate-500 dark:text-slate-400">Attach a spec document</p>
                  <p className="text-[10px] text-slate-400 dark:text-slate-500">PDF, DOCX, or TXT</p>
                </div>
                <span className="text-[11px] text-slate-400 dark:text-slate-500 font-medium shrink-0">Browse</span>
              </>
            )}
            <input ref={fileInputRef} type="file" accept={ACCEPTED_FILE_TYPES} onChange={handleFileUpload} className="hidden" />
          </div>
        </div>
      </div>
    </div>
  );
}
