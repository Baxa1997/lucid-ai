'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — useAgentConnection
//
//  Extracted from useAgentSession.js (Phase 2 Step 3b). Owns:
//    • The manager.subscribe lifecycle
//    • The `connect` callback (token-fetch → manager.connect)
//    • Three snapshot-sync effects (chatMessages, phases, state →
//      manager._*Snapshot) so the workspace can restore on return
//    • Two auto-reconnect / auto-restore effects (mount snapshot
//      restore, token-arrival fresh-connect)
//
//  Why a separate hook instead of inline: useAgentSession was 2200+ lines
//  and the connection layer is the only chunk that touches `manager` at
//  high frequency. Pulling it out clarifies where the WebSocket lifecycle
//  starts/ends, and keeps the hook file focused on state.
// ─────────────────────────────────────────────────────────

import { useCallback, useEffect, useRef } from 'react';
import manager from '@/lib/agentWSManager';

/**
 * @param {object} params
 * @param {string}   params.token            Supabase access token (may be empty until session loads)
 * @param {string}   params.state            Current session state value (for snapshot sync)
 * @param {Array}    params.chatMessages     For snapshot sync
 * @param {Array}    params.phases           For snapshot sync
 *
 * // Refs (stable identity across renders)
 * @param {object}   params.projectIdRef
 * @param {object}   params.repoUrlRef
 * @param {object}   params.repoProviderRef
 * @param {object}   params.gitTokenRef
 * @param {object}   params.branchRef
 * @param {object}   params.tokenRef
 * @param {object}   params.initialTaskPreloaded
 * @param {object}   params.initialTaskRef
 *
 * // Setters
 * @param {Function} params.setState
 * @param {Function} params.setSessionId
 * @param {Function} params.setChatMessages
 * @param {Function} params.setPhases
 * @param {Function} params.setError
 *
 * // Helpers
 * @param {Function} params.pushLog
 * @param {Function} params.pushChat
 * @param {Function} params.getFreshToken
 * @param {Function} params.handleMessage   Subscribed to manager
 *
 * @returns {{ connect: (task?: string) => void }}
 */
export function useAgentConnection({
  token,
  state,
  chatMessages,
  phases,
  projectIdRef,
  repoUrlRef,
  repoProviderRef,
  gitTokenRef,
  branchRef,
  tokenRef,
  initialTaskPreloaded,
  initialTaskRef,
  setState,
  setSessionId,
  setChatMessages,
  setPhases,
  setError,
  pushLog,
  pushChat,
  getFreshToken,
  handleMessage,
}) {
  // Live mirror of `state` for effects that intentionally key off [token]
  // only. Reading `state` directly inside them sees the value frozen at the
  // last token change (stale closure) — if the token arrives while state is
  // transiently non-idle, the auto-connect below would be skipped forever.
  const stateRef = useRef(state);
  stateRef.current = state;

  // ── Subscribe to global manager events ──────────────────
  useEffect(() => {
    if (!manager) return;
    const unsub = manager.subscribe(handleMessage);
    return unsub;
  }, [handleMessage]);

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
    // pushLog identity is stable (useCallback in the hook); others are
    // refs / setters with stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatMessages]);

  useEffect(() => {
    if (manager && manager.projectId === projectIdRef.current) {
      manager._phasesSnapshot = phases;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phases]);

  useEffect(() => {
    if (manager && manager.projectId === projectIdRef.current) {
      manager._statusSnapshot = state;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
    if (stateRef.current === 'idle' || (manager.isOpen && manager.projectId !== projectIdRef.current)) {
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

  return { connect };
}
