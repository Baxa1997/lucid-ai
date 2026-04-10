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
const CONNECT_TIMEOUT_MS = 15000; // 15s timeout for connection

class AgentWSManager {
  constructor() {
    /** @type {WebSocket | null} */
    this.ws = null;
    /** @type {number | null} */
    this._heartbeat = null;
    /** @type {number | null} */
    this._connectTimeout = null;
    /** @type {string} */
    this._projectId = '';
    /** @type {boolean} */
    this._connecting = false;
    /** @type {Function[]} */
    this._listeners = [];
    /** @type {string} current session ID from backend */
    this.sessionId = null;

    // ── Session snapshot — survives React component unmount/remount ──
    // When the workspace page navigates away and back, the new hook instance
    // reads these to immediately restore chat + phases instead of showing empty.
    /** @type {Array|null} last chatMessages state */
    this._chatSnapshot = null;
    /** @type {Array} last phases state */
    this._phasesSnapshot = [];
    /** @type {string} last status state */
    this._statusSnapshot = 'idle';

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

  /** True if currently trying to connect. */
  get isConnecting() {
    return this._connecting;
  }

  /** The project ID this manager is currently connected to. */
  get projectId() {
    return this._projectId;
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

  /** 
   * Connect (or reconnect) to the agent WS.
   * 
   * FIX: If a previous connection is in CLOSING state, we force-cleanup it
   * before starting a new one. This prevents the manager from getting "stuck"
   * when navigating between projects.
   */
  connect({ token, projectId, repoUrl, gitToken, branch, task, modelProvider }) {
    // If already open to the SAME project — do nothing
    if (this.isOpen && this._projectId === projectId) return;

    // If already connecting — do nothing (but check for timeout via _connectTimeout)
    if (this._connecting) return;

    // If connected to a DIFFERENT project, close the old connection first
    if (this.isOpen && this._projectId !== projectId) {
      console.log(`[WS] Switching project: ${this._projectId} → ${projectId}`);
      this.close(1000, 'Switching project');
    }

    // Clear snapshots when starting a fresh connection to a different project
    if (this._projectId !== projectId) {
      this._chatSnapshot = null;
      this._phasesSnapshot = [];
      this._statusSnapshot = 'idle';
    }

    // CRITICAL FIX: If ws exists but is not OPEN (e.g. CLOSING state),
    // force cleanup so we don't get stuck
    if (this.ws !== null && this.ws.readyState !== WebSocket.OPEN) {
      console.log(`[WS] Cleaning up stale WS in state ${this.ws.readyState}`);
      try { this.ws.onclose = null; this.ws.onerror = null; this.ws.onmessage = null; } catch (_) {}
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }

    this._connecting = true;
    this._projectId = projectId;

    const url = token ? `${WS_BASE}?token=${token}` : WS_BASE;
    const ws = new WebSocket(url);
    this.ws = ws;

    // Connection timeout — if WS doesn't open within 15s, clean up
    this._clearConnectTimeout();
    this._connectTimeout = setTimeout(() => {
      if (this._connecting) {
        console.warn('[WS] Connection timeout — cleaning up');
        this._connecting = false;
        try { ws.close(); } catch (_) {}
        this.ws = null;
        this._emit({ type: '_internal', event: 'error', reason: 'Connection timeout' });
      }
    }, CONNECT_TIMEOUT_MS);

    ws.onopen = () => {
      this._connecting = false;
      this._clearConnectTimeout();
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
      this._clearConnectTimeout();
      this._emit({ type: '_internal', event: 'error' });
    };

    ws.onclose = (event) => {
      this._connecting = false;
      this._clearConnectTimeout();
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
    this._clearConnectTimeout();
    this._connecting = false;
    if (this.ws) {
      try { this.ws.close(code, reason); } catch (_) {}
      this.ws = null;
    }
    this._projectId = '';
    this.sessionId = null;
  }

  _clearConnectTimeout() {
    if (this._connectTimeout) {
      clearTimeout(this._connectTimeout);
      this._connectTimeout = null;
    }
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
