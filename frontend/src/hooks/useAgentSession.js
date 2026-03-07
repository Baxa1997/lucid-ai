'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — useAgentSession Hook
//  WebSocket connection to Python AI Engine (ws://…/api/v1/ws)
//
//  Input:  { projectId, task, token, autoStart }
//  Output: { state, sessionId, chatMessages, logs, files, error,
//            startSession, sendMessage, sendCommand, stopSession }
// ─────────────────────────────────────────────────────────

import { useState, useRef, useCallback, useEffect } from 'react';

const WS_BASE = process.env.NEXT_PUBLIC_AGENT_WS_URL || 'ws://localhost:8000/api/v1/ws';
const HEARTBEAT_INTERVAL_MS = 25_000;

/**
 * useAgentSession — manages the full lifecycle of an AI agent session.
 *
 * @param {Object}  opts
 * @param {string}  opts.projectId  – project / workspace identifier
 * @param {string}  [opts.task]     – initial task (sent on connect if autoStart)
 * @param {string}  [opts.token]    – auth token (passed as query param)
 * @param {string}  [opts.repoUrl]  – repository URL to clone
 * @param {string}  [opts.gitToken] – git provider auth token (GitHub PAT / GitLab token)
 * @param {string}  [opts.branch]   – branch to clone and work on
 * @param {boolean} [opts.autoStart] – automatically connect and start on mount
 */
export function useAgentSession({ projectId, task = '', token = '', repoUrl = '', gitToken = '', branch = '', autoStart = false }) {
  // ── State ────────────────────────────────────────────────
  // idle → connecting → preparing → ready → running → ready → ...
  const [state, setState] = useState('idle');
  const [sessionId, setSessionId] = useState(null);
  const [chatMessages, setChatMessages] = useState([]);
  const [logs, setLogs] = useState([]);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState(null);

  // ── Refs ─────────────────────────────────────────────────
  const wsRef = useRef(null);
  const heartbeatRef = useRef(null);
  const reconnectCount = useRef(0);
  const idCounter = useRef(0);
  const initialTaskRef = useRef(task);
  const connectingRef = useRef(false);

  // Store volatile props in refs so connect() doesn't get recreated
  const tokenRef = useRef(token);
  const projectIdRef = useRef(projectId);
  const repoUrlRef = useRef(repoUrl);
  const gitTokenRef = useRef(gitToken);
  const branchRef = useRef(branch);

  // Keep refs synced
  tokenRef.current = token;
  projectIdRef.current = projectId;
  repoUrlRef.current = repoUrl;
  gitTokenRef.current = gitToken;
  branchRef.current = branch;

  const MAX_RECONNECTS = 3;

  // Keep task ref updated
  useEffect(() => {
    initialTaskRef.current = task;
  }, [task]);

  // ── Helpers ──────────────────────────────────────────────
  const uid = () => `evt_${Date.now()}_${++idCounter.current}`;

  const pushChat = useCallback((role, content, meta = {}) => {
    if (!content || !content.trim()) return; // Never push empty content
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

  // ── Heartbeat (keep-alive) ───────────────────────────────
  const startHeartbeat = useCallback(() => {
    stopHeartbeat();
    heartbeatRef.current = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, HEARTBEAT_INTERVAL_MS);
  }, []);

  const stopHeartbeat = useCallback(() => {
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
    }
  }, []);

  // ── Handle incoming WebSocket messages ────────────────────
  const handleEvent = useCallback(
    (raw) => {
      let msg;
      try {
        msg = JSON.parse(raw);
      } catch {
        pushLog(raw, 'system');
        return;
      }

      switch (msg.type) {
        // ─── Status updates ───────────────────────────
        case 'status': {
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
          break;
        }

        // ─── Agent events (action / observation) ────
        case 'agent_event': {
          const content = msg.content || '';
          const eventType = msg.eventType || msg.event || '';
          const thought = msg.thought || '';
          const toolName = msg.toolName || '';

          // ── 1. Thinking block (collapsed by default) ──
          if (thought) {
            pushChat('thinking', thought);
          }

          // ── Skip task_start echo (don't repeat user's task) ──
          if (msg.event === 'task_start') {
            setState('running');
            pushLog(content, 'system');
            break;
          }

          // ── 2. Tool calls ──
          if (eventType === 'ActionEvent') {
            if (toolName === 'finish') {
              if (content && !thought) pushChat('agent', content);
            } else if (content) {
              // Backend marks exploration commands (ls, cat, view, etc.) as readOnly
              if (msg.readOnly) {
                // Just log — don't show in chat
                pushLog(content, 'cmd_output');
              } else {
                // Real modification — show as tool step card
                pushChat('tool', content, { toolName });
                pushLog(content, toolName === 'terminal' ? 'cmd_output' : 'file_write');
              }
            }
          }

          // ── 3. Agent message (direct response to user) ──
          else if (eventType === 'MessageEvent') {
            if (content) {
              pushChat('agent', content);
              pushLog(content, 'agent_message');
            }
          }

          // ── 4. Change summary (end of task) ──
          else if (eventType === 'ChangeSummary') {
            if (content) pushChat('agent', content);
          }

          // ── 5. Error ──
          else if (msg.event === 'error') {
            if (content) {
              pushChat('system', `⚠️ ${content}`);
              pushLog(content, 'error');
            }
          }

          // ── 6. Observation / other — log only, not chat ──
          else if (content) {
            pushLog(content, 'system');
          }

          // File tree update
          if (msg.fileTree && Array.isArray(msg.fileTree)) {
            setFiles(msg.fileTree);
          }
          break;
        }

        // ─── File tree update ────────────────────────
        case 'file_tree': {
          if (msg.tree && Array.isArray(msg.tree)) {
            setFiles(msg.tree);
          }
          break;
        }

        // ─── File change ─────────────────────────────
        case 'file_change': {
          if (Array.isArray(msg.files)) {
            setFiles(msg.files);
          } else if (msg.path) {
            setFiles((prev) =>
              prev.includes(msg.path) ? prev : [...prev, msg.path]
            );
          }
          pushLog(`File changed: ${msg.path || msg.files?.join(', ') || 'unknown'}`, 'file_write');
          break;
        }

        // ─── Terminal / Docker output ────────────────
        case 'log':
        case 'observation': {
          const text = msg.content || msg.message || JSON.stringify(msg);
          pushLog(text, msg.event || 'system');

          if (msg.event === 'agent_message' || msg.event === 'AgentMessageAction') {
            pushChat('agent', msg.content);
          }
          break;
        }

        case 'message': {
          if (msg.content && msg.content.trim()) {
            pushChat('agent', msg.content);
          }
          break;
        }

        case 'complete': {
          setState('ready');
          pushLog('Task completed', 'system');
          break;
        }

        // ─── Error ──────────────────────────────────
        case 'error': {
          const errMsg = msg.message || 'Unknown error';
          pushChat('system', `⚠️ ${errMsg}`);
          pushLog(errMsg, 'error');
          // Only set hard error state for auth/connection errors
          // For agent errors, stay in 'ready' so user can retry
          if (errMsg.includes('Authentication') || errMsg.includes('Timeout waiting')) {
            setError(errMsg);
            setState('error');
          } else {
            // Agent/LLM error — keep connection alive, go back to ready
            setState('ready');
          }
          break;
        }

        // ─── Heartbeat ACK (ignore) ─────────────────
        case 'pong':
        case 'ack':
          break;

        // ─── Fallback ───────────────────────────────
        default:
          pushLog(JSON.stringify(msg), 'system');
      }
    },
    [pushLog, pushChat]
  );

  // ── Connect WebSocket ────────────────────────────────────
  const connect = useCallback(
    (taskToSend) => {
      // Guard against duplicate connections
      if (connectingRef.current) return;
      if (
        wsRef.current &&
        (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }

      connectingRef.current = true;
      setState('connecting');
      setError(null);
      pushLog('Connecting to AI Engine…', 'system');

      // Read from refs (stable, not re-rendered values)
      const currentToken = tokenRef.current;
      const url = currentToken ? `${WS_BASE}?token=${currentToken}` : WS_BASE;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        connectingRef.current = false;
        setState('preparing');
        reconnectCount.current = 0;
        pushLog('Connected — preparing workspace…', 'system');
        startHeartbeat();

        // Read model selection from sessionStorage
        const modelProvider =
          (typeof window !== 'undefined' &&
            sessionStorage.getItem('lucid_model_provider')) ||
          'google';

        // Send handshake (task is optional — workspace setup first)
        const handshake = {
          token: currentToken || '',
          projectId: projectIdRef.current || '',
          modelProvider,
          repoUrl: repoUrlRef.current || '',
          gitToken: gitTokenRef.current || '',
          branch: branchRef.current || '',
          task: taskToSend || '',
        };
        ws.send(JSON.stringify(handshake));

        if (taskToSend) {
          pushLog(`Task queued: ${taskToSend.slice(0, 80)}…`, 'user');
        }
      };

      ws.onmessage = (event) => {
        handleEvent(event.data);
      };

      ws.onerror = () => {
        connectingRef.current = false;
        pushLog('WebSocket error', 'error');
      };

      ws.onclose = (event) => {
        connectingRef.current = false;
        wsRef.current = null;
        stopHeartbeat();

        if ([1000, 4001, 4010].includes(event.code)) {
          setState('stopped');
          pushLog(`Session ended (${event.reason || event.code})`, 'system');
          return;
        }

        // 4100 = navigating away — session stays alive on backend, don't reconnect
        if (event.code === 4100) {
          setState('idle');
          return;
        }

        if (reconnectCount.current < MAX_RECONNECTS) {
          reconnectCount.current += 1;
          pushLog(
            `Reconnecting (${reconnectCount.current}/${MAX_RECONNECTS})…`,
            'system'
          );
          setTimeout(() => connect(taskToSend), 2000);
        } else {
          setState('error');
          setError('Connection lost after multiple attempts.');
          pushLog('Connection lost', 'error');
        }
      };
    },
    [handleEvent, pushLog, startHeartbeat, stopHeartbeat]  // NO token/projectId — read from refs
  );

  // ── Public API ───────────────────────────────────────────

  /**
   * startSession — connect and optionally send an initial task.
   */
  const startSession = useCallback(
    (taskOverride) => {
      const t = taskOverride || initialTaskRef.current;
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        if (t) sendMessageInternal(t);
        return;
      }
      if (t) {
        pushChat('user', t);
      }
      connect(t);
    },
    [connect, pushChat]
  );

  /**
   * sendMessage — send a user message / instruction to the agent.
   */
  const sendMessageInternal = useCallback(
    (text) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        pushLog('Not connected — cannot send message', 'error');
        return;
      }
      wsRef.current.send(JSON.stringify({ type: 'message', content: text }));
      pushChat('user', text);
      pushLog(`→ ${text}`, 'user');
    },
    [pushLog, pushChat]
  );

  const sendMessage = useCallback(
    (text) => {
      if (!text?.trim()) return;

      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        startSession(text.trim());
        return;
      }

      sendMessageInternal(text.trim());
    },
    [startSession, sendMessageInternal]
  );

  /**
   * sendCommand — send a terminal command to the agent.
   */
  const sendCommand = useCallback(
    (cmd) => {
      if (!cmd?.trim()) return;
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        pushLog('Not connected — cannot send command', 'error');
        return;
      }
      wsRef.current.send(JSON.stringify({ type: 'message', content: cmd.trim() }));
      pushLog(`$ ${cmd.trim()}`, 'user');
    },
    [pushLog]
  );

  /**
   * stopSession — gracefully close the WebSocket.
   */
  const stopSession = useCallback(() => {
    stopHeartbeat();
    if (wsRef.current) {
      // Send stop message before closing
      try {
        wsRef.current.send(JSON.stringify({ type: 'stop', content: 'stop' }));
      } catch (_) {}
      wsRef.current.close(1000, 'User stopped session');
      wsRef.current = null;
    }
    setState('stopped');
    pushLog('Session stopped', 'system');
  }, [stopHeartbeat, pushLog]);

  // ── Auto-connect on mount when token is available ────────
  useEffect(() => {
    if (token && state === 'idle') {
      // Connect without a task — workspace will prepare
      connect('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // ── Cleanup on unmount ───────────────────────────────────
  useEffect(() => {
    return () => {
      stopHeartbeat();
      if (wsRef.current) {
        // Close with 4100 = "navigating away" — backend keeps session alive
        wsRef.current.close(4100, 'Component navigating away');
        wsRef.current = null;
      }
    };
  }, [stopHeartbeat]);

  // ── Return ───────────────────────────────────────────────
  return {
    // State
    state,
    sessionId,
    chatMessages,
    logs,
    files,
    error,

    // Aliases for backward compat
    status: state,
    messages: chatMessages,
    terminalLogs: logs,
    // isReady — true when the workspace is prepared and accepting tasks
    isReady: state === 'ready',
    isPreparing: state === 'preparing',

    // Actions
    startSession,
    sendMessage,
    sendCommand,
    stopSession,
  };
}
