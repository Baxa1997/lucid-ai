'use client';

// ─────────────────────────────────────────────────────────
//  Base44-style Custom Select Dropdown
//  Clean white card with checkmark for selected item
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export default function CustomSelect({
  value,
  onChange,
  options = [],         // [{ value: 'x', label: 'Label' }]
  placeholder = 'Select…',
  className = '',
  size = 'md',          // 'sm' | 'md'
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const selected = options.find(o => o.value === value);
  const label = selected?.label || placeholder;

  const sizeClasses = size === 'sm'
    ? 'px-3 py-2 text-[12px]'
    : 'px-4 py-2.5 text-[14px]';

  return (
    <div className={cn("relative", className)} ref={ref}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          "w-full flex items-center justify-between gap-2 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl text-slate-800 dark:text-slate-200 outline-none hover:border-slate-300 dark:hover:border-[#444c56] transition-colors cursor-pointer text-left",
          open && "border-slate-300 dark:border-[#444c56] shadow-sm",
          sizeClasses,
        )}
      >
        <span className="truncate font-medium">{label}</span>
        <ChevronDown className={cn(
          "w-4 h-4 text-slate-400 shrink-0 transition-transform duration-200",
          open && "rotate-180"
        )} />
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute z-50 mt-1.5 w-full min-w-[200px] bg-white dark:bg-[#1c2128] border border-slate-200 dark:border-[#2d333b] rounded-xl shadow-lg dark:shadow-black/40 overflow-hidden py-1 animate-in fade-in slide-in-from-top-1 duration-150">
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
              className={cn(
                "w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors",
                size === 'sm' ? 'text-[12px]' : 'text-[14px]',
                opt.value === value
                  ? "text-slate-900 dark:text-white font-medium"
                  : "text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.04]"
              )}
            >
              <div className="w-4 shrink-0 flex items-center justify-center">
                {opt.value === value && (
                  <Check className="w-4 h-4 text-slate-900 dark:text-white" strokeWidth={2.5} />
                )}
              </div>
              <span className="truncate">{opt.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
