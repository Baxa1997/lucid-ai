'use client';

import { 
  Plus, MessageSquare, FileText, Settings, Zap,
  LogOut, Grid2X2
} from 'lucide-react';
import { useRouter, usePathname } from 'next/navigation';
import { useState, useEffect, useCallback } from 'react';
import { cn } from '@/lib/utils';
import ThemeModeSelector from '@/components/ThemeModeSelector';
import Toast from '@/components/Toast';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

const navItems = [
  { label: 'Conversations', icon: MessageSquare, href: '/dashboard/engineer/conversations' },
  { label: 'Integrations', icon: Grid2X2, href: '/dashboard/engineer/integrations' },
  { label: 'Documentation', icon: FileText, href: '/dashboard/engineer/docs' },
  { label: 'Settings', icon: Settings, href: '/dashboard/engineer/settings' },
];

export default function EngineerLayout({ children }) {
  const router = useRouter();
  const pathname = usePathname();
  const supabase = getSupabaseBrowserClient();

  // ── User state ──
  const [user, setUser] = useState(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  // ── Toast state ──
  const [toast, setToast] = useState(null);

  // Fetch user on mount
  useEffect(() => {
    supabase.auth.getUser().then(({ data: { user } }) => {
      if (user) {
        setUser(user);
      }
    });

    // Check for sign-in toast flag (one-time)
    if (typeof window !== 'undefined') {
      const justSignedIn = sessionStorage.getItem('lucid-just-signed-in');
      if (justSignedIn) {
        sessionStorage.removeItem('lucid-just-signed-in');
        setToast({ message: 'Signed in successfully!', type: 'success' });
      }

      const justSignedOut = sessionStorage.getItem('lucid-just-signed-out');
      if (justSignedOut) {
        sessionStorage.removeItem('lucid-just-signed-out');
        // Won't show here since we redirect to /login, but kept for safety
      }
    }
  }, [supabase]);

  // Derive display info from Supabase user
  const displayName = user?.user_metadata?.full_name || user?.user_metadata?.name || user?.email?.split('@')[0] || 'User';
  const displayEmail = user?.email || '';
  const avatarUrl = user?.user_metadata?.avatar_url || null;
  const initials = displayName
    .split(' ')
    .map((w) => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  // ── Logout handler ──
  const handleLogout = useCallback(async () => {
    setIsLoggingOut(true);
    try {
      await supabase.auth.signOut();

      // Clear auth cookies
      document.cookie = 'sb-access-token=; path=/; max-age=0';
      document.cookie = 'sb-refresh-token=; path=/; max-age=0';

      // Set flag for one-time logout toast on login page
      sessionStorage.setItem('lucid-just-signed-out', 'true');

      router.push('/login');
    } catch (err) {
      console.error('Logout failed:', err);
      setIsLoggingOut(false);
    }
  }, [supabase, router]);

  const isActive = (href) => pathname === href;

  return (
    <div className="h-screen flex bg-[#f0f4f9] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">

      {/* ══ TOAST ══ */}
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onDone={() => setToast(null)}
        />
      )}

      {/* ══ SIDEBAR ══ */}
      <aside className="w-[240px] h-full bg-white dark:bg-[#0d1117] border-r border-slate-200 dark:border-slate-800/80 flex flex-col shrink-0 transition-colors duration-200">

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

        {/* Bottom User + Logout */}
        <div className="p-3 border-t border-slate-100 dark:border-slate-800/60">
          <div className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg">
            {/* Avatar */}
            {avatarUrl ? (
              <img 
                src={avatarUrl} 
                alt={displayName}
                className="w-7 h-7 rounded-md shrink-0 object-cover"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="w-7 h-7 rounded-md bg-slate-800 dark:bg-slate-700 flex items-center justify-center shrink-0">
                <span className="text-[10px] font-semibold text-white leading-none">{initials}</span>
              </div>
            )}

            {/* User info */}
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-medium text-slate-700 dark:text-slate-200 truncate leading-tight">
                {displayName}
              </p>
              <p className="text-[11px] text-slate-400 dark:text-slate-500 truncate leading-tight">
                {displayEmail}
              </p>
            </div>

            {/* Logout button */}
            <button 
              id="logout-button"
              onClick={handleLogout}
              disabled={isLoggingOut}
              className="p-1.5 rounded-md text-slate-300 dark:text-slate-600 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all shrink-0 disabled:opacity-50"
              title="Sign out"
            >
              {isLoggingOut ? (
                <div className="w-3.5 h-3.5 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" />
              ) : (
                <LogOut className="w-3.5 h-3.5" />
              )}
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
