'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { 
  Rocket, Terminal, Code2, Puzzle, Settings, Users, 
  ShieldCheck, Cpu, Zap, Layout, MessageSquare, Globe, BookOpen
} from 'lucide-react';
import { cn } from '@/lib/utils';

const navigation = [
  {
    title: 'Get Started',
    items: [
      { name: 'Introducing Lucid AI', href: '/docs/get-started/lucid-ai-intro', icon: Rocket },
      { name: 'Your First Session', href: '/docs/get-started/first-session', icon: Zap },
      { name: 'Tutorial Library', href: '#', icon: BookOpen },
    ],
  },
  {
    title: 'Essential Guidelines',
    items: [
      { name: 'When to Use Lucid AI', href: '#', icon: Cpu },
      { name: 'Instructing Effectively', href: '#', icon: MessageSquare },
      { name: 'Good vs. Bad Instructions', href: '#', icon: Terminal },
      { name: 'Fit into Existing SDLC', href: '#', icon: Layout },
    ],
  },
  {
    title: 'Onboarding',
    items: [
      { name: 'Repo Setup', href: '#', icon: Code2 },
      { name: 'Index a Repository', href: '#', icon: Puzzle },
      { name: 'VPN Configuration', href: '#', icon: Globe },
      { name: 'Knowledge Onboarding', href: '#', icon: BookOpen },
      { name: 'AGENTS.md', href: '#', icon: Settings },
    ],
  },
];

export default function DocsSidebar() {
  const pathname = usePathname();

  return (
    <nav className="space-y-6">
      {navigation.map((group) => (
        <div key={group.title}>
          <h5 className="mb-2 text-[13px] font-bold text-slate-900 dark:text-slate-200 tracking-tight">
            {group.title}
          </h5>
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const isActive = pathname === item.href;
              return (
                <li key={item.name}>
                  <Link
                    href={item.href}
                    className={cn(
                      "group flex items-center gap-2.5 px-2.5 py-[7px] text-[13.5px] rounded-lg transition-all",
                      isActive 
                        ? "bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-400 font-semibold" 
                        : "text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-slate-200 font-medium"
                    )}
                  >
                    <item.icon className={cn(
                      "w-4 h-4 flex-shrink-0",
                      isActive 
                        ? "text-violet-600 dark:text-violet-400" 
                        : "text-slate-400 dark:text-slate-500 group-hover:text-slate-500 dark:group-hover:text-slate-400"
                    )} />
                    <span className="truncate">{item.name}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
