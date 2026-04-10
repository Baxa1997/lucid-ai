'use client';

// ─────────────────────────────────────────────────────────
//  TabBar — Generic pill-style tab switcher
//  Used by AgentPanel (chat/terminal) and BuildProgressPanel
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';

/**
 * @param {Array<{ id: string, label: string, icon?: React.ComponentType, badge?: number|string }>} tabs
 * @param {string} activeTab
 * @param {(id: string) => void} onTabChange
 * @param {string} [className]
 * @param {'blue'|'emerald'|'slate'} [activeColor] - Default 'blue'
 */
export default function TabBar({ tabs, activeTab, onTabChange, className, activeColor = 'blue' }) {
  const activeStyles = {
    blue:    'bg-blue-50 text-blue-600 border border-blue-200',
    emerald: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
    slate:   'bg-slate-100 text-slate-800 border border-slate-200',
  };

  return (
    <div className={cn('flex items-center gap-1', className)}>
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all',
              isActive
                ? activeStyles[activeColor] || activeStyles.blue
                : 'text-slate-400 hover:text-slate-600 hover:bg-slate-100',
            )}
          >
            {Icon && <Icon className="w-3.5 h-3.5" />}
            {tab.label}
            {tab.badge != null && tab.badge > 0 && (
              <span className="ml-0.5 px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10px] tabular-nums text-slate-500">
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
