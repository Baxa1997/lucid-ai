'use client';

import { 
  Plus, MessageSquare, FileText, Settings, Zap,
  LogOut, Grid2X2, PanelLeftClose, PanelLeft, Sparkles
} from 'lucide-react';
import Link from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { useState, useEffect, useCallback, memo, useRef, createContext, useContext } from 'react';
import { cn } from '@/lib/utils';
import ThemeModeSelector from '@/components/ThemeModeSelector';
import Toast from '@/components/Toast';
import NewProjectWizard from '@/components/agent/NewProjectWizard';
import { getSupabaseBrowserClient, clearAllSupabaseCookies } from '@/lib/supabase/client';
import { createConversation } from '@/lib/conversations';

const WizardContext = createContext({ showWizard: false, setShowWizard: () => {} });
export function useWizard() { return useContext(WizardContext); }

const navItems = [
  { label: 'Usage Docs', icon: FileText, href: '/dashboard/engineer/usage-docs' },
  { label: 'Conversations', icon: MessageSquare, href: '/dashboard/engineer/conversations' },
  { label: 'Integrations', icon: Grid2X2, href: '/dashboard/engineer/integrations' },
  { label: 'Documentation', icon: FileText, href: '/dashboard/engineer/docs' },
  { label: 'Settings', icon: Settings, href: '/dashboard/engineer/settings' },
];

function Tooltip({ children, label, show }) {
  if (!show) return children;
  return (
    <div className="relative group/tooltip">
      {children}
      <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 px-2.5 py-1.5 rounded-lg bg-slate-800 dark:bg-slate-700 text-white text-xs font-medium whitespace-nowrap opacity-0 invisible group-hover/tooltip:opacity-100 group-hover/tooltip:visible transition-all duration-150 pointer-events-none z-50 shadow-lg">
        {label}
        <div className="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-slate-800 dark:border-r-slate-700" />
      </div>
    </div>
  );
}

const NavItem = memo(function NavItem({ item, active, collapsed }) {
  const Icon = item.icon;
  return (
    <Tooltip label={item.label} show={collapsed}>
      <Link
        href={item.href}
        prefetch={true}
        className={cn(
          "w-full flex items-center rounded-lg text-[14px] font-medium transition-all duration-200",
          collapsed
            ? "justify-center px-2 py-3"
            : "gap-3 px-3 py-[10px]",
          active
            ? "bg-slate-100 dark:bg-white/[0.06] text-slate-900 dark:text-white"
            : "text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.03] hover:text-slate-700 dark:hover:text-slate-300"
        )}
      >
        <Icon className={cn(
          "w-5 h-5 shrink-0",
          active ? "text-slate-700 dark:text-slate-200" : "text-slate-400 dark:text-slate-500"
        )} strokeWidth={active ? 2 : 1.75} />
        {!collapsed && (
          <>
            <span className="flex-1 text-left">{item.label}</span>
            {active && (
              <div className="w-1 h-1 rounded-full bg-blue-600 dark:bg-blue-400 shrink-0" />
            )}
          </>
        )}
      </Link>
    </Tooltip>
  );
});

export default function EngineerLayout({ children }) {
  const router = useRouter();
  const pathname = usePathname();
  const supabase = getSupabaseBrowserClient();

  const [collapsed, setCollapsed] = useState(false);
  const [showWizard, setShowWizard] = useState(false);

  useEffect(() => {
    if (showWizard && pathname.includes('/workspace/')) {
      setShowWizard(false);
    }
  }, [pathname, showWizard]);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('lucid-sidebar-collapsed');
      if (saved === 'true') setCollapsed(true);
    }
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem('lucid-sidebar-collapsed', String(next));
      return next;
    });
  }, []);

  const [user, setUser] = useState(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const isLoggingOutRef = useRef(false);

  const [toast, setToast] = useState(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) {
        console.warn('[Layout] No session found, redirecting to login');
        router.replace('/login');
        return;
      }
    });

    supabase.auth.getUser().then(({ data: { user } }) => {
      if (user) {
        setUser(user);
      }
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_OUT' && !isLoggingOutRef.current) {
        console.warn('[Layout] Session lost (SIGNED_OUT event), redirecting to login');
        clearAllSupabaseCookies();
        router.replace('/login');
      }
    });

    if (typeof window !== 'undefined') {
      const justSignedIn = sessionStorage.getItem('lucid-just-signed-in');
      if (justSignedIn) {
        sessionStorage.removeItem('lucid-just-signed-in');
        setToast({ message: 'Signed in successfully!', type: 'success' });
      }

      const justSignedOut = sessionStorage.getItem('lucid-just-signed-out');
      if (justSignedOut) {
        sessionStorage.removeItem('lucid-just-signed-out');
      }
    }

    return () => {
      subscription?.unsubscribe();
    };
  }, [supabase, router]);

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
    isLoggingOutRef.current = true;
    setIsLoggingOut(true);
    try {
      await supabase.auth.signOut();
    } catch (err) {
      console.error('Logout signOut() failed:', err);
    } finally {
      clearAllSupabaseCookies();
      sessionStorage.setItem('lucid-just-signed-out', 'true');
      router.replace('/login');
    }
  }, [supabase, router]);

  const handleWizardComplete = useCallback(async (wizardResult) => {
    const stackLabel = wizardResult.stack || 'project';
    const title = wizardResult.description
      ? wizardResult.description.slice(0, 80)
      : `New ${stackLabel} Project`;

    const {
      stack,
      projectType,
      description,
      figmaUrl,
      backend,
      deployment,
      enhancedPrompt,
    } = wizardResult;

    try {
      // ── Create conversation (no repo yet — backend creates it in Phase 7) ──
      const conversation = await createConversation({
        repoName:     null,
        repoProvider: null,
        repoUrl:      null,
        branch:       'main',
        title,
      });

      const conversationId = conversation?.id || `wizard-${Date.now()}`;

      // ── Persist wizard data to sessionStorage ───────────────────────────────
      // The backend reads stack= from the [LUCID_PROJECT] header and uses its
      // own template registry to clone the right template — no clone_url needed.
      const prompt = enhancedPrompt || description || '';
      if (prompt) {
        try {
          sessionStorage.setItem(`wizard_prompt_${conversationId}`, prompt);
          sessionStorage.setItem(`wizard_meta_${conversationId}`, JSON.stringify({
            stack:       stack      || 'nextjs',
            projectType: projectType || null,
            backend:     backend    || 'none',
            deployment:  deployment || null,
            figmaUrl:    figmaUrl   || '',
          }));
          sessionStorage.setItem(`wizard_desc_${conversationId}`, description || '');
        } catch (_) {}
      }

      // ── Navigate to workspace ────────────────────────────────────────────────
      router.replace(`/dashboard/engineer/workspace/${conversationId}`);

    } catch (err) {
      console.error('[Layout] Wizard completion error:', err);
      const fallbackId = `wizard-${Date.now()}`;
      router.replace(`/dashboard/engineer/workspace/${fallbackId}`);
    }
  }, [router]);


  useEffect(() => {
    if (pathname.includes('/workspace/')) {
      setShowWizard(false);
    }
  }, [pathname]);

  const isActive = (href) => pathname === href;
  const wizardIsActive = showWizard;

  return (
    <WizardContext.Provider value={{ showWizard, setShowWizard }}>
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
        <aside className={cn(
          "h-full bg-white dark:bg-[#0d1117] border-r border-slate-200 dark:border-slate-800/80 flex flex-col shrink-0 transition-all duration-300 ease-in-out",
          collapsed ? "w-[60px]" : "w-[240px]"
        )}>

          {/* Logo + Collapse Toggle */}
          <div className={cn(
            "border-b border-slate-100 dark:border-slate-800/60 flex items-center",
            collapsed ? "px-2 pt-4 pb-4 justify-center" : "px-5 pt-5 pb-5 justify-between"
          )}>
            <Link
              href="/dashboard/engineer"
              prefetch={true}
              className="flex items-center gap-2.5"
              onClick={() => setShowWizard(false)}
            >
              <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
                <Zap className="w-4 h-4 text-white fill-current" />
              </div>
              {!collapsed && (
                <span className="font-bold text-slate-900 dark:text-white text-[17px] tracking-tight">Lucid AI</span>
              )}
            </Link>
            {!collapsed && (
              <button
                onClick={toggleCollapsed}
                className="p-1 rounded-md text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.04] transition-colors"
                title="Collapse sidebar"
              >
                <PanelLeftClose className="w-5 h-5" />
              </button>
            )}
          </div>

          {/* + New Project Button */}
          <div className={cn("pt-4 pb-2", collapsed ? "px-2" : "px-3")}>
            <Tooltip label="New Project" show={collapsed}>
              <button
                onClick={() => {
                  // Navigate to dashboard where user can choose existing repo or new project
                  router.push('/dashboard/engineer');
                }}
                className={cn(
                  "w-full flex items-center justify-center rounded-lg text-[14px] font-semibold transition-colors active:scale-[0.98] shadow-sm",
                  collapsed ? "px-2 py-3" : "gap-2 px-3 py-2.5",
                  "bg-blue-600 dark:bg-white text-white dark:text-slate-900 hover:bg-blue-700 dark:hover:bg-slate-100 shadow-blue-600/20 dark:shadow-none"
                )}
              >
                <Plus className="w-4 h-4 shrink-0" strokeWidth={2.5} />
                {!collapsed && "New Project"}
              </button>
            </Tooltip>
          </div>

          {/* Section Label */}
          {!collapsed && (
            <div className="px-5 pt-4 pb-2">
              <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">
                Workspace
              </span>
            </div>
          )}
          {collapsed && <div className="pt-3" />}

          {/* Wizard active indicator in sidebar */}
          {wizardIsActive && (
            <div className={cn("px-3 pb-1", collapsed && "px-2")}>
              <div className={cn(
                "w-full flex items-center rounded-lg text-[14px] font-medium bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-400",
                collapsed ? "justify-center px-2 py-3" : "gap-3 px-3 py-[10px]"
              )}>
                <Sparkles className="w-5 h-5 shrink-0 text-violet-500 dark:text-violet-400" strokeWidth={1.75} />
                {!collapsed && (
                  <>
                    <span className="flex-1 text-left">New Project</span>
                    <div className="w-1 h-1 rounded-full bg-violet-500 dark:bg-violet-400 shrink-0" />
                  </>
                )}
              </div>
            </div>
          )}

          {/* Navigation */}
          <nav className={cn("flex-1 space-y-px", collapsed ? "px-2" : "px-3")}>
            {navItems.map((item) => (
              <NavItem key={item.href} item={item} active={isActive(item.href)} collapsed={collapsed} />
            ))}
          </nav>

          {/* Expand button when collapsed */}
          {collapsed && (
            <div className="px-2 py-2">
              <Tooltip label="Expand sidebar" show={true}>
                <button
                  onClick={toggleCollapsed}
                  className="w-full flex items-center justify-center p-2 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.04] transition-colors"
                >
                  <PanelLeft className="w-5 h-5" />
                </button>
              </Tooltip>
            </div>
          )}

          {/* Bottom User + Logout */}
          <div className="p-3 border-t border-slate-100 dark:border-slate-800/60">
            <div className={cn(
              "flex items-center rounded-lg",
              collapsed ? "justify-center px-0 py-1" : "gap-2.5 px-2 py-1.5"
            )}>
              {/* Avatar */}
              <Tooltip label={displayName} show={collapsed}>
                {avatarUrl ? (
                  <img 
                    src={avatarUrl} 
                    alt={displayName}
                    className="w-8 h-8 rounded-lg shrink-0 object-cover"
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <div className="w-8 h-8 rounded-lg bg-slate-800 dark:bg-slate-700 flex items-center justify-center shrink-0">
                    <span className="text-[11px] font-semibold text-white leading-none">{initials}</span>
                  </div>
                )}
              </Tooltip>

              {/* User info + logout — only when expanded */}
              {!collapsed && (
                <>
                  <div className="flex-1 min-w-0">
                    <p className="text-[14px] font-medium text-slate-700 dark:text-slate-200 truncate leading-tight">
                      {displayName}
                    </p>
                    <p className="text-[12px] text-slate-400 dark:text-slate-500 truncate leading-tight">
                      {displayEmail}
                    </p>
                  </div>

                  {/* Logout button */}
                  <button 
                    id="logout-button"
                    onClick={handleLogout}
                    disabled={isLoggingOut}
                    className="p-1.5 rounded-md text-slate-300 dark:text-slate-600 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors shrink-0 disabled:opacity-50"
                    title="Sign out"
                  >
                    {isLoggingOut ? (
                      <div className="w-3.5 h-3.5 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" />
                    ) : (
                      <LogOut className="w-4 h-4" />
                    )}
                  </button>
                </>
              )}
            </div>
          </div>
        </aside>

        {/* ══ MAIN CONTENT ══ */}
        <main className={cn("flex-1", wizardIsActive ? "overflow-hidden" : "overflow-y-scroll")}>
          {wizardIsActive ? (
            <div className="h-full flex flex-col relative">
              <div className="flex-1 min-h-0">
                <NewProjectWizard
                  onClose={() => setShowWizard(false)}
                  onWizardComplete={handleWizardComplete}
                />
              </div>
            </div>
          ) : (
            children
          )}
        </main>

        {/* ══ FIXED FLOATING THEME TOGGLE ══ */}
        <div className="fixed bottom-6 right-6 z-50">
          <ThemeModeSelector className="w-10 h-10 rounded-xl shadow-lg shadow-slate-900/10 dark:shadow-black/30 hover:shadow-xl hover:scale-105 transition-all duration-200 [&_svg]:w-4 [&_svg]:h-4" />
        </div>
      </div>
    </WizardContext.Provider>
  );
}
