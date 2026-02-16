'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — useAgentSession Hook
//  WebSocket connection to Python AI Engine (ws://…/ws)
//
//  Input:  { projectId, token }
//  Output: { messages, files, status, terminalLogs,
//            startSession, sendMessage, stopSession }
// ─────────────────────────────────────────────────────────

import { useState, useRef, useCallback, useEffect } from 'react';

const WS_BASE = process.env.NEXT_PUBLIC_AGENT_WS_URL || 'ws://localhost:8000/ws';
const HEARTBEAT_INTERVAL_MS = 25_000; // 25s keep-alive ping

/**
 * useAgentSession — manages the full lifecycle of an AI agent session.
 *
 * @param {Object}  opts
 * @param {string}  opts.projectId  – project / workspace identifier
 * @param {string}  [opts.token]    – auth token (passed as query param)
 * @returns {{
 *   messages:     Array<{ type: 'user'|'agent'|'system', content: string, id: string, ts: number }>,
 *   files:        string[],
 *   status:       'idle'|'connecting'|'connected'|'error',
 *   terminalLogs: Array<{ id: string, content: string, ts: number }>,
 *   error:        string|null,
 *   startSession: (task?: string) => void,
 *   sendMessage:  (text: string) => void,
 *   stopSession:  () => void,
 * }}
 */
export function useAgentSession({ projectId, token = '' }) {
  // ── State ────────────────────────────────────────────────
  const [status, setStatus] = useState('idle');
  const [messages, setMessages] = useState([]);
  const [terminalLogs, setTerminalLogs] = useState([]);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState(null);

  // ── Refs ─────────────────────────────────────────────────
  const wsRef = useRef(null);
  const heartbeatRef = useRef(null);
  const reconnectCount = useRef(0);
  const idCounter = useRef(0);

  const MAX_RECONNECTS = 3;

  // ── Helpers ──────────────────────────────────────────────
  const uid = () => `evt_${Date.now()}_${++idCounter.current}`;

  const pushMessage = useCallback((type, content) => {
    setMessages((prev) => [
      ...prev,
      { id: uid(), type, content, ts: Date.now() },
    ]);
  }, []);

  const pushLog = useCallback((content) => {
    setTerminalLogs((prev) => [
      ...prev,
      { id: uid(), content, ts: Date.now() },
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

  // ── Handle incoming WebSocket messages (The Protocol) ────
  const handleEvent = useCallback(
    (raw) => {
      let msg;
      try {
        msg = JSON.parse(raw);
      } catch {
        // Non-JSON data → treat as terminal output
        pushLog(raw);
        return;
      }

      switch (msg.type) {
        // ─── Terminal / Docker output ──────────────────
        case 'log':
        case 'observation': {
          const text = msg.content || msg.message || JSON.stringify(msg);
          pushLog(text);

          // If observation also carries an agent-facing message,
          // mirror it into the chat so the user sees it.
          if (msg.event === 'agent_message' || msg.event === 'AgentMessageAction') {
            pushMessage('agent', msg.content);
          }
          break;
        }

        // ─── Agent chat message ───────────────────────
        case 'message': {
          pushMessage('agent', msg.content || '');
          break;
        }

        // ─── File-tree change ─────────────────────────
        case 'file_change': {
          // msg.files = ["src/main.py", …] or msg.path = "src/main.py"
          if (Array.isArray(msg.files)) {
            setFiles(msg.files);
          } else if (msg.path) {
            setFiles((prev) =>
              prev.includes(msg.path) ? prev : [...prev, msg.path]
            );
          }
          pushLog(`📝 File changed: ${msg.path || msg.files?.join(', ') || 'unknown'}`);
          break;
        }

        // ─── Agent events (action / state / complete) ─
        case 'agent_event': {
          const content = msg.content || '';
          const eventType = msg.eventType || msg.event || '';

          // Agent thought → chat
          if (
            eventType.includes('Message') ||
            eventType.includes('Think')
          ) {
            pushMessage('agent', content);
          }

          // Command execution → terminal
          if (msg.command) {
            pushLog(`$ ${msg.command}`);
          }
          if (content) {
            pushLog(content);
          }

          // File tree update attached to event
          if (msg.fileTree && Array.isArray(msg.fileTree)) {
            setFiles(msg.fileTree);
          }
          break;
        }

        // ─── Status updates ───────────────────────────
        case 'status': {
          pushLog(`[${msg.status}] ${msg.message || ''}`);
          if (msg.status === 'ready' || msg.status === 'mock_mode') {
            setStatus('connected');
            pushMessage('system', msg.message || 'Agent is ready.');
          }
          break;
        }

        // ─── Task complete ────────────────────────────
        case 'complete': {
          pushMessage('system', '✅ Agent task completed.');
          pushLog('✅ Task completed');
          break;
        }

        // ─── Error ────────────────────────────────────
        case 'error': {
          const errMsg = msg.message || 'Unknown error';
          setError(errMsg);
          setStatus('error');
          pushMessage('system', `⚠️ ${errMsg}`);
          pushLog(`❌ ${errMsg}`);
          break;
        }

        // ─── Heartbeat ACK (ignore) ──────────────────
        case 'pong':
        case 'ack':
          break;

        // ─── Fallback ─────────────────────────────────
        default:
          pushLog(JSON.stringify(msg));
      }
    },
    [pushLog, pushMessage]
  );

  // ── Connect WebSocket ────────────────────────────────────
  const connect = useCallback(
    (initialTask) => {
      // Prevent double-connect
      if (
        wsRef.current &&
        (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }

      setStatus('connecting');
      setError(null);
      pushLog('🔌 Connecting to AI Engine…');

      // Build URL with token query param
      const url = token ? `${WS_BASE}?token=${token}` : WS_BASE;

      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus('connected');
        reconnectCount.current = 0;
        pushLog('🟢 Connected');
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
          task: initialTask || '',
        };
        ws.send(JSON.stringify(handshake));

        if (initialTask) {
          pushLog(`📨 Task sent: ${initialTask.slice(0, 80)}…`);
        }
      };

      ws.onmessage = (event) => {
        handleEvent(event.data);
      };

      ws.onerror = () => {
        pushLog('⚠️ WebSocket error');
      };

      ws.onclose = (event) => {
        wsRef.current = null;
        stopHeartbeat();

        // Normal close codes
        if ([1000, 4001, 4010].includes(event.code)) {
          setStatus('idle');
          pushLog(`🔴 Session ended (${event.reason || event.code})`);
          return;
        }

        // Auto-reconnect
        if (reconnectCount.current < MAX_RECONNECTS) {
          reconnectCount.current += 1;
          pushLog(
            `🔄 Reconnecting (${reconnectCount.current}/${MAX_RECONNECTS})…`
          );
          setTimeout(() => connect(initialTask), 2000);
        } else {
          setStatus('error');
          setError('Connection lost after multiple attempts.');
          pushLog('❌ Connection lost');
        }
      };
    },
    [token, projectId, handleEvent, pushLog, startHeartbeat, stopHeartbeat]
  );

  // ── Public API ───────────────────────────────────────────

  /**
   * startSession — connect and optionally send an initial task.
   * If already connected, sends the task as a follow-up message.
   */
  const startSession = useCallback(
    (task) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        // Already connected → just send as message
        if (task) sendMessageInternal(task);
        return;
      }
      if (task) {
        pushMessage('user', task);
      }
      connect(task);
    },
    [connect, pushMessage]
  );

  /**
   * sendMessage — send a user message / instruction to the agent.
   * Wire format: { type: "action", content: text }
   */
  const sendMessageInternal = useCallback(
    (text) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        pushLog('⚠️ Not connected — cannot send message');
        return;
      }
      wsRef.current.send(JSON.stringify({ type: 'action', content: text }));
      pushMessage('user', text);
      pushLog(`→ ${text}`);
    },
    [pushLog, pushMessage]
  );

  // Wrapper so external callers get a stable reference
  const sendMessage = useCallback(
    (text) => {
      if (!text?.trim()) return;

      // If not connected yet, start a session with this as the task
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        startSession(text.trim());
        return;
      }

      sendMessageInternal(text.trim());
    },
    [startSession, sendMessageInternal]
  );

  /**
   * stopSession — gracefully close the WebSocket.
   */
  const stopSession = useCallback(() => {
    stopHeartbeat();
    if (wsRef.current) {
      wsRef.current.close(1000, 'User stopped session');
      wsRef.current = null;
    }
    setStatus('idle');
    pushLog('🛑 Session stopped');
  }, [stopHeartbeat, pushLog]);

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
    messages,
    files,
    status,
    terminalLogs,
    error,

    // Actions
    startSession,
    sendMessage,
    stopSession,
  };
}
