'use client';

// ─────────────────────────────────────────────────────────
//  WizardStep3Backend — Backend setup selection
// ─────────────────────────────────────────────────────────

import { cn } from '@/lib/utils';
import { Globe, Database, Server } from 'lucide-react';

const BACKEND_OPTS = [
  { id: 'none',     icon: Globe,     label: 'No backend',      sub: 'Frontend only — static site',       bg: 'bg-slate-50 dark:bg-slate-700/80',        color: 'text-slate-500 dark:text-slate-400' },
  { id: 'supabase', icon: Database,  label: 'Use Supabase',    sub: 'Auth, database, storage included',  bg: 'bg-emerald-50 dark:bg-emerald-500/10',    color: 'text-emerald-600 dark:text-emerald-400', badge: '+$25/mo' },
  { id: 'own',      icon: Server,    label: 'Own backend',      sub: 'Connect via MCP server',            bg: 'bg-blue-50 dark:bg-blue-500/10',          color: 'text-blue-600 dark:text-blue-400' },
];

export default function WizardStep3Backend({ backend, mcpUrl, onUpdate }) {
  return (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[552px]">
        <div className="text-center mb-8">
          <h2 className="text-[28px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Backend setup</h2>
          <p className="text-[15px] text-slate-400 dark:text-slate-500 mt-2">How should your project handle data?</p>
        </div>

        <div className="space-y-3">
          {BACKEND_OPTS.map((o) => {
            const Icon = o.icon;
            return (
              <button
                key={o.id}
                onClick={() => onUpdate({ backend: o.id })}
                className={cn(
                  'w-full flex items-center gap-4 px-5 py-4 rounded-2xl transition-all duration-200 text-left',
                  backend === o.id
                    ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                    : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]',
                )}
              >
                <div className={cn('w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors',
                  backend === o.id ? 'border-blue-500 bg-blue-500' : 'border-slate-300 dark:border-slate-600')}>
                  {backend === o.id && <div className="w-2 h-2 rounded-full bg-white" />}
                </div>
                <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center shrink-0', o.bg)}>
                  <span className={o.color}><Icon className="w-5 h-5" /></span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={cn('text-[14px] font-semibold', backend === o.id ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>{o.label}</span>
                    {o.badge && <span className="px-1.5 py-0.5 bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-[10px] font-bold rounded">{o.badge}</span>}
                  </div>
                  <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">{o.sub}</p>
                </div>
              </button>
            );
          })}
        </div>

        {backend === 'own' && (
          <div className="mt-4 animate-fade-in">
            <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">MCP Server URL</label>
            <div className="relative">
              <Server className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <input
                type="url"
                value={mcpUrl}
                onChange={(e) => onUpdate({ mcpUrl: e.target.value })}
                placeholder="https://api.yourbackend.com/mcp"
                className="w-full pl-11 pr-4 py-3 text-sm border border-slate-200 dark:border-slate-700 rounded-2xl bg-white dark:bg-slate-800/60 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 outline-none focus:border-blue-400 dark:focus:border-blue-500 focus:shadow-[0_0_0_3px_rgba(59,130,246,0.08)] transition-all shadow-sm"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
