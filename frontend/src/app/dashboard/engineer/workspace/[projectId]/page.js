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
import { WorkspaceContext } from "@/contexts/WorkspaceContext";
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

  // Repo info from chat_sessions (platform vs user repos)
  const [repoInfo, setRepoInfo] = useState({
    platformRepoUrl: null,
    userRepoUrl: null,
    userRepoProvider: null,
    vercelUrl: null,
  });

  // Clear wizard sessionStorage once platform_repo_url is confirmed in DB.
  // This means the project was successfully created — no need to retry on next visit.
  useEffect(() => {
    if (!repoInfo.platformRepoUrl) return;
    try {
      sessionStorage.removeItem(`wizard_prompt_${conversationId}`);
      sessionStorage.removeItem(`wizard_meta_${conversationId}`);
      sessionStorage.removeItem(`wizard_desc_${conversationId}`);
    } catch (_) {}
  }, [repoInfo.platformRepoUrl, conversationId]);

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
            // Don't load temporary tunnel URLs — they expire between sessions.
            // Localtunnel (loca.lt), ngrok, localhost are all session-scoped.
            // Only permanent deployment URLs (e.g. vercel.app) should be restored.
            const isTemporaryUrl =
              storedUrl &&
              (storedUrl.includes("loca.lt") ||
                storedUrl.includes("localtunnel") ||
                storedUrl.includes("ngrok.io") ||
                storedUrl.includes("localhost"));
            setRepoInfo({
              platformRepoUrl: sessions[0].platform_repo_url || null,
              userRepoUrl: sessions[0].user_repo_url || null,
              userRepoProvider: sessions[0].user_repo_provider || null,
              vercelUrl: isTemporaryUrl ? null : storedUrl,
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
    stopSession,
    pushToBranch,
    setInitialMessages,
    steps,
    phases,
    completionSummary,
    deployUrl,
    previewUrl,
    resolvingInfo,
    resolvingProgress,
    writtenFiles,
    errorStage,
    previewError,
    retryCount,
    retry,
  } = useAgentSession({
    projectId: conversationId,
    token: effectiveToken,
    task: wizardTask, // Pass wizard prompt in handshake for instant start
    repoUrl: conversation?.repo_url || "",
    gitToken,
    branch: conversation?.branch || "main",
  });

  // ── Derived: path classification from resolver ──────────
  const isNewProject = resolvingInfo?.path === "new_project";
  const isExistingProject = resolvingInfo?.path === "existing_repo";

  // Code tab is always visible — it shows an empty state until files are available.
  const codeTabVisible = true;

  // ── Sync deployUrl / previewUrl from WebSocket to repoInfo (live update) ──
  useEffect(() => {
    if (deployUrl) {
      setRepoInfo((prev) => ({...prev, vercelUrl: deployUrl}));
    }
  }, [deployUrl]);

  // Live Preview URL from dev server tunnel (takes priority during dev)
  useEffect(() => {
    if (previewUrl && !repoInfo.vercelUrl) {
      setRepoInfo((prev) => ({...prev, vercelUrl: previewUrl}));
    }
    // repoInfo.vercelUrl must be in deps — otherwise the condition reads a stale capture
    // and can overwrite a deployUrl that arrived earlier.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewUrl, repoInfo.vercelUrl]);

  // ── Layout state ────────────────────────────────────────
  const [chatOpen, setChatOpen] = useState(true);
  const [chatWidth, setChatWidth] = useState(360); // px — default open width
  const CHAT_MIN_WIDTH = 350;
  const CHAT_MAX_WIDTH = 520;
  const chatDragRef = useRef(null); // stores drag state without re-render
  const [chatDragging, setChatDragging] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);
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
  // If the WS hasn't sent a file_tree yet but we have a repo_name, fetch from GitLab.
  const fileTreeFetched = useRef(false);
  useEffect(() => {
    if (fileTreeFetched.current) return;
    if (files.length > 0) {
      fileTreeFetched.current = true;
      return;
    }
    if (!conversation?.repo_name) return;

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
  }, [conversation?.repo_name, conversation?.branch, files.length]);

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

    if (isActivelyBuilding) {
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
  }, [status, phases, isWizardMode, repoInfo.vercelUrl, previewError]);

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

    // Clone complete for existing project — switch to Code so file tree is visible.
    // Wizard projects skip this (they go straight to 'running').
    if (status === "ready" && !isWizardMode && panelOverrideRef.current) {
      setRightPanel("code");
    }

    // Agent actively working — show preview.
    if (status === "running" && panelOverrideRef.current) {
      setRightPanel("preview");
    }
  }, [status, isWizardMode]);

  // Auto-switch to preview when build completes and vercelUrl is available
  useEffect(() => {
    if (repoInfo.vercelUrl && rightPanel === "build") {
      setRightPanel("preview");
    }
  }, [repoInfo.vercelUrl, rightPanel]);

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
  };

  return (
    <WorkspaceContext.Provider value={ctxValue}>
    <div
      className="flex flex-col h-screen bg-[#f8f9fb] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">
      {/* Export Code Modal */}
      <ExportCodeModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
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
              className="w-8 h-8 rounded-xl bg-[#ef6820] flex items-center justify-center shrink-0 hover:opacity-90 transition-opacity">
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
                  <div className="w-9 h-9 rounded-full bg-[#ef6820] flex items-center justify-center shrink-0 text-white font-bold text-[15px]">
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

                {/* Credits card */}
                <div className="mx-3 my-3 border border-slate-200 dark:border-[#2d333b] rounded-xl overflow-hidden bg-[#fafafa] dark:bg-[#0d1117]">
                  {/* Message credits */}
                  <div className="px-4 pt-4 pb-3">
                    <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 mb-2.5">
                      Message credits
                    </p>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-[8px] bg-[#f0e8e0] dark:bg-slate-700 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-orange-500 rounded-full"
                          style={{width: "0%"}}
                        />
                      </div>
                      <span className="text-[13px] text-slate-700 dark:text-slate-300 shrink-0 font-medium">
                        0/25
                      </span>
                    </div>
                  </div>

                  {/* Daily Credits */}
                  <div className="flex items-center justify-between px-4 py-3 border-t border-slate-100 dark:border-[#2d333b]">
                    <span className="text-[13px] text-slate-600 dark:text-slate-200">
                      Daily Credits
                    </span>
                    <span className="text-[13px] text-slate-700 dark:text-slate-300 font-medium">
                      0/5
                    </span>
                  </div>

                  {/* Integration credits */}
                  <div className="px-4 pt-3 pb-4 border-t border-slate-100 dark:border-[#2d333b]">
                    <p className="text-[13px] text-slate-600 dark:text-slate-200 mb-2">
                      Integration credits
                    </p>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-[8px] bg-[#f0e8e0] dark:bg-slate-700 rounded-full" />
                      <span className="text-[13px] text-slate-700 dark:text-slate-300 shrink-0 font-medium">
                        0/100
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-2">
                      Renews monthly
                    </p>
                    <button className="text-[13px] font-semibold text-[#ef6820] hover:text-orange-600 mt-1 transition-colors block">
                      Upgrade your plan
                    </button>
                  </div>
                </div>

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
              <div className="w-7 h-7 rounded-lg bg-[#ef6820] flex items-center justify-center shrink-0 overflow-hidden">
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

          {/* Upgrade — exact Base44 gradient: soft peach to cream, orange border, orange text */}
          <button
            className="flex items-center gap-1.5 h-8 px-3 rounded-lg text-[13px] font-semibold transition-all ml-1"
            style={{
              background:
                "linear-gradient(85deg, rgba(255,102,0,0.26) -70.38%, rgba(255,222,162,0.18) 98.95%)",
              border: "1px solid #FFCBB4",
              color: "#FF631F",
            }}>
            <Diamond className="w-3.5 h-3.5 fill-current" />
            Upgrade
          </button>

          {/* Export — outlined button next to Publish */}
          <button
            onClick={() => setShowExportModal(true)}
            className="h-8 flex items-center gap-1.5 px-3 rounded-lg text-[13px] font-semibold border border-[#e5e7eb] dark:border-[#444c56] text-[#374151] dark:text-slate-200 hover:bg-[#f3f4f6] dark:hover:bg-white/[0.06] transition-all"
            title="Export code">
            <Download className="w-3.5 h-3.5" />
            Export
          </button>

          {/* Publish — solid dark h-8 */}
          <button
            onClick={() => {
              if (repoInfo.vercelUrl) window.open(repoInfo.vercelUrl, "_blank");
            }}
            className="h-8 flex items-center px-4 rounded-lg text-[13px] font-semibold bg-[#111827] dark:bg-white text-white dark:text-[#111827] hover:bg-[#1f2937] dark:hover:bg-slate-100 transition-all"
            title="Publish to Vercel">
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
            !chatOpen && "border-r-0",
          )}
          style={{
            width: chatOpen ? chatWidth : 0,
            borderRight: chatOpen ? "1px solid #e3e5eb" : "none",
          }}>
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
                    ? "bg-[#ef6820] opacity-100 scale-y-110"
                    : "bg-[#d1d5db] opacity-0 group-hover:opacity-100",
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
