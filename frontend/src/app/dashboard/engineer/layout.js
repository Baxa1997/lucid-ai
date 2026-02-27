'use client';

import { 
  Plus, MessageSquare, List, Link2, FileText, Settings, Zap,
  LogOut, Grid2X2
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
      <aside className="w-[240px] h-full bg-white dark:bg-slate-900 border-r border-slate-200/70 dark:border-slate-800 flex flex-col shrink-0 transition-colors duration-200">
        

        {/* Logo */}
        <div className="px-5 pt-5 pb-4">
          <button
            onClick={() => router.push('/dashboard/engineer')}
            className="flex items-center gap-2.5 group"
          >
            <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-gradient-to-br from-blue-600 to-blue-700 rounded-lg flex items-center justify-center shadow-sm">
              <Zap className="w-4 h-4 text-white fill-current" />
            </div>
            <span className="font-extrabold text-slate-900 dark:text-slate-100 text-base tracking-tight">Lucid AI</span>
          </div>
          </button>
        </div>

        {/* + New Project Button */}
        <div className="px-3.5 mb-5">
          <button 
            onClick={() => router.push('/dashboard/engineer')}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-xl text-[15px] font-bold hover:bg-blue-700 transition-all shadow-sm shadow-blue-600/20 active:scale-[0.98]"
          >
            <Plus className="w-4 h-4" strokeWidth={2.5} />
            New Project
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3.5 space-y-0.5">
          {navItems.map((item) => {
            const active = isActive(item.href);
            const Icon = item.icon;
            return (
              <button
                key={item.href}
                onClick={() => router.push(item.href)}
                className={cn(
                  "w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-[15px] font-medium transition-all duration-200",
                  active
                    ? "bg-blue-50 text-blue-600 font-semibold"
                    : "text-slate-500 hover:bg-slate-50 hover:text-slate-700"
                )}
              >
                <Icon className={cn(
                  "w-[18px] h-[18px] shrink-0",
                  active ? "text-blue-600" : "text-slate-400"
                )} />
                {item.label}
              </button>
            );
          })}
        </nav>

        {/* Theme Selector */}
        <div className="px-3.5 mb-2">
          <div className="flex items-center justify-between px-3 py-2.5">
            <span className="text-[12px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Theme</span>
            <ThemeModeSelector />
          </div>
        </div>

        {/* Bottom User */}
        <div className="p-3.5 border-t border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2.5 px-2 py-2">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-slate-700 to-slate-800 flex items-center justify-center shrink-0 shadow-sm">
              <span className="text-[12px] font-bold text-white">AR</span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[15px] font-semibold text-slate-800 dark:text-slate-200 truncate leading-tight">Alex Rivard</p>
              <p className="text-[12px] text-slate-400 dark:text-slate-500 truncate leading-tight">Pro Developer</p>
            </div>
            <button className="p-1 rounded-md text-slate-300 hover:text-slate-500 hover:bg-slate-50 transition-colors shrink-0">
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
    </div>
  );
}
