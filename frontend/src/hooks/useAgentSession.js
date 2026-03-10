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
  const [chatMessages, setChatMessages] = useState([]);
  const [logs, setLogs] = useState([]);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState(null);
  const historyLoadedRef = useRef(false);

  // ── Structured progress steps for current task ───────────
  // Each step: { id, step, label, done }
  const [steps, setSteps] = useState([]);
  const [finishSummary, setFinishSummary] = useState('');

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

  // Keep ref in sync with state
  useEffect(() => { stepsRef.current = steps; }, [steps]);

  // ── Flush current steps into a chat message ──────────────
  const flushStepsToChat = useCallback((summary) => {
    // Guard: prevent double flush
    if (flushedRef.current) return;
    flushedRef.current = true;

    const currentSteps = stepsRef.current;

    // Build a clean message from completed steps + summary
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
        { id: uid(), role: 'agent', content, ts: Date.now() },
      ]);
    }

    // Clear steps separately (NOT nested inside setChatMessages)
    setSteps([]);
    setFinishSummary('');
  }, []);

  // ── Handle incoming messages ─────────────────────────────
  const handleMessage = useCallback(
    (msg) => {
      // Internal manager events
      if (msg.type === '_internal') {
        if (msg.event === 'connected') {
          setState('preparing');
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
          // Flush all steps + summary into a single chat message
          setFinishSummary(summary || '');
          // Small delay to let last step update render
          setTimeout(() => flushStepsToChat(summary || ''), 150);
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
        if (['initializing', 'cloning', 'preparing'].includes(st)) {
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
          // Clear steps for new task
          setSteps([]);
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

      if (msg.type === 'complete') {
        setState('ready');
        pushLog('Task completed', 'system');
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

      if (msg.type === 'pong' || msg.type === 'ack') return;

      pushLog(JSON.stringify(msg), 'system');
    },
    [pushLog, pushChat, flushStepsToChat]
  );

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
      if (manager.isOpen || manager.isConnecting) return;

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

  // ── Auto-connect on mount when token is available ────────
  useEffect(() => {
    if (!manager || !token) return;

    if (manager.isOpen) {
      setState('ready');
      if (manager.sessionId) setSessionId(manager.sessionId);
      return;
    }

    if (manager.isConnecting) {
      setState('connecting');
      return;
    }

    if (state === 'idle') {
      connect('');
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
    (text) => {
      if (!manager?.isOpen) {
        pushLog('Not connected — cannot send message', 'error');
        return;
      }
      manager.send({ type: 'message', content: text });
      pushChat('user', text);
      pushLog(`→ ${text}`, 'user');
    },
    [pushLog, pushChat]
  );

  const sendMessage = useCallback(
    (text) => {
      if (!text?.trim()) return;
      if (!manager?.isOpen) {
        startSession(text.trim());
        return;
      }
      sendMessageInternal(text.trim());
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
      try { manager.send({ type: 'stop', content: 'stop' }); } catch (_) {}
      manager.close(1000, 'User stopped session');
    }
    setState('stopped');
    pushLog('Session stopped', 'system');
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
      const key = `${m.role}::${(m.content || '').slice(0, 100)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    const hydrated = deduped.map((m, i) => ({
      id: m.id || `saved_${i}`,
      role: m.role === 'assistant' ? 'agent' : m.role,
      content: m.content || '',
      ts: new Date(m.created_at).getTime() || Date.now(),
      fromHistory: true,
    }));

    if (hydrated.length > 0) {
      setChatMessages((prev) => {
        if (prev.length > 0) return [...hydrated, ...prev];
        return hydrated;
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
    error,

    // Structured progress steps
    steps,
    finishSummary,

    // Aliases for backward compat
    status: state,
    messages: chatMessages,
    terminalLogs: logs,
    isReady: state === 'ready',
    isPreparing: state === 'preparing' || state === 'connecting',

    // Actions
    startSession,
    sendMessage,
    sendCommand,
    stopSession,
    pushToBranch,
    setInitialMessages,
  };
}
