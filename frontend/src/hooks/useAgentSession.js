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

const MAX_RECONNECTS = 8;
// Exponential backoff in ms — 8 attempts spread over ~6 minutes covers
// laptop sleep, browser tab throttling, transient network drops, and
// short backend restarts without exhausting retries on the user.
const RECONNECT_BACKOFF_MS = [2000, 5000, 10000, 20000, 30000, 60000, 60000, 60000];

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

  // ── Live agent status — shown in chat panel during generation ───────────────
  // { label: string, subtext: string } | null
  // Updated on task_phase (active) and key progress messages; cleared on complete.
  const [agentStatus, setAgentStatus] = useState(null);

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
    setChatMessages((prev) => {
      // [DEDUP-DEBUG] Trace every user/agent message append so we can find
      // who's responsible for the duplicate user prompt on workspace entry.
      // Stack trace shows the caller (chat_history handler, sendMessage,
      // the [token] effect, etc.). Remove once the duplicate path is fixed.
      try {
        if (typeof window !== 'undefined' && role === 'user') {
          const preview = (content || '').slice(0, 60).replace(/\s+/g, ' ');
          const dupCount = prev.filter(p => p.role === 'user' && (p.content || '').slice(0, 60) === preview).length;
          // eslint-disable-next-line no-console
          console.warn('[DEDUP-DEBUG] pushChat user msg', { preview, dupCount, prevLen: prev.length, callsite: new Error().stack?.split('\n').slice(2, 6).join(' | ') });
        }
      } catch (_) {}
      return [
        ...prev,
        { id: uid(), role, content, ts: Date.now(), ...meta },
      ];
    });
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
  const handleMessage = useCallback(
    (msg) => {
      // ─── Stop suppression ─────────────────────────────────
      // If user clicked Stop, drop residual pipeline events that are still
      // in-flight (agent_event, step, file_write_event, chat messages, etc.)
      // Only state/connection/error events pass through so the UI can
      // correctly transition back to 'ready' when backend finishes unwinding.
      if (stopRequestedRef.current) {
        const alwaysAllow = new Set([
          '_internal',        // connection events
          'workspace_state',  // canonical state transitions
          'status',           // ready/error status
          'stopped',          // explicit stopped event
          'stop_ack',         // stop confirmation
          'error',            // critical errors
          'pong',
          'ack',
        ]);
        if (!alwaysAllow.has(msg.type)) return;
      }

      // ─── Canonical workspace state — single source of truth ──────
      // The backend emits this on EVERY state transition so the frontend
      // never has to infer state from other message types.
      if (msg.type === 'workspace_state') {
        const stateMap = {
          entry:        'connecting',
          resolving:    'preparing',
          cloning:      'cloning',
          installing:   'installing',
          starting:     'starting',
          health_check: 'health_check',
          ready:        'ready',
          updating:     'running',
          error:        'error',
        };
        const frontendState = stateMap[msg.state] || msg.state;
        setState(frontendState);
        if (msg.sessionId) {
          setSessionId(msg.sessionId);
          try { sessionStorage.setItem(`ws_session_${projectIdRef.current}`, msg.sessionId); } catch (_) {}
        }
        if (msg.state === 'updating') {
          // Reset flush guard + clear stale phases for new task
          flushedRef.current = false;
          setCompletionSummary('');
          setPhases([]);
          setWrittenFiles([]);
        }
        if (msg.state === 'ready') {
          reconnectCount.current = 0;
          stopRequestedRef.current = false;  // backend is idle — residual events safe to show again
        }
        if (msg.state === 'error') {
          setError(msg.message || 'Workspace error');
          if (msg.error_stage) setErrorStage(msg.error_stage);
        }
        // Any non-error transition clears the workspace error stage
        if (msg.state !== 'error') {
          setErrorStage(null);
        }
        if (msg.message) pushLog(msg.message, 'system');
        return;
      }

      // ─── Resolving progress — live steps during RESOLVING state ──
      if (msg.type === 'resolving_progress') {
        setResolvingProgress({ message: msg.message || '', pct: msg.pct || 0 });
        if (msg.message) pushLog(msg.message, 'system');
        return;
      }

      // ─── Workspace empty — new project, no files yet ─────────
      // Backend emits this for Path A (new_project) so the Code tab can
      // show "Waiting for AI to generate code..." instead of an error.
      if (msg.type === 'workspace_empty') {
        pushLog(msg.message || 'Waiting for AI to generate code...', 'system');
        return;
      }

      // ─── Resolving info — final path classification result ────
      if (msg.type === 'resolving_info') {
        setResolvingInfo({
          path:         msg.path || '',
          message:      msg.message || '',
          detail:       msg.detail || '',
          stack:        msg.stack || '',
          templateName: msg.templateName || '',
          description:  msg.description || '',
          repoDisplay:  msg.repoDisplay || '',
          branch:       msg.branch || 'main',
        });
        if (msg.message) pushLog(msg.message, 'system');
        return;
      }

      // Internal manager events
      if (msg.type === '_internal') {
        if (msg.event === 'connected') {
          // Successful connect — clear the one-shot auth-refresh guard so a
          // future 4010 (e.g. token expires again hours later) can recover.
          authRefreshAttemptedRef.current = false;
          setState('preparing');
          setErrorStage(null);
          // Do NOT clear previewError here. If the dev server crashed before the
          // reconnect, clearing the error would flip the phase from
          // "live-with-error-overlay" (recoverable) to the stuck
          // "live-with-restart-overlay" (no error, no URL → overlay never clears).
          // previewError is cleared by: preview_ready, manual retry, or stopPreview.
          setErrorCode(null);
          pushLog('Connected — preparing workspace…', 'system');
        } else if (msg.event === 'visibility_resume') {
          // Tab became visible after being backgrounded. agentWSManager
          // already detected the WS is dead — reset the retry counter and
          // trigger an immediate reconnect (no backoff — user is actively
          // waiting). This fixes "I switched tabs and came back, no preview".
          reconnectCount.current = 0;
          setState('reconnecting');
          pushLog('Tab resumed — reconnecting…', 'system');
          if (manager && !manager.isOpen && !manager.isConnecting) {
            getFreshToken().then((freshToken) => {
              if (!manager.isOpen && !manager.isConnecting) {
                manager.connect({
                  token: freshToken,
                  projectId: projectIdRef.current,
                  repoUrl: repoUrlRef.current,
                  repoProvider: repoProviderRef.current,
                  gitToken: gitTokenRef.current,
                  branch: branchRef.current,
                  task: '',
                });
              }
            });
          }
        } else if (msg.event === 'error') {
          // ws.onerror is always followed by ws.onclose — the close handler
          // below owns the retry/fail logic so it sees the close code. We
          // just surface the error in the log here.
          pushLog(`WebSocket error${msg.reason ? ' — ' + msg.reason : ''}`, 'error');
        } else if (msg.event === 'closed') {
          // 4010 = server rejected with "authentication required" — JWT
          // expired or was invalid. Try a one-shot supabase refreshSession()
          // first; if Supabase still has a valid refresh_token it'll mint a
          // new access_token and we reconnect silently. Only surface
          // AUTH_EXPIRED if the refresh itself fails (truly expired session).
          if (msg.code === 4010) {
            if (authRefreshAttemptedRef.current) {
              // We already tried — refresh didn't fix it.
              setState('error');
              setErrorCode('AUTH_EXPIRED');
              setError('Your session has expired. Please sign in again.');
              pushLog('Session expired — please sign in again', 'error');
              return;
            }
            authRefreshAttemptedRef.current = true;
            setState('reconnecting');
            (async () => {
              try {
                const supabase = getSupabaseBrowserClient();
                const { data, error: refreshErr } = await supabase.auth.refreshSession();
                const fresh = data?.session?.access_token;
                if (refreshErr || !fresh) {
                  setState('error');
                  setErrorCode('AUTH_EXPIRED');
                  setError('Your session has expired. Please sign in again.');
                  pushLog('Session expired — please sign in again', 'error');
                  return;
                }
                tokenRef.current = fresh;
                if (manager && !manager.isOpen && !manager.isConnecting) {
                  manager.connect({
                    token: fresh,
                    projectId: projectIdRef.current,
                    repoUrl: repoUrlRef.current,
                    repoProvider: repoProviderRef.current,
                    gitToken: gitTokenRef.current,
                    branch: branchRef.current,
                    task: '',
                  });
                }
              } catch (e) {
                setState('error');
                setErrorCode('AUTH_EXPIRED');
                setError('Your session has expired. Please sign in again.');
                pushLog('Session expired — please sign in again', 'error');
              }
            })();
          } else if ([1000, 4001].includes(msg.code)) {
            setState('stopped');
            pushLog(`Session ended (${msg.reason || msg.code})`, 'system');
          } else if (msg.code === 4100) {
            // Page leaving — keep state as-is
          } else {
            if (reconnectCount.current < MAX_RECONNECTS) {
              const attempt = reconnectCount.current; // 0-indexed for backoff lookup
              const delay = RECONNECT_BACKOFF_MS[attempt] ?? 60000;
              reconnectCount.current += 1;
              setState('reconnecting');
              pushLog(
                `Reconnecting (${reconnectCount.current}/${MAX_RECONNECTS}) in ${Math.round(delay / 1000)}s…`,
                'system',
              );
              setTimeout(() => {
                if (manager && !manager.isOpen && !manager.isConnecting) {
                  getFreshToken().then((freshToken) => {
                    if (!manager.isOpen && !manager.isConnecting) {
                      manager.connect({
                        token: freshToken,
                        projectId: projectIdRef.current,
                        repoUrl: repoUrlRef.current,
                        repoProvider: repoProviderRef.current,
                        gitToken: gitTokenRef.current,
                        branch: branchRef.current,
                        task: '',
                      });
                    }
                  });
                }
              }, delay);
            } else {
              setState('error');
              setErrorCode('RECONNECT_FAILED');
              setError('Connection lost after multiple attempts. Click Retry to try again.');
              pushLog('Connection lost — manual retry required', 'error');
            }
          }
        }
        return;
      }

      // ─── Structured progress steps from backend ────
      if (msg.type === 'step') {
        const { step, label, done, summary } = msg;

        if (step === 'got_task') {
          // Reset flush guard for new task
          flushedRef.current = false;
        }

        if (step === 'finished') {
          // Flush phases + file changes into a permanent chat message
          setFinishSummary(summary || '');
          setAgentStatus(null);
          setTimeout(() => flushPhasesToChat(summary || ''), 150);
          setState('ready');
          return;
        }

        // Update or add step
        setSteps((prev) => {
          const existing = prev.findIndex((s) => s.step === step);
          if (existing >= 0) {
            const updated = [...prev];
            updated[existing] = { ...updated[existing], done, label };
            return updated;
          }
          return [...prev, { id: uid(), step, label, done }];
        });
        return;
      }

      // ─── Status updates ───────────────────────────
      if (msg.type === 'status') {
        const st = msg.status;
        if (st === 'initializing') {
          // Initial handshake/setup is not necessarily a repository clone.
          // Keeping this as "preparing" avoids the workspace briefly saying
          // "Cloning repository..." before the resolver has chosen a path.
          setState('preparing');
          if (msg.message) pushLog(msg.message, 'system');
        } else if (st === 'cloning') {
          // Show a dedicated "cloning" state only for real clone progress.
          setState('cloning');
          if (msg.message) pushLog(msg.message, 'system');
        } else if (st === 'preparing') {
          setState('preparing');
          if (msg.message) pushLog(msg.message, 'system');
        } else if (st === 'ready' || st === 'mock_mode') {
          setState('ready');
          stopRequestedRef.current = false;  // backend finished unwinding after Stop
          if (msg.sessionId) {
            setSessionId(msg.sessionId);
            try { sessionStorage.setItem(`ws_session_${projectIdRef.current}`, msg.sessionId); } catch (_) {}
          }
          if (msg.reconnected) {
            pushLog('Reconnected to existing workspace.', 'system');
          } else if (msg.message) {
            pushLog(msg.message, 'system');
          }
          reconnectCount.current = 0;
        } else if (st === 'completed') {
          setState('ready');
          stopRequestedRef.current = false;
        } else {
          if (msg.message) pushLog(`[${st}] ${msg.message}`, 'system');
        }
        return;
      }

      // ─── Agent events — go to logs only ────────────
      if (msg.type === 'agent_event') {
        const content = msg.content || '';
        const eventType = msg.eventType || msg.event || '';
        const thought = msg.thought || '';
        const toolName = msg.toolName || '';

        if (thought) pushLog(`💭 ${thought}`, 'thinking');

        if (msg.event === 'task_start') {
          setState('running');
          // Clear steps + phases for new task so buildLabel starts from phase 0
          setSteps([]);
          setPhases([]);
          setWrittenFiles([]);
          flushedRef.current = false;
          setCompletionSummary('');
          setFinishSummary('');
          pushLog(content, 'system');
          return;
        }

        if (eventType === 'ActionEvent') {
          if (content) pushLog(content, toolName === 'terminal' ? 'cmd_output' : 'file_write');
        } else if (eventType === 'MessageEvent') {
          if (content) pushLog(content, 'agent_message');
        } else if (msg.event === 'error') {
          if (content) {
            pushChat('system', `⚠️ ${content}`);
            pushLog(content, 'error');
          }
        } else if (content) {
          pushLog(content, 'system');
        }

        if (msg.fileTree && Array.isArray(msg.fileTree)) setFiles(msg.fileTree);
        return;
      }

      // ─── File write event — attach pill to last agent message ──
      if (msg.type === 'file_write_event') {
        const filename = msg.filename || '';
        const action = msg.action || 'write';
        const incomingContent = typeof msg.content === 'string' ? msg.content : null;
        const phase = msg.phase || null;
        const size = typeof msg.size === 'number' ? msg.size : null;
        const truncated = !!msg.content_truncated;

        if (filename) {
          setWrittenFiles(prev => prev.includes(filename) ? prev : [...prev, filename]);
          pushLog(`[${action}] ${filename}`, 'file_write');

          // Snapshot file content for the inline DiffViewer.
          // We keep one previous version per file so the user can always see
          // "what just changed" without ballooning memory by storing history.
          if (incomingContent !== null) {
            setFileContents(prev => {
              const existing = prev[filename];
              const previousContent = existing ? existing.current : '';
              return {
                ...prev,
                [filename]: {
                  current: incomingContent,
                  previous: previousContent,
                  truncated,
                },
              };
            });
          }

          // Per-file metrics: counts, latest action/phase, and elapsed time
          // since the FIRST write to this file in the current run.
          setFileMetrics(prev => {
            const now = Date.now();
            const existing = prev[filename];
            const firstWriteTs = existing?.firstWriteTs ?? now;
            return {
              ...prev,
              [filename]: {
                writes: (existing?.writes ?? 0) + 1,
                lastAction: action,
                lastPhase: phase,
                lastSize: size,
                firstWriteTs,
                lastWriteTs: now,
                elapsedMs: now - firstWriteTs,
              },
            };
          });
          setChatMessages((prev) => {
            if (prev.length === 0) return prev;
            const last = prev[prev.length - 1];
            // Attach to the last agent message (plan or empty agent bubble)
            if (last.role === 'agent') {
              const updated = [...prev];
              updated[updated.length - 1] = {
                ...last,
                fileWrites: [...(last.fileWrites || []), { filename, action }],
              };
              return updated;
            }
            // No agent message yet — create one to hold the write pills
            return [...prev, {
              id: uid(),
              role: 'agent',
              content: '',
              fileWrites: [{ filename, action }],
              ts: Date.now(),
            }];
          });
        }
        return;
      }

      // ─── File tree update ────────────────────────
      if (msg.type === 'file_tree') {
        if (msg.tree && Array.isArray(msg.tree)) setFiles(msg.tree);
        return;
      }

      if (msg.type === 'file_change') {
        if (Array.isArray(msg.files)) {
          setFiles(msg.files);
        } else if (msg.path) {
          setFiles((prev) => prev.includes(msg.path) ? prev : [...prev, msg.path]);
        }
        pushLog(`File changed: ${msg.path || msg.files?.join(', ') || 'unknown'}`, 'file_write');
        return;
      }

      if (msg.type === 'log' || msg.type === 'observation') {
        const text = msg.content || msg.message || JSON.stringify(msg);
        pushLog(text, msg.event || 'system');
        return;
      }

      if (msg.type === 'message') {
        if (msg.content && msg.content.trim()) pushLog(msg.content, 'agent_message');
        return;
      }

      // ─── Claude Message — log only (structured events handled separately) ──
      // file_write_event and chat_message now carry the user-visible data.
      // claude_message is raw SDK output kept only for terminal/debug logs.
      if (msg.type === 'claude_message') {
        const raw = msg.content || '';
        if (raw.length > 5) {
          pushLog(`[Claude] ${raw.slice(0, 300)}`, 'agent_message');
        }
        return;
      }

      // ─── Claude Result — update phase 4 ────────────
      if (msg.type === 'claude_result') {
        const resultText = msg.result || 'Code written successfully';
        pushLog(`[Claude Result] ${resultText.slice(0, 300)}`, 'agent_message');
        // Update phase 4 with the actual result
        setPhases(prev => {
          const updated = [...prev];
          const idx = updated.findIndex(p => p.phase === 4);
          if (idx >= 0) {
            updated[idx] = { ...updated[idx], description: resultText.slice(0, 200), status: 'done' };
          }
          return updated;
        });
        return;
      }

      // ─── Task Phase — structured progress ──────────
      if (msg.type === 'task_phase') {
        // Set running state when first phase arrives
        if (msg.phase === 1 && msg.status === 'active') {
          setState('running');
          // Reset flush guard for new task
          flushedRef.current = false;
          // Clear previous task's completion state
          setCompletionSummary('');
          setPhases([{ ...msg }]);
        } else {
          setPhases(prev => {
            const updated = [...prev];
            const idx = updated.findIndex(p => p.phase === msg.phase);
            if (idx >= 0) {
              updated[idx] = { ...updated[idx], ...msg };
            } else {
              updated.push({ ...msg });
            }
            return updated.sort((a, b) => a.phase - b.phase);
          });
        }
        // Update live status indicator only. The TaskProgress component
        // already renders task_phase events as an animated progress bar,
        // so duplicating them as chat-stream entries was just noise.
        if (msg.status === 'active') {
          const PHASE_ICONS = { 1: '✓', 2: '📁', 3: '🔍', 4: '📐', 5: '✍️', 6: '🔨', 7: '🚀', 8: '🌐' };
          const icon = PHASE_ICONS[msg.phase] || '⚡';
          setAgentStatus({ label: `${icon} ${msg.title}`, subtext: msg.description || '' });
        } else if (msg.status === 'error') {
          setAgentStatus({ label: `❌ ${msg.title}`, subtext: msg.description || 'Failed' });
        }
        return;
      }

      // ─── Generation Progress — batched project generation ────
      if (msg.type === 'generation_progress') {
        window.dispatchEvent(new CustomEvent('generation_progress', { detail: msg }));
        pushLog(`[Gen] ${msg.message || msg.batch || ''}`, 'system');
        return;
      }

      if (msg.type === 'batch_complete') {
        window.dispatchEvent(new CustomEvent('batch_complete', { detail: msg }));
        pushLog(`[Gen] ✓ ${msg.batch} complete (${msg.files_created?.length || 0} files)`, 'system');
        return;
      }

      if (msg.type === 'generation_complete') {
        window.dispatchEvent(new CustomEvent('generation_complete', { detail: msg }));
        pushLog(`[Gen] ✅ ${msg.message || 'Project generated!'} (${msg.total_files || 0} files)`, 'system');
        return;
      }

      // ─── Deploy Ready — Vercel auto-deploy URL (silent — shown in Publish popup) ────
      if (msg.type === 'deploy_ready') {
        setDeployUrl(msg.url);
        pushLog(`[Deploy] Live: ${msg.url}`, 'system');
        return;
      }

      // ─── Published — auto-publish on first generation completed ──
      // Backend pushed staging to the production branch and created a Vercel project. We surface
      // the live URL in chat and feed it into deployUrl so the workspace
      // header / Publish modal pick it up too.
      if (msg.type === 'published') {
        // setDeployUrl already surfaces the live URL in the workspace header
        // (the "Open" / "Share" buttons), so a duplicate chat bubble just
        // adds deployment chatter to a stream the user wants to keep focused
        // on plan + statuses. Keep the URL in logs for debugging only.
        if (msg.vercelUrl) {
          setDeployUrl(msg.vercelUrl);
          pushLog(`[Published] ${msg.vercelUrl}`, 'system');
        }
        return;
      }

      // ─── Repo Created ─────────────────────────────────────
      if (msg.type === 'repo_created') {
        if (msg.platformOwned && msg.branch === 'staging') {
          // new_project_mode: brand-new project workspace created.
          // The 'published' event (above) handles the live-URL message;
          // we keep this one quiet (logs only) so non-developers don't see
          // git/branch chatter in their chat feed.
          pushLog(`[New Repo] ${msg.repoUrl}`, 'system');
        } else if (msg.branch && msg.branchUrl && msg.prUrl) {
          // branch-push mode: pushed to a feature branch — show PR link
          pushChat('system',
            `📦 Code pushed to branch **\`${msg.branch}\`**\n` +
            `📂 [View branch](${msg.branchUrl})\n` +
            `🔀 **[Open Pull Request](${msg.prUrl})**`
          );
          pushLog(`[Repo] ${msg.repoUrl} → branch: ${msg.branch}`, 'system');
        } else if (msg.repoUrl) {
          // generic: repo created or updated
          pushChat('system', `📦 Repository: [${msg.repoName || msg.repoUrl}](${msg.repoUrl})`);
          pushLog(`[Platform Repo] ${msg.repoUrl}`, 'system');
        }
        return;
      }


      // ─── Preview Status — sandbox creation progress ────
      if (msg.type === 'preview_status') {
        setPreviewLoading(true);
        setPreviewStatusMsg(msg.message || msg.status || 'Setting up preview…');
        if (msg.status) setPreviewStage(msg.status);
        // Stamp the start time on the FIRST preview_status of a fresh load
        // (don't reset it on subsequent stage updates — we want elapsed-since-start).
        setPreviewStartedAt((prev) => prev || Date.now());
        pushLog(`[Preview] ${msg.message || msg.status || ''}`, 'system');
        return;
      }

      // ─── Preview Ready ────────────────────────────
      if (msg.type === 'preview_ready') {
        setPreviewUrl(msg.preview_url);
        if (_previewStorageKey && typeof window !== 'undefined' && msg.preview_url) {
          try { sessionStorage.setItem(_previewStorageKey, msg.preview_url); } catch {}
        }
        setPreviewTaskId(msg.task_id);
        setPreviewError(null);
        setPreviewLoading(false);
        setPreviewStatusMsg('');
        setPreviewStage('');
        setPreviewStartedAt(null);
        setPreviewEverReady(true);
        pushLog(`[Preview] ${msg.message || 'Preview ready'}`, 'system');
        return;
      }

      // ─── Preview Error — non-fatal (dev server / tunnel failed) ──
      // Workspace stays READY; user can click "Restart Preview" to retry.
      if (msg.type === 'preview_error') {
        const stage = msg.error_stage || 'start';
        const message = msg.message || 'Preview unavailable — click Restart Preview to retry.';
        setPreviewError({ stage, message });
        setPreviewUrl(null);
        if (_previewStorageKey && typeof window !== 'undefined') {
          try { sessionStorage.removeItem(_previewStorageKey); } catch {}
        }
        setPreviewLoading(false);
        setPreviewStatusMsg('');
        pushLog(`[Preview Error] ${message}`, 'error');
        return;
      }

      // ─── Sandpack preview files ───────────────────────────
      // Backend sends the full workspace file tree + detected template.
      // SandpackPreview bundles and runs everything in-browser (~2-5s).
      if (msg.type === 'preview_files') {
        if (msg.files && typeof msg.files === 'object') {
          setPreviewFileMap({ files: msg.files, template: msg.template || 'react' });
          pushLog(`[Preview] Received ${Object.keys(msg.files).length} files — booting Sandpack (${msg.template || 'react'})…`, 'system');
        }
        return;
      }

      // ─── Structured pipeline failure ─────────────────────────
      // Backend emits this with { phase, code, message, retriable } so we
      // can show specific recovery UX (e.g. rate-limit → "wait a minute",
      // auth → "reconnect", content_blocked → "rephrase your prompt") instead
      // of the generic "Generation failed".
      if (msg.type === 'pipeline_failure') {
        const friendly = msg.message || "Generation hit a snag.";
        const codeHint = ({
          rate_limit:      "Our AI provider is rate-limiting us right now. Wait a minute and try again.",
          auth_error:      "Your account session may have expired. Sign out and back in, then retry.",
          content_blocked: "The AI provider blocked this request. Try rephrasing your prompt.",
          network:         "Network hiccup talking to the AI provider. Please retry.",
          timeout:         "The AI took too long to respond. Please retry.",
          bad_request:     "Something about that request was malformed. Please retry, and if it keeps failing reach out.",
        })[msg.code] || "Please try again. If it keeps happening, reach out and share what you were doing.";

        pushChat('system',
          `❌ **${friendly}**\n${codeHint}`
        );
        pushLog(`[Failure] phase=${msg.phase} code=${msg.code} retriable=${msg.retriable} — ${msg.message}`, 'system');
        if (!msg.retriable) {
          setState('ready');
          setAgentStatus(null);
        }
        return;
      }

      if (msg.type === 'complete') {
        // Only transition to 'ready' — do NOT push a chat message.
        // The TaskProgress UI already shows the completion state clearly.
        // Pushing 'Task completed.' to chat was confusing during multi-phase pipelines.
        setState('ready');
        setAgentStatus(null);
        if (msg.message) pushLog(`[Complete] ${msg.message}`, 'system');

        // Refresh task status
        setSteps([]);
        setFinishSummary(msg.message || '');
        // DO NOT clear previewUrl here — the E2B sandbox preview should
        // persist between pipeline runs so the user can see changes.
        // Preview is only cleared on explicit disconnect or page refresh.
        return;
      }

      // ─── Git push result — show in chat ────────────
      if (msg.type === 'git_push_result') {
        if (msg.pushed) {
          setChatMessages((prev) => [
            ...prev,
            {
              id: uid(),
              role: 'push_result',
              content: msg.summary || 'Changes pushed successfully',
              branch: msg.branch || '',
              baseBranch: msg.baseBranch || '',
              repoUrl: msg.repoUrl || '',
              branchUrl: msg.branchUrl || '',
              prUrl: msg.prUrl || '',
              provider: msg.provider || '',
              providerLabel: msg.providerLabel || '',
              newBranch: msg.newBranch || false,
              ts: Date.now(),
            },
          ]);
          pushLog(`Pushed to ${msg.branch}`, 'system');
        }
        return;
      }

      // ─── Auto staging push after manual edits ──────────
      if (msg.type === 'staging_push_result') {
        const branch = msg.branch || 'staging';
        const files = Number(msg.filesPushed || 0);
        if (msg.pushed) {
          pushLog(`Auto-pushed ${files || 1} file${files === 1 ? '' : 's'} to ${branch}`, 'system');
        } else {
          pushLog(msg.message || `Staging branch ${branch} is already up to date`, 'system');
        }
        return;
      }

      // ─── Clarify ────────────────────────────────
      // The pipeline could not understand the prompt (gibberish / too short).
      // Rendered as a normal assistant chat message — NOT a red error banner —
      // so the user can simply reply with more detail and try again.
      if (msg.type === 'clarify') {
        const clarifyMsg = msg.message || 'Could you tell me more about what you want to build?';
        setAgentStatus(null);
        // Clear lingering phase data so the in-chat status pill doesn't
        // keep showing "Researching your idea…" after a clarify event
        // has handed control back to the user.
        setPhases([]);
        setState('ready');
        pushChat('assistant', clarifyMsg);
        pushLog(clarifyMsg, 'system');
        return;
      }

      // ─── Error ──────────────────────────────────
      if (msg.type === 'error') {
        const errMsg = msg.message || 'Unknown error';
        const code = msg.code || null;
        setAgentStatus(null);
        setErrorCode(code);
        // Stale-backend rescue: older ai_engine builds emit gibberish-
        // detection feedback as `type: 'error'` with the legacy
        // "Please describe your project in a few words" copy. Until the
        // VPS is redeployed with the friendly `type: 'clarify'` flow,
        // recognize that shape here and treat it as a clarification
        // question — render it as a warm assistant message instead of
        // a red system error banner with the ⚠️ prefix.
        const isLegacyGibberishMsg =
          /please describe your project/i.test(errMsg) ||
          /modern coffee shop landing page/i.test(errMsg) ||
          /fitness coach portfolio/i.test(errMsg);
        if (isLegacyGibberishMsg) {
          // Use the SAME text as /api/intent-check's fail-closed path so
          // that backend-rejected and frontend-rejected gibberish read
          // identically. (Previously these diverged — confusing the user
          // when consecutive messages produced different copy.)
          const friendly =
            "I couldn't quite read that. Could you describe what you'd like to build or change? " +
            "For example: \"a landing page for my coffee shop\" or \"make the hero darker\".";
          pushChat('assistant', friendly);
          pushLog(errMsg, 'system');
          // Clear phases so we don't leave a stale "Researching…" pill behind.
          setPhases([]);
          setState('ready');
          return;
        }
        pushChat('system', `⚠️ ${errMsg}`);
        pushLog(errMsg, 'error');
        // Fatal codes force the session into error state; the UI reads
        // errorCode to render the right recovery prompt (re-auth /
        // reconnect-pat / reload). Legacy string checks remain as a
        // fallback for older backends that don't send `code`.
        const fatalCodes = new Set([
          'AUTH_EXPIRED', 'HANDSHAKE_TIMEOUT', 'HANDSHAKE_INVALID',
          'PAT_INVALID', 'SANDBOX_DEAD', 'RATE_LIMITED',
        ]);
        if (
          (code && fatalCodes.has(code))
          || errMsg.includes('Authentication')
          || errMsg.includes('Timeout waiting')
        ) {
          setError(errMsg);
          setState('error');
        } else {
          setState('ready');
        }
        return;
      }

      if (msg.type === 'stopped') {
        setState('ready');
        setAgentStatus(null);
        pushChat('system', `⛔ ${msg.message || 'Task stopped by user.'}`);
        pushLog(`[Stopped] ${msg.message || 'Task stopped'}`, 'system');
        setSteps([]);
        setFinishSummary('Task stopped by user.');
        return;
      }

      if (msg.type === 'pong' || msg.type === 'ack') return;

      // ─── Plan awaiting confirmation ─────────────────
      // The backend has shown a plan and is waiting for the user to confirm
      // or reject it before spending money on code generation.
      if (msg.type === 'plan_awaiting_confirmation') {
        setPlanAwaiting(true);
        pushLog(msg.message || 'Waiting for plan confirmation...', 'system');
        return;
      }

      // ─── Chat history — backend replays recent messages on reconnect ──
      // Received when client reconnects to an existing backend session.
      // We only hydrate if the local snapshot is empty (avoids overwriting
      // live messages that already arrived via the snapshot restore path).
      if (msg.type === 'chat_history') {
        if (!historyLoadedRef.current && Array.isArray(msg.messages) && msg.messages.length > 0) {
          historyLoadedRef.current = true;
          const hydrated = msg.messages.flatMap((m, i) => {
            let content = m.content || '';

            // Strip wizard header from user messages
            if (m.role === 'user' && content.includes('[LUCID_PROJECT]')) {
              const sep = content.indexOf('\n\n');
              if (sep !== -1) content = content.slice(sep + 2).trim();
            }

            // ── Detect persisted plan messages ────────────────────
            // Plan messages are saved as JSON: {"messageType": "plan", "planData":{...}}
            // Role may be 'assistant' or 'agent' depending on the backend event type.
            if ((m.role === 'assistant' || m.role === 'agent') && content.trimStart().startsWith('{"messageType"')) {
              try {
                const parsed = JSON.parse(content);
                if (parsed.messageType === 'plan' && parsed.planData) {
                  return [{
                    id: m.id || `wshist_plan_${i}`,
                    role: 'agent',
                    messageType: 'plan',
                    planData: parsed.planData,
                    fileWrites: [],
                    ts: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
                    fromHistory: true,
                  }];
                }
              } catch (_) {
                // not valid JSON — fall through to plain text
              }
            }

            // ── Detect persisted clarification messages ──────────
            // Persisted as JSON: {"kind","question","options","original_task"}.
            // We restore the question card; if the next message in this
            // history list is a ClarificationResponse from the user it
            // means they already answered, so render as resolved.
            if ((m.role === 'assistant' || m.role === 'agent') && content.trimStart().startsWith('{"kind"')) {
              try {
                const parsed = JSON.parse(content);
                if (parsed.question && Array.isArray(parsed.options)) {
                  const next = msg.messages[i + 1];
                  const answeredLabel = (
                    next && next.role === 'user' && next.event_type === 'ClarificationResponse'
                  ) ? (next.content || null) : null;
                  return [{
                    id: m.id || `wshist_clarify_${i}`,
                    role: 'agent',
                    messageType: 'clarification',
                    clarification: {
                      kind: parsed.kind || '',
                      question: parsed.question,
                      options: parsed.options,
                      originalTask: parsed.original_task || '',
                      answered: !!answeredLabel,
                      answerLabel: answeredLabel,
                    },
                    ts: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
                    fromHistory: true,
                  }];
                }
              } catch (_) {
                // not valid JSON — fall through to plain text
              }
            }

            // Skip raw ClarificationResponse rows — they are merged into
            // the question card above as `answerLabel`.
            if (m.role === 'user' && m.event_type === 'ClarificationResponse') {
              return [];
            }

            // Plain text messages
            if (!content.trim()) return [];
            return [{
              id: m.id || `wshist_${i}`,
              role: m.role === 'assistant' ? 'agent' : (m.role || 'agent'),
              content,
              ts: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
              fromHistory: true,
            }];
          });

          if (hydrated.length > 0) {
            setChatMessages(prev => {
              // [DEDUP-DEBUG] log what's in prev when WS chat_history hydrates
              try {
                if (typeof window !== 'undefined') {
                  const userPrevs = prev.filter(p => p.role === 'user').map(p => ({ id: p.id, c: (p.content || '').slice(0, 60) }));
                  const userHydrated = hydrated.filter(p => p.role === 'user').map(p => ({ id: p.id, c: (p.content || '').slice(0, 60) }));
                  // eslint-disable-next-line no-console
                  console.warn('[DEDUP-DEBUG] chat_history WS hydrate', { prevUsers: userPrevs, hydratedUsers: userHydrated });
                }
              } catch (_) {}
              // Drop init_msg_0 placeholder — WS history is authoritative ordering
              const livePrev = prev.filter(p => p.id !== 'init_msg_0');
              if (livePrev.length === 0) return hydrated;
              // Dedup on TWO axes:
              //   1) id — matches re-replays where the backend sent the
              //      same DB row twice.
              //   2) role + normalized content — matches the case where the
              //      user's own freshly-typed prompt already lives in
              //      `livePrev` with a local uid(), while the backend
              //      replay carries the same text under a Supabase UUID.
              // The normalize step strips `prefix::` (which hydrated content
              // already had stripped at line ~1033) plus whitespace + case,
              // so a locally-stored "task::do X" matches a hydrated "do X".
              const norm = (s) => {
                if (typeof s !== 'string') return '';
                let v = s.trim();
                const sep = v.indexOf('::');
                if (sep !== -1 && sep < 40) v = v.slice(sep + 2).trim();
                return v.replace(/\s+/g, ' ').toLowerCase().slice(0, 120);
              };
              const roleKey = (r) => (r === 'assistant' ? 'agent' : r);
              const existingById = new Map(livePrev.map(p => [p.id, p]));
              const existingByContent = new Set(
                livePrev
                  .filter(p => typeof p.content === 'string' && p.content.trim())
                  .map(p => `${roleKey(p.role)}::${norm(p.content)}`)
              );
              const toAdd = hydrated.filter(h => {
                if (existingById.has(h.id)) return false;
                if (typeof h.content === 'string' && h.content.trim()) {
                  const key = `${roleKey(h.role)}::${norm(h.content)}`;
                  if (existingByContent.has(key)) return false;
                }
                return true;
              });
              return toAdd.length > 0 ? [...toAdd, ...livePrev] : livePrev;
            });
          }
        }
        return;
      }

      // ─── Chat message — direct chat bubble from backend ────
      if (msg.type === 'chat_message') {
        const role = msg.role || 'agent';
        const content = msg.content || '';

        // Structured plan message — rendered as a special plan card in the UI.
        // Backend re-emits the pending plan on every reconnect (ws.py ~L477),
        // so each reconnect would otherwise stack another plan card. Dedup
        // by stable signature (sections + brand + tagline).
        if (msg.messageType === 'plan' && msg.planData) {
          const planSig = (() => {
            try {
              const pd = msg.planData || {};
              const secs = Array.isArray(pd.sections) ? pd.sections.map(s => s?.title || s?.headline || s?.id || '').join('|') : '';
              return `${pd.brandName || ''}::${pd.tagline || ''}::${secs}`;
            } catch { return ''; }
          })();
          const dupPlan = chatMessagesRef.current.some(
            p => p.messageType === 'plan' && p._planSig === planSig
          );
          setCurrentPlanData(msg.planData);
          if (dupPlan) return;
          setChatMessages((prev) => [
            ...prev,
            {
              id: uid(),
              role: 'agent',
              messageType: 'plan',
              planData: msg.planData,
              fileWrites: [],
              ts: Date.now(),
              _planSig: planSig,
            },
          ]);
          return;
        }

        if (content.trim()) {
          // Dedup: drop a chat_message echo if the trailing chat already
          // contains an identical role+content within the last 3 entries.
          // Backend can re-emit a freshly-persisted user prompt (e.g. when
          // the proxy's bind_chat fires after the handshake task path) and
          // without this guard the bubble appears twice.
          const norm = (s) => (typeof s === 'string' ? s.trim().replace(/\s+/g, ' ').toLowerCase().slice(0, 160) : '');
          const incoming = `${role === 'assistant' ? 'agent' : role}::${norm(content)}`;
          const tail = chatMessagesRef.current.slice(-3);
          const isDup = tail.some(p => {
            const prevKey = `${p.role === 'assistant' ? 'agent' : p.role}::${norm(p.content)}`;
            return prevKey === incoming;
          });
          if (isDup) return;
          pushChat(role, content);
        }
        return;
      }

      // ─── Progress — show in terminal logs; also update live status subtext ────
      if (msg.type === 'progress') {
        const text = msg.message || '';
        pushLog(text, 'system');
        // Update live status indicator subtext (keep current label)
        setAgentStatus(prev => prev ? { ...prev, subtext: text } : null);
        return;
      }

      // ─── Quality gate report ───────────────────────────
      // Emitted once per landing generation by landing_quality_gate.run.
      // We just stash it on state — the QualityReportPanel reads it and
      // renders the failed checks with a per-check "Regenerate" button
      // (the regen endpoint lands in A6).
      if (msg.type === 'quality_report') {
        if (msg.report && typeof msg.report === 'object') {
          setQualityReport(msg.report);
        }
        return;
      }

      // ─── Warning — show in chat ───────────────────────
      if (msg.type === 'warning') {
        const text = msg.message || '';
        pushLog(text, 'warning');
        // Multi-tab collision: keep the chat clean (no wall-of-text bubble)
        // and surface it as a log entry + a dedicated code on the error
        // channel so a page-level toast can render it separately.
        if (msg.code === 'MULTI_TAB') {
          setErrorCode('MULTI_TAB');
        } else {
          pushChat('system', `⚠️ ${text}`);
        }
        return;
      }

      // ─── Clarification needed — backend wants the user to disambiguate ──
      // Backend detected conflicting archetype signals (e.g. "landing page"
      // + cart/checkout). The pipeline is paused on the server side; it
      // resumes only after the user picks an option, which fires
      // submitClarification() and sends `clarification_response` back.
      if (msg.type === 'clarification_needed') {
        setChatMessages((prev) => [
          ...prev,
          {
            id: uid(),
            role: 'agent',
            messageType: 'clarification',
            clarification: {
              kind: msg.kind || '',
              clarifyKey: msg.clarify_key || '',
              question: msg.question || '',
              options: Array.isArray(msg.options) ? msg.options : [],
              originalTask: msg.original_task || '',
              answered: false,
              answerLabel: null,
            },
            ts: Date.now(),
          },
        ]);
        return;
      }

      pushLog(JSON.stringify(msg), 'system');
    },
    [pushLog, pushChat, flushPhasesToChat]
  );

  // ── Subscribe to global manager events ──────────────────
  useEffect(() => {
    if (!manager) return;
    const unsub = manager.subscribe(handleMessage);
    return unsub;
  }, [handleMessage]);

  // ── Cleanup on unmount: close connection if navigating away ──
  // NOTE: We do NOT close here — the manager persists across navigation.
  // But we DO need to reset the connecting flag if we're still connecting
  // when the component unmounts, to avoid ghost states.
  useEffect(() => {
    return () => {
      // If we navigate away to a DIFFERENT project page, the new page's
      // hook will call manager.connect() with the new projectId, which
      // handles closing the old connection.
    };
  }, []);

  // ── Connect function ─────────────────────────────────────
  const connect = useCallback(
    (taskToSend) => {
      if (!manager) return;

      // If already open to THIS project, don't reconnect
      if (manager.isOpen && manager.projectId === projectIdRef.current) return;
      if (manager.isConnecting) return;

      setState('connecting');
      setError(null);
      pushLog('Connecting to AI Engine…', 'system');

      getFreshToken().then((freshToken) => {
        manager.connect({
          token: freshToken,
          projectId: projectIdRef.current,
          repoUrl: repoUrlRef.current,
          repoProvider: repoProviderRef.current,
          gitToken: gitTokenRef.current,
          branch: branchRef.current,
          task: taskToSend || '',
        });
      });

      if (taskToSend) {
        pushLog(`Task queued: ${taskToSend.slice(0, 80)}…`, 'user');
      }
    },
    [pushLog]
  );

  // ── Immediate snapshot restore on mount (before token is available) ──
  // Non-wizard workspaces wait for convLoading before effectiveToken becomes
  // truthy, so the [token] effect fires late. This effect runs once on mount
  // to restore the snapshot immediately, avoiding a blank chat on return.
  useEffect(() => {
    if (!manager) return;
    if (manager.isOpen && manager.projectId === projectIdRef.current) {
      const snap = manager._statusSnapshot;
      setState(snap && snap !== 'idle' ? snap : 'ready');
      if (manager.sessionId) setSessionId(manager.sessionId);
      if (manager._chatSnapshot?.length > 0) {
        // [DEDUP-DEBUG] mount-snapshot restore replaces chatMessages wholesale.
        // If the manager-cached snapshot already contains the user prompt and
        // setInitialMessages/chat_history later run without `historyLoadedRef`
        // having been set on this fresh hook instance, we'll see duplicates.
        try {
          if (typeof window !== 'undefined') {
            const users = (manager._chatSnapshot || []).filter(p => p.role === 'user').map(p => ({ id: p.id, c: (p.content || '').slice(0, 60) }));
            // eslint-disable-next-line no-console
            console.warn('[DEDUP-DEBUG] mount-snapshot restore', { snapUsers: users, historyLoadedRef: historyLoadedRef.current });
          }
        } catch (_) {}
        setChatMessages(manager._chatSnapshot);
      }
      if (manager._phasesSnapshot?.length > 0) setPhases(manager._phasesSnapshot);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — runs once on mount only

  // ── Sync snapshots to manager whenever state changes ──────
  // These survive React unmount so the workspace can restore state on return.
  useEffect(() => {
    if (manager && manager.projectId === projectIdRef.current) {
      manager._chatSnapshot = chatMessages;
    }
  }, [chatMessages]);

  useEffect(() => {
    if (manager && manager.projectId === projectIdRef.current) {
      manager._phasesSnapshot = phases;
    }
  }, [phases]);

  useEffect(() => {
    if (manager && manager.projectId === projectIdRef.current) {
      manager._statusSnapshot = state;
    }
  }, [state]);

  // ── Auto-connect on mount when token is available ────────
  useEffect(() => {
    if (!manager || !token) return;

    // If already open to THIS project — restore snapshot state immediately
    if (manager.isOpen && manager.projectId === projectIdRef.current) {
      // Restore last known status (may be 'running' if agent is still going)
      const snap = manager._statusSnapshot;
      setState(snap && snap !== 'idle' ? snap : 'ready');
      if (manager.sessionId) setSessionId(manager.sessionId);

      // Restore chat messages from snapshot (avoids blank chat on return)
      if (manager._chatSnapshot?.length > 0) {
        setChatMessages(manager._chatSnapshot);
      }
      // Restore phases
      if (manager._phasesSnapshot?.length > 0) {
        setPhases(manager._phasesSnapshot);
      }
      return;
    }

    // If already connecting — just track state
    if (manager.isConnecting) {
      setState('connecting');
      return;
    }

    // Fresh connection (new project or cold start)
    if (state === 'idle' || (manager.isOpen && manager.projectId !== projectIdRef.current)) {
      const taskToSend = initialTaskRef.current || '';
      if (taskToSend) {
        // Show user message in chat ONCE. Strip the [LUCID_PROJECT] header for display.
        const displayText = taskToSend.includes('\n\n')
          ? taskToSend.split('\n\n').slice(1).join('\n\n')
          : taskToSend;
        // Guard: lazy useState initializer may have already added this message.
        // Pushing again would create a visible duplicate.
        if (!initialTaskPreloaded.current) {
          pushChat('user', displayText);
        }
      }
      connect(taskToSend);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // ── Public API ───────────────────────────────────────────

  const startSession = useCallback(
    (taskOverride) => {
      const t = taskOverride || initialTaskRef.current;
      if (manager?.isOpen) {
        if (t) sendMessageInternal(t);
        return;
      }
      if (t) pushChat('user', t);
      connect(t);
    },
    [connect, pushChat]
  );

  const sendMessageInternal = useCallback(
    (text, images = [], options = {}) => {
      if (!manager?.isOpen) {
        pushLog('Not connected — cannot send message', 'error');
        return;
      }
      const payload = { type: 'message', content: text };
      if (options.mode) payload.mode = options.mode;
      if (options.webSearch !== undefined) payload.web_search = options.webSearch;
      // Click-to-edit (Base44) — element picked from the preview iframe.
      // Backend reads ``editable_target`` to skip Step 3b's vocab build and
      // synthesize a 100%-confidence EditIntent directly.
      if (options.editableTarget && typeof options.editableTarget === 'object') {
        payload.editable_target = options.editableTarget;
      }
      if (images.length > 0) {
        payload.images = images.map((img) => ({
          name: img.name,
          data: img.data,
          type: img.type || 'image',
          ...(img.url && { url: img.url }),
        }));
      }
      manager.send(payload);
      // suppressEcho: caller already rendered the user's message locally
      // (e.g. the workspace intent guard shows the user's ORIGINAL prompt,
      // then sends the cleaned summary to the WS) — skip the duplicate bubble.
      if (!options.suppressEcho) {
        pushChat('user', text, { images: images.length > 0 ? images : undefined });
      }
      pushLog(`→ ${text}`, 'user');
      // Clear stop suppression — user is starting a new task, any residual
      // events from a prior wind-down should be discarded; from here on the
      // new task's events should flow through normally.
      stopRequestedRef.current = false;
      // Show thinking indicator immediately; clear stale phases AND agentStatus
      // from the previous task so the status bar starts fresh — otherwise a
      // leftover "Researching…" label leaks into the next intake turn. With
      // both cleared, the empty-phase state reads "Analyzing your request…".
      setPhases([]);
      setAgentStatus(null);
      flushedRef.current = false;
      setCompletionSummary('');
      setState('running');
    },
    [pushLog, pushChat]
  );

  const sendMessage = useCallback(
    (text, images = [], options = {}) => {
      if (!text?.trim() && images.length === 0) return;
      if (!manager?.isOpen) {
        startSession(text?.trim() || '');
        return;
      }
      sendMessageInternal(text?.trim() || '', images, options);
    },
    [startSession, sendMessageInternal]
  );

  const sendCommand = useCallback(
    (cmd) => {
      if (!cmd?.trim()) return;
      if (!manager?.isOpen) {
        pushLog('Not connected — cannot send command', 'error');
        return;
      }
      manager.send({ type: 'message', content: cmd.trim() });
      pushLog(`$ ${cmd.trim()}`, 'user');
    },
    [pushLog]
  );

  const sendManualEdit = useCallback((patch, editableTarget = null) => {
    if (!patch || typeof patch !== 'object') return false;
    const targetPath = patch.path || editableTarget?.path || 'selection';
    if (!manager?.isOpen) {
      pushLog(`Manual edit changed preview only - connection is offline (${targetPath})`, 'warning');
      return false;
    }
    manager.send({
      type: 'manual_edit',
      patch,
      editable_target: editableTarget || null,
    });
    pushLog(`Manual edit applied: ${targetPath}`, 'file_write');
    return true;
  }, [pushLog]);

  const pushToBranch = useCallback((newBranchName) => {
    if (!manager?.isOpen) {
      pushLog('Not connected — cannot push', 'error');
      return;
    }
    manager.send({
      type: 'push',
      content: 'Manual push from UI',
      newBranch: newBranchName || null
    });
    pushLog(newBranchName ? `Pushing to new branch: ${newBranchName}…` : 'Pushing to current branch…', 'user');
  }, [pushLog]);

  const stopSession = useCallback(() => {
    // Start suppressing residual in-flight events immediately — backend takes
    // up to ~75s to fully unwind (cancellation + sandbox force-kill). Cleared
    // when status=ready arrives or the user sends a new message.
    stopRequestedRef.current = true;
    if (manager) {
      try {
        manager.send({ type: 'stop_task', task_id: sessionId });
      } catch (_) {}
    }
    setState('stopped');
    pushLog('Stopping session...', 'system');
  }, [pushLog, sessionId]);

  const stopPreview = useCallback(() => {
    if (manager?.isOpen) {
      try {
        manager.send({ type: 'stop_preview' });
      } catch (_) {}
    }
    setPreviewUrl(null);
    setPreviewLoading(false);
    setPreviewStatusMsg('');
    setPreviewError(null);
    setPreviewEverReady(false);
  }, []);

  // ── Retry — Phase 8 error recovery ───────────────────────
  // hint='preview': re-run dev server without re-cloning (WS stays open)
  // hint=undefined/other: full reconnect (clone failed, auth failed, etc.)
  const retry = useCallback((hint) => {
    setRetryCount(prev => prev + 1);

    if (hint === 'preview') {
      // Non-fatal preview error — ask the backend to restart the dev server
      if (manager?.isOpen) {
        manager.send({ type: 'retry_preview' });
        setPreviewError(null);
        pushLog('Restarting preview server…', 'system');
      }
    } else {
      // Fatal workspace error (e.g. clone failed) — close & reconnect.
      // Reset the internal reconnect counter so MAX_RECONNECTS fires
      // only on *automatic* reconnects, not after a manual retry.
      reconnectCount.current = 0;
      setError(null);
      setErrorStage(null);
      setErrorCode(null);
      setState('connecting');
      pushLog('Retrying connection…', 'system');
      if (manager) {
        manager.close(1000, 'User retry');
        setTimeout(() => {
          getFreshToken().then((freshToken) => {
            manager.connect({
              token: freshToken,
              projectId: projectIdRef.current,
              repoUrl: repoUrlRef.current,
              repoProvider: repoProviderRef.current,
              gitToken: gitTokenRef.current,
              branch: branchRef.current,
              task: '',
            });
          });
        }, 500);
      }
    }
  }, [pushLog]);

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
        // [DEDUP-DEBUG] log what's in prev when Supabase setInitialMessages hydrates
        try {
          if (typeof window !== 'undefined') {
            const userPrevs = prev.filter(p => p.role === 'user').map(p => ({ id: p.id, c: (p.content || '').slice(0, 60) }));
            const userHydrated = hydrated.filter(p => p.role === 'user').map(p => ({ id: p.id, c: (p.content || '').slice(0, 60) }));
            // eslint-disable-next-line no-console
            console.warn('[DEDUP-DEBUG] setHistoricalMessages (Supabase)', { prevUsers: userPrevs, hydratedUsers: userHydrated });
          }
        } catch (_) {}
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

    // Live agent status shown in chat panel during generation
    agentStatus,

    // Plan confirmation — approve or reject the plan before code generation
    planAwaiting,
    currentPlanData,
    planConfirmed,
    confirmPlan: useCallback(() => {
      if (!manager) return;
      // If the WS dropped (e.g. MAX_RECONNECTS exhausted) the manager is
      // idle — neither open nor connecting. send() would silently push the
      // payload into _pendingSends where nothing flushes it, leaving the
      // user staring at "starting code generation" while the backend
      // never receives the confirm. Force a fresh connect first.
      if (!manager.isOpen && !manager.isConnecting) {
        pushLog('Connection lost — reconnecting before confirming plan...', 'system');
        reconnectCount.current = 0;
        getFreshToken().then((freshToken) => {
          if (!manager.isOpen && !manager.isConnecting) {
            manager.connect({
              token: freshToken,
              projectId: projectIdRef.current,
              repoUrl: repoUrlRef.current,
              repoProvider: repoProviderRef.current,
              gitToken: gitTokenRef.current,
              branch: branchRef.current,
              task: '',
            });
          }
        });
      }
      const sent = manager.send({ type: 'plan_confirm' });
      // Queued sends flush on the next onopen — treat that as success.
      if (sent || manager.isConnecting) {
        setPlanAwaiting(false);
        setPlanConfirmed(true);
        pushLog('Plan confirmed — starting code generation...', 'system');
      } else {
        pushLog('Failed to send confirmation — please retry', 'error');
      }
    }, [pushLog]),
    rejectPlan: useCallback((correction) => {
      if (!manager) return;
      if (!manager.isOpen && !manager.isConnecting) {
        pushLog('Connection lost — reconnecting before rejecting plan...', 'system');
        reconnectCount.current = 0;
        getFreshToken().then((freshToken) => {
          if (!manager.isOpen && !manager.isConnecting) {
            manager.connect({
              token: freshToken,
              projectId: projectIdRef.current,
              repoUrl: repoUrlRef.current,
              repoProvider: repoProviderRef.current,
              gitToken: gitTokenRef.current,
              branch: branchRef.current,
              task: '',
            });
          }
        });
      }
      const sent = manager.send({ type: 'plan_reject', correction });
      if (sent || manager.isConnecting) {
        setPlanAwaiting(false);
        setPlanConfirmed(false);
        setCurrentPlanData(null);
        pushLog(`Plan rejected — re-researching: ${correction?.slice(0, 60)}...`, 'system');
      } else {
        pushLog('Failed to send rejection — please retry', 'error');
      }
    }, [pushLog]),

    // Clarification — user answered a disambiguation question. Two
    // flavours share this path: the legacy archetype-conflict question
    // (kind=undefined, the `archetype` arg is a layout archetype id),
    // and the Stage-0 intent clarifier (kind="intent_clarify",
    // `clarifyKey` carries the question key, `archetype` carries the
    // chosen option id). Both re-run the pipeline server-side with the
    // appropriate context marker prepended to the original task.
    submitClarification: useCallback(({ messageId, archetype, label, originalTask, kind, clarifyKey }) => {
      if (!manager) return;
      if (!manager.isOpen && !manager.isConnecting) {
        pushLog('Connection lost — reconnecting before sending answer...', 'system');
        reconnectCount.current = 0;
        getFreshToken().then((freshToken) => {
          if (!manager.isOpen && !manager.isConnecting) {
            manager.connect({
              token: freshToken,
              projectId: projectIdRef.current,
              repoUrl: repoUrlRef.current,
              repoProvider: repoProviderRef.current,
              gitToken: gitTokenRef.current,
              branch: branchRef.current,
              task: '',
            });
          }
        });
      }
      const sent = manager.send({
        type: 'clarification_response',
        archetype,
        option_label: label,
        task: originalTask,
        kind: kind || '',
        clarify_key: clarifyKey || '',
      });
      if (sent || manager.isConnecting) {
        setChatMessages((prev) => prev.map((m) => (
          m.id === messageId
            ? { ...m, clarification: { ...m.clarification, answered: true, answerLabel: label } }
            : m
        )));
        // The backend is now processing this answer (next clarification or
        // generation). Show the intake status: running + no phases yet reads
        // "Analyzing your request…" until real task_phase events arrive.
        setPhases([]);
        setAgentStatus(null);
        setState('running');
        pushLog(`Answer received: ${label} — analyzing…`, 'system');
      } else {
        pushLog('Failed to send choice — please retry', 'error');
      }
    }, [pushLog]),
  };
}
