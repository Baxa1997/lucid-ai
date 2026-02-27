'use client';

import { 
  Plus, MessageSquare, FileText, Settings, Zap,
  LogOut, Grid2X2, ChevronRight
} from 'lucide-react';
import { useRouter, usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import ThemeModeSelector from '@/components/ThemeModeSelector';

const navItems = [
  { label: 'Conversations', icon: MessageSquare, href: '/dashboard/engineer/conversations' },
  { label: 'Integrations', icon: Grid2X2, href: '/dashboard/engineer/integrations' },
  { label: 'Documentation', icon: FileText, href: '/dashboard/engineer/docs' },
  { label: 'Settings', icon: Settings, href: '/dashboard/engineer/settings' },
];

export default function EngineerLayout({ children }) {
  const router = useRouter();
  const pathname = usePathname();

  const isActive = (href) => pathname === href;

  return (
    <div className="h-screen flex bg-[#f0f4f9] dark:bg-slate-950 overflow-hidden transition-colors duration-200">

      {/* ══ SIDEBAR ══ */}
      <aside className="w-[240px] h-full bg-white dark:bg-[#0f1117] border-r border-slate-200 dark:border-slate-800/80 flex flex-col shrink-0 transition-colors duration-200">

        {/* Logo */}
        <div className="px-5 pt-5 pb-5 border-b border-slate-100 dark:border-slate-800/60">
          <button
            onClick={() => router.push('/dashboard/engineer')}
            className="flex items-center gap-2.5"
          >
            <div className="w-7 h-7 bg-blue-600 rounded-md flex items-center justify-center">
              <Zap className="w-3.5 h-3.5 text-white fill-current" />
            </div>
            <span className="font-bold text-slate-900 dark:text-white text-[15px] tracking-tight">Lucid AI</span>
          </button>
        </div>

        {/* + New Project Button */}
        <div className="px-3 pt-4 pb-2">
          <button 
            onClick={() => router.push('/dashboard/engineer')}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 bg-slate-900 dark:bg-white text-white dark:text-slate-900 rounded-lg text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-all active:scale-[0.98]"
          >
            <Plus className="w-3.5 h-3.5" strokeWidth={2.5} />
            New Project
          </button>
        </div>

        {/* Section Label */}
        <div className="px-5 pt-4 pb-1.5">
          <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">
            Workspace
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 space-y-px">
          {navItems.map((item) => {
            const active = isActive(item.href);
            const Icon = item.icon;
            return (
              <button
                key={item.href}
                onClick={() => router.push(item.href)}
                className={cn(
                  "w-full flex items-center gap-2.5 px-3 py-[9px] rounded-lg text-[13px] font-medium transition-all duration-150",
                  active
                    ? "bg-slate-100 dark:bg-white/[0.06] text-slate-900 dark:text-white"
                    : "text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.03] hover:text-slate-700 dark:hover:text-slate-300"
                )}
              >
                <Icon className={cn(
                  "w-4 h-4 shrink-0",
                  active ? "text-slate-700 dark:text-slate-200" : "text-slate-400 dark:text-slate-500"
                )} strokeWidth={active ? 2 : 1.75} />
                <span className="flex-1 text-left">{item.label}</span>
                {active && (
                  <div className="w-1 h-1 rounded-full bg-blue-600 dark:bg-blue-400 shrink-0" />
                )}
              </button>
            );
          })}
        </nav>

        {/* Bottom User */}
        <div className="p-3 border-t border-slate-100 dark:border-slate-800/60">
          <div className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-slate-50 dark:hover:bg-white/[0.03] transition-colors cursor-pointer">
            <div className="w-7 h-7 rounded-md bg-slate-800 dark:bg-slate-700 flex items-center justify-center shrink-0">
              <span className="text-[10px] font-semibold text-white leading-none">AR</span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-medium text-slate-700 dark:text-slate-200 truncate leading-tight">Alex Rivard</p>
              <p className="text-[11px] text-slate-400 dark:text-slate-500 truncate leading-tight">Pro</p>
            </div>
            <button 
              className="p-1 rounded text-slate-300 dark:text-slate-600 hover:text-slate-500 dark:hover:text-slate-400 transition-colors shrink-0"
              title="Sign out"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </aside>

      {/* ══ MAIN CONTENT ══ */}
      <main className="flex-1 overflow-y-scroll">
        <div
          key={pathname}
          className="animate-page-enter h-full"
        >
          {children}
        </div>
      </main>

      {/* ══ FIXED FLOATING THEME TOGGLE ══ */}
      <div className="fixed bottom-6 right-6 z-50">
        <ThemeModeSelector className="w-10 h-10 rounded-xl shadow-lg shadow-slate-900/10 dark:shadow-black/30 hover:shadow-xl hover:scale-105 transition-all duration-200 [&_svg]:w-4 [&_svg]:h-4" />
      </div>
    </div>
  );
}
