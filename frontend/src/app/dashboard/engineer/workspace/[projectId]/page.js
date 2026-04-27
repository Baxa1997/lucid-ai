"use client";

// ─────────────────────────────────────────────────────────
//  Lucid AI — Full Page Conversation Workspace
//  3-Panel IDE Layout: [FileExplorer] | [Chat] | [Build/Preview/Code/Terminal]
// ─────────────────────────────────────────────────────────

import {useState, useRef, useEffect, useCallback, use} from "react";
import {useRouter} from "next/navigation";
import {cn} from "@/lib/utils";
import {WorkspaceErrorBoundary} from "@/components/WorkspaceErrorBoundary";
import {
  Settings,
  X,
  User,
  LayoutGrid,
  Users,
  Shield,
  SlidersHorizontal,
  Gift,
  BookOpen,
  HelpCircle,
  CreditCard,
  Sparkles,
  Plus,
  History,
  Diamond,
  Download,
  ChevronLeft,
  Lock,
  Rocket,
  ExternalLink,
  Copy,
  Check,
  RefreshCw,
} from "lucide-react";
import {useAgentSession} from "@/hooks/useAgentSession";
import agentWSManager from "@/lib/agentWSManager";
import {
  getConversation,
  getMessages,
  addMessage as saveMessage,
  updateConversation,
  getChatHistory,
} from "@/lib/conversations";
import {getSupabaseBrowserClient} from "@/lib/supabase/client";
import ExportCodeModal from "@/components/ExportCodeModal";
import {WorkspaceContext} from "@/contexts/WorkspaceContext";
import ChatPanel from "@/components/workspace/ChatPanel";
import RightPanel from "@/components/workspace/RightPanel";

// ── Main Page Component ────────────────────────────────────
export default function ConversationPage({params}) {
  return (
    <WorkspaceErrorBoundary>
      <ConversationPageInner params={params} />
    </WorkspaceErrorBoundary>
  );
}

function ConversationPageInner({params}) {
  const {projectId} = use(params);
  const router = useRouter();
  const conversationId = decodeURIComponent(projectId || "unknown");

  // ── Detect wizard mode IMMEDIATELY (sync, before any async work) ──
  // If sessionStorage has a wizard_prompt, this is a brand-new project from
  // the wizard. We skip blocking on conversation/git-token loading and
  // connect the WebSocket immediately.
  const [isWizardMode] = useState(() => {
    try {
      return !!sessionStorage.getItem(
        `wizard_prompt_${decodeURIComponent(projectId || "unknown")}`,
      );
    } catch {
      return false;
    }
  });

  // Build the wizard prompt eagerly so we can pass it in the WS handshake
  // Also store the user-visible description (without [LUCID_PROJECT] header)
  const [wizardDesc] = useState(() => {
    try {
      const descKey = `wizard_desc_${decodeURIComponent(projectId || "unknown")}`;
      return sessionStorage.getItem(descKey) || "";
    } catch {
      return "";
    }
  });

  const [wizardTask] = useState(() => {
    try {
      const key = `wizard_prompt_${decodeURIComponent(projectId || "unknown")}`;
      const prompt = sessionStorage.getItem(key);
      if (!prompt) return "";

      const cid = decodeURIComponent(projectId || "unknown");
      const metaKey = `wizard_meta_${cid}`;
      const metaStr = sessionStorage.getItem(metaKey);
      const descKey = `wizard_desc_${cid}`;

      let finalPrompt = prompt;
      if (metaStr) {
        try {
          const meta = JSON.parse(metaStr);
          const origDesc = sessionStorage.getItem(descKey) || "";

          const parts = [
            `description=${origDesc || "project"}`,
            `stack=${meta.stack || "nextjs"}`,
            `backend=${meta.backend || "none"}`,
          ];
          if (meta.projectType) parts.push(`project_type=${meta.projectType}`);
          if (meta.deployment) parts.push(`deployment=${meta.deployment}`);
          if (meta.figmaUrl) parts.push(`figma_url=${meta.figmaUrl}`);

          const header = `[LUCID_PROJECT] ${parts.join(" | ")}`;
          finalPrompt = `${header}\n\n${prompt}`;
        } catch (_) {}
      }

      // NOTE: Do NOT clear sessionStorage here.
      // It persists until we confirm the project was created (platform_repo_url
      // stored in DB). This lets re-entry after a failed generation retry
      // automatically. Cleanup happens in the repoInfo effect below.

      return finalPrompt;
    } catch {
      return "";
    }
  });

  // ── Conversation data from Supabase ─────────────────────
  const [conversation, setConversation] = useState(null);
  const [savedMessages, setSavedMessages] = useState([]);
  const [convLoading, setConvLoading] = useState(true);
  // Tracks the ID of the last message we processed for saving.
  // Using an ID instead of array length is safe when history is prepended
  // (which shifts all indices but doesn't change the tail).
  const prevLastMsgIdRef = useRef(null);

  // Repo info from chat_sessions (platform vs user repos).
  //
  // ``vercelUrl`` is what the iframe shows — populated exclusively by the
  // LIVE local dev server (via the WS ``preview_ready`` event). The deployed
  // Vercel URL from the DB is kept in ``deployedUrl`` and exposed only via
  // an explicit "Open deployed site" button, so the in-app preview always
  // reflects the current source rather than a possibly stale deployment.
  const [repoInfo, setRepoInfo] = useState({
    platformRepoUrl: null,
    userRepoUrl: null,
    userRepoProvider: null,
    vercelUrl: null,
    deployedUrl: null,
  });

  // Clear wizard sessionStorage once the project is confirmed complete in DB.
  // Trigger on any of the "project exists" signals — platform repo URL,
  // local preview, or published deploy URL. Without this, the wizard prompt
  // stays in sessionStorage and re-triggers generation on every visit.
  useEffect(() => {
    if (!repoInfo.platformRepoUrl && !repoInfo.vercelUrl && !repoInfo.deployedUrl) return;
    try {
      sessionStorage.removeItem(`wizard_prompt_${conversationId}`);
      sessionStorage.removeItem(`wizard_meta_${conversationId}`);
      sessionStorage.removeItem(`wizard_desc_${conversationId}`);
    } catch (_) {}
  }, [repoInfo.platformRepoUrl, repoInfo.vercelUrl, repoInfo.deployedUrl, conversationId]);

  // Load conversation and messages on mount (with timeout)
  // For wizard mode, use a much shorter timeout since we don't need this data to connect.
  useEffect(() => {
    let cancelled = false;
    const timeoutMs = isWizardMode ? 1500 : 5000;
    const timeout = setTimeout(() => {
      if (!cancelled) setConvLoading(false);
    }, timeoutMs);

    (async () => {
      try {
        const conv = await getConversation(conversationId);
        if (!cancelled && conv) {
          setConversation(conv);
          // Try unified chat_messages first (same table backend writes to).
          // Fall back to old messages table for legacy conversations.
          let msgs = await getChatHistory(conversationId);
          console.log("[Chat] chat_messages:", msgs.length, "items");
          if (!msgs.length) {
            msgs = await getMessages(conversationId);
            console.log("[Chat] fallback messages:", msgs.length, "items");
          }
          if (!cancelled) setSavedMessages(msgs);
        }

        // Load repo info from chat_sessions — always try, even without a conversation
        try {
          const sb = getSupabaseBrowserClient();
          const {data: sessions} = await sb
            .from("chat_sessions")
            .select(
              "platform_repo_url, user_repo_url, user_repo_provider, vercel_url",
            )
            .eq("project_id", conversationId)
            .order("created_at", {ascending: false})
            .limit(1);
          if (!cancelled && sessions?.[0]) {
            const storedUrl = sessions[0].vercel_url || null;
            // Ephemeral tunnel/sandbox URLs don't survive between sessions,
            // so never expose them as a "deployed site". localhost URLs are
            // also not something to offer as a public deployment.
            const isTemporaryUrl =
              storedUrl &&
              (storedUrl.includes(".e2b.dev") ||
                storedUrl.includes(".e2b.app") ||
                storedUrl.includes(".loca.lt") ||
                storedUrl.includes(".ngrok") ||
                storedUrl.includes(".trycloudflare.com") ||
                storedUrl.startsWith("http://localhost"));
            // Liveness probe (server-side — sees real HTTP status) so we
            // don't surface a dead Vercel deployment as "Open deployed
            // site". A fail-open strategy keeps the button when the probe
            // itself errors, because a flaky probe shouldn't hide a
            // working URL the user may want.
            let deployedUrl = null;
            if (storedUrl && !isTemporaryUrl) {
              try {
                const controller = new AbortController();
                const abortTimer = setTimeout(() => controller.abort(), 7000);
                const res = await fetch(
                  `/api/preview-probe?url=${encodeURIComponent(storedUrl)}`,
                  {signal: controller.signal, cache: "no-store"},
                );
                clearTimeout(abortTimer);
                const data = await res.json().catch(() => ({}));
                deployedUrl = data?.alive ? storedUrl : null;
                if (!data?.alive) {
                  console.warn(
                    "[Workspace] Stored deploy URL not alive — hiding 'Open deployed site' button:",
                    storedUrl, data,
                  );
                }
              } catch (probeErr) {
                console.warn("[Workspace] preview probe failed, keeping stored URL:", probeErr);
                deployedUrl = storedUrl;
              }
            }
            setRepoInfo({
              platformRepoUrl: sessions[0].platform_repo_url || null,
              userRepoUrl: sessions[0].user_repo_url || null,
              userRepoProvider: sessions[0].user_repo_provider || null,
              // iframe shows ONLY the live local preview (populated by the
              // WS preview_ready event). A stale deploy is never rendered.
              vercelUrl: null,
              deployedUrl,
            });
          }
        } catch (e) {
          console.warn("[Workspace] Could not load repo info:", e);
        }
      } catch (err) {
        console.error("Failed to load conversation:", err);
      } finally {
        if (!cancelled) {
          clearTimeout(timeout);
          setConvLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      clearTimeout(timeout);
    };
  }, [conversationId, isWizardMode]);

  // ── Auth token for WebSocket ────────────────────────────
  const [token, setToken] = useState("");
  const [gitToken, setGitToken] = useState("");
  const [userName, setUserName] = useState("");

  useEffect(() => {
    fetch("/api/agent/token")
      .then((r) => r.json())
      .then((d) => {
        if (d.token) setToken(d.token);
      })
      .catch(() => {});
    // Get user name for header subtitle
    const supabase = getSupabaseBrowserClient();
    supabase.auth.getUser().then(({data: {user}}) => {
      if (user) {
        const name =
          user.user_metadata?.full_name ||
          user.user_metadata?.name ||
          user.email?.split("@")[0] ||
          "";
        setUserName(name.split(" ")[0]); // First name only
      }
    });
  }, []);

  // Load git token from integrations — runs in PARALLEL, not blocked by conversation
  const [gitTokenLoaded, setGitTokenLoaded] = useState(false);
  useEffect(() => {
    // For wizard mode, mark as loaded immediately — backend uses its own token
    if (isWizardMode) {
      setGitTokenLoaded(true);
      // Still try to load in background for follow-up tasks
      (async () => {
        try {
          const {getIntegrations} = await import("@/lib/integrations");
          const intg = await getIntegrations();
          if (intg.github?.token) setGitToken(intg.github.token);
          else if (intg.gitlab?.token) setGitToken(intg.gitlab.token);
        } catch {}
      })();
      return;
    }

    // For non-wizard: wait for conversation, but also handle null (new conv)
    if (!conversation && !convLoading) {
      setGitTokenLoaded(true);
      return;
    }
    if (!conversation) return;

    (async () => {
      try {
        const {getIntegrations} = await import("@/lib/integrations");
        const intg = await getIntegrations();

        if (conversation.repo_provider === "github" && intg.github?.token) {
          setGitToken(intg.github.token);
        } else if (
          conversation.repo_provider === "gitlab" &&
          intg.gitlab?.token
        ) {
          setGitToken(intg.gitlab.token);
        } else if (!conversation.repo_provider) {
          if (intg.github?.token) setGitToken(intg.github.token);
          else if (intg.gitlab?.token) setGitToken(intg.gitlab.token);
        }
      } catch (err) {
        console.error("Failed to load git token:", err);
      } finally {
        setGitTokenLoaded(true);
      }
    })();
  }, [conversation, convLoading, isWizardMode]);

  // ── Agent session hook ──────────────────────────────────
  // For WIZARD projects: connect immediately with just the auth token.
  //   No repo URL or git token needed — backend handles everything.
  // For EXISTING projects: wait for conversation + git token before connecting.
  const effectiveToken = isWizardMode
    ? token // Fast path: connect as soon as auth token is ready
    : convLoading || !gitTokenLoaded
      ? ""
      : token;

  const {
    status,
    sessionId,
    messages,
    terminalLogs,
    files,
    setFiles,
    error,
    sendMessage,
    startSession,
    isReady,
    isPreparing,
    isReconnecting,
    stopSession,
    pushToBranch,
    setInitialMessages,
    steps,
    phases,
    completionSummary,
    deployUrl,
    previewUrl,
    previewLoading,
    previewStatusMsg,
    stopPreview,
    resolvingInfo,
    resolvingProgress,
    writtenFiles,
    fileContents,
    fileMetrics,
    errorStage,
    previewError,
    retryCount,
    retry,
    agentStatus,
    planAwaiting,
    currentPlanData,
    planConfirmed,
    confirmPlan,
    rejectPlan,
    previewFileMap,
  } = useAgentSession({
    projectId: conversationId,
    token: effectiveToken,
    task: wizardTask, // Pass wizard prompt in handshake for instant start
    // For platform-owned repos (wizard projects), do NOT send repoUrl here.
    // The backend resolver will recover it from DB and use PLATFORM_GITHUB_TOKEN
    // (which has access). The user's personal gitToken lacks org access.
    repoUrl: conversation?.is_platform_owned
      ? ""
      : conversation?.repo_url || "",
    gitToken,
    branch: conversation?.branch || "main",
  });

  // ── Derived: path classification from resolver ──────────
  const isNewProject = resolvingInfo?.path === "new_project";
  const isExistingProject = resolvingInfo?.path === "existing_repo";

  // Code tab is always visible — it shows an empty state until files are available.
  const codeTabVisible = true;

  // ── Sync URLs from WebSocket to repoInfo ───────────────────────────
  // Split into TWO channels:
  //   • previewUrl  → goes into ``vercelUrl``  (iframe src — live dev server)
  //   • deployUrl   → goes into ``deployedUrl`` (button target — Vercel)
  // A stored deploy URL must never hijack the iframe; the preview always
  // reflects the current source-of-truth, and publishing is an explicit
  // action the user takes via the "Open deployed site" button.
  useEffect(() => {
    if (deployUrl) {
      setRepoInfo((prev) => ({...prev, deployedUrl: deployUrl}));
    }
  }, [deployUrl]);

  useEffect(() => {
    if (previewUrl) {
      setRepoInfo((prev) => ({...prev, vercelUrl: previewUrl}));
    }
  }, [previewUrl]);

  // ── Layout state ────────────────────────────────────────
  const [chatOpen, setChatOpen] = useState(true);
  const [chatWidth, setChatWidth] = useState(360); // px — default open width
  const CHAT_MIN_WIDTH = 350;
  const CHAT_MAX_WIDTH = 520;
  const chatDragRef = useRef(null); // stores drag state without re-render
  const [chatDragging, setChatDragging] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);
  const [showPublishModal, setShowPublishModal] = useState(false);
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [copiedHeaderUrl, setCopiedHeaderUrl] = useState(false);
  const [publishVisibility, setPublishVisibility] = useState("private");
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState("");
  const [publishResult, setPublishResult] = useState(null); // { repoUrl, vercelUrl, message }

  const handlePublish = async () => {
    setPublishing(true);
    setPublishError("");
    try {
      const res = await fetch(`/api/publish/${projectId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visibility: publishVisibility }),
      });
      const data = await res.json();
      if (!res.ok) {
        setPublishError(data.error || `Publish failed (${res.status})`);
        return;
      }
      setPublishResult(data);
      setPublishConfirmed(true);
      // Persist the live URL into workspace state so reopening the modal
      // (or refreshing the page) shows the success state instead of the
      // visibility picker. Without this, vercelDeployUrl resets to null
      // until the next WS reconnect or page reload re-hydrates from DB.
      if (data.vercelUrl) {
        setRepoInfo((prev) => ({ ...prev, deployedUrl: data.vercelUrl }));
      }
    } catch (err) {
      setPublishError(err?.message || "Network error");
    } finally {
      setPublishing(false);
    }
  };

  // ── Subscription state (used by Upgrade button, usage meter, Export gate) ──
  // Single fetch on mount; refreshed when the workspace tab becomes visible
  // again (e.g. after returning from /billing in another tab).
  const [subscription, setSubscription] = useState(null);
  // Set to true when user clicks "Skip for testing" on the upgrade modal —
  // ExportCodeModal then sends bypassLimits=true to /api/export-code so the
  // full export flow runs end-to-end on the user's actual plan.
  const [exportBypassLimits, setExportBypassLimits] = useState(false);
  const [showExportUpgradeModal, setShowExportUpgradeModal] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const refresh = () =>
      fetch("/api/stripe/subscription")
        .then((r) => r.json())
        .then((d) => { if (!cancelled) setSubscription(d); })
        .catch(() => {});
    refresh();
    const onVisible = () => { if (document.visibilityState === "visible") refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const canExport = subscription?.limits?.canExport ?? false;

  // Click handler for the toolbar Export button — opens upgrade modal for
  // Free/Starter, normal export modal for Pro+.
  const handleExportClick = () => {
    if (canExport) {
      setExportBypassLimits(false);
      setShowExportModal(true);
    } else {
      setShowExportUpgradeModal(true);
    }
  };

  const handleExportSkip = () => {
    setShowExportUpgradeModal(false);
    setExportBypassLimits(true);
    setShowExportModal(true);
  };

  // ── ProjectDashboard handlers ──
  // Rename — writes title to chat_sessions via Supabase (same path the chat
  // history uses). Updates local conversation state on success.
  const handleProjectRename = async (newTitle) => {
    if (!conversationId) return;
    const sb = getSupabaseBrowserClient();
    const { error } = await sb
      .from('chat_sessions')
      .update({ title: newTitle })
      .eq('project_id', conversationId);
    if (error) throw error;
    setConversation((prev) => (prev ? { ...prev, title: newTitle } : prev));
  };

  // Delete — calls existing /api/delete-project, then routes to the dashboard.
  const handleProjectDelete = async () => {
    if (!conversationId) return;
    const res = await fetch('/api/delete-project', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId: conversationId }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || 'Delete failed');
    }
    router.replace('/dashboard/engineer');
  };

  // A "real" deployment URL — used by the Publish modal to show status and
  // copy/open buttons. The iframe's ``vercelUrl`` is now always the local
  // dev server, so the deploy URL lives in ``deployedUrl`` (populated from
  // the DB on load and from WS ``deployUrl`` events after a push/publish).
  const vercelDeployUrl = (() => {
    const u = repoInfo.deployedUrl;
    if (!u) return null;
    try {
      const parsed = new URL(u);
      if (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1") return null;
      return u;
    } catch {
      return null;
    }
  })();
  const [showAppDropdown, setShowAppDropdown] = useState(false);
  const [showProfileDropdown, setShowProfileDropdown] = useState(false);
  const appDropdownRef = useRef(null);
  const profileDropdownRef = useRef(null);
  // Ref-based flag: true = auto panel-switching is allowed.
  // Set to false when the user manually picks a tab; reset to true when they
  // send a new message so the next task gets the correct panel transitions.
  const panelOverrideRef = useRef(true);

  // ── Hydrate chat history from Supabase into the hook ────
  useEffect(() => {
    if (savedMessages.length > 0 && setInitialMessages) {
      setInitialMessages(savedMessages);
    }
  }, [savedMessages, setInitialMessages]);

  // ── Fetch file tree from GitLab when returning to an existing project ──
  // Only triggered for GitLab repos — GitHub repos get their tree from the WS
  // (background E2B clone sends a file_tree event after cloning).
  const fileTreeFetched = useRef(false);
  useEffect(() => {
    if (fileTreeFetched.current) return;
    if (files.length > 0) {
      fileTreeFetched.current = true;
      return;
    }
    // Only fetch from GitLab API for GitLab repos
    if (!conversation?.repo_name || conversation?.repo_provider !== "gitlab") return;

    fileTreeFetched.current = true;
    (async () => {
      try {
        const res = await fetch(
          `/api/gitlab/tree?repo=${encodeURIComponent(conversation.repo_name)}&ref=${encodeURIComponent(conversation.branch || "main")}`,
        );
        if (res.ok) {
          const data = await res.json();
          if (data.files && data.files.length > 0 && files.length === 0) {
            // Inject into the hook's files state
            if (typeof setFiles === "function") setFiles(data.files);
          }
        }
      } catch (err) {
        console.warn("[workspace] GitLab tree fetch failed:", err);
      }
    })();
  }, [conversation?.repo_name, conversation?.branch, conversation?.repo_provider, files.length]);

  // ── Auto-start wizard task when workspace becomes ready ──
  // NOTE: For wizard mode, the task is now sent in the WebSocket handshake
  // (via the `task` prop to useAgentSession). This effect is kept ONLY as a
  // safety net — the handshake path is the primary flow.
  const wizardAutoStarted = useRef(isWizardMode); // Already started if wizard mode
  useEffect(() => {
    if (wizardAutoStarted.current) return;

    // If wizard task was passed in handshake, just mark as started
    if (wizardTask) {
      wizardAutoStarted.current = true;
      return;
    }

    // Fallback: check sessionStorage — only runs if handshake missed the task
    // and the project was never successfully created (no platform_repo_url).
    if (status === "ready") {
      try {
        const key = `wizard_prompt_${conversationId}`;
        const prompt = sessionStorage.getItem(key);
        if (prompt && !repoInfo.platformRepoUrl) {
          wizardAutoStarted.current = true;
          // Don't clear sessionStorage here — the repoInfo effect handles cleanup
          // once the generation actually succeeds.
          setTimeout(() => startSession(prompt), 300);
        }
      } catch (_) {}
    }
  }, [
    status,
    conversationId,
    startSession,
    wizardTask,
    repoInfo.platformRepoUrl,
  ]);

  // ── Frontend save (guaranteed backup for legacy messages table) ──────────
  // Backend writes assistant messages to chat_messages (primary).
  // This effect writes only to the legacy 'messages' table as a fallback.
  // We track by last-message ID so that prepending history (which shifts
  // all indices) never causes already-saved messages to be re-processed.
  useEffect(() => {
    if (!conversation?.id || !messages || messages.length === 0) return;

    const lastMsg = messages[messages.length - 1];
    // Nothing new at the tail — history was prepended or a message was mutated.
    if (lastMsg.id === prevLastMsgIdRef.current) return;

    // Find where we left off (by the previous tail ID).
    const prevIdx = prevLastMsgIdRef.current
      ? messages.findIndex((m) => m.id === prevLastMsgIdRef.current)
      : -1;
    prevLastMsgIdRef.current = lastMsg.id;

    // Only process genuinely new messages appended after our last known tail.
    const newMessages = prevIdx >= 0 ? messages.slice(prevIdx + 1) : [lastMsg];

    for (const msg of newMessages) {
      if (msg.fromHistory) continue;
      // User messages saved in handleSend already — skip to avoid duplicates.
      if (msg.role === "user") continue;
      if ((msg.role === "agent" || msg.role === "assistant") && msg.content) {
        // Write only to the legacy table — backend owns chat_messages.
        saveMessage(conversation.id, {role: "assistant", content: msg.content});
      }
    }
  }, [messages, conversation?.id]);

  // ── Update conversation status in DB ────────────────────
  const prevStatusRef = useRef(null);
  useEffect(() => {
    if (!conversationId || prevStatusRef.current === status) return;
    prevStatusRef.current = status;

    if (status === "running") {
      updateConversation(conversationId, {status: "active"});
    } else if (status === "ready" && messages.length > 0) {
      // Only mark completed if there are messages (actual work was done)
      updateConversation(conversationId, {status: "completed"});
    } else if (status === "error") {
      updateConversation(conversationId, {status: "error"});
    }
  }, [status, conversationId, messages.length]);

  // ── 3-Panel IDE Layout State ─────────────────────────────
  // rightPanel: 'preview' | 'dashboard' | 'code' | 'terminal'
  // Default to "code" when returning to an existing workspace (manager snapshot
  // shows a live/ready session) to avoid the brief "Preview Not Available" flash
  // that appears while repoInfo loads asynchronously.
  const [rightPanel, setRightPanel] = useState(() => {
    if (typeof window === "undefined") return "preview";
    if (isWizardMode) return "preview";
    try {
      const mgr = agentWSManager;
      if (
        mgr?.projectId === conversationId &&
        mgr._statusSnapshot &&
        mgr._statusSnapshot !== "idle" &&
        mgr._statusSnapshot !== "connecting" &&
        mgr._chatSnapshot?.length > 0
      ) {
        // Returning to an existing workspace — start on Code.
        return "code";
      }
    } catch (_) {}
    return "preview";
  });

  // ── Building screen latch ─────────────────────────────────
  // Prevents two flicker bugs:
  //   1. "Building" shown during `idle` for existing projects (cold reconnect)
  //   2. Flash to "Preview Not Available" during the brief `ready` window
  //      between workspace init and first `running` state for wizard projects.
  //
  // Rules:
  //   - Always starts true — the workspace is always connecting on page load
  //   - Debounce 1.2s before hiding — absorbs the preparing→ready→running gap
  //   - For wizard: stays true through the whole build
  //   - For existing: clears ~1.2s after status reaches 'ready' with no active task
  // Initialize buildingActive from manager snapshot — avoids the 1.2s building screen
  // flash when returning to a workspace that is already in a ready/running state.
  const [buildingActive, setBuildingActive] = useState(() => {
    if (typeof window === "undefined") return true;
    const mgr = agentWSManager;
    if (
      mgr?.projectId === conversationId &&
      mgr._statusSnapshot &&
      mgr._statusSnapshot !== "idle" &&
      mgr._statusSnapshot !== "connecting" &&
      mgr._chatSnapshot?.length > 0
    ) {
      // Keep building screen only if agent is actively running
      return mgr._statusSnapshot === "running";
    }
    return true;
  });
  const buildingTimerRef = useRef(null);

  useEffect(() => {
    if (buildingTimerRef.current) {
      clearTimeout(buildingTimerRef.current);
      buildingTimerRef.current = null;
    }

    const hasActivePhase = phases.some((p) => p.status === "active");
    const isActivelyBuilding =
      status === "running" ||
      hasActivePhase ||
      status === "connecting" ||
      status === "cloning" ||
      status === "installing" ||
      status === "starting" ||
      status === "health_check" ||
      status === "preparing";

    // Keep building screen active while background preview is setting up
    // (cloning repo, installing deps, waiting for dev server health check).
    // Do NOT gate on !repoInfo.vercelUrl — the DB may have a stale/broken URL
    // stored from a previous session. We must show BuildingScreen until the
    // fresh WS-provided URL arrives, overwriting the stale one.
    const isBgPreviewRunning = previewLoading;

    if (isActivelyBuilding || isBgPreviewRunning) {
      setBuildingActive(true);
    } else if (
      // Don't start the debounce-hide during initial idle — wait for at least
      // one real connection attempt before hiding the building screen.
      status !== "idle"
    ) {
      if (
        isWizardMode &&
        !repoInfo.vercelUrl &&
        !previewError &&
        status === "ready"
      ) {
        setBuildingActive(true);
        return;
      }
      // Debounce: absorbs the brief `ready` window between workspace init and task start
      buildingTimerRef.current = setTimeout(() => {
        setBuildingActive(false);
        buildingTimerRef.current = null;
      }, 1200);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, phases, isWizardMode, repoInfo.vercelUrl, previewError, previewLoading]);

  // Cleanup latch timer on unmount
  useEffect(
    () => () => {
      if (buildingTimerRef.current) clearTimeout(buildingTimerRef.current);
    },
    [],
  );

  // ── HMR failure detection ───────────────────────────────
  // When the agent finishes writing files, check if any written file is a config
  // or new route — those can't be hot-reloaded and require a full page refresh.
  // We detect the transition running → ready and fire a hard reload if needed.
  const _HMR_HARD_RELOAD_RE =
    /vite\.config\.|next\.config\.|tailwind\.config\.|postcss\.config\.|package\.json$|tsconfig\.|\/page\.[tj]sx?$|\/layout\.[tj]sx?$/;
  const prevStatusForHMRRef = useRef(null);
  useEffect(() => {
    const prev = prevStatusForHMRRef.current;
    prevStatusForHMRRef.current = status;

    if (prev === "running" && status === "ready" && repoInfo.vercelUrl) {
      const needsHardReload = writtenFiles.some((f) =>
        _HMR_HARD_RELOAD_RE.test(f),
      );
      if (needsHardReload) {
        // Give the dev server 2s to finish restarting, then hard-reload the iframe.
        setTimeout(() => {
          const iframe = iframeRef.current;
          if (!iframe) return;
          try {
            iframe.contentWindow.location.reload();
          } catch {
            // Cross-origin fallback: reset src to trigger a reload
            const src = iframe.src;
            iframe.src = "";
            setTimeout(() => {
              iframe.src = src;
            }, 100);
          }
        }, 2000);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  // Track previous vercelUrl to detect the transition null → url (first time preview is ready).
  const prevVercelUrlRef = useRef(repoInfo.vercelUrl);
  useEffect(() => {
    const prev = prevVercelUrlRef.current;
    prevVercelUrlRef.current = repoInfo.vercelUrl;
    // When a preview URL first becomes available, always switch to Preview
    // regardless of what tab the user is on. Reset panelOverrideRef so future
    // status-based auto-switches can still fire (e.g. next task → running → preview).
    if (!prev && repoInfo.vercelUrl) {
      setRightPanel("preview");
      panelOverrideRef.current = true;
    }
  }, [repoInfo.vercelUrl]);

  // Auto-switch right panel on status transitions — only when the user hasn't
  // manually picked a tab (panelOverrideRef.current === true).
  // Using a ref (not state) means reading/writing never triggers a re-render,
  // and the effect deps are just [status] so it only fires on actual transitions.
  const prevStatusForPanel = useRef(null);
  useEffect(() => {
    if (prevStatusForPanel.current === status) return;
    prevStatusForPanel.current = status;

    // Initial workspace setup — show preview so the building animation is visible.
    if (
      (status === "cloning" || status === "preparing") &&
      panelOverrideRef.current
    ) {
      setRightPanel("preview");
    }

    // Clone complete for existing project — switch to Preview.
    // Wizard projects skip this (they go straight to 'running').
    if (status === "ready" && !isWizardMode && panelOverrideRef.current) {
      setRightPanel("preview");
    }

    // Agent actively working — show preview.
    if (status === "running" && panelOverrideRef.current) {
      setRightPanel("preview");
    }
  }, [status, isWizardMode]);

  // Auto-switch to preview when a live URL arrives from WebSocket.
  // Watches deployUrl/previewUrl directly (not repoInfo.vercelUrl) so it fires
  // only for fresh WS events, not for DB-loaded URLs on page entry.
  // Always switch — a fresh preview URL is significant enough to override the
  // user's last tab choice (they want to see the running app).
  useEffect(() => {
    if (deployUrl) {
      setRightPanel("preview");
      panelOverrideRef.current = true;
    }
  }, [deployUrl]);

  useEffect(() => {
    if (previewUrl) {
      setRightPanel("preview");
      panelOverrideRef.current = true;
    }
  }, [previewUrl]);

  const iframeRef = useRef(null);

  // Close app/profile dropdowns on outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (
        appDropdownRef.current &&
        !appDropdownRef.current.contains(e.target)
      ) {
        setShowAppDropdown(false);
      }
      if (
        profileDropdownRef.current &&
        !profileDropdownRef.current.contains(e.target)
      ) {
        setShowProfileDropdown(false);
      }
    };
    if (showAppDropdown || showProfileDropdown) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showAppDropdown, showProfileDropdown]);

  // ── Render ─────────────────────────────────────────────
  const ctxValue = {
    // Identifiers
    conversationId,
    sessionId,
    // Session state
    messages,
    status,
    isPreparing,
    terminalLogs,
    files,
    error,
    errorStage,
    previewError,
    previewLoading,
    previewStatusMsg,
    retryCount,
    retry,
    phases,
    completionSummary,
    resolvingInfo,
    resolvingProgress,
    isNewProject,
    // Actions
    sendMessage,
    stopSession,
    stopPreview,
    // Conversation data
    conversation,
    setConversation,
    convLoading,
    isWizardMode,
    wizardDesc,
    repoInfo,
    // Panel state
    rightPanel,
    setRightPanel,
    buildingActive,
    // Refs (stable, don't trigger re-renders)
    iframeRef,
    panelOverrideRef,
    // Modal trigger
    setShowExportModal,
    // Live agent status — shown in chat panel during generation
    agentStatus,
    // Plan confirmation
    planAwaiting,
    currentPlanData,
    planConfirmed,
    confirmPlan,
    rejectPlan,
    // WebContainers file map — triggers browser sandbox boot in RightPanel
    previewFileMap,
    // Per-file content + metrics for inline DiffViewer in the Code tab
    fileContents,
    fileMetrics,
    // Project-dashboard surface (rendered inside RightPanel's "dashboard" tab)
    subscription,
    handleProjectRename,
    handleProjectDelete,
    router,
    vercelDeployUrl,
  };

  return (
    <WorkspaceContext.Provider value={ctxValue}>
      <div className="flex flex-col h-screen bg-[#f8f9fb] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">
        {/* Reconnecting banner */}
        {isReconnecting && (
          <div className="flex items-center justify-center gap-2 px-4 py-2 bg-yellow-50 dark:bg-yellow-900/20 border-b border-yellow-200 dark:border-yellow-800 text-yellow-800 dark:text-yellow-300 text-sm font-medium">
            <svg
              className="animate-spin h-4 w-4 shrink-0"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24">
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            Reconnecting to workspace…
          </div>
        )}
        {/* ── Upgrade modal (shown when Free/Starter user clicks Export) ── */}
        {showExportUpgradeModal && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
            <div className="relative w-full max-w-md bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] shadow-2xl p-8">
              <button
                onClick={() => setShowExportUpgradeModal(false)}
                className="absolute top-4 right-4 p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06]">
                <X className="w-4 h-4" />
              </button>
              <div className="w-12 h-12 rounded-2xl bg-orange-50 dark:bg-orange-500/10 flex items-center justify-center mb-5">
                <Rocket className="w-6 h-6 text-[#dc5426]" />
              </div>
              <h2 className="text-xl font-bold text-slate-900 dark:text-white mb-2">Code export is a Pro feature</h2>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
                Push your generated project to GitHub, GitLab, or Bitbucket — included on Pro and Enterprise plans.
              </p>
              <div className="space-y-2 mb-6">
                {["Push to GitHub, GitLab, or Bitbucket", "Optional CI/CD pipeline", "Public or private repo", "Includes README and Dockerfile"].map((f) => (
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
                className="w-full py-3 rounded-xl text-sm font-bold flex items-center justify-center gap-2 bg-gradient-to-r from-[#dc5426] to-orange-500 text-white hover:opacity-90 shadow-sm shadow-orange-600/20 transition-all active:scale-[0.98]">
                <CreditCard className="w-4 h-4" /> Upgrade to Pro — from $59/mo
              </a>
              <button
                onClick={handleExportSkip}
                className="w-full mt-3 py-2.5 rounded-xl text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-all">
                Skip for testing
              </button>
            </div>
          </div>
        )}

        {/* Export Code Modal */}
        <ExportCodeModal
          isOpen={showExportModal}
          bypassLimits={exportBypassLimits}
          onClose={() => { setShowExportModal(false); setExportBypassLimits(false); }}
          projectSlug={
            conversation?.repo_name?.split("/").pop() ||
            (conversation?.title || "project")
              .toLowerCase()
              .replace(/[^a-z0-9]+/g, "-")
              .replace(/^-|-$/g, "")
              .slice(0, 50) ||
            conversationId
          }
          projectId={conversationId}
        />

        {/* ── Publish Modal ────────────────────────────────── */}
        {showPublishModal && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center"
            onClick={(e) => {
              if (e.target === e.currentTarget) {
                setShowPublishModal(false);
              }
            }}>
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
            <div className="relative bg-white dark:bg-[#161b22] rounded-2xl shadow-2xl border border-slate-200 dark:border-[#2d333b] w-full max-w-md mx-4 overflow-hidden">
              {/* Header — single unified state, no pre/post split */}
              <div className="flex items-center justify-between px-6 pt-6 pb-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-slate-900 dark:bg-white flex items-center justify-center">
                    <svg
                      className="w-5 h-5 text-white dark:text-slate-900"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}>
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4"
                      />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-[15px] font-bold text-slate-900 dark:text-white">
                      Publish your project
                    </h3>
                    <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">
                      Make your project available on the internet
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setShowPublishModal(false)}
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors">
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}>
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </div>

              {/* Body — single unified layout: URL+copy on top, visibility below */}
              {(() => {
                const liveUrl = publishResult?.vercelUrl || vercelDeployUrl || "";
                const hasUrl = !!liveUrl;
                const buildingNow = publishConfirmed && publishResult?.vercelUrl;
                return (
                  <div className="px-6 pb-4 space-y-3">
                    {/* URL row — always visible. Disabled when no URL yet. */}
                    <div>
                      <label className="block text-[11.5px] font-semibold text-slate-600 dark:text-slate-400 mb-1.5">
                        Live URL
                      </label>
                      <div
                        className={cn(
                          "flex items-center gap-2 h-9 pl-3 pr-1 rounded-xl border bg-slate-50 dark:bg-[#0d1117] transition-colors",
                          hasUrl
                            ? "border-slate-200 dark:border-[#2d333b]"
                            : "border-slate-200 dark:border-[#2d333b] opacity-70",
                        )}>
                        <div
                          className={cn(
                            "w-1.5 h-1.5 rounded-full shrink-0",
                            buildingNow
                              ? "bg-amber-500 animate-pulse"
                              : hasUrl
                                ? "bg-emerald-500"
                                : "bg-slate-300 dark:bg-slate-600",
                          )}
                        />
                        <input
                          type="text"
                          readOnly
                          disabled={!hasUrl}
                          value={
                            hasUrl
                              ? liveUrl.replace(/^https?:\/\//, "")
                              : "Your URL will appear here after publishing"
                          }
                          className={cn(
                            "flex-1 min-w-0 bg-transparent border-0 outline-none text-[12px] truncate",
                            hasUrl
                              ? "text-slate-700 dark:text-slate-200 cursor-text"
                              : "text-slate-400 dark:text-slate-500 cursor-not-allowed",
                          )}
                          title={hasUrl ? liveUrl : ""}
                        />
                        <button
                          type="button"
                          disabled={!hasUrl}
                          onClick={async () => {
                            if (!hasUrl) return;
                            try {
                              await navigator.clipboard.writeText(liveUrl);
                              setCopiedUrl(true);
                              setTimeout(() => setCopiedUrl(false), 2000);
                            } catch {}
                          }}
                          className={cn(
                            "shrink-0 w-7 h-7 flex items-center justify-center rounded-lg transition-all",
                            hasUrl
                              ? "text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-200 dark:hover:bg-white/[0.08]"
                              : "text-slate-300 dark:text-slate-600 cursor-not-allowed",
                          )}
                          title={hasUrl ? "Copy link" : "Publish first to get a link"}>
                          {copiedUrl ? (
                            <Check className="w-3.5 h-3.5 text-[#16a34a]" />
                          ) : (
                            <Copy className="w-3.5 h-3.5" />
                          )}
                        </button>
                        {hasUrl && (
                          <button
                            type="button"
                            onClick={() => window.open(liveUrl, "_blank")}
                            className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-200 dark:hover:bg-white/[0.08] transition-all"
                            title="Open in new tab">
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                            </svg>
                          </button>
                        )}
                      </div>
                      {buildingNow && (
                        <p className="mt-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                          Building on Vercel — give it ~30s, then refresh.
                        </p>
                      )}
                    </div>

                    {/* Visibility select */}
                    <div>
                      <label
                        htmlFor="publish-visibility-select"
                        className="block text-[11.5px] font-semibold text-slate-600 dark:text-slate-400 mb-1.5">
                        Visibility
                      </label>
                      <select
                        id="publish-visibility-select"
                        value={publishVisibility}
                        onChange={(e) => setPublishVisibility(e.target.value)}
                        disabled={publishing}
                        className="w-full h-9 px-3 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] text-[13px] text-slate-700 dark:text-slate-200 outline-none focus:border-[#111827] dark:focus:border-white transition-colors disabled:opacity-60 disabled:cursor-not-allowed">
                        <option value="private">Private — only you can open the project</option>
                        <option value="public">Public — anyone with the link can open it</option>
                      </select>
                    </div>

                    {publishError && (
                      <div className="px-3 py-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-900/40 text-[12px] text-red-700 dark:text-red-300">
                        {publishError}
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Actions — single primary button: "Publish" or "Update" */}
              <div className="px-6 pb-6 flex items-center gap-2">
                <button
                  onClick={handlePublish}
                  disabled={publishing}
                  className={cn(
                    "flex-1 h-9 flex items-center justify-center gap-2 rounded-xl text-[13px] font-semibold transition-all",
                    publishing
                      ? "bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 cursor-wait"
                      : "bg-[#111827] dark:bg-white text-white dark:text-[#111827] hover:bg-[#1f2937] dark:hover:bg-slate-100 shadow-sm",
                  )}>
                  {publishing ? (
                    <>
                      <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
                        <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                      </svg>
                      Publishing…
                    </>
                  ) : vercelDeployUrl || publishResult?.vercelUrl ? (
                    <>
                      <RefreshCw className="w-3.5 h-3.5" />
                      Update
                    </>
                  ) : (
                    <>
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
                      </svg>
                      Publish
                    </>
                  )}
                </button>
                <button
                  onClick={() => setShowPublishModal(false)}
                  disabled={publishing}
                  className="h-9 px-4 rounded-xl text-[13px] font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors border border-slate-200 dark:border-[#2d333b] disabled:opacity-50">
                  Done
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ════════════════════════════════════════════════
          TOP HEADER BAR — Base44 pixel-perfect
      ════════════════════════════════════════════════ */}
        <header className="shrink-0 h-[52px] bg-[#f8f9fc] dark:bg-[#161b22] border-b border-[#e8eaef] dark:border-[#2d333b] flex items-center px-3 z-20">
          {/* Left: Platform logo / App dropdown / History / Sidebar toggle */}
          <div
            className="flex items-center gap-2 min-w-0"
            style={{flex: "0 0 auto"}}>
            {/* Platform logo — triggers profile/workspace dropdown */}
            <div className="relative" ref={profileDropdownRef}>
              <button
                onClick={() => {
                  setShowProfileDropdown((v) => !v);
                  setShowAppDropdown(false);
                }}
                className="w-8 h-8 rounded-xl bg-gradient-to-br from-[#dc5426] to-orange-600 flex items-center justify-center shrink-0 hover:opacity-90 transition-opacity">
                <svg width="16" height="14" viewBox="0 0 16 14" fill="none">
                  <rect y="0" width="16" height="2" rx="1" fill="white" />
                  <rect y="6" width="16" height="2" rx="1" fill="white" />
                  <rect y="12" width="16" height="2" rx="1" fill="white" />
                </svg>
              </button>

              {/* Profile / Workspace dropdown — Base44 style */}
              {showProfileDropdown && (
                <div className="absolute top-full left-0 mt-1.5 w-[272px] bg-white dark:bg-[#1c1c1e] border border-[#e5e7eb] dark:border-[#2d333b] rounded-xl shadow-lg z-50 overflow-hidden">
                  {/* User info header */}
                  <div className="flex items-center gap-3 px-4 py-3 border-b border-[#f0f0f0] dark:border-[#2d333b]">
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-[#dc5426] to-orange-600 flex items-center justify-center shrink-0 text-white font-bold text-[15px]">
                      {userName ? userName.charAt(0).toUpperCase() : "U"}
                    </div>
                    <div className="min-w-0">
                      <p className="text-[14px] font-semibold text-slate-900 dark:text-white leading-tight truncate">
                        {userName || "My Workspace"}
                      </p>
                      <p className="text-[12px] text-slate-400 dark:text-slate-500 leading-tight">
                        Free plan
                      </p>
                    </div>
                  </div>

                  {/* Back to all apps */}
                  <button
                    onClick={() => {
                      setShowProfileDropdown(false);
                      window.location.href = "/dashboard/engineer";
                    }}
                    className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 dark:hover:bg-white/[0.04] border-b border-[#f0f0f0] dark:border-[#2d333b] transition-colors">
                    <ChevronLeft className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    <span className="text-[13px] text-slate-600 dark:text-slate-300">
                      All apps
                    </span>
                  </button>

                  {/* ── Plan + usage card (reads /api/stripe/subscription) ── */}
                  {(() => {
                    const planName = subscription?.plan
                      ? subscription.plan.charAt(0).toUpperCase() + subscription.plan.slice(1)
                      : "Free";
                    const tokensUsed   = subscription?.usage?.tokensUsed ?? 0;
                    const tokenQuota   = subscription?.limits?.monthlyTokenQuota ?? 0;
                    const projUsed     = subscription?.usage?.projectsCreated ?? 0;
                    const projLimit    = subscription?.limits?.maxProjectsPerMonth;
                    const extraBalance = subscription?.extraTokenBalance ?? 0;

                    const tokenPct = tokenQuota > 0 ? Math.min(100, (tokensUsed / tokenQuota) * 100) : 0;
                    const fmtTok = (n) => n >= 1_000_000 ? `${(n/1_000_000).toFixed(n%1_000_000===0?0:1)}M` : n >= 1_000 ? `${Math.round(n/1_000)}k` : `${n}`;

                    return (
                      <div className="mx-3 my-3 border border-slate-200 dark:border-[#2d333b] rounded-xl overflow-hidden bg-[#fafafa] dark:bg-[#0d1117]">
                        {/* Plan name header */}
                        <div className="flex items-center justify-between px-4 pt-4 pb-2">
                          <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">
                            {planName} plan
                          </p>
                          <span className="text-[10px] uppercase tracking-wider font-bold text-[#dc5426]">
                            {subscription?.isPaid ? "Active" : "Free"}
                          </span>
                        </div>

                        {/* Tokens this month */}
                        <div className="px-4 pb-3">
                          <p className="text-[12px] text-slate-500 dark:text-slate-400 mb-2">
                            Tokens this month
                          </p>
                          <div className="flex items-center gap-3">
                            <div className="flex-1 h-[8px] bg-orange-100 dark:bg-slate-700 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-[#dc5426] rounded-full transition-all"
                                style={{width: `${tokenPct}%`}}
                              />
                            </div>
                            <span className="text-[12px] text-slate-700 dark:text-slate-300 shrink-0 font-medium">
                              {fmtTok(tokensUsed)}/{fmtTok(tokenQuota)}
                            </span>
                          </div>
                          {extraBalance > 0 && (
                            <p className="text-[11px] text-emerald-600 dark:text-emerald-400 mt-1.5">
                              +{fmtTok(extraBalance)} extra (carry-over)
                            </p>
                          )}
                        </div>

                        {/* Projects */}
                        <div className="flex items-center justify-between px-4 py-3 border-t border-slate-100 dark:border-[#2d333b]">
                          <span className="text-[13px] text-slate-600 dark:text-slate-200">
                            {subscription?.plan === "free" ? "Projects (lifetime)" : "Projects this month"}
                          </span>
                          <span className="text-[13px] text-slate-700 dark:text-slate-300 font-medium">
                            {projUsed}/{projLimit ?? "∞"}
                          </span>
                        </div>

                        {/* CTA */}
                        <div className="px-4 pt-3 pb-4 border-t border-slate-100 dark:border-[#2d333b]">
                          <a
                            href="/dashboard/engineer/billing"
                            className="text-[13px] font-semibold text-[#dc5426] hover:text-[#b8421e] transition-colors block">
                            {subscription?.isPaid ? "Manage plan" : "Upgrade your plan"}
                          </a>
                        </div>
                      </div>
                    );
                  })()}

                  {/* Menu items */}
                  <div className="py-1.5 border-t border-[#f0f0f0] dark:border-[#2d333b]">
                    {[
                      {
                        icon: Settings,
                        label: "Settings",
                        href: "/dashboard/engineer/settings",
                      },
                      {
                        icon: CreditCard,
                        label: "Pricing plans",
                        href: "/dashboard/engineer/billing",
                      },
                      {icon: Gift, label: "Win free credits", href: null},
                      {icon: BookOpen, label: "Documentation", href: null},
                      {icon: HelpCircle, label: "Get help", href: null},
                    ].map(({icon: Icon, label, href}) => (
                      <button
                        key={label}
                        onClick={() => {
                          setShowProfileDropdown(false);
                          if (href) window.location.href = href;
                        }}
                        className="w-full flex items-center gap-3 px-4 py-2 text-[13px] text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors text-left">
                        <Icon className="w-3.5 h-3.5 text-slate-400 dark:text-slate-500 shrink-0" />
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Breadcrumb separator */}
            <span className="text-slate-300 dark:text-slate-600 text-[18px] font-light select-none">
              /
            </span>

            {/* App title — dropdown trigger */}
            <div className="relative" ref={appDropdownRef}>
              <button
                onClick={() => {
                  setShowAppDropdown((v) => !v);
                  setShowProfileDropdown(false);
                }}
                className={cn(
                  "flex items-center gap-2 px-2 py-1 rounded-lg transition-colors",
                  showAppDropdown
                    ? "bg-slate-100 dark:bg-white/[0.06]"
                    : "hover:bg-slate-100 dark:hover:bg-white/[0.06]",
                )}>
                {/* App icon — rounded square like Base44 */}
                <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-[#dc5426] to-orange-600 flex items-center justify-center shrink-0 overflow-hidden">
                  <Sparkles className="w-3.5 h-3.5 text-white" />
                </div>
                <div className="flex flex-col justify-center min-w-0 text-left">
                  <span className="text-[13px] font-semibold text-slate-900 dark:text-white leading-tight truncate max-w-[140px]">
                    {conversation?.title || wizardDesc || "New Project"}
                  </span>
                  <span className="text-[11px] text-slate-400 dark:text-slate-500 leading-tight truncate max-w-[140px]">
                    {userName ? `${userName}'s Workspace W...` : "AI Workspace"}
                  </span>
                </div>
              </button>

              {/* App dropdown */}
              {showAppDropdown && (
                <div className="absolute top-full left-0 mt-1.5 w-52 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl shadow-xl z-50 py-1.5 overflow-hidden">
                  {[
                    {icon: LayoutGrid, label: "App Overview"},
                    {icon: Users, label: "Users"},
                    {icon: Shield, label: "Security"},
                    {icon: SlidersHorizontal, label: "App Settings"},
                  ].map(({icon: Icon, label}) => (
                    <button
                      key={label}
                      onClick={() => setShowAppDropdown(false)}
                      className="w-full flex items-center gap-3 px-4 py-2.5 text-[14px] text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.06] transition-colors text-left">
                      <Icon className="w-4 h-4 text-slate-500 dark:text-slate-400 shrink-0" />
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* History */}
            <button className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors shrink-0">
              <History className="w-4 h-4" />
            </button>

            {/* Sidebar toggle — bordered button matching Base44 style */}
            <button
              onClick={() => setChatOpen(!chatOpen)}
              className="p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-white hover:bg-slate-50 dark:hover:bg-white/[0.06] rounded-lg border border-slate-200 dark:border-[#2d333b] transition-colors shrink-0"
              title={chatOpen ? "Hide Chat" : "Show Chat"}>
              {chatOpen ? (
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round">
                  <line x1="3" y1="4" x2="3" y2="20" />
                  <polyline points="11 8 7 12 11 16" />
                  <line x1="7" y1="12" x2="21" y2="12" />
                </svg>
              ) : (
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round">
                  <line x1="3" y1="4" x2="3" y2="20" />
                  <polyline points="13 8 17 12 13 16" />
                  <line x1="7" y1="12" x2="17" y2="12" />
                </svg>
              )}
            </button>
          </div>

          {/* Center: Tab Switcher — Base44 segmented control style */}
          <div className="flex-1 flex items-center justify-center">
            <div
              className="flex items-center p-0.5 rounded-[8px] gap-0.5"
              style={{background: "rgba(0,0,0,0.05)"}}>
              {[
                {key: "preview", label: "Preview", visible: true},
                {key: "dashboard", label: "Dashboard", visible: true},
                {key: "code", label: "Code", visible: codeTabVisible},
              ]
                .filter((tab) => tab.visible)
                .map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => {
                      setRightPanel(tab.key);
                      panelOverrideRef.current = false;
                    }}
                    className={cn(
                      "h-[26px] px-3 text-[13px] transition-all rounded-[6px] whitespace-nowrap",
                      rightPanel === tab.key
                        ? "bg-white dark:bg-[#2d333b] font-semibold text-[#111827] dark:text-white shadow-sm"
                        : "font-normal text-[#6b7280] dark:text-slate-400 hover:text-[#374151] dark:hover:text-white",
                    )}>
                    {tab.label}
                  </button>
                ))}
            </div>
          </div>

          {/* Right: Actions — Base44 pixel-perfect */}
          <div className="flex items-center gap-1" style={{flex: "0 0 auto"}}>
            {/* Avatar + invite button */}
            <div className="flex items-center gap-1.5 mr-1">
              {/* User avatar — 28px circle */}
              <div className="w-7 h-7 rounded-full bg-slate-500 flex items-center justify-center overflow-hidden shrink-0 text-white text-[11px] font-bold">
                <User className="w-3.5 h-3.5 text-white" />
              </div>
              {/* Invite collaborator — separate 22px circle with border */}
              <button
                className="w-[22px] h-[22px] rounded-full border border-[#d1d5db] dark:border-[#444c56] flex items-center justify-center shrink-0 text-[#6b7280] dark:text-slate-400 hover:border-[#9ca3af] hover:bg-[#f3f4f6] dark:hover:bg-white/[0.06] transition-all"
                title="Invite collaborator">
                <Plus className="w-3 h-3" />
              </button>
            </div>

            {/* Upgrade — only show when user is on a non-paid plan */}
            {!subscription?.isPaid && (
              <a
                href="/dashboard/engineer/billing"
                className="flex items-center gap-1.5 h-8 px-3 rounded-lg text-[13px] font-semibold transition-all ml-1"
                style={{
                  background:
                    "linear-gradient(85deg, rgba(220,84,38,0.15) -70.38%, rgba(234,88,12,0.10) 98.95%)",
                  border: "1px solid rgba(220,84,38,0.35)",
                  color: "#dc5426",
                }}
                title="Upgrade to unlock more">
                <Diamond className="w-3.5 h-3.5 fill-current" />
                Upgrade
              </a>
            )}

            {/* Export — locked for non-Pro plans; click opens upgrade modal */}
            <button
              onClick={handleExportClick}
              className={cn(
                "h-8 flex items-center gap-1.5 px-3 rounded-lg text-[13px] font-semibold border transition-all",
                canExport
                  ? "border-[#e5e7eb] dark:border-[#444c56] text-[#374151] dark:text-slate-200 hover:bg-[#f3f4f6] dark:hover:bg-white/[0.06]"
                  : "border-[#e5e7eb] dark:border-[#444c56] text-[#9ca3af] dark:text-slate-500 hover:bg-[#f9fafb] dark:hover:bg-white/[0.04]",
              )}
              title={canExport ? "Export code" : "Export is a Pro feature — click to upgrade"}>
              {canExport ? <Download className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
              Export
            </button>

            {/* Publish — always shows the visibility selector first. If a
                live URL already exists, we surface it as a small banner inside
                the modal so the user can re-publish (push latest changes) on
                the same flow as the first publish. Previous behaviour skipped
                straight to the "live!" view, which masked Vercel deploy
                failures and produced stale URLs. */}
            <button
              onClick={() => {
                setPublishConfirmed(false);
                // Surface any prior URL as a banner so the user knows the
                // current state, but DON'T pre-fill publishResult — that's
                // the "we just published" state and would jump past selector.
                setPublishResult(null);
                setPublishError("");
                setCopiedUrl(false);
                setPublishVisibility("private");
                setShowPublishModal(true);
              }}
              className="h-8 flex items-center px-4 rounded-lg text-[13px] font-semibold bg-[#111827] dark:bg-white text-white dark:text-[#111827] hover:bg-[#1f2937] dark:hover:bg-slate-100 transition-all"
              title={vercelDeployUrl ? "Open publish settings" : "Publish"}>
              Publish
            </button>
          </div>
        </header>

        {/* ════════════════════════════════════════════════
          BODY — 2-panel layout: Chat (left) + Preview/Code (right)
        ════════════════════════════════════════════════ */}
        <div className="flex-1 flex min-h-0 overflow-hidden">
          {/* ══ CHAT PANEL (left, collapsible + resizable) ══ */}
          <div
            className={cn(
              "shrink-0 flex flex-col min-w-0 bg-[#f8f9fc] dark:bg-[#0d1117] overflow-hidden",
              !chatDragging && "transition-all duration-300 ease-in-out",
              chatOpen
                ? "border-r border-[#e3e5eb] dark:border-[#21262d]"
                : "border-r-0",
            )}
            style={{width: chatOpen ? chatWidth : 0}}>
            <ChatPanel />
          </div>
          {/* end chat panel */}

          {/* ── Resize handle — only visible when chat is open ── */}
          {chatOpen && (
            <div
              className="shrink-0 w-1 relative group cursor-col-resize z-10 select-none"
              style={{marginLeft: -1}}
              onPointerDown={(e) => {
                e.preventDefault();
                const startX = e.clientX;
                const startWidth = chatWidth;
                setChatDragging(true);
                chatDragRef.current = {startX, startWidth};

                const onMove = (me) => {
                  const delta = me.clientX - chatDragRef.current.startX;
                  const next = Math.min(
                    CHAT_MAX_WIDTH,
                    Math.max(
                      CHAT_MIN_WIDTH,
                      chatDragRef.current.startWidth + delta,
                    ),
                  );
                  setChatWidth(next);
                };
                const onUp = () => {
                  setChatDragging(false);
                  chatDragRef.current = null;
                  window.removeEventListener("pointermove", onMove);
                  window.removeEventListener("pointerup", onUp);
                };
                window.addEventListener("pointermove", onMove);
                window.addEventListener("pointerup", onUp);
              }}>
              {/* Visible drag pill */}
              <div className="absolute inset-y-0 left-0 right-0 flex items-center justify-center">
                <div
                  className={cn(
                    "w-[3px] h-12 rounded-full transition-all duration-150",
                    chatDragging
                      ? "bg-gradient-to-br from-[#dc5426] to-orange-600 opacity-100 scale-y-110"
                      : "bg-slate-300 dark:bg-slate-600 opacity-0 group-hover:opacity-100",
                  )}
                />
              </div>
            </div>
          )}

          <RightPanel />
        </div>
        {/* end body flex */}
      </div>
    </WorkspaceContext.Provider>
  );
}
