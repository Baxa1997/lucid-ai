'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Toast Notification Component
//  Lightweight, auto-dismiss toast for auth events
// ─────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import { CheckCircle, LogOut as LogOutIcon, X } from 'lucide-react';

/**
 * Usage:
 *   <Toast message="Signed in successfully!" type="success" onDone={() => {}} />
 *
 * Types: 'success' | 'logout' | 'error'
 * Auto-dismisses after 3 seconds.
 */
export default function Toast({ message, type = 'success', onDone }) {
  const [visible, setVisible] = useState(false);
  const [exiting, setExiting] = useState(false);

  useEffect(() => {
    // Slide in
    requestAnimationFrame(() => setVisible(true));

    // Auto dismiss after 3s
    const timer = setTimeout(() => {
      setExiting(true);
      setTimeout(() => onDone?.(), 300);
    }, 3000);

    return () => clearTimeout(timer);
  }, [onDone]);

  const dismiss = () => {
    setExiting(true);
    setTimeout(() => onDone?.(), 300);
  };

  const icon =
    type === 'logout' ? (
      <LogOutIcon className="w-4 h-4 text-amber-500 shrink-0" />
    ) : (
      <CheckCircle className="w-4 h-4 text-emerald-500 shrink-0" />
    );

  const borderColor =
    type === 'logout' ? 'border-amber-200 dark:border-amber-500/30' : 'border-emerald-200 dark:border-emerald-500/30';

  return (
    <div
      className={`fixed top-5 right-5 z-[9999] transition-all duration-300 ease-out ${
        visible && !exiting
          ? 'translate-x-0 opacity-100'
          : 'translate-x-8 opacity-0'
      }`}
    >
      <div
        className={`flex items-center gap-3 px-4 py-3 bg-white dark:bg-slate-900 border ${borderColor} rounded-xl shadow-lg shadow-black/5 dark:shadow-black/30 min-w-[260px] max-w-[360px]`}
      >
        {icon}
        <span className="text-sm font-medium text-slate-700 dark:text-slate-200 flex-1">
          {message}
        </span>
        <button
          onClick={dismiss}
          className="p-0.5 rounded text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors shrink-0"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
