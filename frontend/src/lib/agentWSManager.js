'use client';

// ─────────────────────────────────────────────────────────
//  Global WebSocket Manager
//  Lives outside React lifecycle — survives sidebar navigation.
//
//  The WebSocket connection persists even when the workspace
//  component unmounts (e.g., user clicks "Conversations" tab).
//  When user returns to workspace, the hook reconnects to the
//  same manager and receives any buffered events.
// ─────────────────────────────────────────────────────────

const WS_BASE = process.env.NEXT_PUBLIC_AGENT_WS_URL || 'ws://localhost:8000/api/v1/ws';
const HEARTBEAT_MS = 25000;

class AgentWSManager {
  constructor() {
    /** @type {WebSocket | null} */
    this.ws = null;
    /** @type {number | null} */
    this._heartbeat = null;
    /** @type {string} */
    this._projectId = '';
    /** @type {boolean} */
    this._connecting = false;
    /** @type {Function[]} */
    this._listeners = [];
    /** @type {string} current session ID from backend */
    this.sessionId = null;

    // Listen for page close — only then do we close the WS
    if (typeof window !== 'undefined') {
      window.addEventListener('beforeunload', () => {
        this.close(4100, 'Page leaving');
      });
    }
  }

  /** True if the WS is connected and open. */
  get isOpen() {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  /** True if connecting. */
  get isConnecting() {
    return this._connecting || (this.ws !== null && this.ws.readyState === WebSocket.CONNECTING);
  }

  /** Subscribe to events. Returns unsubscribe function. */
  subscribe(listener) {
    this._listeners.push(listener);
    return () => {
      this._listeners = this._listeners.filter((l) => l !== listener);
    };
  }

  _emit(event) {
    for (const fn of this._listeners) {
      try { fn(event); } catch (_) {}
    }
  }

  /** Connect (or reconnect) to the agent WS. */
  connect({ token, projectId, repoUrl, gitToken, branch, task, modelProvider }) {
    // If already open or connecting (any project) — never open a second connection.
    // A new connection sends a new handshake that can create a brand-new container
    // on the backend, destroying the active workspace.
    if (this.isOpen || this._connecting) return;
    if (this.ws !== null) return; // still in CLOSING state

    this._connecting = true;
    this._projectId = projectId;

    const url = token ? `${WS_BASE}?token=${token}` : WS_BASE;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this._connecting = false;
      this._startHeartbeat();

      ws.send(JSON.stringify({
        token: token || '',
        projectId: projectId || '',
        modelProvider: modelProvider,
        repoUrl: repoUrl || '',
        gitToken: gitToken || '',
        branch: branch || '',
        task: task || '',
      }));

      this._emit({ type: '_internal', event: 'connected' });
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        // Track session ID
        if (msg.sessionId) this.sessionId = msg.sessionId;
        this._emit(msg);
      } catch (_) {}
    };

    ws.onerror = () => {
      this._connecting = false;
      this._emit({ type: '_internal', event: 'error' });
    };

    ws.onclose = (event) => {
      this._connecting = false;
      this._stopHeartbeat();
      this.ws = null;
      this._emit({ type: '_internal', event: 'closed', code: event.code, reason: event.reason });
    };
  }

  /** Send a JSON message. */
  send(data) {
    if (this.isOpen) {
      this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }

  /** Explicitly close. */
  close(code = 1000, reason = '') {
    this._stopHeartbeat();
    if (this.ws) {
      try { this.ws.close(code, reason); } catch (_) {}
      this.ws = null;
    }
    this._projectId = '';
    this.sessionId = null;
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this._heartbeat = setInterval(() => {
      if (this.isOpen) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, HEARTBEAT_MS);
  }

  _stopHeartbeat() {
    if (this._heartbeat) {
      clearInterval(this._heartbeat);
      this._heartbeat = null;
    }
  }
}

// Module-level singleton — persists across React renders / unmounts
const manager = typeof window !== 'undefined' ? new AgentWSManager() : null;

export default manager;
