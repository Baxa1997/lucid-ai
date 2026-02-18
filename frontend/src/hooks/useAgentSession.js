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
 * @param {boolean} [opts.autoStart] – automatically connect and start on mount
 */
export function useAgentSession({ projectId, task = '', token = '', autoStart = false }) {
  // ── State ────────────────────────────────────────────────
  const [state, setState] = useState('idle'); // idle | starting | connecting | connected | error | stopped
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

  const MAX_RECONNECTS = 3;

  // Keep task ref updated
  useEffect(() => {
    initialTaskRef.current = task;
  }, [task]);

  // ── Helpers ──────────────────────────────────────────────
  const uid = () => `evt_${Date.now()}_${++idCounter.current}`;

  const pushChat = useCallback((role, content) => {
    setChatMessages((prev) => [
      ...prev,
      { id: uid(), role, content, ts: Date.now() },
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
          pushLog(`[${msg.status}] ${msg.message || ''}`, 'system');

          if (msg.sessionId) {
            setSessionId(msg.sessionId);
          }

          if (msg.status === 'ready' || msg.status === 'mock_mode') {
            setState('connected');
            pushChat('system', msg.message || 'Agent is ready.');
          } else if (msg.status === 'completed') {
            pushChat('system', msg.message || 'Task completed.');
          } else if (msg.status === 'initializing') {
            setState('starting');
          }
          break;
        }

        // ─── Agent events (action / observation) ──────
        case 'agent_event': {
          const content = msg.content || '';
          const eventType = msg.eventType || msg.event || '';

          // Agent thought / message → chat panel
          if (
            eventType.includes('Message') ||
            eventType.includes('Think')
          ) {
            pushChat('agent', content);
          }

          // Command execution → terminal
          if (msg.command) {
            pushLog(`$ ${msg.command}`, 'cmd_output');
          }
          if (content) {
            // Determine log type based on event
            let logType = 'system';
            if (eventType.includes('CmdOutput')) logType = 'cmd_output';
            else if (eventType.includes('FileWrite') || eventType.includes('FileEdit')) logType = 'file_write';
            else if (eventType.includes('Error')) logType = 'error';
            else if (msg.event === 'agent_message' || eventType.includes('Message')) logType = 'agent_message';
            pushLog(content, logType);
          }

          // Agent message → also to chat
          if (msg.event === 'agent_message' || msg.event === 'AgentMessageAction') {
            pushChat('agent', content);
          }

          // File tree update attached to event
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

        // ─── Agent chat message ─────────────────────
        case 'message': {
          pushChat('agent', msg.content || '');
          break;
        }

        // ─── Task complete ──────────────────────────
        case 'complete': {
          pushChat('system', 'Agent task completed.');
          pushLog('Task completed', 'system');
          break;
        }

        // ─── Error ──────────────────────────────────
        case 'error': {
          const errMsg = msg.message || 'Unknown error';
          setError(errMsg);
          setState('error');
          pushChat('system', errMsg);
          pushLog(errMsg, 'error');
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
      if (
        wsRef.current &&
        (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }

      setState('connecting');
      setError(null);
      pushLog('Connecting to AI Engine…', 'system');

      const url = token ? `${WS_BASE}?token=${token}` : WS_BASE;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setState('connected');
        reconnectCount.current = 0;
        pushLog('Connected', 'system');
        startHeartbeat();

        // Read model selection from sessionStorage
        const modelProvider =
          (typeof window !== 'undefined' &&
            sessionStorage.getItem('lucid_model_provider')) ||
          'google';

        // Send initial handshake
        const handshake = {
          token: token || '',
          projectId: projectId || '',
          modelProvider,
          repoUrl: '',
          task: taskToSend || '',
        };
        ws.send(JSON.stringify(handshake));

        if (taskToSend) {
          pushLog(`Task sent: ${taskToSend.slice(0, 80)}…`, 'user');
        }
      };

      ws.onmessage = (event) => {
        handleEvent(event.data);
      };

      ws.onerror = () => {
        pushLog('WebSocket error', 'error');
      };

      ws.onclose = (event) => {
        wsRef.current = null;
        stopHeartbeat();

        if ([1000, 4001, 4010].includes(event.code)) {
          setState('stopped');
          pushLog(`Session ended (${event.reason || event.code})`, 'system');
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
    [token, projectId, handleEvent, pushLog, startHeartbeat, stopHeartbeat]
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

  // ── Auto-start on mount ──────────────────────────────────
  useEffect(() => {
    if (autoStart && task) {
      startSession(task);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart]);

  // ── Cleanup on unmount ───────────────────────────────────
  useEffect(() => {
    return () => {
      stopHeartbeat();
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmount');
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

    // Actions
    startSession,
    sendMessage,
    sendCommand,
    stopSession,
  };
}
