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

const MAX_RECONNECTS = 3;

/**
 * useAgentSession — manages the full lifecycle of an AI agent session.
 */
export function useAgentSession({ projectId, task = '', token = '', repoUrl = '', gitToken = '', branch = '', autoStart = false }) {
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
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewTaskId, setPreviewTaskId] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewStatusMsg, setPreviewStatusMsg] = useState('');

  // ── WebContainers preview ─────────────────────────────────
  // Sandpack preview — { files: Record<string,string>, template: string }
  // sent by backend preview_files message; RightPanel renders SandpackPreview.
  const [previewFileMap, setPreviewFileMap] = useState(null);

  // ── Vercel deploy URL (persists after deployment) ────────
  const [deployUrl, setDeployUrl] = useState(null);

  // ── Written files in the current agent run (for HMR failure detection) ──
  // Accumulates filenames from file_write_event; reset at the start of each task.
  const [writtenFiles, setWrittenFiles] = useState([]);

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

  // Store volatile props in refs so callbacks don't go stale
  const tokenRef = useRef(token);
  const projectIdRef = useRef(projectId);
  const repoUrlRef = useRef(repoUrl);
  const gitTokenRef = useRef(gitToken);
  const branchRef = useRef(branch);

  tokenRef.current = token;
  projectIdRef.current = projectId;
  repoUrlRef.current = repoUrl;
  gitTokenRef.current = gitToken;
  branchRef.current = branch;

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

  // Keep refs in sync with state
  useEffect(() => { stepsRef.current = steps; }, [steps]);
  useEffect(() => { phasesRef.current = phases; }, [phases]);

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
          setState('preparing');
          setErrorStage(null);
          setPreviewError(null);
          pushLog('Connected — preparing workspace…', 'system');
        } else if (msg.event === 'error') {
          pushLog('WebSocket error', 'error');
        } else if (msg.event === 'closed') {
          if ([1000, 4001, 4010].includes(msg.code)) {
            setState('stopped');
            pushLog(`Session ended (${msg.reason || msg.code})`, 'system');
          } else if (msg.code === 4100) {
            // Page leaving — keep state as-is
          } else {
            if (reconnectCount.current < MAX_RECONNECTS) {
              reconnectCount.current += 1;
              setState('reconnecting');
              pushLog(`Reconnecting (${reconnectCount.current}/${MAX_RECONNECTS})…`, 'system');
              setTimeout(() => {
                if (manager && !manager.isOpen && !manager.isConnecting) {
                  manager.connect({
                    token: tokenRef.current,
                    projectId: projectIdRef.current,
                    repoUrl: repoUrlRef.current,
                    gitToken: gitTokenRef.current,
                    branch: branchRef.current,
                    task: '',
                  });
                }
              }, 2000);
            } else {
              setState('error');
              setError('Connection lost after multiple attempts.');
              pushLog('Connection lost', 'error');
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
        if (['initializing', 'cloning'].includes(st)) {
          // Show a dedicated "cloning" state so the UI can display
          // "Cloning your codebase..." rather than the generic preparing label.
          setState('cloning');
          if (msg.message) pushLog(msg.message, 'system');
        } else if (st === 'preparing') {
          setState('preparing');
          if (msg.message) pushLog(msg.message, 'system');
        } else if (st === 'ready' || st === 'mock_mode') {
          setState('ready');
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
        if (filename) {
          setWrittenFiles(prev => prev.includes(filename) ? prev : [...prev, filename]);
          pushLog(`[${action}] ${filename}`, 'file_write');
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
        // Update live chat status indicator
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

      // ─── Repo Created ─────────────────────────────────────
      if (msg.type === 'repo_created') {
        if (msg.platformOwned && msg.branch === 'main') {
          // new_project_mode: brand-new private repo created for this project
          pushChat('system',
            `✅ **Project repository created!**\n` +
            `📦 **[${msg.repoName || 'View Repository'}](${msg.repoUrl})**\n` +
            `🌿 Branch: \`main\``
          );
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
        pushLog(`[Preview] ${msg.message || msg.status || ''}`, 'system');
        return;
      }

      // ─── Preview Ready ────────────────────────────
      if (msg.type === 'preview_ready') {
        setPreviewUrl(msg.preview_url);
        setPreviewTaskId(msg.task_id);
        setPreviewError(null);
        setPreviewLoading(false);
        setPreviewStatusMsg('');
        pushLog(`[Preview] ${msg.message || 'Preview ready'}`, 'system');
        return;
      }

      // ─── Preview Error — non-fatal (dev server / tunnel failed) ──
      // Workspace stays READY; user can click "Restart Preview" to retry.
      if (msg.type === 'preview_error') {
        const stage = msg.error_stage || 'start';
        const message = msg.message || 'Preview unavailable — click Restart Preview to retry.';
        setPreviewError({ stage, message });
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
              repoUrl: msg.repoUrl || '',
              prUrl: msg.prUrl || '',
              newBranch: msg.newBranch || false,
              ts: Date.now(),
            },
          ]);
          pushLog(`Pushed to ${msg.branch}`, 'system');
        }
        return;
      }

      // ─── Error ──────────────────────────────────
      if (msg.type === 'error') {
        const errMsg = msg.message || 'Unknown error';
        setAgentStatus(null);
        pushChat('system', `⚠️ ${errMsg}`);
        pushLog(errMsg, 'error');
        if (errMsg.includes('Authentication') || errMsg.includes('Timeout waiting')) {
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
            // Python json.dumps adds spaces after colons, so check for the key only.
            if (m.role === 'assistant' && content.trimStart().startsWith('{"messageType"')) {
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
              // Drop init_msg_0 placeholder — WS history is authoritative ordering
              const livePrev = prev.filter(p => p.id !== 'init_msg_0');
              if (livePrev.length === 0) return hydrated;
              const existingById = new Map(livePrev.map(p => [p.id, p]));
              const toAdd = hydrated.filter(h => !existingById.has(h.id));
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

        // Structured plan message — rendered as a special plan card in the UI
        if (msg.messageType === 'plan' && msg.planData) {
          setCurrentPlanData(msg.planData);
          setChatMessages((prev) => [
            ...prev,
            {
              id: uid(),
              role: 'agent',
              messageType: 'plan',
              planData: msg.planData,
              fileWrites: [],
              ts: Date.now(),
            },
          ]);
          return;
        }

        if (content.trim()) {
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

      // ─── Warning — show in chat ───────────────────────
      if (msg.type === 'warning') {
        const text = msg.message || '';
        pushLog(text, 'warning');
        pushChat('system', `⚠️ ${text}`);
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

      manager.connect({
        token: tokenRef.current,
        projectId: projectIdRef.current,
        repoUrl: repoUrlRef.current,
        gitToken: gitTokenRef.current,
        branch: branchRef.current,
        task: taskToSend || '',
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
      if (manager._chatSnapshot?.length > 0) setChatMessages(manager._chatSnapshot);
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
      if (images.length > 0) {
        payload.images = images.map((img) => ({
          name: img.name,
          data: img.data,
          type: img.type || 'image',
          ...(img.url && { url: img.url }),
        }));
      }
      manager.send(payload);
      pushChat('user', text, { images: images.length > 0 ? images : undefined });
      pushLog(`→ ${text}`, 'user');
      // Show thinking indicator immediately; clear stale phases from previous task
      // so the buildLabel logic starts fresh (no stale currentPhaseNum).
      setPhases([]);
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
      // Fatal workspace error (e.g. clone failed) — close & reconnect
      setError(null);
      setErrorStage(null);
      setState('connecting');
      pushLog('Retrying connection…', 'system');
      if (manager) {
        manager.close(1000, 'User retry');
        setTimeout(() => {
          manager.connect({
            token: tokenRef.current,
            projectId: projectIdRef.current,
            repoUrl: repoUrlRef.current,
            gitToken: gitTokenRef.current,
            branch: branchRef.current,
            task: '',
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
      if (m.role === 'assistant' && content.trimStart().startsWith('{"messageType"')) {
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
        // Dedup against existing live WS messages (keep them at the end)
        const prevKeys = new Set(livePrev.map(p => `${p.role}::${(p.content || '').slice(0, 80)}`));
        const toAdd = hydrated.filter(h => !prevKeys.has(`${h.role}::${(h.content || '').slice(0, 80)}`));
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
    stopSession,
    pushToBranch,
    setInitialMessages,

    // Preview (noVNC / E2B legacy)
    previewUrl,
    previewTaskId,
    previewLoading,
    previewStatusMsg,
    clearPreview: () => { setPreviewUrl(null); setPreviewTaskId(null); setPreviewLoading(false); setPreviewStatusMsg(''); },
    stopPreview,

    // Sandpack preview — { files, template } from backend
    previewFileMap,

    // Vercel deploy URL
    deployUrl,

    // Files written in the current agent run — used for HMR failure detection
    writtenFiles,

    // Phase 8: error recovery
    errorStage,      // 'clone' | null — which init stage caused the workspace error
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
      if (!manager?.isOpen) return;
      manager.send({ type: 'plan_confirm' });
      setPlanAwaiting(false);
      setPlanConfirmed(true);
      pushLog('Plan confirmed — starting code generation...', 'system');
    }, [pushLog]),
    rejectPlan: useCallback((correction) => {
      if (!manager?.isOpen) return;
      manager.send({ type: 'plan_reject', correction });
      setPlanAwaiting(false);
      setPlanConfirmed(false);
      setCurrentPlanData(null);
      pushLog(`Plan rejected — re-researching: ${correction?.slice(0, 60)}...`, 'system');
    }, [pushLog]),
  };
}
