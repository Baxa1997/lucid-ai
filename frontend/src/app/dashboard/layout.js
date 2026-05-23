"use client";

export const dynamic = "force-dynamic";

import {
  Plus,
  FileText,
  Settings,
  Zap,
  LogOut,
  Grid2X2,
  PanelLeftClose,
  PanelLeft,
  Sparkles,
  AlertTriangle,
  X,
  ChevronUp,
  Home,
  FolderGit2,
  LayoutTemplate,
  User,
  HelpCircle,
  Gift,
  Share2,
  CreditCard,
  Mail,
} from "lucide-react";
import Link from "next/link";
import {useRouter, usePathname} from "next/navigation";
import {
  useState,
  useEffect,
  useCallback,
  memo,
  useRef,
  createContext,
  useContext,
} from "react";
import {cn} from "@/lib/utils";
import ThemeModeSelector from "@/components/ThemeModeSelector";
import Toast from "@/components/Toast";
import NewProjectWizard from "@/components/agent/NewProjectWizard";
import CommandPalette from "@/components/CommandPalette";
import {
  getSupabaseBrowserClient,
  clearAllSupabaseCookies,
} from "@/lib/supabase/client";
import {listConversations} from "@/lib/conversations";
import {useInvitations} from "@/hooks/useInvitations";
import InvitationsListener from "@/components/notifications/InvitationsListener";

const WizardContext = createContext({
  showWizard: false,
  setShowWizard: () => {},
});
export function useWizard() {
  return useContext(WizardContext);
}

const navItems = [
  {label: "Home", icon: Home, href: "/dashboard"},
  {label: "All apps", icon: FolderGit2, href: "/dashboard/projects"},
  {
    label: "Templates",
    icon: LayoutTemplate,
    href: "/dashboard/templates",
  },
  {
    label: "Integrations",
    icon: Grid2X2,
    href: "/dashboard/integrations",
  },
  {
    label: "Invitations",
    icon: Mail,
    href: "/dashboard/invitations",
    badgeKey: "invitations",
  },
  {label: "Settings", icon: Settings, href: "/dashboard/settings"},
  {label: "Billing", icon: CreditCard, href: "/dashboard/billing"},
];

function Tooltip({children, label, show}) {
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

function DiscardWizardModal({onDiscard, onCancel}) {
  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onCancel}
      />
      <div className="relative w-full max-w-sm bg-white dark:bg-[#151b23] rounded-xl border border-slate-200 dark:border-slate-700/50 shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center gap-3 px-5 pt-5 pb-0">
          <div className="w-9 h-9 rounded-lg bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 flex items-center justify-center">
            <AlertTriangle className="w-4 h-4 text-amber-500" />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">
              Discard New Project?
            </h3>
            <p className="text-[12px] text-slate-400 dark:text-slate-500">
              Your wizard progress will be lost.
            </p>
          </div>
          <button
            onClick={onCancel}
            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex items-center gap-2.5 px-5 py-4">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2.5 text-sm font-semibold text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] rounded-lg border border-slate-200 dark:border-slate-700/50 transition-all">
            Keep Editing
          </button>
          <button
            onClick={onDiscard}
            className="flex-1 px-4 py-2.5 text-sm font-bold text-white bg-red-600 hover:bg-red-700 rounded-lg shadow-sm transition-all active:scale-[0.98]">
            Discard
          </button>
        </div>
      </div>
    </div>
  );
}

const NavItem = memo(function NavItem({
  item,
  active,
  collapsed,
  wizardActive,
  onWizardIntercept,
  badgeCount = 0,
}) {
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
          "group relative w-full flex items-center rounded-xl text-[14px] transition-all duration-150",
          collapsed ? "justify-center px-2 py-2.5" : "gap-3 px-3 py-[10px]",
          active
            ? "bg-orange-50 dark:bg-orange-900/20 text-[#dc5426] dark:text-orange-400 font-semibold"
            : "text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.05] hover:text-slate-900 dark:hover:text-white font-medium",
        )}>
        <div className="relative shrink-0">
          <Icon
            className={cn(
              "w-[20px] h-[20px] transition-colors",
              active
                ? "text-[#dc5426] dark:text-orange-400"
                : "text-slate-500 dark:text-slate-400",
            )}
            strokeWidth={1.75}
          />
          {collapsed && badgeCount > 0 && (
            <span className="absolute -top-1 -right-1 min-w-[16px] h-[16px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
              {badgeCount > 9 ? "9+" : badgeCount}
            </span>
          )}
        </div>
        {!collapsed && (
          <>
            <span className="flex-1 text-left truncate">{item.label}</span>
            {badgeCount > 0 && (
              <span className="ml-auto min-w-[20px] h-[20px] px-1.5 rounded-full bg-red-500 text-white text-[11px] font-bold flex items-center justify-center">
                {badgeCount > 99 ? "99+" : badgeCount}
              </span>
            )}
          </>
        )}
      </Link>
    </Tooltip>
  );
});

export default function EngineerLayout({children}) {
  const router = useRouter();
  const pathname = usePathname();
  const supabase = getSupabaseBrowserClient();

  // Sidebar collapsed state.
  //
  // The visible width is driven by a CSS variable (--lucid-sidebar-w) that
  // an inline script in app/layout.js sets on <html> *before* React renders,
  // reading from localStorage. This eliminates the SSR/CSR "open then
  // close" flicker on refresh — the first paint already has the correct
  // width.
  //
  // React state still tracks `collapsed` for icon swaps and label
  // visibility. The lazy initialiser reads from the same data attribute
  // the script set, so SSR (returns false) → client first render (reads
  // dataset) won't introduce a *visual* mismatch because width is
  // CSS-driven. The transition is disabled until after first mount so
  // even the className swap can never animate.
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return document.documentElement.dataset.sidebarCollapsed === "true";
  });
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const [showWizard, setShowWizard] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [cmdProjects, setCmdProjects] = useState([]);
  const [cmdConversations, setCmdConversations] = useState([]);

  const [pendingNavHref, setPendingNavHref] = useState(null);
  const [showProfileMenu, setShowProfileMenu] = useState(false);

  // Pending-invitation count — drives the sidebar badge + powers
  // the Realtime channel that InvitationsListener listens on for
  // toast / browser-notification dispatch.
  const {count: invitationsCount} = useInvitations();

  useEffect(() => {
    if (showWizard && pathname.includes("/workspace/")) {
      setShowWizard(false);
    }
  }, [pathname, showWizard]);

  useEffect(() => {
    // Fetch data for command palette
    fetch("/api/platform-repos")
      .then((r) => r.json())
      .then((d) => setCmdProjects(d.repos || []))
      .catch(() => {});
    listConversations()
      .then((d) => setCmdConversations(d || []))
      .catch(() => {});
  }, []);

  // ⌘K keyboard shortcut
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setShowCommandPalette((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("lucid-sidebar-collapsed", String(next));
        // Keep the CSS variable in sync so the next refresh paints with
        // the correct width before React hydrates.
        document.documentElement.style.setProperty(
          "--lucid-sidebar-w",
          next ? "60px" : "260px",
        );
        document.documentElement.dataset.sidebarCollapsed = next ? "true" : "false";
      } catch (_) {}
      return next;
    });
  }, []);

  const [user, setUser] = useState(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const isLoggingOutRef = useRef(false);

  const [toast, setToast] = useState(null);

  useEffect(() => {
    supabase.auth.getSession().then(({data: {session}}) => {
      if (!session) {
        console.warn("[Layout] No session found, redirecting to login");
        router.replace("/login");
        return;
      }
    });

    supabase.auth.getUser().then(({data: {user}}) => {
      if (user) {
        setUser(user);
      }
    });

    const {
      data: {subscription},
    } = supabase.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT" && !isLoggingOutRef.current) {
        console.warn(
          "[Layout] Session lost (SIGNED_OUT event), redirecting to login",
        );
        clearAllSupabaseCookies();
        router.replace("/login");
      }
    });

    if (typeof window !== "undefined") {
      const justSignedIn = sessionStorage.getItem("lucid-just-signed-in");
      if (justSignedIn) {
        sessionStorage.removeItem("lucid-just-signed-in");
        setToast({message: "Signed in successfully!", type: "success"});
      }

      const justSignedOut = sessionStorage.getItem("lucid-just-signed-out");
      if (justSignedOut) {
        sessionStorage.removeItem("lucid-just-signed-out");
      }
    }

    return () => {
      subscription?.unsubscribe();
    };
  }, [supabase, router]);

  const displayName =
    user?.user_metadata?.full_name ||
    user?.user_metadata?.name ||
    user?.email?.split("@")[0] ||
    "User";
  const displayEmail = user?.email || "";
  const avatarUrl = user?.user_metadata?.avatar_url || null;
  const initials = displayName
    .split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  // ── Logout handler ──
  // `signOut()` with the default `scope: 'global'` does a server round-trip
  // to revoke the refresh token. On slow networks (or while an active
  // WebSocket is holding the page busy) that call can stall for tens of
  // seconds — the user sees the button stuck on "Signing out…". For UI
  // logout we don't need server-side revocation; we just need the local
  // session cleared and the user redirected. `scope: 'local'` is instant
  // and never touches the network.
  //
  // We still wrap it in a 1.5s race-with-timeout so a misbehaving SDK
  // version can't pin the button: after 1.5s we force the redirect.
  const handleLogout = useCallback(async () => {
    isLoggingOutRef.current = true;
    setIsLoggingOut(true);

    const signOutPromise = supabase.auth
      .signOut({ scope: "local" })
      .catch((err) => {
        console.error("Logout signOut() failed:", err);
      });
    const timeoutPromise = new Promise((resolve) => setTimeout(resolve, 1500));

    try {
      await Promise.race([signOutPromise, timeoutPromise]);
    } finally {
      clearAllSupabaseCookies();
      sessionStorage.setItem("lucid-just-signed-out", "true");
      router.replace("/login");
    }
  }, [supabase, router]);

  const handleWizardComplete = useCallback(
    (wizardResult) => {
      const {
        stack,
        projectType,
        description,
        figmaUrl,
        backend,
        deployment,
        enhancedPrompt,
      } = wizardResult;

      const conversationId = crypto.randomUUID();

      const prompt = enhancedPrompt || description || "";
      if (prompt) {
        try {
          sessionStorage.setItem(`wizard_prompt_${conversationId}`, prompt);
          sessionStorage.setItem(
            `wizard_meta_${conversationId}`,
            JSON.stringify({
              stack: stack || "nextjs",
              projectType: projectType || null,
              backend: backend || "none",
              deployment: deployment || null,
              figmaUrl: figmaUrl || "",
            }),
          );
          sessionStorage.setItem(
            `wizard_desc_${conversationId}`,
            description || "",
          );
        } catch (_) {}
      }

      router.replace(`/dashboard/workspace/${conversationId}`);
    },
    [router],
  );

  useEffect(() => {
    if (pathname.includes("/workspace/")) {
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

  const isWorkspace = pathname.includes("/workspace/");

  return (
    <WizardContext.Provider value={{showWizard, setShowWizard}}>
      <div className="h-screen flex bg-[#fefcfa] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">
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

        {!isWorkspace && (
          <aside
            className={cn(
              "h-full bg-white dark:bg-[#0d1117] border-r border-slate-200/60 dark:border-slate-800/40 flex flex-col shrink-0",
            )}
            style={{
              // Width is driven by the inline-script-set CSS variable so the
              // first paint already matches the user's stored preference.
              // Falls back to 260px if the script never ran (script blocked).
              width: "var(--lucid-sidebar-w, 260px)",
              transition: mounted ? "width 250ms cubic-bezier(0.4, 0, 0.2, 1)" : "none",
            }}>
            {/* Inner content gates on `mounted`. Before mount we show a
                neutral skeleton (animated gray pills) instead of a blank
                shell — same column structure works at both 60px and 260px
                widths, so it never looks "broken" or "missing." Once
                hydrated, the real content swaps in with the correct
                collapsed state read from the html dataset. */}
            {!mounted && (
              <div className="flex flex-col h-full animate-pulse" aria-hidden="true">
                <div className="h-[56px] shrink-0 border-b border-slate-100 dark:border-slate-800/40 flex items-center justify-center px-3">
                  <div className="w-8 h-8 rounded-lg bg-slate-200 dark:bg-slate-800/60" />
                </div>
                <div className="px-3 pt-3 pb-2">
                  <div className="h-9 rounded-xl bg-slate-200 dark:bg-slate-800/60" />
                </div>
                <div className="px-3 pt-2 flex flex-col gap-1.5">
                  <div className="h-9 rounded-lg bg-slate-200/70 dark:bg-slate-800/40" />
                  <div className="h-9 rounded-lg bg-slate-200/70 dark:bg-slate-800/40" />
                  <div className="h-9 rounded-lg bg-slate-200/70 dark:bg-slate-800/40" />
                  <div className="h-9 rounded-lg bg-slate-200/70 dark:bg-slate-800/40" />
                  <div className="h-9 rounded-lg bg-slate-200/70 dark:bg-slate-800/40" />
                </div>
                <div className="mt-auto p-3 border-t border-slate-100 dark:border-slate-800/40">
                  <div className="h-10 rounded-xl bg-slate-200 dark:bg-slate-800/60" />
                </div>
              </div>
            )}
            {mounted && (<>
            <div
              className={cn(
                "flex items-center h-[56px] shrink-0 border-b border-slate-100 dark:border-slate-800/40",
                collapsed ? "px-0 justify-center" : "px-5 justify-between",
              )}>
              {collapsed ? (
                <Tooltip label="Expand sidebar" show={true}>
                  <button
                    onClick={toggleCollapsed}
                    className="w-10 h-10 rounded-xl bg-[#dc5426] flex items-center justify-center hover:bg-[#b8421e] transition-all shadow-sm shadow-[#dc5426]/20">
                    <Zap className="w-4 h-4 text-white fill-current" />
                  </button>
                </Tooltip>
              ) : (
                <>
                  <Link
                    href="/dashboard"
                    prefetch={true}
                    onClick={(e) => {
                      if (wizardIsActive) {
                        e.preventDefault();
                        setPendingNavHref("/dashboard");
                      }
                    }}
                    className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
                    <div className="w-8 h-8 bg-[#dc5426] rounded-lg flex items-center justify-center shrink-0 shadow-sm shadow-[#dc5426]/20">
                      <Zap className="w-4 h-4 text-white fill-current" />
                    </div>
                    <span className="font-bold text-slate-900 dark:text-white text-[17px] tracking-tight">
                      Lucid AI
                    </span>
                  </Link>
                  <button
                    onClick={toggleCollapsed}
                    className="flex items-center justify-center w-7 h-7 rounded-[6px] text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-[#fefcfa] dark:hover:bg-white/[0.04] transition-all"
                    title="Collapse sidebar">
                    <PanelLeft className="w-[22px] h-[22px]" />
                  </button>
                </>
              )}
            </div>

            <div className={cn("pt-3 pb-2", collapsed ? "px-2" : "px-4")}>
              <Tooltip label="New Project" show={collapsed}>
                <Link
                  href="/dashboard"
                  prefetch={true}
                  onClick={(e) => {
                    if (wizardIsActive) {
                      e.preventDefault();
                      setPendingNavHref("/dashboard");
                    }
                  }}
                  className={cn(
                    "w-full flex items-center justify-center rounded-xl text-[13px] font-semibold transition-all duration-150 active:scale-[0.97]",
                    collapsed ? "p-2.5" : "gap-2 px-3 py-2.5",
                    "bg-[#dc5426] text-white hover:bg-[#b8421e] shadow-sm shadow-[#dc5426]/20",
                  )}>
                  <Plus className="w-4 h-4 shrink-0" strokeWidth={2.5} />
                  {!collapsed && "New Project"}
                </Link>
              </Tooltip>
            </div>

            {/* ── Wizard active indicator ── */}
            {wizardIsActive && (
              <div className={cn("px-4 pb-0.5 pt-2", collapsed && "px-2")}>
                <div
                  className={cn(
                    "w-full flex items-center rounded-xl text-[13px] font-medium bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] dark:text-orange-400 border border-orange-200/60 dark:border-orange-500/15",
                    collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2",
                  )}>
                  <Sparkles
                    className="w-4 h-4 shrink-0 text-[#dc5426] dark:text-orange-400"
                    strokeWidth={2}
                  />
                  {!collapsed && (
                    <>
                      <span className="flex-1 text-left">New Project</span>
                      <div className="w-1.5 h-1.5 rounded-full bg-[#dc5426] dark:bg-orange-400 animate-pulse shrink-0" />
                    </>
                  )}
                </div>
              </div>
            )}

            {/* ── Navigation ── */}
            <nav className={cn("flex-1 pt-2", collapsed ? "px-2" : "px-4")}>
              <div className="flex flex-col gap-px">
                {navItems.map((item) => (
                  <NavItem
                    key={item.href}
                    item={item}
                    active={isActive(item.href)}
                    collapsed={collapsed}
                    wizardActive={wizardIsActive}
                    onWizardIntercept={(href) => setPendingNavHref(href)}
                    badgeCount={
                      item.badgeKey === "invitations" ? invitationsCount : 0
                    }
                  />
                ))}
              </div>
            </nav>

            {/* ── Bottom: User Profile with Plan Badge ── */}
            <div className="border-t border-slate-100 dark:border-slate-800/40 p-2.5">
              {/* Upgrade Card (expanded only) */}
              {!collapsed && (
                <div className="mb-3 mx-0.5">
                  <div className="rounded-xl p-3.5 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-700/30">
                    <div className="flex items-start gap-2.5 mb-2.5">
                      <div className="w-7 h-7 rounded-lg bg-orange-500 flex items-center justify-center shrink-0">
                        <Sparkles className="w-3.5 h-3.5 text-white" />
                      </div>
                      <div>
                        <p className="text-[12.5px] font-bold text-amber-900 dark:text-amber-200 leading-tight">
                          Upgrade your plan
                        </p>
                        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 leading-snug">
                          Get more out of your apps
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={() => router.push("/pricing")}
                      className="w-full flex items-center justify-center gap-1.5 py-1.5 bg-orange-500 hover:bg-orange-600 text-white rounded-lg text-[12px] font-semibold transition-colors">
                      Upgrade
                    </button>
                  </div>
                </div>
              )}

              {/* Profile Trigger + Menu */}
              <div className="relative">
                {/* Profile Menu Popup — anchored directly above profile button */}
                {showProfileMenu && (
                  <>
                    <div
                      className="fixed inset-0 z-40"
                      onClick={() => setShowProfileMenu(false)}
                    />
                    <div
                      className={cn(
                        "absolute bg-white dark:bg-[#1c2128] rounded-2xl border border-slate-200 dark:border-slate-700/60 shadow-2xl dark:shadow-black/50 overflow-hidden z-50",
                        collapsed
                          ? "left-full bottom-0 ml-2 w-64"
                          : "bottom-full left-0 right-0 mb-2 w-auto",
                      )}>
                      {/* User info header */}
                      <div className="flex items-center gap-3.5 px-5 py-4 border-b border-slate-100 dark:border-slate-700/40">
                        {avatarUrl ? (
                          <img
                            src={avatarUrl}
                            alt={displayName}
                            className="w-10 h-10 rounded-full shrink-0 object-cover"
                            referrerPolicy="no-referrer"
                          />
                        ) : (
                          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-slate-600 to-slate-800 dark:from-slate-400 dark:to-slate-600 flex items-center justify-center shrink-0">
                            <span className="text-[13px] font-bold text-white dark:text-slate-900 leading-none">
                              {initials}
                            </span>
                          </div>
                        )}
                        <div className="min-w-0">
                          <p className="text-[15px] font-semibold text-slate-900 dark:text-white truncate leading-tight">
                            {displayName}
                          </p>
                          <p className="text-[12px] text-slate-400 dark:text-slate-500 truncate leading-tight mt-0.5">
                            {displayEmail}
                          </p>
                        </div>
                      </div>
                      {/* Menu Items — Section 1 */}
                      <div className="py-1.5">
                        <button
                          onClick={() => {
                            setShowProfileMenu(false);
                            router.push("/dashboard/settings");
                          }}
                          className="w-full flex items-center gap-3.5 px-5 py-3 text-[14px] font-normal text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-left">
                          <User
                            className="w-[18px] h-[18px] text-slate-400 dark:text-slate-500"
                            strokeWidth={1.75}
                          />
                          View profile
                        </button>
                        <button
                          onClick={() => {
                            setShowProfileMenu(false);
                            if (wizardIsActive) {
                              setPendingNavHref("/dashboard/settings");
                            } else {
                              router.push("/dashboard/settings");
                            }
                          }}
                          className="w-full flex items-center gap-3.5 px-5 py-3 text-[14px] font-normal text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-left">
                          <Settings
                            className="w-[18px] h-[18px] text-slate-400 dark:text-slate-500"
                            strokeWidth={1.75}
                          />
                          Account settings
                        </button>
                        <button className="w-full flex items-center gap-3.5 px-5 py-3 text-[14px] font-normal text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-left">
                          <HelpCircle
                            className="w-[18px] h-[18px] text-slate-400 dark:text-slate-500"
                            strokeWidth={1.75}
                          />
                          Help & support
                        </button>
                      </div>
                      {/* Divider */}
                      <div className="border-t border-slate-100 dark:border-slate-700/40" />
                      {/* Menu Items — Section 2 */}
                      <div className="py-1.5">
                        <button
                          onClick={() => {
                            setShowProfileMenu(false);
                            router.push("/pricing");
                          }}
                          className="w-full flex items-center gap-3.5 px-5 py-3 text-[14px] font-normal text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-left">
                          <Sparkles
                            className="w-[18px] h-[18px] text-slate-400 dark:text-slate-500"
                            strokeWidth={1.75}
                          />
                          Upgrade plan
                        </button>
                        <button className="w-full flex items-center gap-3.5 px-5 py-3 text-[14px] font-normal text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-left">
                          <Share2
                            className="w-[18px] h-[18px] text-slate-400 dark:text-slate-500"
                            strokeWidth={1.75}
                          />
                          Refer a friend
                        </button>
                      </div>
                      {/* Divider */}
                      <div className="border-t border-slate-100 dark:border-slate-700/40" />
                      {/* Log out */}
                      <div className="py-1.5">
                        <button
                          onClick={() => {
                            setShowProfileMenu(false);
                            handleLogout();
                          }}
                          disabled={isLoggingOut}
                          className="w-full flex items-center gap-3.5 px-5 py-3 text-[14px] font-normal text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors text-left disabled:opacity-50">
                          {isLoggingOut ? (
                            <div className="w-[18px] h-[18px] border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" />
                          ) : (
                            <LogOut
                              className="w-[18px] h-[18px] text-slate-400 dark:text-slate-500"
                              strokeWidth={1.75}
                            />
                          )}
                          {isLoggingOut ? "Signing out…" : "Log out"}
                        </button>
                      </div>
                    </div>
                  </>
                )}

                {/* Profile Button */}
                <button
                  onClick={() => setShowProfileMenu(!showProfileMenu)}
                  className={cn(
                    "w-full flex items-center rounded-xl hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors cursor-pointer",
                    showProfileMenu && "bg-slate-50 dark:bg-white/[0.04]",
                    collapsed ? "justify-center p-1.5" : "gap-3 px-2 py-2",
                  )}>
                  <Tooltip label={displayName} show={collapsed}>
                    {avatarUrl ? (
                      <img
                        src={avatarUrl}
                        alt={displayName}
                        className="w-8 h-8 rounded-full shrink-0 object-cover"
                        referrerPolicy="no-referrer"
                      />
                    ) : (
                      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-slate-600 to-slate-800 dark:from-slate-400 dark:to-slate-600 flex items-center justify-center shrink-0">
                        <span className="text-[11px] font-bold text-white dark:text-slate-900 leading-none">
                          {initials}
                        </span>
                      </div>
                    )}
                  </Tooltip>
                  {!collapsed && (
                    <>
                      <div className="flex-1 min-w-0 text-left">
                        <p className="text-[13px] font-medium text-slate-700 dark:text-slate-200 truncate leading-tight">
                          {displayName}
                        </p>
                      </div>
                    </>
                  )}
                </button>
              </div>
            </div>
            </>)}
          </aside>
        )}

        {/* ══ MAIN CONTENT ══ */}
        <main
          className={cn(
            "flex-1 min-w-0",
            isWorkspace ? "overflow-hidden" : "overflow-y-scroll",
          )}>
          {children}
        </main>

        {/* ══ WIZARD MODAL OVERLAY ══ */}
        {wizardIsActive && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            {/* Backdrop */}
            <div
              className="absolute inset-0 bg-black/40 dark:bg-black/60 backdrop-blur-sm"
              onClick={() => setShowWizard(false)}
            />
            {/* Modal */}
            <div className="relative w-full max-w-[780px] h-[85vh] max-h-[700px] mx-4 bg-white dark:bg-[#0d1117] rounded-2xl border border-slate-200 dark:border-slate-700/50 shadow-2xl dark:shadow-black/50 overflow-hidden animate-scale-in">
              <NewProjectWizard
                onClose={() => setShowWizard(false)}
                onWizardComplete={handleWizardComplete}
              />
            </div>
          </div>
        )}

        {/* ══ COMMAND PALETTE ══ */}
        <CommandPalette
          isOpen={showCommandPalette}
          onClose={() => setShowCommandPalette(false)}
          projects={cmdProjects}
          conversations={cmdConversations}
          onWizard={() => {
            setShowCommandPalette(false);
            setShowWizard(true);
          }}
        />

        {/* ══ FIXED FLOATING THEME TOGGLE ══ */}
        <div className="fixed bottom-6 right-6 z-50">
          <ThemeModeSelector className="w-10 h-10 rounded-xl shadow-lg shadow-slate-900/10 dark:shadow-black/30 hover:shadow-xl hover:scale-105 transition-all duration-200 [&_svg]:w-4 [&_svg]:h-4" />
        </div>

        {/* ══ INVITATION TOAST / BROWSER NOTIFICATION LISTENER ══ */}
        <InvitationsListener />
      </div>
    </WizardContext.Provider>
  );
}
