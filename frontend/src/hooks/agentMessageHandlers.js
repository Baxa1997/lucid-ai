// ─────────────────────────────────────────────────────────
//  Lucid AI — Agent WS message dispatcher
//
//  Extracted from useAgentSession.js (Phase 2 Step 3a) so the hook
//  file isn't ~2200 lines. Pure module — no React, no hooks.
//
//  Why a deps bag instead of imports/closures?
//    The hook owns all the React state setters (~30) and refs (~18)
//    needed by message handlers. Threading each via context or per-
//    callsite imports would be unwieldy; instead, the hook builds a
//    deps object once per render (cheap — just references to stable
//    setters and refs) and the dispatcher destructures it on each
//    call. No identity churn on `handleMessage` because the hook
//    keeps the deps in a ref and the dispatcher reads `.current`.
// ─────────────────────────────────────────────────────────

import manager from '@/lib/agentWSManager';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';
import { routeErrorEnvelope } from '@/lib/errorEnvelope';
import {
  formatImageBinderSummary,
  formatQualitySummary,
  formatBuildStart,
  formatBuildResult,
  formatRepoCreateStarted,
  formatRepoCreateDone,
  formatResearchStarted,
  formatCodeWriteStarted,
  formatAgentActivity,
  isLegacyDuplicateOfTypedEvent,
} from '@/lib/wsEvents';

export const MAX_RECONNECTS = 8;
// Exponential backoff in ms — 8 attempts spread over ~6 minutes covers
// laptop sleep, browser tab throttling, transient network drops, and
// short backend restarts without exhausting retries on the user.
export const RECONNECT_BACKOFF_MS = [2000, 5000, 10000, 20000, 30000, 60000, 60000, 60000];

/**
 * Build the WS-message dispatcher.
 *
 * @param {() => object} getDeps — Returns the current deps bag. Called
 *   fresh on every message so the dispatcher always sees the latest
 *   setters/refs without React closure staleness.
 * @returns {(msg: object) => void}
 */
export function createMessageDispatcher(getDeps) {
  return function dispatchAgentMessage(msg) {
    const {
      // ── React state setters ───────────────────────
      setState,
      setSessionId,
      setError,
      setErrorCode,
      setErrorStage,
      setSteps,
      setFinishSummary,
      setCompletionSummary,
      setPhases,
      setDeclaredPipeline,
      setAgentStatus,
      setResolvingInfo,
      setResolvingProgress,
      setPreviewUrl,
      setPreviewTaskId,
      setPreviewLoading,
      setPreviewStatusMsg,
      setPreviewStage,
      setPreviewStartedAt,
      setPreviewEverReady,
      setPreviewFileMap,
      setPreviewError,
      setDeployUrl,
      setQualityReport,
      setWrittenFiles,
      setFileContents,
      setFileMetrics,
      setPlanAwaiting,
      setCurrentPlanData,
      setChatMessages,
      setFiles,
      setAwaitingResponse,
      // ── Mutable refs (object identity stable) ────
      chatMessagesRef,
      stepsRef,            // (read elsewhere; unused here but kept for parity)
      flushedRef,
      phasesRef,           // (read elsewhere; unused here but kept for parity)
      stopRequestedRef,
      reconnectCount,
      authRefreshAttemptedRef,
      tokenRef,
      projectIdRef,
      repoUrlRef,
      repoProviderRef,
      gitTokenRef,
      branchRef,
      historyLoadedRef,
      // ── Helper callbacks (memoised in the hook) ──
      pushChat,
      pushLog,
      pushAgentActivity,
      flushPhasesToChat,
      getFreshToken,
      // ── Per-session computed values ──────────────
      uid,
      _previewStorageKey,
    } = getDeps();

      // ─── Stop suppression ─────────────────────────────────
      // If user clicked Stop, drop residual pipeline events that are still
      // in-flight (agent_event, step, file_write_event, chat messages, etc.)
      // Only state/connection/error events pass through so the UI can
      // correctly transition back to 'ready' when backend finishes unwinding.
      if (stopRequestedRef.current) {
        const alwaysAllow = new Set([
          '_internal',        // connection events
          'workspace_state',  // canonical state transitions
          'agent.status',     // canonical workflow activity
          'agent.workflow',   // prompt-router decision
          'status',           // ready/error status
          'stopped',          // explicit stopped event
          'stop_ack',         // stop confirmation
          'error',            // critical errors
          'pong',
          'ack',
        ]);
        if (!alwaysAllow.has(msg.type)) return;
      }

      // ─── Phase 2 Step 5: live agent activity pill ─────────
      // Decoupled from task_phase (which drives the phase chart). Every
      // typed sub-step + agent_event thought/tool-use becomes a transient
      // pill above the chat input. formatAgentActivity returns null for
      // unrelated types (workspace_state, status, etc.) — no-op then.
      // Mounted at the TOP so individual handlers don't need to remember
      // to wire it; they can early-return as before.
      try {
        const act = formatAgentActivity(msg);
        if (act) pushAgentActivity(act);
      } catch {}

      // ─── Awaiting-response release (Phase 2 honest-status refactor) ──
      // Any concrete backend response — chat, state change, error, plan,
      // clarification, etc. — releases the optimistic "Sending…" flag
      // set in sendMessageInternal. We exclude pong/ack/internal because
      // those are connection-level noise, not the agent answering.
      if (
        setAwaitingResponse &&
        msg.type !== 'pong' &&
        msg.type !== 'ack' &&
        msg.type !== '_internal'
      ) {
        setAwaitingResponse(false);
      }

      // ─── Canonical agent status — render verbatim ───────────────
      if (msg.type === 'agent.status') {
        setAgentStatus({
          key: msg.key || '',
          label: msg.label || '',
          description: msg.description || '',
          state: msg.state || 'active',
          source: msg.source || 'agent',
          workflowId: msg.workflow_id || '',
        });
        if (msg.state === 'active') setState('running');
        if (msg.label) pushLog(`[Agent status] ${msg.label}`, 'system');
        return;
      }

      if (msg.type === 'agent.workflow') {
        pushLog(
          `[Workflow] ${msg.decided_by || 'agent'} selected ${msg.workflow_id || msg.next_action || 'unknown'}`,
          'system',
        );
        return;
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
          setAgentStatus((current) => current?.state === 'waiting' ? current : null);
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
          setAgentStatus((current) => current?.state === 'waiting' ? current : null);
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
          setAgentStatus(null);
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

      // ─── Codex Message — parallel to claude_message ─
      // Raw event narration from the Codex CLI fallback fixer. Same
      // contract as claude_message: log-only, no chat bubble.
      if (msg.type === 'codex_message') {
        const content = msg.content || '';
        if (content) {
          pushLog(`[Codex] ${content.slice(0, 300)}`, 'agent_message');
        }
        return;
      }

      // ─── Terminal log — Bash stream from claude_cli ─
      // `stream: "stdin"` carries the command Claude is about to run
      // (prefixed by `$`); `stream: "stdout"` carries its output. Lost
      // entirely before this handler — the user couldn't see what
      // commands Claude was running.
      if (msg.type === 'terminal_log') {
        const data = msg.data || '';
        const stream = msg.stream === 'stdin' ? 'cmd_input' : 'cmd_output';
        if (data) pushLog(data, stream);
        return;
      }

      // ─── Classification — auto-selected task params ─
      // step3_classify decides developer level + model + complexity for
      // this run. Backend emits a structured event; we surface the
      // chosen knobs as a single log line so the user can see what was
      // picked without dumping the JSON.
      if (msg.type === 'classification') {
        const dev = msg.developer || 'auto';
        const model = msg.model || 'auto';
        const taskType = msg.task_type || 'feature_simple';
        const intent = typeof msg.intent === 'string' ? msg.intent.slice(0, 100) : '';
        pushLog(
          `[classification] ${dev} · ${model} · ${taskType}${intent ? ` — ${intent}` : ''}`,
          'system',
        );
        return;
      }

      // ─── Project indexed — connected-repo metadata ─
      // Fires once per pipeline run when working against an external repo.
      // The backend's `message` field is already user-friendly; surface it
      // as a system chat note + keep the structured fields in the log.
      if (msg.type === 'project_indexed') {
        if (msg.message) pushChat('system', msg.message);
        pushLog(
          `[Index] framework=${msg.framework || 'unknown'} ` +
          `language=${msg.language || 'unknown'} ` +
          `pm=${msg.packageManager || 'npm'} routes=${msg.routeCount || 0}`,
          'system',
        );
        return;
      }

      // ─── Preview stopped — backend confirmed shutdown ─
      // Could be FE-initiated (user clicked Stop Preview) or backend-
      // initiated (auto-shutdown, sandbox death). Either way, mirror
      // what stopPreview() does locally so the iframe doesn't keep
      // pointing at a dead URL.
      if (msg.type === 'preview_stopped') {
        setPreviewUrl(null);
        setPreviewLoading(false);
        setPreviewStatusMsg('');
        setPreviewError(null);
        setPreviewEverReady(false);
        if (_previewStorageKey && typeof window !== 'undefined') {
          try { sessionStorage.removeItem(_previewStorageKey); } catch {}
        }
        pushLog('[Preview] stopped', 'system');
        return;
      }

      // ─── Pipeline declare (Phase 2 Step 2) ────────
      // Backend tells us upfront what phases this run will go through, so
      // the chart can render against an actual list rather than assuming
      // 1-8. Fail-soft: legacy backends never send this — consumers fall
      // back to numeric phase fields on task_phase events.
      if (msg.type === 'pipeline.declare') {
        const pipelineId = msg.pipeline_id || 'new';
        const declared = Array.isArray(msg.phases) ? msg.phases : [];
        // Validate each entry has the fields we expect; silently drop bad
        // ones rather than crashing the chart.
        const safe = declared
          .filter((p) => p && typeof p.key === 'string' && typeof p.label === 'string')
          .map((p) => ({
            key: p.key,
            label: p.label,
            index: typeof p.index === 'number' ? p.index : null,
          }));
        setDeclaredPipeline({ pipelineId, phases: safe });
        pushLog(`[Pipeline] declared ${pipelineId}: ${safe.map((p) => p.key).join(' → ')}`, 'system');
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
        // No imperative status update here — the TaskProgress chart consumes
        // `phases` directly, and the agent activity pill is driven by the
        // typed sub-step events via formatAgentActivity (Step 5).
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

      // ─── Clarify / gibberish_detected ─────────────
      // The pipeline could not understand the prompt (gibberish / too short).
      // Rendered as a normal assistant chat message — NOT a red error banner —
      // so the user can simply reply with more detail and try again.
      //
      // `gibberish_detected` is a legacy shape from landing_pipeline.py that
      // carries the same intent as `clarify` (the canonical step1_validate
      // shape). Without this branch the pipeline would stall on "Analyzing
      // your request…" forever because no other handler resets state=ready
      // for this message type.
      if (msg.type === 'clarify' || msg.type === 'gibberish_detected') {
        const clarifyMsg = msg.message || 'Could you tell me more about what you want to build?';
        // Clear lingering phase data so the in-chat status pill doesn't
        // keep showing "Researching your idea…" after a clarify event
        // has handed control back to the user.
        setPhases([]);
        setState('ready');
        setAgentStatus({
          key: 'waiting_for_details',
          label: 'Waiting for project details...',
          description: clarifyMsg,
          state: 'waiting',
          source: 'agent',
          workflowId: 'clarify',
        });
        pushChat('assistant', clarifyMsg);
        pushLog(clarifyMsg, 'system');
        return;
      }

      // ─── Unified error envelope (Phase 2 Step 1) ────────────
      // Pure routing lives in lib/errorEnvelope so it can be tested in
      // isolation. `routeErrorEnvelope` returns null when the message is
      // legacy (no `severity` field), letting us fall through to the
      // existing handler — purely additive, never breaks anything.
      {
        const action = routeErrorEnvelope(msg);
        if (action) {
          pushLog(action.logMessage, action.logLevel);
          if (action.chatMessage) pushChat(action.chatRole, action.chatMessage);
          if (action.setErrorCode !== null) setErrorCode(action.setErrorCode);
          if (action.setError !== null) setError(action.setError);
          if (action.setState !== null) setState(action.setState);
          return;
        }
      }

      // ─── Error (legacy shape) ────────────────────
      // Pre-Phase-2 emissions without a `severity` field. Kept verbatim so
      // unmigrated callsites keep working until they're switched over to
      // emit_error() in subsequent commits.
      if (msg.type === 'error') {
        const errMsg = msg.message || 'Unknown error';
        const code = msg.code || null;
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
          // Dedup: drop a chat_message echo when the IMMEDIATELY PREVIOUS
          // message is an identical role+content. Backend can re-emit a
          // freshly-persisted user prompt (e.g. when the proxy's bind_chat
          // fires after the handshake task path) and without this guard
          // the bubble appears twice.
          //
          // We check ONLY the last message (not the last 3) because legit
          // repeats matter: a follow-up guard emits the SAME clarify text
          // every time the user sends gibberish. With a 3-message window
          // the second/third/Nth attempt's response gets dropped because
          // the earlier agent bubble is still in the tail, even though
          // the user has typed in between. The user then sees their own
          // messages stacking up with no agent response.
          const norm = (s) => (typeof s === 'string' ? s.trim().replace(/\s+/g, ' ').toLowerCase().slice(0, 160) : '');
          const incoming = `${role === 'assistant' ? 'agent' : role}::${norm(content)}`;
          const last = chatMessagesRef.current[chatMessagesRef.current.length - 1];
          if (last) {
            const lastKey = `${last.role === 'assistant' ? 'agent' : last.role}::${norm(last.content)}`;
            if (lastKey === incoming) return;
          }
          pushChat(role, content);
        }
        return;
      }

      // ─── Image binder summary (Phase 2 Step 4 typed event) ────
      // Replaces the free-form "✅ Bound N/M images" progress text. Pure
      // formatting lives in lib/wsEvents so it's independently testable.
      if (msg.type === 'image_binder.summary') {
        const f = formatImageBinderSummary(msg);
        pushLog(f.log, 'system');
        return;
      }

      // ─── Quality gate summary (Phase 2 Step 4 typed event) ────
      // Aggregate counts only. The per-check chart still arrives as
      // `quality_report`. The legacy "✅ Quality gate: …" progress lines
      // are suppressed by isLegacyDuplicateOfTypedEvent below.
      if (msg.type === 'quality.summary') {
        const f = formatQualitySummary(msg);
        pushLog(f.log, 'system');
        return;
      }

      // ─── Build start / result (Phase 2 Step 4 typed events) ────
      // The legacy `type: "build"` payloads from BuildValidator have no
      // FE handler, so dual-emit doesn't fight an existing chip. These
      // typed events carry structured fields the chart can render.
      if (msg.type === 'build.start') {
        const f = formatBuildStart(msg);
        pushLog(f.log, 'system');
        return;
      }
      if (msg.type === 'build.result') {
        const f = formatBuildResult(msg);
        pushLog(f.log, f.status === 'pass' ? 'system' : 'warning');
        return;
      }

      // ─── Repo create (Phase 2 Step 4 typed events) ────
      // The follow-up `repo_created` event (handled separately above)
      // already carries the URL used for navigation; these typed events
      // just feed the chart chip and replace the duplicate progress text
      // (which is suppressed via isLegacyDuplicateOfTypedEvent).
      if (msg.type === 'repo.create_started') {
        const f = formatRepoCreateStarted(msg);
        pushLog(f.log, 'system');
        return;
      }
      if (msg.type === 'repo.create_done') {
        const f = formatRepoCreateDone(msg);
        pushLog(f.log, f.status === 'pass' ? 'system' : 'warning');
        return;
      }

      // ─── Medium-impact sub-step markers (Phase 2 Step 4) ────
      // These duplicate `task_phase` but carry a tiny structured payload
      // (research kind / task_type / model) so the chart can label the
      // sub-step instead of just incrementing a generic progress bar.
      if (msg.type === 'fixers.run_started') {
        pushLog('[fixers] sweep starting', 'system');
        return;
      }
      if (msg.type === 'brief.distill_started') {
        pushLog('[brief] distill starting', 'system');
        return;
      }
      if (msg.type === 'research.started') {
        const f = formatResearchStarted(msg);
        pushLog(f.log, 'system');
        return;
      }
      if (msg.type === 'cli.session_started') {
        const model = typeof msg.model === 'string' ? msg.model : '';
        pushLog(`[cli] session_started${model ? ` model=${model}` : ''}`, 'system');
        return;
      }
      if (msg.type === 'code.write_started') {
        const f = formatCodeWriteStarted(msg);
        pushLog(f.log, 'system');
        return;
      }

      // ─── Progress — show in terminal logs; also update live status subtext ────
      if (msg.type === 'progress') {
        const text = msg.message || '';
        // Suppress legacy free-form messages whose typed-event counterpart
        // already updated state above. Once the legacy emission is removed
        // from the backend (after one release), the suppression list can shrink.
        if (isLegacyDuplicateOfTypedEvent(text)) {
          return;
        }
        pushLog(text, 'system');
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
  };
}
