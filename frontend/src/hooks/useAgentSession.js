'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — useAgentSession Hook
//  Uses the global AgentWSManager so the connection survives
//  sidebar navigation (component unmount/remount).
//
//  PRODUCTION MODE:
//  - Backend sends structured step messages
//  - Backend handles ALL persistence to Supabase
//  - Frontend only renders steps + final summary
//  - Frontend READS history from DB on mount (never writes)
// ─────────────────────────────────────────────────────────

import { useState, useRef, useCallback, useEffect } from 'react';
import manager from '@/lib/agentWSManager';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';
import { createMessageDispatcher } from './agentMessageHandlers';
import { useAgentConnection } from './useAgentConnection';
import { useAgentActions } from './useAgentActions';

// NOTE: The bulky WS-message dispatcher (~1200 lines, 50+ message types)
// lives in agentMessageHandlers.js. It's a pure function that takes a
// deps bag (state setters, refs, helper callbacks). This hook owns the
// deps and threads them in via a ref so handleMessage stays identity-
// stable across renders (no re-subscribe churn).

/**
 * useAgentSession — manages the full lifecycle of an AI agent session.
 */
export function useAgentSession({ projectId, task = '', token = '', repoUrl = '', repoProvider = '', gitToken = '', branch = '', autoStart = false }) {
  // ── State ────────────────────────────────────────────────
  const [state, setState] = useState('idle');
  const [sessionId, setSessionId] = useState(null);

  // Track whether the initial wizard user message was pre-populated in state.
  // Prevents [token] effect from adding a duplicate when token arrives.
  const initialTaskPreloaded = useRef(false);

  // Pre-populate wizard user message synchronously so it's visible on first render —
  // no flicker/swap with a synthetic JSX placeholder.
  const [chatMessages, setChatMessages] = useState(() => {
    const taskArg = task || '';
    if (!taskArg) return [];
    const displayText = taskArg.includes('\n\n')
      ? taskArg.split('\n\n').slice(1).join('\n\n').trim()
      : taskArg.trim();
    if (!displayText) return [];
    initialTaskPreloaded.current = true;
    return [{ id: 'init_msg_0', role: 'user', content: displayText, ts: Date.now() }];
  });
  const [logs, setLogs] = useState([]);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState(null);
  const historyLoadedRef = useRef(false);
  // Mirror of chatMessages that callbacks (chat_message dedup, etc.) can
  // read synchronously without going through setState. Kept in sync below.
  const chatMessagesRef = useRef([]);

  // ── Structured progress steps for current task ───────────
  // Each step: { id, step, label, done }
  const [steps, setSteps] = useState([]);
  const [finishSummary, setFinishSummary] = useState('');

  // Completion summary — persists after pipeline finishes so TaskProgress
  // can render a polished completion card instead of raw chat text.
  const [completionSummary, setCompletionSummary] = useState('');

  // ── Structured phases for TaskProgress UI ─────────────────
  // Each phase: { phase, title, description, status }
  const [phases, setPhases] = useState([]);

  // ── Declared phase list (Phase 2 Step 2) ──────────────────
  // Backend emits ONE `pipeline.declare` event at the start of every run
  // listing every phase this pipeline will go through. The TaskProgress
  // chart renders against this list rather than assuming 1-8. When no
  // declare has been seen yet (legacy backend or in-flight reconnect),
  // consumers fall back to the numeric phase fields on task_phase events.
  //
  // Shape: { pipelineId: 'new'|'edit', phases: [{key, label, index}, ...] }
  const [declaredPipeline, setDeclaredPipeline] = useState(null);

  // Canonical current workflow status, emitted directly by the prompt router
  // and executing pipeline. UI surfaces render this verbatim.
  const [agentStatus, setAgentStatus] = useState(null);

  // ── Resolving info — path classification from backend ──────
  // Set once per connection during the RESOLVING state.
  // { path, message, detail, stack?, templateName?, description?, repoDisplay?, branch? }
  const [resolvingInfo, setResolvingInfo] = useState(null);
  // Live progress during resolving (latest message + 0-100 pct)
  const [resolvingProgress, setResolvingProgress] = useState({ message: '', pct: 0 });

  // ── Preview state (noVNC / E2B legacy) ───────────────────
  // previewUrl is persisted to sessionStorage so page refresh restores the
  // iframe instead of showing a blank panel — the backend only emits
  // `preview_ready` once per sandbox boot and does not re-emit on WS reconnect.
  const _previewStorageKey = projectId ? `ws_preview_${projectId}` : null;
  // Never restore previewUrl from sessionStorage on mount — the stored URL
  // points at an ephemeral dev-server port that may be dead (e.g. after a
  // container restart). Restoring it causes the iframe to immediately load
  // a 502 page. The backend re-emits preview_ready on reconnect within
  // seconds if the server is still alive; otherwise the spinner stays until
  // bg_preview restarts it — both are correct and non-jarring.
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewTaskId, setPreviewTaskId] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewStatusMsg, setPreviewStatusMsg] = useState('');
  // Raw stage from backend's preview_status events (cloning / installing /
  // starting / health_check / install_done / restarting / etc.). Drives the
  // stepper UI in RightPanel so the user sees "Step 2 of 4" instead of just
  // a static "Setting up preview…" label.
  const [previewStage, setPreviewStage] = useState('');
  // Wall-clock timestamp of when preview loading started (first preview_status
  // or auto-launch). Used to show elapsed time so the user can tell that
  // something is actually progressing during a multi-minute install.
  const [previewStartedAt, setPreviewStartedAt] = useState(null);
  // Latches true on the first `preview_ready` event. Lets the UI tell apart
  // "preview hasn't started yet" (show preparing) from "preview was running
  // and stopped" (show restart). Reset only by stopPreview.
  const [previewEverReady, setPreviewEverReady] = useState(false);

  // ── WebContainers preview ─────────────────────────────────
  // Sandpack preview — { files: Record<string,string>, template: string }
  // sent by backend preview_files message; RightPanel renders SandpackPreview.
  const [previewFileMap, setPreviewFileMap] = useState(null);

  // ── Vercel deploy URL (persists after deployment) ────────
  const [deployUrl, setDeployUrl] = useState(null);

  // ── Quality-gate report ──────────────────────────────────
  // Backend's landing_quality_gate emits one `quality_report` event per
  // generation when the page is finished. The shape matches
  // landing_quality_gate.evaluate(): { purpose, checks: [...], summary }.
  // We hold the latest report so a panel above the chat can surface
  // failed checks ("application form missing", "no pay numbers") and
  // wire each one to a future "regenerate this section" action.
  const [qualityReport, setQualityReport] = useState(null);

  // ── Written files in the current agent run (for HMR failure detection) ──
  // Accumulates filenames from file_write_event; reset at the start of each task.
  const [writtenFiles, setWrittenFiles] = useState([]);

  // ── Per-file metrics + content snapshots (powers the inline DiffViewer) ──
  //   fileContents : { [filename]: { current: string, previous: string|null } }
  //                  current = latest content seen; previous = last-but-one
  //                  (lets us diff "what just changed" without storing history).
  //   fileMetrics  : { [filename]: { writes, lastAction, lastPhase, lastSize,
  //                                  firstWriteTs, lastWriteTs, elapsedMs } }
  const [fileContents, setFileContents] = useState({});
  const [fileMetrics, setFileMetrics] = useState({});

  // ── Error recovery (Phase 8) ─────────────────────────────
  // errorStage: which init step failed fatally ('clone' | 'install' | null)
  //   → drives the workspace error card in the Preview panel
  // previewError: non-fatal preview failure (dev server / tunnel)
  //   → drives a subtle "Restart Preview" card; workspace stays READY
  // retryCount: how many times the user has clicked retry (never resets)
  //   → after 3 attempts, show fallback "contact support" card
  const [errorStage, setErrorStage] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [retryCount, setRetryCount] = useState(0);

  // ── Structured error surface ─────────────────────────────
  // errorCode: machine-readable error from the backend
  //   AUTH_EXPIRED   → prompt re-auth (sign in again)
  //   PAT_INVALID    → prompt user to reconnect GitHub/GitLab
  //   SANDBOX_DEAD   → container gone, recovery is reconnect
  //   PHASE_TIMEOUT  → a generation phase hit its hard cap
  //   RECONNECT_FAILED → 3 reconnects failed, manual retry required
  const [errorCode, setErrorCode] = useState(null);

  // ── Live agent ACTIVITY pill (Phase 2 Step 5) ────────────────────────────────
  // Decoupled from task_phase: task_phase drives the TaskProgress phase chart,
  // while agentActivity surfaces *what the agent is doing right now* as a
  // transient pill above the chat input. Driven by:
  //   - typed sub-step events (image_binder.summary, quality.summary, build.*,
  //     repo.create_*, fixers.run_started, brief.distill_started,
  //     research.started, cli.session_started, code.write_started)
  //   - agent_event thoughts + tool use
  // NOT driven by task_phase — that signal lives in the phase chart, which
  // already has its own label/description columns.
  // Shape: { icon: string, message: string, kind: string, ts: number } | null
  const [agentActivity, setAgentActivity] = useState(null);
  // Auto-clear ref — when nothing updates the pill for N seconds, fade it out.
  const activityTimerRef = useRef(null);
  const pushAgentActivity = useCallback((next) => {
    if (activityTimerRef.current) {
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = null;
    }
    if (!next) {
      setAgentActivity(null);
      return;
    }
    setAgentActivity({ ...next, ts: Date.now() });
    // 8s idle → fade. Fresh updates restart the timer above.
    activityTimerRef.current = setTimeout(() => {
      setAgentActivity(null);
      activityTimerRef.current = null;
    }, 8000);
  }, []);
  // Clear the pill when the agent is no longer actively running. The
  // typed-event ts is fresh-ish, but stuck "Running…" lines after
  // completion are a worse UX than nothing.
  useEffect(() => {
    if (state === 'ready' || state === 'error' || state === 'idle') {
      if (activityTimerRef.current) {
        clearTimeout(activityTimerRef.current);
        activityTimerRef.current = null;
      }
      setAgentActivity(null);
    }
  }, [state]);

  // ── Awaiting-response flag (replaces the optimistic setState('running')) ──
  //
  // The send action sets this true; the dispatcher clears it on EVERY
  // backend message (so any response — chat, clarification, error,
  // workspace_state — releases the flag). This gives the chat UI a way
  // to show "Sending…" feedback after a submit without lying about
  // workspace state. Compare to the previous behaviour where the FE set
  // state='running' optimistically — that pretended the agent was busy
  // even when the backend was about to reject the message in <100ms.
  const [awaitingResponse, setAwaitingResponse] = useState(false);

  // ── Plan confirmation state ──────────────────────────────
  // planAwaiting: backend has sent a plan and is waiting for confirm/reject
  // currentPlanData: the planData object from the latest plan message
  // planConfirmed: true after user clicks Confirm (from either panel or chat bubble)
  const [planAwaiting, setPlanAwaiting] = useState(false);
  const [currentPlanData, setCurrentPlanData] = useState(null);
  const [planConfirmed, setPlanConfirmed] = useState(false);

  // ── Refs ─────────────────────────────────────────────────
  const idCounter = useRef(0);
  const initialTaskRef = useRef(task);
  const reconnectCount = useRef(0);
  // One-shot guard so a 4010 close runs supabase.auth.refreshSession() at
  // most once per session — if the refresh can't recover the token we fall
  // through to the user-facing AUTH_EXPIRED error. Reset on each clean open.
  const authRefreshAttemptedRef = useRef(false);

  // Store volatile props in refs so callbacks don't go stale
  const tokenRef = useRef(token);
  const projectIdRef = useRef(projectId);
  const repoUrlRef = useRef(repoUrl);
  const repoProviderRef = useRef(repoProvider);
  const gitTokenRef = useRef(gitToken);
  const branchRef = useRef(branch);

  tokenRef.current = token;
  projectIdRef.current = projectId;
  repoUrlRef.current = repoUrl;
  repoProviderRef.current = repoProvider;
  gitTokenRef.current = gitToken;
  branchRef.current = branch;

  // Supabase auto-refreshes the access token in the background, but the
  // `token` prop captured at hook mount goes stale on long sessions. Always
  // ask the client for the latest session before reconnecting so a tab that
  // sat idle past the 1h JWT TTL doesn't loop on AUTH_EXPIRED.
  const getFreshToken = useCallback(async () => {
    try {
      const supabase = getSupabaseBrowserClient();
      const { data } = await supabase.auth.getSession();
      return data?.session?.access_token || tokenRef.current;
    } catch {
      return tokenRef.current;
    }
  }, []);

  useEffect(() => { initialTaskRef.current = task; }, [task]);

  // ── Helpers ──────────────────────────────────────────────
  const uid = () => `evt_${Date.now()}_${++idCounter.current}`;

  const pushChat = useCallback((role, content, meta = {}) => {
    if (!content || !content.trim()) return;
    setChatMessages((prev) => [
      ...prev,
      { id: uid(), role, content, ts: Date.now(), ...meta },
    ]);
  }, []);

  const pushLog = useCallback((content, type = 'system') => {
    setLogs((prev) => [
      ...prev,
      { id: uid(), content, type, timestamp: Date.now() },
    ]);
  }, []);

  // Use a ref to track steps for safe flush (no nesting state updates)
  const stepsRef = useRef([]);
  const flushedRef = useRef(false);
  const phasesRef = useRef([]);

  // When user clicks Stop, backend takes up to ~75s to fully unwind.
  // During that window, events already emitted by the pipeline (chat messages,
  // step events, progress) are still in-flight. Suppress them so the UI stays
  // clean after Stop. Cleared when status=ready arrives or user sends a new message.
  const stopRequestedRef = useRef(false);

  // Keep refs in sync with state
  useEffect(() => { stepsRef.current = steps; }, [steps]);
  useEffect(() => { phasesRef.current = phases; }, [phases]);
  useEffect(() => { chatMessagesRef.current = chatMessages; }, [chatMessages]);

  // ── Flush current phases + steps into a chat message ─────
  const flushPhasesToChat = useCallback((summary) => {
    // Guard: prevent double flush
    if (flushedRef.current) return;
    flushedRef.current = true;

    // Keep phases visible for the completion card in TaskProgress.
    // Store completionSummary so the TaskProgress component can render it.
    if (summary) {
      setCompletionSummary(summary);
    }

    // When phases exist, TaskProgress already shows the full UI card.
    // Don't duplicate it as a text chat message — only save summary text.
    const currentPhases = phasesRef.current;
    if (currentPhases.length > 0) {
      // Phases are rendered by TaskProgress — no chat message needed.
      // Just clean up steps.
      setSteps([]);
      setFinishSummary('');
      return;
    }

    // Fallback: if only steps (no phases), build a minimal text summary
    const currentSteps = stepsRef.current;
    const stepLines = currentSteps
      .filter((s) => s.done)
      .map((s) => `✅ ${s.label}`);
    let content = stepLines.join('\n');

    if (summary) {
      content += `\n\n${summary}`;
    }

    if (content.trim()) {
      setChatMessages((prev) => [
        ...prev,
        {
          id: uid(),
          role: 'agent',
          content,
          ts: Date.now(),
        },
      ]);
    }

    setSteps([]);
    setFinishSummary('');
  }, []);

  // ── Handle incoming messages ─────────────────────────────
  // Deps bag for the dispatcher — refreshed on every render so the
  // dispatcher always sees current setters / refs / helpers without
  // React closure staleness. Setters and refs are stable references
  // already; the mutated bag is cheap (just rebinds 50 properties).
  const dispatcherDepsRef = useRef(null);
  dispatcherDepsRef.current = {
    // State setters
    setState, setSessionId, setError, setErrorCode, setErrorStage,
    setSteps, setFinishSummary, setCompletionSummary,
    setPhases, setDeclaredPipeline, setAgentStatus, setResolvingInfo, setResolvingProgress,
    setPreviewUrl, setPreviewTaskId, setPreviewLoading, setPreviewStatusMsg,
    setPreviewStage, setPreviewStartedAt, setPreviewEverReady, setPreviewFileMap,
    setPreviewError, setDeployUrl, setQualityReport, setWrittenFiles,
    setFileContents, setFileMetrics, setPlanAwaiting, setCurrentPlanData,
    setChatMessages, setFiles,
    setAwaitingResponse,
    // Refs
    chatMessagesRef, stepsRef, flushedRef, phasesRef, stopRequestedRef,
    reconnectCount, authRefreshAttemptedRef,
    tokenRef, projectIdRef, repoUrlRef, repoProviderRef, gitTokenRef, branchRef,
    historyLoadedRef,
    // Helpers
    pushChat, pushLog, pushAgentActivity, flushPhasesToChat, getFreshToken,
    // Per-session values
    uid, _previewStorageKey,
  };

  // Instantiate the dispatcher once. It reads fresh deps every call via
  // the ref, so React-tracked dep changes never invalidate handleMessage's
  // identity → manager.subscribe never re-runs from dep churn.
  const dispatcherRef = useRef(null);
  if (dispatcherRef.current === null) {
    dispatcherRef.current = createMessageDispatcher(() => dispatcherDepsRef.current);
  }
  const handleMessage = useCallback((msg) => {
    dispatcherRef.current(msg);
  }, []);

  // ── Connection layer (extracted to useAgentConnection) ────
  // Owns the manager subscribe lifecycle, the `connect` callback, the
  // snapshot sync effects, and the auto-connect-on-token-arrival flow.
  const { connect } = useAgentConnection({
    token, state, chatMessages, phases,
    projectIdRef, repoUrlRef, repoProviderRef, gitTokenRef, branchRef, tokenRef,
    initialTaskPreloaded, initialTaskRef,
    setState, setSessionId, setChatMessages, setPhases, setError,
    pushLog, pushChat, getFreshToken, handleMessage,
  });

  // ── Public API — outbound actions (extracted to useAgentActions) ──
  const {
    startSession,
    sendMessage,
    sendCommand,
    sendManualEdit,
    pushToBranch,
    stopSession,
    stopPreview,
    retry,
    confirmPlan,
    rejectPlan,
    submitClarification,
  } = useAgentActions({
    sessionId,
    projectIdRef, repoUrlRef, repoProviderRef, gitTokenRef, branchRef,
    reconnectCount, stopRequestedRef, flushedRef, initialTaskRef,
    setState, setError, setErrorStage, setErrorCode, setSessionId,
    setRetryCount, setPhases, setCompletionSummary, setAgentStatus,
    setPreviewUrl, setPreviewLoading, setPreviewStatusMsg,
    setPreviewError, setPreviewEverReady,
    setPlanAwaiting, setPlanConfirmed, setCurrentPlanData,
    setChatMessages,
    setAwaitingResponse,
    pushLog, pushChat, getFreshToken, connect,
  });

  // ── Hydrate chat history from Supabase (called once by page) ──
  const setInitialMessages = useCallback((savedMsgs) => {
    if (historyLoadedRef.current) return;
    if (!savedMsgs || savedMsgs.length === 0) return;
    historyLoadedRef.current = true;

    // Only show user + assistant messages (clean conversation)
    const filtered = savedMsgs
      .filter((m) => m.role === 'user' || m.role === 'assistant' || m.role === 'agent');

    // Deduplicate by role+content (both backend and frontend may save)
    const seen = new Set();
    const deduped = filtered.filter((m) => {
      const key = `${m.role === 'assistant' ? 'agent' : m.role}::${(m.content || '').slice(0, 100)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    const hydrated = deduped.flatMap((m, i) => {
      let content = m.content || '';
      // Strip [LUCID_PROJECT] header that backend stores in user messages
      if ((m.role === 'user') && content.includes('[LUCID_PROJECT]')) {
        const sep = content.indexOf('\n\n');
        if (sep !== -1) content = content.slice(sep + 2).trim();
      }
      // Detect persisted plan messages (same logic as chat_history WS handler)
      if ((m.role === 'assistant' || m.role === 'agent') && content.trimStart().startsWith('{"messageType"')) {
        try {
          const parsed = JSON.parse(content);
          if (parsed.messageType === 'plan' && parsed.planData) {
            return [{
              id: m.id || `saved_plan_${i}`,
              role: 'agent',
              messageType: 'plan',
              planData: parsed.planData,
              fileWrites: [],
              ts: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
              fromHistory: true,
            }];
          }
        } catch (_) { /* not valid JSON — fall through */ }
      }
      if (!content.trim()) return [];
      return [{
        id: m.id || `saved_${i}`,
        role: m.role === 'assistant' ? 'agent' : m.role,
        content,
        ts: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
        fromHistory: true,
      }];
    });

    if (hydrated.length > 0) {
      setChatMessages((prev) => {
        // Drop the pre-populated init_msg_0 — history is the authoritative order.
        // init_msg_0 is the synchronous placeholder added by useState; once real
        // history arrives it must be replaced so the user message appears first.
        const livePrev = prev.filter(p => p.id !== 'init_msg_0');
        if (livePrev.length === 0) return hydrated;
        // Same normalize logic as the chat_history WS handler — strips
        // `prefix::` tags + whitespace + case so locally-stored messages
        // ("task::do X") dedup correctly against hydrated history ("do X").
        const norm = (s) => {
          if (typeof s !== 'string') return '';
          let v = s.trim();
          const sep = v.indexOf('::');
          if (sep !== -1 && sep < 40) v = v.slice(sep + 2).trim();
          return v.replace(/\s+/g, ' ').toLowerCase().slice(0, 120);
        };
        const prevKeys = new Set(livePrev.map(p => `${p.role}::${norm(p.content)}`));
        const toAdd = hydrated.filter(h => !prevKeys.has(`${h.role}::${norm(h.content)}`));
        return toAdd.length > 0 ? [...toAdd, ...livePrev] : livePrev;
      });
    }
  }, []);

  // ── Return ───────────────────────────────────────────────
  return {
    state,
    sessionId,
    chatMessages,
    logs,
    files,
    setFiles,
    error,

    // ── Per-file content snapshots + metrics (powers DiffViewer + Code-tab badges) ──
    fileContents,
    fileMetrics,

    // Structured progress steps
    steps,
    finishSummary,

    // Structured phases for TaskProgress
    phases,

    // Phase 2 Step 2: declared phase list (backend's authoritative chart shape).
    // null until the first pipeline.declare arrives on a run.
    declaredPipeline,

    // Prompt-router / pipeline-owned status. No frontend phase guessing.
    agentStatus,

    // Completion summary for polished completion card
    completionSummary,

    // Resolving path info — drive the loading screen
    resolvingInfo,
    resolvingProgress,

    // Aliases for backward compat
    status: state,
    messages: chatMessages,
    terminalLogs: logs,
    isReady: state === 'ready',
    isReconnecting: state === 'reconnecting',
    isPreparing: state === 'preparing' || state === 'connecting' || state === 'cloning' || state === 'installing' || state === 'starting' || state === 'health_check' || state === 'reconnecting',

    // Actions
    startSession,
    sendMessage,
    sendCommand,
    sendManualEdit,
    stopSession,
    pushToBranch,
    setInitialMessages,
    // Direct-injection escape hatch for client-side guards (e.g. the
    // workspace ChatPanel's pre-send Gemini intent-check). Bypasses the
    // WebSocket — just appends to the local chat view.
    addLocalChatMessage: pushChat,

    // Preview (noVNC / E2B legacy)
    previewUrl,
    previewTaskId,
    previewLoading,
    previewStatusMsg,
    previewStage,
    previewStartedAt,
    previewEverReady,
    clearPreview: () => { setPreviewUrl(null); setPreviewTaskId(null); setPreviewLoading(false); setPreviewStatusMsg(''); setPreviewEverReady(false); },
    stopPreview,

    // Sandpack preview — { files, template } from backend
    previewFileMap,

    // Vercel deploy URL
    deployUrl,

    // Quality-gate report (set once per landing generation)
    qualityReport,
    dismissQualityReport: () => setQualityReport(null),

    // Files written in the current agent run — used for HMR failure detection
    writtenFiles,

    // Phase 8: error recovery
    errorStage,      // 'clone' | null — which init stage caused the workspace error
    errorCode,       // 'AUTH_EXPIRED' | 'PAT_INVALID' | 'SANDBOX_DEAD' | 'RECONNECT_FAILED' | ...
    previewError,    // { stage, message } | null — non-fatal preview failure
    retryCount,      // number — how many retries the user has attempted
    retry,           // (hint?: 'preview') => void — trigger recovery

    // Live agent ACTIVITY pill (Phase 2 Step 5) — decoupled from task_phase.
    // Shape: { icon, message, kind, ts } | null. Renders above the chat input.
    agentActivity,

    // Optimistic "Sending…" feedback — true between the moment the user
    // submits a message and the first concrete backend response. Lets the
    // chat status pill say "Sending…" without lying about workspace state.
    awaitingResponse,

    // Plan confirmation — approve or reject the plan before code generation
    planAwaiting,
    currentPlanData,
    planConfirmed,
    confirmPlan,
    rejectPlan,

    // Clarification — user answered a disambiguation question.
    submitClarification,
  };
}
