"use client";

// ─────────────────────────────────────────────────────────
//  Lucid AI — Engineer Dashboard (v3 — Production Grade)
//  Prompt Hero → Stats → Project Cards → Activity
// ─────────────────────────────────────────────────────────

import {
  Plus,
  Github,
  Rocket,
  Clock,
  Sparkles,
  MessageSquare,
  ArrowRight,
  Loader2,
  Layers,
  Globe,
  Zap,
  Activity,
  BarChart3,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  MoreHorizontal,
  Code2,
  Play,
  Lightbulb,
  Eye,
  Paperclip,
  Link2,
  SlidersHorizontal,
  Image,
  Download,
  GitBranch,
  Gitlab,
  X,
  CreditCard,
} from "lucide-react";
import {useRouter} from "next/navigation";
import {useState, useEffect, useRef} from "react";
import {cn} from "@/lib/utils";
import {listConversations} from "@/lib/conversations";
import {
  getIntegrations,
  fetchGitHubRepos,
  fetchGitLabRepos,
  fetchGitHubBranches,
  fetchGitLabBranches,
} from "@/lib/integrations";
import {useWizard} from "./layout";
import CustomSelect from "@/components/ui/CustomSelect";
import {getSupabaseBrowserClient} from "@/lib/supabase/client";

// ── Helpers ──────────────────────────────────────
function formatTime(dateStr) {
  if (!dateStr) return "";
  const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

const IDEAS = [
  {
    label: "CRM dashboard",
    emoji: "📊",
    prompt:
      "A modern CRM dashboard with contact management, deal pipeline view, activity tracking, and analytics charts. Clean professional design with sidebar navigation.",
  },
  {
    label: "E-commerce store",
    emoji: "🛒",
    prompt:
      "An e-commerce storefront with product catalog, shopping cart, checkout flow, user accounts, and order history. Modern design with hero banner and category filtering.",
  },
  {
    label: "Portfolio website",
    emoji: "🎨",
    prompt:
      "A personal portfolio website with hero section, project showcase grid, about me page, skills section, and contact form. Minimal, elegant design with dark mode.",
  },
  {
    label: "SaaS landing",
    emoji: "🚀",
    prompt:
      "A SaaS product landing page with hero section, feature grid, pricing table, testimonials, FAQ accordion, and footer. Modern gradient design with CTAs.",
  },
  {
    label: "Blog platform",
    emoji: "📝",
    prompt:
      "A blog platform with article listing, rich text editor, categories/tags, comment system, and author profiles. Clean typography-focused design.",
  },
  {
    label: "Admin panel",
    emoji: "⚙️",
    prompt:
      "A full-featured admin dashboard with data tables, CRUD forms, user management, role-based access, charts/analytics, and settings page. Professional enterprise design.",
  },
];

// Emoji icons for projects (deterministic based on name hash)
const PROJECT_EMOJIS = [
  "🔥",
  "⚡",
  "🚀",
  "💎",
  "🎯",
  "🌟",
  "🎨",
  "🔮",
  "🌊",
  "🍀",
  "🦊",
  "🎪",
];
const EMOJI_BG_COLORS = [
  "bg-orange-50 dark:bg-orange-500/10",
  "bg-amber-50 dark:bg-amber-500/10",
  "bg-orange-50 dark:bg-orange-500/10",
  "bg-orange-50 dark:bg-orange-500/10",
  "bg-amber-50 dark:bg-amber-500/10",
  "bg-rose-50 dark:bg-rose-500/10",
  "bg-orange-50 dark:bg-orange-500/10",
  "bg-amber-50 dark:bg-amber-500/10",
  "bg-orange-50 dark:bg-orange-500/10",
  "bg-pink-50 dark:bg-pink-500/10",
  "bg-orange-50 dark:bg-orange-500/10",
  "bg-amber-50 dark:bg-amber-500/10",
];

function getProjectHash(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

/* ════════════════════════════════════════════════════════
   PROJECT CARD — Base44 clean "Apps" style
   ════════════════════════════════════════════════════════ */
function ProjectCard({project, index, onClick, isLaunching}) {
  const [showMenu, setShowMenu] = useState(false);
  const hasDeployment = !!project.deployUrl;
  const rawName = project.projectName || project.repoName || "Untitled";
  const displayName = rawName
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (l) => l.toUpperCase())
    .slice(0, 50);
  const hash = getProjectHash(rawName);
  const emoji = PROJECT_EMOJIS[hash % PROJECT_EMOJIS.length];
  const bgColor = EMOJI_BG_COLORS[hash % EMOJI_BG_COLORS.length];

  return (
    <div
      className="group bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-5 hover:shadow-md dark:hover:shadow-black/20 hover:border-slate-300 dark:hover:border-[#444c56] transition-all duration-200 cursor-pointer"
      onClick={() => !isLaunching && onClick()}>
      {/* Top row: icon + name + menu */}
      <div className="flex items-start gap-3 mb-2.5">
        <div
          className={cn(
            "w-10 h-10 rounded-xl flex items-center justify-center shrink-0",
            bgColor,
          )}>
          <span className="text-base leading-none">{emoji}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <h3 className="text-[14px] font-semibold text-slate-900 dark:text-white truncate leading-tight">
              {displayName}
            </h3>
            <div
              className="relative shrink-0 ml-2"
              onClick={(e) => e.stopPropagation()}>
              <button
                onClick={() => setShowMenu(!showMenu)}
                className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors opacity-0 group-hover:opacity-100">
                <MoreHorizontal className="w-4 h-4" />
              </button>
              {showMenu && (
                <>
                  <div
                    className="fixed inset-0 z-30"
                    onClick={() => setShowMenu(false)}
                  />
                  <div className="absolute right-0 top-full mt-1 z-40 w-44 bg-white dark:bg-[#1c2128] rounded-xl border border-slate-200 dark:border-[#444c56] shadow-xl dark:shadow-black/50 overflow-hidden py-1 animate-scale-in">
                    <button
                      onClick={() => {
                        setShowMenu(false);
                        onClick();
                      }}
                      className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                      <Play className="w-3.5 h-3.5" /> Open Workspace
                    </button>
                    {hasDeployment && (
                      <a
                        href={project.deployUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                        <Eye className="w-3.5 h-3.5" /> View Live
                      </a>
                    )}
                    <button className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                      <Code2 className="w-3.5 h-3.5" /> Export Code
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Description */}
      <p className="text-[12px] text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed mb-3">
        {project.description ||
          `An AI-generated application based on ${rawName.replace(/[-_]/g, " ")}.`}
      </p>

      {/* Footer */}
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400 dark:text-slate-500">
        <span>By {project.ownerEmail || "you"}</span>
        <span className="mx-0.5">·</span>
        <span>
          Created {formatTime(project.createdAt || project.updatedAt)}
        </span>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════
   GHOST CARD (+ New Project)
   ════════════════════════════════════════════════════════ */
function GhostCard({onClick}) {
  return (
    <button
      onClick={onClick}
      className="group rounded-2xl border-2 border-dashed border-slate-200 dark:border-[#2d333b] hover:border-orange-300 dark:hover:border-orange-500/30 hover:bg-slate-50/50 dark:hover:bg-[#161b22]/50 transition-all duration-200 flex flex-col items-center justify-center min-h-[160px] p-5">
      <div className="w-10 h-10 rounded-xl bg-slate-100 dark:bg-[#21262d] flex items-center justify-center mb-2 group-hover:bg-orange-100 dark:group-hover:bg-orange-500/20 transition-colors">
        <Plus className="w-5 h-5 text-slate-400 group-hover:text-[#dc5426] transition-colors" />
      </div>
      <span className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 group-hover:text-[#dc5426] dark:group-hover:text-orange-400 transition-colors">
        New Project
      </span>
    </button>
  );
}

/* ════════════════════════════════════════════════════════
   ACTIVITY ROW
   ════════════════════════════════════════════════════════ */
function ActivityRow({conversation, onClick}) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3.5 px-4 py-3.5 hover:bg-slate-50 dark:hover:bg-white/[0.015] transition-colors group text-left">
      <div
        className={cn(
          "w-2 h-2 rounded-full shrink-0",
          conversation.status === "active"
            ? "bg-[#dc5426]"
            : conversation.status === "completed"
              ? "bg-orange-400"
              : "bg-slate-400",
        )}
      />
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-slate-700 dark:text-slate-200 truncate group-hover:text-slate-900 dark:group-hover:text-white transition-colors">
          {conversation.title || conversation.repo_name || "Conversation"}
        </p>
        <div className="flex items-center gap-2.5 mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
          {conversation.repo_name && (
            <span className="flex items-center gap-1 truncate max-w-[200px]">
              <Github className="w-3 h-3 shrink-0" />
              {conversation.repo_name}
            </span>
          )}
          <span className="flex items-center gap-1 shrink-0">
            <Clock className="w-3 h-3" />
            {formatTime(conversation.updated_at)}
          </span>
        </div>
      </div>
      <ArrowRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-700 shrink-0 opacity-0 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all" />
    </button>
  );
}

/* ════════════════════════════════════════════════════════
   HOW IT WORKS
   ════════════════════════════════════════════════════════ */
function HowItWorks() {
  const steps = [
    {
      n: "01",
      label: "Describe your idea",
      desc: "Type what you want in plain English",
      icon: Lightbulb,
      color: "from-[#dc5426] to-orange-500",
    },
    {
      n: "02",
      label: "AI generates it",
      desc: "Full-stack app built in minutes",
      icon: Sparkles,
      color: "from-orange-500 to-[#b8421e]",
    },
    {
      n: "03",
      label: "Deploy live",
      desc: "Ship to production in one click",
      icon: Rocket,
      color: "from-[#dc5426] to-orange-500",
    },
  ];
  return (
    <div className="grid grid-cols-3 gap-4 stagger-children">
      {steps.map((s) => (
        <div
          key={s.n}
          className="relative p-6 rounded-2xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] text-center">
          <div
            className={`w-11 h-11 rounded-xl bg-gradient-to-br ${s.color} mx-auto mb-3 flex items-center justify-center shadow-md`}>
            <s.icon className="w-5 h-5 text-white" />
          </div>
          <span className="text-[10px] font-extrabold text-slate-300 dark:text-slate-600 uppercase tracking-widest">
            {s.n}
          </span>
          <h4 className="text-[14px] font-bold text-slate-800 dark:text-slate-100 mt-1">
            {s.label}
          </h4>
          <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-1.5 leading-relaxed">
            {s.desc}
          </p>
        </div>
      ))}
    </div>
  );
}

/* ════════════════════════════════════════════════════════
   MAIN PAGE
   ════════════════════════════════════════════════════════ */
export default function EngineerDashboardPage() {
  const router = useRouter();
  const {setShowWizard} = useWizard();
  const promptRef = useRef(null);

  const [homeMode, setHomeMode] = useState("build");
  const [integrations, setIntegrations] = useState({
    github: null,
    gitlab: null,
  });
  const [selectedProvider, setSelectedProvider] = useState("github");
  const [selectedRepo, setSelectedRepo] = useState(null);
  const [selectedBranch, setSelectedBranch] = useState(null);
  const [repoSearch, setRepoSearch] = useState("");
  const [gitRepos, setGitRepos] = useState([]);
  const [gitReposLoading, setGitReposLoading] = useState(false);
  const [gitBranches, setGitBranches] = useState([]);
  const [gitBranchesLoading, setGitBranchesLoading] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);
  // Separate launch state for the "Import & analyze" flow so its button
  // shows a spinner without affecting the wizard's launch state.
  const [isImporting, setIsImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [subscription, setSubscription] = useState(null);
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [pendingAction, setPendingAction] = useState(null); // 'build' | 'import'
  const [platformRepos, setPlatformRepos] = useState([]);
  const [platformLoading, setPlatformLoading] = useState(true);
  const [conversations, setConversations] = useState([]);
  const [convoLoading, setConvoLoading] = useState(true);
  const [user, setUser] = useState(null);
  const [promptText, setPromptText] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const fileInputRef = useRef(null);
  const [advancedOpts, setAdvancedOpts] = useState({
    stack: "auto",
    backend: "none",
    figmaUrl: "",
  });

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    supabase.auth.getUser().then(({data: {user: u}}) => {
      if (u) setUser(u);
    });
    getIntegrations()
      .then(setIntegrations)
      .catch(() => {});
    fetch("/api/platform-repos")
      .then((r) => r.json())
      .then((d) => setPlatformRepos(d.repos || []))
      .catch(() => setPlatformRepos([]))
      .finally(() => setPlatformLoading(false));
    listConversations()
      .then((data) => setConversations(data || []))
      .catch(() => setConversations([]))
      .finally(() => setConvoLoading(false));
    fetch("/api/stripe/subscription")
      .then((r) => r.ok ? r.json() : null)
      .then((data) => { if (data) setSubscription(data); })
      .catch(() => {});
    // Check for pre-filled template prompt
    try {
      const templatePrompt = sessionStorage.getItem("lucid_template_prompt");
      if (templatePrompt) {
        setPromptText(templatePrompt);
        sessionStorage.removeItem("lucid_template_prompt");
        setTimeout(() => promptRef.current?.focus(), 100);
      }
    } catch {}
  }, []);

  useEffect(() => {
    if (homeMode !== "import") return;
    setGitRepos([]);
    setSelectedRepo(null);
    setSelectedBranch(null);
    setGitBranches([]);
    setGitReposLoading(true);
    const load = async () => {
      try {
        const ints = await getIntegrations();
        setIntegrations(ints);
        let repos = [];
        if (selectedProvider === "github" && ints.github?.token) {
          repos = await fetchGitHubRepos(ints.github.token);
        } else if (selectedProvider === "gitlab" && ints.gitlab?.token) {
          repos = await fetchGitLabRepos(
            ints.gitlab.host || "https://gitlab.com",
            ints.gitlab.token,
          );
        }
        setGitRepos(repos);
      } catch {
        setGitRepos([]);
      } finally {
        setGitReposLoading(false);
      }
    };
    load();
  }, [homeMode, selectedProvider]);

  useEffect(() => {
    if (!selectedRepo) {
      setGitBranches([]);
      setSelectedBranch(null);
      return;
    }
    setGitBranches([]);
    setSelectedBranch(null);
    setGitBranchesLoading(true);
    const load = async () => {
      try {
        const ints = integrations;
        let branches = [];
        if (selectedProvider === "github" && ints.github?.token) {
          branches = await fetchGitHubBranches(
            ints.github.token,
            selectedRepo.name,
          );
        } else if (selectedProvider === "gitlab" && ints.gitlab?.token) {
          branches = await fetchGitLabBranches(
            ints.gitlab.host || "https://gitlab.com",
            ints.gitlab.token,
            selectedRepo.id,
          );
        }
        const branchObjs = branches.map((b) => ({name: b}));
        setGitBranches(branchObjs);
        setSelectedBranch(
          selectedRepo.defaultBranch || branchObjs[0]?.name || null,
        );
      } catch {
        setGitBranches([]);
      } finally {
        setGitBranchesLoading(false);
      }
    };
    load();
  }, [selectedRepo]);

  const totalProjects = platformRepos.length;
  const activeCount = conversations.filter((c) => c.status === "active").length;
  const deployedCount = platformRepos.filter((pr) => pr.deployUrl).length;
  const thisMonthCount = (() => {
    const s = new Date();
    s.setDate(1);
    s.setHours(0, 0, 0, 0);
    return conversations.filter((c) => new Date(c.created_at) >= s).length;
  })();

  /* ── Build from prompt ── */
  const isAtProjectLimit = () => {
    if (!subscription) return false;
    if (subscription.isPaid) return false;
    const limit = subscription.limits?.maxProjects ?? 1;
    return (subscription.usage?.projectsCreated ?? 0) >= limit;
  };

  const handleSkipUpgrade = () => {
    const action = pendingAction;
    setPendingAction(null);
    setShowUpgradeModal(false);
    if (action === 'build') handleBuildFromPrompt(true);
    else if (action === 'import') handleImportExistingRepo(true);
  };

  const handleBuildFromPrompt = async (force = false) => {
    const text = promptText.trim();
    if (!text) return;
    if (!force && isAtProjectLimit()) { setPendingAction('build'); setShowUpgradeModal(true); return; }
    setIsLaunching(true);
    try {
      // Resolve stack synchronously from user selection (no API call)
      // 'auto' defaults to 'nextjs' — the backend Gemini research step
      // handles intelligent stack detection as part of the pipeline.
      const resolvedStack =
        advancedOpts.stack === "auto" ? "nextjs" : advancedOpts.stack;

      // Generate a stable UUID for this workspace — no DB round-trip needed
      const cid = crypto.randomUUID();

      // Store raw prompt in sessionStorage — the backend pipeline's
      // Gemini research phase replaces the old frontend prompt enhancement.
      try {
        sessionStorage.setItem(`wizard_prompt_${cid}`, text);
        sessionStorage.setItem(
          `wizard_meta_${cid}`,
          JSON.stringify({
            stack: resolvedStack,
            projectType: null,
            backend: advancedOpts.backend,
            deployment: "hosted",
            figmaUrl: advancedOpts.figmaUrl || "",
          }),
        );
        sessionStorage.setItem(`wizard_desc_${cid}`, text);
      } catch {}

      // Navigate IMMEDIATELY — user enters workspace in < 300ms
      router.replace(`/dashboard/engineer/workspace/${cid}`);
    } catch (err) {
      console.error("[Dashboard] Build error:", err);
      setIsLaunching(false);
    }
  };

  const handleLaunchProject = (pr) => {
    if (!pr.projectId) return;
    setIsLaunching(true);
    router.push(`/dashboard/engineer/workspace/${pr.projectId}`);
  };

  /* ── Import an external git repo and open its workspace ──
     Writes a chat_sessions row with the user_repo_* fields so that when
     the workspace page opens, the backend's reconnect path loads the row,
     sees `user_repo_url`, and fires _background_preview to clone + install
     + start the dev server automatically (package manager is auto-detected
     from the lockfile — pnpm-lock.yaml > yarn.lock > package-lock.json). */
  const handleImportExistingRepo = async (force = false) => {
    if (!selectedRepo || !selectedBranch) return;
    if (!force && isAtProjectLimit()) { setPendingAction('import'); setShowUpgradeModal(true); return; }
    setIsImporting(true);
    setImportError("");
    try {
      const sb = getSupabaseBrowserClient();
      const {
        data: {user: authUser},
      } = await sb.auth.getUser();
      if (!authUser) {
        throw new Error("Not signed in");
      }

      // We drive the workspace by project_id, so allocate one up front —
      // the same URL will be stable across reconnects.
      const projectId = crypto.randomUUID();
      const repoUrl =
        selectedRepo.url ||
        (selectedProvider === "github"
          ? `https://github.com/${selectedRepo.name}`
          : selectedRepo.name);

      const {error: insertErr} = await sb.from("chat_sessions").insert({
        user_id: authUser.id,
        project_id: projectId,
        title: selectedRepo.name || "Imported project",
        user_repo_url: repoUrl,
        user_repo_provider: selectedProvider,
        // platform_repo_branch doubles as the "branch to use" on load
        // (workspace page reads it verbatim for both flows).
        platform_repo_branch: selectedBranch,
      });
      if (insertErr) throw insertErr;

      router.replace(`/dashboard/engineer/workspace/${projectId}`);
    } catch (err) {
      console.error("[Dashboard] Import error:", err);
      setImportError(err?.message || "Import failed — please try again.");
      setIsImporting(false);
    }
  };

  const hasProjects = platformRepos.length > 0 || conversations.length > 0;
  const isLoaded = !platformLoading && !convoLoading;

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  return (
    <div className="h-full bg-[#fefcfa] dark:bg-[#0d1117] flex flex-col">
      {/* ── Upgrade Modal ── */}
      {showUpgradeModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="relative w-full max-w-md bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] shadow-2xl p-8">
            <button
              onClick={() => setShowUpgradeModal(false)}
              className="absolute top-4 right-4 p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06]"
            >
              <X className="w-4 h-4" />
            </button>
            <div className="w-12 h-12 rounded-2xl bg-orange-50 dark:bg-orange-500/10 flex items-center justify-center mb-5">
              <Rocket className="w-6 h-6 text-[#dc5426]" />
            </div>
            <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-2">
              Upgrade to Pro
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
              You&apos;ve used your 1 free project. Upgrade to Pro for unlimited projects, code export, and more.
            </p>
            <div className="space-y-2 mb-6">
              {["Unlimited projects", "Code export to GitHub & GitLab", "CI/CD automation", "Advanced AI templates"].map((f) => (
                <div key={f} className="flex items-center gap-2.5 text-sm text-slate-600 dark:text-slate-300">
                  <div className="w-4 h-4 rounded-full bg-emerald-100 dark:bg-emerald-500/20 flex items-center justify-center shrink-0">
                    <svg viewBox="0 0 10 10" className="w-2.5 h-2.5 text-emerald-600 dark:text-emerald-400"><path d="M2 5l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/></svg>
                  </div>
                  {f}
                </div>
              ))}
            </div>
            <a
              href="/dashboard/engineer/billing"
              className="w-full py-3 rounded-xl text-sm font-bold flex items-center justify-center gap-2 bg-gradient-to-r from-[#dc5426] to-orange-500 text-white hover:opacity-90 shadow-sm shadow-orange-600/20 transition-all active:scale-[0.98]"
            >
              <CreditCard className="w-4 h-4" /> View Plans — from $24/mo
            </a>
            <button
              onClick={handleSkipUpgrade}
              className="w-full mt-3 py-2.5 rounded-xl text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-all"
            >
              Skip for now
            </button>
          </div>
        </div>
      )}
      <div className="flex-1 overflow-y-auto">
        {/* ── HOME HERO ── */}
        <div className="max-w-[800px] mx-auto px-8 pt-[52px] pb-10 text-center">
          {/* Mode Toggle — TOP */}
          <div className="flex justify-center mb-7">
            <div className="flex gap-1 bg-[#fefcfa] dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] p-[3px] rounded-[14px]">
              <button
                onClick={() => setHomeMode("build")}
                className={cn(
                  "flex items-center gap-[7px] px-[18px] py-2 rounded-[10px] text-[13px] font-medium transition-all duration-150",
                  homeMode === "build"
                    ? "bg-white dark:bg-[#0d1117] text-slate-900 dark:text-white font-semibold shadow-[0_1px_3px_rgba(0,0,0,0.07),0_1px_2px_rgba(0,0,0,0.04)]"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white",
                )}>
                <Plus className="w-3.5 h-3.5" /> Create new project
              </button>
              <button
                onClick={() => setHomeMode("import")}
                className={cn(
                  "flex items-center gap-[7px] px-[18px] py-2 rounded-[10px] text-[13px] font-medium transition-all duration-150",
                  homeMode === "import"
                    ? "bg-white dark:bg-[#0d1117] text-slate-900 dark:text-white font-semibold shadow-[0_1px_3px_rgba(0,0,0,0.07),0_1px_2px_rgba(0,0,0,0.04)]"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white",
                )}>
                <Download className="w-3.5 h-3.5" /> Import existing project
              </button>
            </div>
          </div>

          {homeMode === "build" ? (
            <>
              {/* Title */}
              <h1 className="text-[38px] font-[800] text-slate-900 dark:text-white tracking-[-0.04em] leading-[1.12]">
                What will you <span className="text-[#dc5426]">build next</span>
                ?
              </h1>
              <p className="text-[14.5px] text-slate-500 dark:text-slate-400 mt-[10px] leading-[1.6]">
                Describe your app idea and Lucid AI will generate a complete,
                working application.
              </p>

              {/* Composer */}
              <div
                className={cn(
                  "mt-7 text-left bg-white dark:bg-[#161b22] rounded-[20px] border overflow-hidden transition-all duration-150",
                  "shadow-[0_1px_3px_rgba(0,0,0,0.07),0_1px_2px_rgba(0,0,0,0.04)]",
                  promptText.trim()
                    ? "border-[#dc5426]/50"
                    : "border-slate-200 dark:border-[#2d333b]",
                )}>
                <textarea
                  ref={promptRef}
                  value={promptText}
                  onChange={(e) => setPromptText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && promptText.trim()) {
                      e.preventDefault();
                      handleBuildFromPrompt();
                    }
                  }}
                  placeholder="Describe the app you want to create..."
                  className="w-full px-[22px] pt-5 pb-[14px] text-[14.5px] leading-[1.65] bg-transparent text-slate-900 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none resize-none min-h-[110px]"
                />
                <div className="flex items-center justify-between px-[14px] py-[10px] border-t border-slate-100 dark:border-[#2d333b] bg-[oklch(99%_0.003_255)] dark:bg-[#161b22]">
                  <div className="flex items-center gap-[5px]">
                    <div className="relative">
                      <button
                        onClick={() => {
                          setShowAttachMenu(!showAttachMenu);
                          setShowAdvanced(false);
                        }}
                        type="button"
                        className="flex items-center gap-[5px] px-[10px] py-[5px] border border-slate-200 dark:border-[#2d333b] rounded-[6px] text-[12px] font-medium text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-[#fefcfa] dark:hover:bg-white/[0.04] transition-all">
                        <Paperclip className="w-3.5 h-3.5" /> Attach
                      </button>
                      {showAttachMenu && (
                        <>
                          <div
                            className="fixed inset-0 z-30"
                            onClick={() => setShowAttachMenu(false)}
                          />
                          <div className="absolute left-0 bottom-full mb-2 z-40 w-52 bg-white dark:bg-[#1c2128] rounded-xl border border-slate-200 dark:border-[#444c56] shadow-xl overflow-hidden py-1 animate-scale-in">
                            <button
                              onClick={() => {
                                setShowAttachMenu(false);
                                fileInputRef.current?.click();
                              }}
                              className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors">
                              <Paperclip className="w-4 h-4 text-slate-400" />{" "}
                              Attach file
                            </button>
                            <button
                              onClick={() => {
                                setShowAttachMenu(false);
                                const url = prompt("Enter a URL:");
                                if (url)
                                  setPromptText(
                                    `I need a website like this: ${url}`,
                                  );
                              }}
                              className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors">
                              <Link2 className="w-4 h-4 text-slate-400" /> Start
                              from URL
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept="image/*,video/*"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f)
                          setPromptText((p) => p + `\n[Attached: ${f.name}]`);
                      }}
                    />
                    <button
                      onClick={() => {
                        setShowAdvanced(!showAdvanced);
                        setShowAttachMenu(false);
                      }}
                      type="button"
                      className={cn(
                        "flex items-center gap-[5px] px-[10px] py-[5px] border rounded-[6px] text-[12px] font-medium transition-all",
                        showAdvanced
                          ? "border-slate-300 dark:border-slate-600 text-slate-700 dark:text-white bg-slate-100 dark:bg-white/[0.06]"
                          : "border-slate-200 dark:border-[#2d333b] text-slate-400 dark:text-slate-500 hover:text-slate-600 hover:bg-[#fefcfa] dark:hover:bg-white/[0.04]",
                      )}>
                      <SlidersHorizontal className="w-3.5 h-3.5" /> Template
                    </button>
                  </div>
                  <button
                    onClick={handleBuildFromPrompt}
                    type="button"
                    disabled={isLaunching}
                    className={cn(
                      "flex items-center gap-[6px] px-[18px] py-2 rounded-[10px] text-[13px] font-semibold transition-all duration-150 active:scale-[0.97]",
                      promptText.trim() && !isLaunching
                        ? "bg-[#dc5426] hover:bg-[#b8421e] text-white"
                        : "bg-[oklch(40%_0.01_265)] dark:bg-[#21262d] text-white dark:text-slate-400 opacity-80",
                    )}>
                    {isLaunching ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <>
                        Plan project <ArrowRight className="w-3.5 h-3.5" />
                      </>
                    )}
                  </button>
                </div>
                {showAdvanced && (
                  <div className="mx-[14px] mb-3 p-4 bg-slate-50 dark:bg-[#0d1117] rounded-xl border border-slate-200 dark:border-[#21262d] animate-slide-up">
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 block">
                          Stack
                        </label>
                        <CustomSelect
                          value={advancedOpts.stack}
                          onChange={(v) =>
                            setAdvancedOpts((p) => ({...p, stack: v}))
                          }
                          size="sm"
                          options={[
                            {value: "auto", label: "✨ Auto-detect"},
                            {value: "nextjs", label: "Next.js"},
                            {value: "react", label: "React"},
                            {value: "vue", label: "Vue.js"},
                            {value: "html-css", label: "HTML & CSS"},
                          ]}
                        />
                      </div>
                      <div>
                        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 block">
                          Backend
                        </label>
                        <CustomSelect
                          value={advancedOpts.backend}
                          onChange={(v) =>
                            setAdvancedOpts((p) => ({...p, backend: v}))
                          }
                          size="sm"
                          options={[
                            {value: "none", label: "No backend"},
                            {value: "supabase", label: "Supabase"},
                            {value: "own", label: "Own backend (MCP)"},
                          ]}
                        />
                      </div>
                      <div>
                        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 block">
                          Figma URL
                        </label>
                        <input
                          type="url"
                          value={advancedOpts.figmaUrl}
                          onChange={(e) =>
                            setAdvancedOpts((p) => ({
                              ...p,
                              figmaUrl: e.target.value,
                            }))
                          }
                          placeholder="Optional..."
                          className="w-full px-3 py-2 text-[12px] bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-lg text-slate-700 dark:text-slate-300 placeholder:text-slate-400 outline-none focus:border-[#dc5426]"
                        />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Chips */}
              <div className="flex flex-wrap items-center justify-center gap-[7px] mt-[18px]">
                <span className="text-[12px] text-slate-400 dark:text-slate-500 font-medium">
                  Quick start:
                </span>
                {IDEAS.map((chip) => (
                  <button
                    key={chip.label}
                    type="button"
                    onClick={() => {
                      setPromptText(chip.prompt);
                      promptRef.current?.focus();
                    }}
                    className="px-[13px] py-[5px] border border-slate-200 dark:border-[#2d333b] rounded-full text-[12.5px] font-medium text-slate-600 dark:text-slate-400 bg-white dark:bg-[#161b22] hover:border-[#dc5426]/50 hover:text-[#dc5426] hover:bg-orange-50 dark:hover:bg-orange-900/10 transition-all">
                    {chip.label}
                  </button>
                ))}
              </div>

              {/* Stats */}
              {hasProjects && (
                <div className="grid grid-cols-4 gap-[14px] text-left mt-8">
                  {[
                    {
                      label: "Projects",
                      value: totalProjects,
                      icon: Layers,
                      iconColor: "text-blue-500",
                      trend: "Total in workspace",
                    },
                    {
                      label: "Active",
                      value: activeCount,
                      icon: Zap,
                      iconColor: "text-[#dc5426]",
                      trend: "Currently building",
                    },
                    {
                      label: "Deployed",
                      value: deployedCount,
                      icon: Globe,
                      iconColor: "text-red-400",
                      trend: "Live now",
                    },
                    {
                      label: "This month",
                      value: thisMonthCount,
                      icon: Activity,
                      iconColor: "text-[#dc5426]",
                      trend: "+8 vs last month",
                    },
                  ].map((s) => (
                    <div
                      key={s.label}
                      className="bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-[12px] px-4 py-3">
                      <div className="flex items-center gap-2 mb-1.5">
                        <s.icon className={cn("w-4 h-4", s.iconColor)} />
                        <span className="text-[22px] font-extrabold text-slate-900 dark:text-white leading-none tracking-[-0.04em]">
                          {platformLoading ? "—" : s.value}
                        </span>
                      </div>
                      <p className="text-[12px] font-bold text-slate-700 dark:text-slate-200">
                        {s.label}
                      </p>
                      <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">
                        {s.trend}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              {/* ── IMPORT VIEW ── */}
              <h1 className="text-[38px] font-[800] text-slate-900 dark:text-white tracking-[-0.04em] leading-[1.12]">
                Connect an{" "}
                <span className="text-[#dc5426]">existing project</span>
              </h1>
              <p className="text-[14.5px] text-slate-500 dark:text-slate-400 mt-[10px] leading-[1.6]">
                Import from your Git provider and let Lucid AI understand and
                enhance your codebase.
              </p>

              {/* Provider cards */}
              <div className="grid grid-cols-3 gap-3 mt-5">
                {[
                  {
                    id: "github",
                    label: "GitHub",
                    icon: Github,
                    user: integrations.github?.username,
                    connected: !!integrations.github?.connected,
                    iconBg: "bg-slate-900",
                    iconColor: "text-white",
                  },
                  {
                    id: "gitlab",
                    label: "GitLab",
                    icon: Gitlab,
                    user: integrations.gitlab?.username,
                    connected: !!integrations.gitlab?.connected,
                    iconBg: "bg-[#fc6d26]",
                    iconColor: "text-white",
                  },
                  {
                    id: "bitbucket",
                    label: "Bitbucket",
                    icon: GitBranch,
                    user: null,
                    connected: false,
                    iconBg: "bg-[#0052cc]",
                    iconColor: "text-white",
                  },
                ].map((p) => {
                  const isSelected = selectedProvider === p.id;
                  return (
                    <button
                      key={p.id}
                      onClick={() =>
                        p.connected
                          ? setSelectedProvider(p.id)
                          : router.push("/dashboard/engineer/integrations")
                      }
                      className={cn(
                        "flex items-center gap-3 px-4 py-[8px] rounded-[14px] bg-white dark:bg-[#161b22] text-left transition-all w-full",
                        isSelected
                          ? "border-2 border-[#dc5426] shadow-[0_0_0_3px_rgba(220,84,38,0.08)]"
                          : "border-2 border-slate-200 dark:border-[#2d333b] hover:border-slate-300 dark:hover:border-[#444c56]",
                      )}>
                      {/* Icon */}
                      <div
                        className={cn(
                          "w-10 h-10 rounded-[10px] flex items-center justify-center shrink-0",
                          p.iconBg,
                        )}>
                        <p.icon
                          className={cn("w-[18px] h-[18px]", p.iconColor)}
                        />
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0 text-left">
                        <div className="text-[13.5px] font-bold text-slate-900 dark:text-white leading-tight">
                          {p.label}
                        </div>
                        {p.connected ? (
                          <div className="flex items-center gap-1 mt-[3px] min-w-0">
                            <span className="w-[6px] h-[6px] rounded-full bg-[#dc5426] shrink-0" />
                            <span className="text-[11.5px] text-slate-500 dark:text-slate-400 truncate">
                              Connected as{" "}
                              <strong className="font-semibold text-slate-700 dark:text-slate-300">
                                {p.user}
                              </strong>
                            </span>
                          </div>
                        ) : (
                          <div className="text-[11.5px] text-slate-400 mt-[3px]">
                            Not connected
                          </div>
                        )}
                      </div>

                      {/* Right side */}
                      {isSelected ? (
                        <div className="w-5 h-5 rounded-full bg-[#dc5426] flex items-center justify-center shrink-0">
                          <svg
                            className="w-3 h-3 text-white"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                            strokeWidth={3}>
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M5 13l4 4L19 7"
                            />
                          </svg>
                        </div>
                      ) : !p.connected ? (
                        <span className="shrink-0 px-3 py-[6px] border border-slate-200 dark:border-[#2d333b] rounded-[8px] text-[12px] font-semibold text-slate-600 dark:text-slate-400 bg-white dark:bg-transparent whitespace-nowrap">
                          Connect →
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>

              {/* Repo selector */}
              <div className="mt-2 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-[16px] overflow-hidden shadow-[0_1px_3px_rgba(0,0,0,0.06)]">
                <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100 dark:border-[#2d333b]">
                  <span className="text-[14px] font-bold text-slate-900 dark:text-white">
                    Select a repository
                  </span>
                  <div className="flex items-center gap-2 px-3 py-[7px] border border-slate-200 dark:border-[#2d333b] rounded-[10px] bg-[#fefcfa] dark:bg-[#0d1117] w-48">
                    <svg
                      className="w-3.5 h-3.5 text-slate-400 shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}>
                      <circle cx="11" cy="11" r="8" />
                      <path strokeLinecap="round" d="m21 21-4.35-4.35" />
                    </svg>
                    <input
                      value={repoSearch}
                      onChange={(e) => setRepoSearch(e.target.value)}
                      placeholder="Search repos..."
                      className="flex-1 text-[12.5px] bg-transparent text-slate-700 dark:text-slate-300 placeholder:text-slate-400 outline-none"
                    />
                  </div>
                </div>
                <div className="divide-y divide-slate-100 dark:divide-[#2d333b] max-h-[260px] overflow-y-auto">
                  {gitReposLoading ? (
                    <div className="flex items-center justify-center py-10 gap-2 text-slate-400">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span className="text-[13px]">Loading repositories…</span>
                    </div>
                  ) : gitRepos.length === 0 ? (
                    <div className="text-center py-10 text-[13px] text-slate-400">
                      No repositories found.{" "}
                      <button
                        onClick={() =>
                          router.push("/dashboard/engineer/integrations")
                        }
                        className="text-[#dc5426] underline">
                        Connect {selectedProvider}
                      </button>
                    </div>
                  ) : (
                    gitRepos
                      .filter((r) =>
                        r.name.toLowerCase().includes(repoSearch.toLowerCase()),
                      )
                      .map((repo) => (
                        // Use repo.id for equality — fetchGit* helpers don't expose
                        // `fullName`, so the old `repo.fullName === repo.fullName`
                        // compared undefined === undefined and marked every row selected.
                        <button
                          key={repo.id}
                          onClick={() => setSelectedRepo(repo)}
                          className={cn(
                            "w-full flex items-center gap-3 px-5 py-[14px] text-left transition-colors hover:bg-slate-50 dark:hover:bg-white/[0.02]",
                            selectedRepo?.id === repo.id &&
                              "bg-orange-50/50 dark:bg-orange-900/10",
                          )}>
                          {selectedProvider === "github" ? (
                            <Github className="w-4 h-4 text-slate-400 shrink-0" />
                          ) : (
                            <Gitlab className="w-4 h-4 text-slate-400 shrink-0" />
                          )}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-[13.5px] font-semibold text-slate-900 dark:text-white truncate">
                                {repo.name}
                              </span>
                              {repo.private && (
                                <span className="px-1.5 py-0.5 text-[10px] font-bold text-amber-700 bg-amber-100 dark:bg-amber-500/20 dark:text-amber-400 rounded-[4px] shrink-0">
                                  Private
                                </span>
                              )}
                            </div>
                            <p className="text-[11.5px] text-slate-400 mt-0.5 truncate">
                              {repo.defaultBranch}
                              {repo.description ? ` · ${repo.description}` : ""}
                            </p>
                          </div>
                          {selectedRepo?.id === repo.id && (
                            <div className="w-4 h-4 rounded-full bg-[#dc5426] flex items-center justify-center shrink-0">
                              <svg
                                className="w-2.5 h-2.5 text-white"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth={3}>
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M5 13l4 4L19 7"
                                />
                              </svg>
                            </div>
                          )}
                        </button>
                      ))
                  )}
                </div>
              </div>

              {/* Branch selector — shown after a repo is chosen */}
              {selectedRepo && (
                <div className="mt-3 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-[14px] px-5 py-4">
                  <div className="flex justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-[13px] text-justify font-bold text-slate-900 dark:text-white">
                        Branch
                      </p>
                      <p className="text-[11.5px] text-slate-400 mt-0.5">
                        Select the branch to import
                      </p>
                    </div>
                    {gitBranchesLoading ? (
                      <div className="flex items-center gap-1.5 text-slate-400 text-[12px]">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />{" "}
                        Loading…
                      </div>
                    ) : (
                      <select
                        value={selectedBranch || ""}
                        onChange={(e) => setSelectedBranch(e.target.value)}
                        className="px-3 py-2 text-[13px] font-medium bg-[#fefcfa] dark:bg-[#0d1117] border border-slate-200 dark:border-[#2d333b] rounded-[8px] text-slate-800 dark:text-slate-200 outline-none focus:border-[#dc5426] min-w-[160px]">
                        {gitBranches.map((b) => (
                          <option key={b.name} value={b.name}>
                            {b.name}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>
              )}

              {/* Import CTA */}
              <button
                onClick={handleImportExistingRepo}
                disabled={!selectedRepo || !selectedBranch || isImporting}
                className={cn(
                  "w-full mt-4 flex items-center justify-center gap-2 py-4 rounded-[14px] text-[14px] font-semibold transition-all",
                  selectedRepo && selectedBranch && !isImporting
                    ? "bg-[oklch(30%_0.01_265)] dark:bg-slate-800 text-white hover:bg-[oklch(25%_0.01_265)] active:scale-[0.99]"
                    : "bg-slate-200 dark:bg-[#21262d] text-slate-400 cursor-not-allowed",
                )}>
                {isImporting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Opening workspace…
                  </>
                ) : (
                  <>
                    Import &amp; analyze project{" "}
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
              {importError && (
                <p className="mt-2 text-[12px] text-red-600 dark:text-red-400 text-center">
                  {importError}
                </p>
              )}
            </>
          )}
        </div>

        {/* ── PROJECTS SECTION ── */}
        {homeMode === "build" && (
          <div className="px-8 lg:px-10 pb-10 max-w-[1100px] mx-auto mt-6">
            {isLoaded && !hasProjects ? (
              <HowItWorks />
            ) : (
              <div>
                <div className="flex items-center justify-between mb-5">
                  <div>
                    <h2 className="text-[15px] font-bold text-slate-800 dark:text-white">
                      Recent Projects
                    </h2>
                    <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">
                      Your latest work, sorted by activity
                    </p>
                  </div>
                  {platformRepos.length > 8 && (
                    <button
                      onClick={() =>
                        router.push("/dashboard/engineer/projects")
                      }
                      className="text-[12px] font-bold text-[#dc5426] hover:opacity-80 transition-opacity">
                      View all →
                    </button>
                  )}
                </div>
                {platformLoading ? (
                  <div className="max-w-sm">
                    <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-5">
                      <div className="flex items-start gap-3 mb-2.5">
                        <div className="w-10 h-10 rounded-xl bg-slate-100 dark:bg-[#21262d] animate-pulse shrink-0" />
                        <div className="flex-1 space-y-2 pt-1">
                          <div className="h-4 bg-slate-100 dark:bg-[#21262d] rounded w-3/5 animate-pulse" />
                        </div>
                      </div>
                      <div className="space-y-2 mb-3">
                        <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-4/5 animate-pulse" />
                        <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-2/3 animate-pulse" />
                      </div>
                      <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-1/2 animate-pulse" />
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                    {platformRepos.slice(0, 8).map((pr, idx) => (
                      <ProjectCard
                        key={pr.projectId}
                        project={pr}
                        index={idx}
                        onClick={() => handleLaunchProject(pr)}
                        isLaunching={isLaunching}
                      />
                    ))}
                    <GhostCard onClick={() => setShowWizard(true)} />
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
