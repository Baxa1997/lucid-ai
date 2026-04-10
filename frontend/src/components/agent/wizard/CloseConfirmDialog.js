'use client';

// ─────────────────────────────────────────────────────────
//  CloseConfirmDialog — "Discard this project?" modal
// ─────────────────────────────────────────────────────────

import { Trash2 } from 'lucide-react';

export default function CloseConfirmDialog({ onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 dark:bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-2xl w-[380px] mx-4 animate-slide-up overflow-hidden">
        <div className="px-6 pt-6 pb-5">
          <div className="w-10 h-10 rounded-xl bg-red-50 dark:bg-red-500/10 flex items-center justify-center mb-4">
            <Trash2 className="w-5 h-5 text-red-500" />
          </div>
          <h3 className="text-[16px] font-bold text-slate-900 dark:text-slate-100 mb-1">Discard this project?</h3>
          <p className="text-[13px] text-slate-500 dark:text-slate-400">All your progress will be permanently lost.</p>
        </div>
        <div className="flex items-center justify-end gap-2.5 px-6 py-4 bg-slate-50 dark:bg-slate-800/50 border-t border-slate-100 dark:border-slate-800">
          <button onClick={onCancel} className="px-4 py-2 text-[13px] font-medium text-slate-600 dark:text-slate-400 hover:bg-white dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 rounded-lg transition-colors">Keep editing</button>
          <button onClick={onConfirm} className="px-4 py-2 text-[13px] font-semibold text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors shadow-sm">Discard</button>
        </div>
      </div>
    </div>
  );
}
