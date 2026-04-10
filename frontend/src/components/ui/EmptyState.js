'use client';

// ─────────────────────────────────────────────────────────
//  EmptyState — Centered icon + heading + sub-text
//  Repeated pattern in ChatTab, FilesList, FileTree
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';

/**
 * @param {React.ComponentType} icon  - Lucide icon component
 * @param {string} title
 * @param {string} [description]
 * @param {string} [iconClassName]    - Extra classes for the icon wrapper
 * @param {string} [className]        - Outer wrapper classes
 */
export default function EmptyState({ icon: Icon, title, description, iconClassName, className }) {
  return (
    <div className={cn('flex flex-col items-center justify-center h-full text-center px-6', className)}>
      <div className={cn(
        'w-14 h-14 rounded-2xl flex items-center justify-center mb-4',
        iconClassName || 'bg-blue-50 border border-blue-200',
      )}>
        <Icon className="w-6 h-6 text-blue-600" />
      </div>
      <p className="text-sm font-semibold text-slate-700">{title}</p>
      {description && (
        <p className="text-xs text-slate-400 mt-1.5 max-w-[260px] leading-relaxed">
          {description}
        </p>
      )}
    </div>
  );
}
