'use client';

import Link from 'next/link';
import { Search, Sparkles, Box } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import ThemeToggle from './ThemeToggle';

const tabs = [
  { name: 'Documentation', href: '/docs' },
  { name: 'Enterprise', href: '#' },
  { name: 'Use Cases', href: '#' },
  { name: 'API Reference', href: '#' },
  { name: 'Release Notes', href: '#' },
];

export default function DocsHeader() {
  const pathname = usePathname();

  return (
    <header className="fixed top-0 left-0 right-0 z-50 bg-white dark:bg-slate-950 border-b border-slate-200 dark:border-slate-800 transition-colors duration-200">
      {/* Row 1: Logo, Search, Actions */}
      <div className="flex items-center justify-between h-[52px] px-5">
        {/* Left: Logo + Search */}
        <div className="flex items-center gap-6">
          <Link href="/" className="flex items-center gap-2">
            <div className="w-7 h-7 bg-gradient-to-br from-violet-600 to-indigo-600 rounded-lg flex items-center justify-center text-white">
              <Box className="w-4 h-4 stroke-[2.5]" />
            </div>
            <span className="text-[16px] font-bold tracking-tight text-slate-900 dark:text-slate-100">Lucid AI <span className="text-slate-400 dark:text-slate-500 font-normal">Docs</span></span>
          </Link>

          <div className="hidden md:flex items-center relative">
            <div className="absolute left-3 text-slate-400 dark:text-slate-500">
              <Search className="w-3.5 h-3.5" />
            </div>
            <input 
              type="text" 
              placeholder="Search..." 
              className={cn(
                "pl-9 pr-10 py-1.5 w-[280px] text-[13px] rounded-lg transition-all",
                "bg-slate-50 border border-slate-200 text-slate-700 placeholder:text-slate-400",
                "focus:outline-none focus:ring-2 focus:ring-violet-500/20 focus:border-violet-500",
                "dark:bg-slate-900 dark:border-slate-700 dark:text-slate-300 dark:placeholder:text-slate-500",
                "dark:focus:ring-violet-400/20 dark:focus:border-violet-400"
              )}
            />
            <div className="absolute right-2.5 px-1 py-0.5 border border-slate-200 dark:border-slate-700 rounded text-[10px] text-slate-400 dark:text-slate-500 font-medium bg-white dark:bg-slate-800">
              ⌘K
            </div>
          </div>
        </div>

        {/* Right: Actions */}
        <div className="flex items-center gap-2">
          <button className="flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium text-slate-600 dark:text-slate-400 hover:text-violet-600 dark:hover:text-violet-400 hover:bg-violet-50 dark:hover:bg-violet-500/10 rounded-lg transition-all">
            <Sparkles className="w-3.5 h-3.5 text-violet-500 dark:text-violet-400" />
            Ask AI
          </button>

          <span className="text-slate-300 dark:text-slate-700 hidden md:inline">|</span>
          
          <Link href="#" className="text-[13px] font-medium text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 px-2 py-1.5 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors hidden md:block">
            Support
          </Link>

          <Link href="/" className="bg-gradient-to-r from-violet-600 to-indigo-600 text-white text-[13px] font-semibold px-4 py-1.5 rounded-lg hover:shadow-lg hover:shadow-violet-500/25 transition-all">
            Lucid AI →
          </Link>
          
          <ThemeToggle />
        </div>
      </div>

      {/* Row 2: Navigation Tabs */}
      <div className="flex items-center gap-7 px-5 h-[44px] border-t border-slate-100 dark:border-slate-800/50">
        {tabs.map((tab) => {
          const isActive = pathname === tab.href || (tab.href !== '/' && tab.href !== '#' && pathname.startsWith(tab.href));
          return (
            <Link 
              key={tab.name} 
              href={tab.href}
              className={cn(
                "text-[14px] font-medium transition-colors relative h-full flex items-center whitespace-nowrap",
                isActive 
                  ? "text-slate-900 dark:text-slate-100 font-semibold" 
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
              )}
            >
              {tab.name}
              {isActive && (
                <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-violet-600 dark:bg-violet-500" />
              )}
            </Link>
          );
        })}
      </div>
    </header>
  );
}
