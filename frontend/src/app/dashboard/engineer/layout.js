'use client';

import { 
  Plus, MessageSquare, FileText, Settings, Zap,
  LogOut, Grid2X2, PanelLeftClose, PanelLeft, Sparkles,
  AlertTriangle, X, ChevronUp
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
  // { label: 'Documentation', icon: FileText, href: '/dashboard/engineer/docs' },
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

function DiscardWizardModal({ onDiscard, onCancel }) {
  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative w-full max-w-sm bg-white dark:bg-[#151b23] rounded-xl border border-slate-200 dark:border-slate-700/50 shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center gap-3 px-5 pt-5 pb-0">
          <div className="w-9 h-9 rounded-lg bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 flex items-center justify-center">
            <AlertTriangle className="w-4 h-4 text-amber-500" />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">Discard New Project?</h3>
            <p className="text-[12px] text-slate-400 dark:text-slate-500">Your wizard progress will be lost.</p>
          </div>
          <button onClick={onCancel} className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex items-center gap-2.5 px-5 py-4">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2.5 text-sm font-semibold text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] rounded-lg border border-slate-200 dark:border-slate-700/50 transition-all"
          >
            Keep Editing
          </button>
          <button
            onClick={onDiscard}
            className="flex-1 px-4 py-2.5 text-sm font-bold text-white bg-red-600 hover:bg-red-700 rounded-lg shadow-sm transition-all active:scale-[0.98]"
          >
            Discard
          </button>
        </div>
      </div>
    </div>
  );
}

const NavItem = memo(function NavItem({ item, active, collapsed, wizardActive, onWizardIntercept }) {
  const Icon = item.icon;
  return (
    <Tooltip label={item.label} show={collapsed}>
      <Link
        href={item.href}
        prefetch={true}
        onClick={(e) => {
          if (wizardActive) {
            e.preventDefault();
            onWizardIntercept(item.href);
          }
        }}
        className={cn(
          "w-full flex items-center rounded-lg text-[13px] font-medium transition-colors duration-150",
          collapsed
            ? "justify-center px-2 py-2.5"
            : "gap-3 px-3 py-[9px]",
          active
            ? "bg-slate-100 dark:bg-white/[0.06] text-slate-900 dark:text-white"
            : "text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-white/[0.03] hover:text-slate-700 dark:hover:text-slate-300"
        )}
      >
        <Icon className={cn(
          "w-[18px] h-[18px] shrink-0",
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


  const [pendingNavHref, setPendingNavHref] = useState(null);
  const [showProfileMenu, setShowProfileMenu] = useState(false);

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
      const conversation = await createConversation({
        repoName:     null,
        repoProvider: null,
        repoUrl:      null,
        branch:       'main',
        title,
      });

      const conversationId = conversation?.id || `wizard-${Date.now()}`;

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

  const handleDiscardWizard = useCallback(() => {
    setShowWizard(false);
    const href = pendingNavHref;
    setPendingNavHref(null);
    if (href) {
      router.push(href);
    }
  }, [pendingNavHref, router]);

  const handleCancelDiscard = useCallback(() => {
    setPendingNavHref(null);
  }, []);

  return (
    <WizardContext.Provider value={{ showWizard, setShowWizard }}>
      <div className="h-screen flex bg-[#f0f4f9] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">

        {toast && (
          <Toast
            message={toast.message}
            type={toast.type}
            onDone={() => setToast(null)}
          />
        )}

        {pendingNavHref && (
          <DiscardWizardModal
            onDiscard={handleDiscardWizard}
            onCancel={handleCancelDiscard}
          />
        )}

        <aside
          className={cn(
            "h-full bg-white dark:bg-[#0d1117] border-r border-slate-200/80 dark:border-slate-800/60 flex flex-col shrink-0",
            collapsed ? "w-[60px]" : "w-[240px]"
          )}
          style={{ transition: 'width 250ms cubic-bezier(0.4, 0, 0.2, 1)' }}
        >

          <div className={cn(
            "flex items-center border-b border-slate-100 dark:border-slate-800/40 h-[56px] shrink-0",
            collapsed ? "px-0 justify-center" : "px-4 justify-between"
          )}>
            {collapsed ? (
              <Tooltip label="Expand sidebar" show={true}>
                <button
                  onClick={toggleCollapsed}
                  className="w-10 h-10 rounded-xl bg-blue-600 flex items-center justify-center hover:bg-blue-700 transition-colors"
                >
                  <Zap className="w-4 h-4 text-white fill-current" />
                </button>
              </Tooltip>
            ) : (
              <>
                <Link
                  href="/dashboard/engineer"
                  prefetch={true}
                  onClick={(e) => {
                    if (wizardIsActive) {
                      e.preventDefault();
                      setPendingNavHref('/dashboard/engineer');
                    }
                  }}
                  className="flex items-center gap-2.5 hover:opacity-80 transition-opacity"
                >
                  <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
                    <Zap className="w-4 h-4 text-white fill-current" />
                  </div>
                  <span className="font-bold text-slate-900 dark:text-white text-[16px] tracking-tight">Lucid AI</span>
                </Link>
                <button
                  onClick={toggleCollapsed}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.04] transition-colors"
                  title="Collapse sidebar"
                >
                  <PanelLeftClose className="w-[18px] h-[18px]" />
                </button>
              </>
            )}
          </div>

          <div className={cn("pt-3 pb-1", collapsed ? "px-2" : "px-3")}>
            <Tooltip label="New Project" show={collapsed}>
              <Link
                href="/dashboard/engineer"
                prefetch={true}
                onClick={(e) => {
                  if (wizardIsActive) {
                    e.preventDefault();
                    setPendingNavHref('/dashboard/engineer');
                  }
                }}
                className={cn(
                  "w-full flex items-center justify-center rounded-lg text-[13px] font-semibold transition-all duration-150 active:scale-[0.97]",
                  collapsed ? "p-2.5" : "gap-2 px-3 py-2",
                  "bg-blue-600 dark:bg-blue-600 text-white hover:bg-blue-700 dark:hover:bg-blue-500 shadow-sm shadow-blue-600/20"
                )}
              >
                <Plus className="w-4 h-4 shrink-0" strokeWidth={2.5} />
                {!collapsed && "New Project"}
              </Link>
            </Tooltip>
          </div>

          {/* ── Section Label ── */}
          {!collapsed && (
            <div className="px-5 pt-4 pb-1.5">
              <span className="text-[10px] font-bold text-slate-400/70 dark:text-slate-600 uppercase tracking-[0.1em]">
                Workspace
              </span>
            </div>
          )}
          {collapsed && <div className="pt-2" />}

          {/* ── Wizard active indicator ── */}
          {wizardIsActive && (
            <div className={cn("px-3 pb-0.5", collapsed && "px-2")}>
              <div className={cn(
                "w-full flex items-center rounded-lg text-[13px] font-medium bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-400 border border-violet-200/60 dark:border-violet-500/15",
                collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2"
              )}>
                <Sparkles className="w-4 h-4 shrink-0 text-violet-500 dark:text-violet-400" strokeWidth={2} />
                {!collapsed && (
                  <>
                    <span className="flex-1 text-left">New Project</span>
                    <div className="w-1.5 h-1.5 rounded-full bg-violet-500 dark:bg-violet-400 animate-pulse shrink-0" />
                  </>
                )}
              </div>
            </div>
          )}

          {/* ── Navigation ── */}
          <nav className={cn("flex-1 space-y-0.5 pt-1", collapsed ? "px-2" : "px-3")}>
            {navItems.map((item) => (
              <NavItem
                key={item.href}
                item={item}
                active={isActive(item.href)}
                collapsed={collapsed}
                wizardActive={wizardIsActive}
                onWizardIntercept={(href) => setPendingNavHref(href)}
              />
            ))}
          </nav>

          {/* ── Bottom: User Profile with Menu ── */}
          <div className="relative border-t border-slate-100 dark:border-slate-800/40 p-2.5">

            {/* Profile Menu Popup */}
            {showProfileMenu && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setShowProfileMenu(false)} />
                <div className={cn(
                  "absolute bg-white dark:bg-[#1c2128] rounded-xl border border-slate-200 dark:border-slate-700/60 shadow-xl dark:shadow-black/40 overflow-hidden z-50 w-52",
                  collapsed
                    ? "left-full bottom-2 ml-2"
                    : "bottom-full left-2.5 right-2.5 mb-2 w-auto"
                )}>
                  {/* User info header */}
                  <div className="px-3.5 py-3 border-b border-slate-100 dark:border-slate-700/40">
                    <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 truncate">{displayName}</p>
                    <p className="text-[11px] text-slate-400 dark:text-slate-500 truncate">{displayEmail}</p>
                  </div>
                  {/* Menu Items */}
                  <div className="py-1">
                    <button
                      onClick={() => {
                        setShowProfileMenu(false);
                        if (wizardIsActive) {
                          setPendingNavHref('/dashboard/engineer/settings');
                        } else {
                          router.push('/dashboard/engineer/settings');
                        }
                      }}
                      className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[13px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-left"
                    >
                      <Settings className="w-4 h-4 text-slate-400 dark:text-slate-500" />
                      Settings
                    </button>
                    <button
                      onClick={() => { setShowProfileMenu(false); handleLogout(); }}
                      disabled={isLoggingOut}
                      className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[13px] font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors text-left disabled:opacity-50"
                    >
                      {isLoggingOut ? (
                        <div className="w-4 h-4 border-2 border-red-300 border-t-red-600 rounded-full animate-spin" />
                      ) : (
                        <LogOut className="w-4 h-4" />
                      )}
                      {isLoggingOut ? 'Signing out…' : 'Sign out'}
                    </button>
                  </div>
                </div>
              </>
            )}

            {/* Profile Trigger */}
            <button
              onClick={() => setShowProfileMenu(!showProfileMenu)}
              className={cn(
                "w-full flex items-center rounded-lg hover:bg-slate-100 dark:hover:bg-white/[0.05] transition-colors cursor-pointer",
                showProfileMenu && "bg-slate-100 dark:bg-white/[0.05]",
                collapsed ? "justify-center p-1.5" : "gap-2.5 px-2.5 py-2"
              )}
            >
              <Tooltip label={displayName} show={collapsed}>
                {avatarUrl ? (
                  <img 
                    src={avatarUrl} 
                    alt={displayName}
                    className="w-8 h-8 rounded-lg shrink-0 object-cover ring-1 ring-slate-200/60 dark:ring-slate-700/40"
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <div className="w-8 h-8 rounded-lg bg-slate-800 dark:bg-slate-700 flex items-center justify-center shrink-0 ring-1 ring-slate-700/40">
                    <span className="text-[11px] font-semibold text-white leading-none">{initials}</span>
                  </div>
                )}
              </Tooltip>
              {!collapsed && (
                <>
                  <div className="flex-1 min-w-0 text-left">
                    <p className="text-[13px] font-medium text-slate-700 dark:text-slate-200 truncate leading-tight">
                      {displayName}
                    </p>
                    <p className="text-[11px] text-slate-400 dark:text-slate-500 truncate leading-tight">
                      {displayEmail}
                    </p>
                  </div>
                  <ChevronUp className={cn(
                    "w-4 h-4 text-slate-400 dark:text-slate-500 shrink-0 transition-transform duration-200",
                    showProfileMenu && "rotate-180"
                  )} />
                </>
              )}
            </button>
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
