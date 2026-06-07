'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — useAgentActions
//
//  Extracted from useAgentSession.js (Phase 2 Step 3c). Owns every
//  outbound WS action the UI can invoke:
//    Text / images:  startSession, sendMessage
//    Terminal:       sendCommand
//    Editor:         sendManualEdit
//    Git:            pushToBranch
//    Control:        stopSession, stopPreview, retry
//    Plan flow:      confirmPlan, rejectPlan
//    Clarification:  submitClarification
//
//  Many of these share the same "ensure-connection-then-send" pattern —
//  if the WebSocket dropped (e.g. reconnect cap exhausted, idle close),
//  fire a fresh connect first so the payload doesn't get stranded in
//  manager._pendingSends. That shared logic lives in `ensureConnected`
//  inside this hook.
// ─────────────────────────────────────────────────────────

import { useCallback } from 'react';
import manager from '@/lib/agentWSManager';

/**
 * @returns {{
 *   startSession: Function,
 *   sendMessage: Function,
 *   sendCommand: Function,
 *   sendManualEdit: Function,
 *   pushToBranch: Function,
 *   stopSession: Function,
 *   stopPreview: Function,
 *   retry: Function,
 *   confirmPlan: Function,
 *   rejectPlan: Function,
 *   submitClarification: Function,
 * }}
 */
export function useAgentActions({
  // State values used in callback bodies
  sessionId,
  // Refs
  projectIdRef,
  repoUrlRef,
  repoProviderRef,
  gitTokenRef,
  branchRef,
  reconnectCount,
  stopRequestedRef,
  flushedRef,
  initialTaskRef,
  // Setters
  setState,
  setError,
  setErrorStage,
  setErrorCode,
  setSessionId,
  setRetryCount,
  setPhases,
  setCompletionSummary,
  setAgentStatus,
  setPreviewUrl,
  setPreviewLoading,
  setPreviewStatusMsg,
  setPreviewError,
  setPreviewEverReady,
  setPlanAwaiting,
  setPlanConfirmed,
  setCurrentPlanData,
  setChatMessages,
  setAwaitingResponse,
  // Helpers
  pushLog,
  pushChat,
  getFreshToken,
  connect,
}) {
  // ── Shared: ensure-connected-then-send guard ──────────────
  // Used by confirmPlan / rejectPlan / submitClarification before send.
  // If the WS dropped (e.g. MAX_RECONNECTS exhausted) the manager is idle
  // — neither open nor connecting. send() would silently push the payload
  // into _pendingSends where nothing flushes it.
  const ensureConnected = useCallback((logLabel) => {
    if (!manager) return;
    if (!manager.isOpen && !manager.isConnecting) {
      pushLog(`Connection lost — reconnecting before ${logLabel}...`, 'system');
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushLog]);

  // ── Text / image message senders ──────────────────────────
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
      // Clear stale phases / completion summary / flush guard so the new
      // task's intake doesn't carry over UI artifacts from the previous run.
      // These are UI-only cleanups (no state lying about agent status).
      setPhases([]);
      setAgentStatus(null);
      flushedRef.current = false;
      setCompletionSummary('');
      // Set the awaiting-response flag so the chat UI can show "Sending…"
      // feedback without lying about workspace state. The dispatcher
      // clears this on the first backend message that comes back.
      // Previously this branch did setState('running') optimistically,
      // which made the BuildingScreen show "Analyzing your request…"
      // even when the backend rejected the prompt in <100ms (gibberish).
      setAwaitingResponse(true);
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
    [pushLog, pushChat]
  );

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
    [connect, pushChat, sendMessageInternal, initialTaskRef]
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

  // ── Terminal command ──────────────────────────────────────
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

  // ── Click-to-edit / inline edit ───────────────────────────
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

  // ── Git push ──────────────────────────────────────────────
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

  // ── Stop active task ──────────────────────────────────────
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushLog, sessionId]);

  // ── Stop preview server ───────────────────────────────────
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Retry — Phase 8 error recovery ────────────────────────
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushLog]);

  // ── Plan confirm / reject ─────────────────────────────────
  const confirmPlan = useCallback(() => {
    if (!manager) return;
    ensureConnected('confirming plan');
    const sent = manager.send({ type: 'plan_confirm' });
    // Queued sends flush on the next onopen — treat that as success.
    if (sent || manager.isConnecting) {
      setPlanAwaiting(false);
      setPlanConfirmed(true);
      pushLog('Plan confirmed — starting code generation...', 'system');
    } else {
      pushLog('Failed to send confirmation — please retry', 'error');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushLog, ensureConnected]);

  const rejectPlan = useCallback((correction) => {
    if (!manager) return;
    ensureConnected('rejecting plan');
    const sent = manager.send({ type: 'plan_reject', correction });
    if (sent || manager.isConnecting) {
      setPlanAwaiting(false);
      setPlanConfirmed(false);
      setCurrentPlanData(null);
      pushLog(`Plan rejected — re-researching: ${correction?.slice(0, 60)}...`, 'system');
    } else {
      pushLog('Failed to send rejection — please retry', 'error');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushLog, ensureConnected]);

  // ── Clarification answer ──────────────────────────────────
  // Two flavours share this path:
  //   - legacy archetype-conflict question (kind=undefined, the
  //     `archetype` arg is a layout archetype id),
  //   - Stage-0 intent clarifier (kind="intent_clarify", `clarifyKey`
  //     carries the question key, `archetype` carries the chosen
  //     option id).
  // Both re-run the pipeline server-side with the appropriate context
  // marker prepended to the original task.
  const submitClarification = useCallback(({
    messageId, archetype, label, originalTask, kind, clarifyKey,
  }) => {
    if (!manager) return;
    ensureConnected('sending answer');
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
      // Backend is now processing this answer. Clear stale phases so the
      // intake doesn't carry leftover titles. Use awaitingResponse for
      // the "Sending…" feedback (released by the next backend event) —
      // do NOT lie about state='running' before the backend confirms.
      setPhases([]);
      setAgentStatus(null);
      setAwaitingResponse(true);
      pushLog(`Answer received: ${label} — sending…`, 'system');
    } else {
      pushLog('Failed to send choice — please retry', 'error');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushLog, ensureConnected]);

  return {
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
  };
}
