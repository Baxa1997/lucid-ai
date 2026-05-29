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
    /** @type {string|null} last Redis Stream event ID — sent on reconnect for delta replay */
    this._lastEventId = null;
    /**
     * Outbound queue for messages issued while the socket is not OPEN
     * (mid-reconnect, in-flight close, etc.). Without this, a click on
     * "Confirm Plan" between a disconnect and the next onopen is silently
     * dropped → backend never receives the confirmation → UI shows
     * confirmed but nothing happens.
     * @type {Array<string>}
     */
    this._pendingSends = [];

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

      // Tab-visibility healing — when the user comes back to the workspace
      // tab after backgrounding it (switched browsers, slept laptop, etc.),
      // verify the WS is alive. If it's dead, fire `_internal: visibility_resume`
      // so useAgentSession can reset its retry counter and trigger a fresh
      // connect. Without this, a backgrounded tab that lost its WS while
      // hidden is stuck — the manager never knows to reconnect, so the
      // user sees a frozen UI with no preview/chat updates.
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        // Tab is now visible. Two interesting cases:
        //   1. WS already OPEN — the backend's existing connection is fine,
        //      just nudge with a ping so any zombie TCP state surfaces.
        //   2. WS dead/closing — emit visibility_resume so useAgentSession
        //      reconnects with a fresh counter (the existing reconnect
        //      logic uses the same WS event loop, so this just kicks it).
        if (this.isOpen) {
          try { this.ws.send(JSON.stringify({ type: 'ping' })); } catch (_) {}
          return;
        }
        if (!this._connecting) {
          this._emit({ type: '_internal', event: 'visibility_resume' });
        }
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
  connect({ token, projectId, repoUrl, repoProvider, gitToken, branch, task, modelProvider }) {
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
      this._lastEventId = null;
      this._handshakeTaskSentFor = null;
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
        // Detach the stale socket's handlers first so it can't also fire
        // a late `onclose` with a different code and race us.
        try { ws.onopen = null; ws.onerror = null; ws.onclose = null; ws.onmessage = null; } catch (_) {}
        try { ws.close(); } catch (_) {}
        this.ws = null;
        // Emit a synthetic `closed` (not `error`) so the hook's existing
        // close-handler drives the retry/fail loop. A prior bug emitted
        // only `error` here, leaving the UI stuck on "Reconnecting…"
        // forever whenever the server was unreachable.
        this._emit({
          type: '_internal',
          event: 'closed',
          code: 1006,
          reason: 'Connection timeout',
        });
      }
    }, CONNECT_TIMEOUT_MS);

    ws.onopen = () => {
      this._connecting = false;
      this._clearConnectTimeout();
      this._startHeartbeat();

      // Wizard handshake fallback: if `task` arrived empty but sessionStorage
      // still holds the wizard prompt for this project, recover it here. This
      // avoids the SSR/hydration race where the workspace page's useState
      // lazy initializer returns '' on the server (sessionStorage absent),
      // and React reuses that '' on hydration — so the hook is wired with
      // task='' even though the prompt is sitting in sessionStorage.
      //
      // Critical: only fire on the FIRST handshake for this projectId. On
      // reconnect, all `manager.connect({task: ''})` paths intentionally
      // send empty task — re-recovering would re-trigger the pipeline.
      let resolvedTask = task || '';
      const isFirstHandshake = !this._handshakeTaskSentFor || this._handshakeTaskSentFor !== projectId;
      if (!resolvedTask && isFirstHandshake && projectId && typeof window !== 'undefined') {
        try {
          const stored = window.sessionStorage.getItem(`wizard_prompt_${projectId}`);
          const isValidated = window.sessionStorage.getItem(`wizard_validated_${projectId}`) === '1';
          if (stored && !isValidated) {
            window.sessionStorage.setItem(`wizard_pending_validation_${projectId}`, stored);
            window.sessionStorage.removeItem(`wizard_prompt_${projectId}`);
            window.sessionStorage.removeItem(`wizard_desc_${projectId}`);
            window.dispatchEvent(new CustomEvent('lucid:wizard-pending-validation', {
              detail: { projectId },
            }));
            // eslint-disable-next-line no-console
            console.warn('[WS] unvalidated wizard prompt recovered — deferred to workspace guard');
          } else if (stored) {
            const metaStr = window.sessionStorage.getItem(`wizard_meta_${projectId}`);
            const desc = window.sessionStorage.getItem(`wizard_desc_${projectId}`) || '';
            if (metaStr) {
              try {
                const meta = JSON.parse(metaStr);
                const parts = [
                  `description=${desc || 'project'}`,
                  `stack=${meta.stack || 'nextjs'}`,
                  `backend=${meta.backend || 'none'}`,
                ];
                if (meta.projectType) parts.push(`project_type=${meta.projectType}`);
                if (meta.deployment) parts.push(`deployment=${meta.deployment}`);
                if (meta.figmaUrl) parts.push(`figma_url=${meta.figmaUrl}`);
                resolvedTask = `[LUCID_PROJECT] ${parts.join(' | ')}\n\n${stored}`;
              } catch {
                resolvedTask = stored;
              }
            } else {
              resolvedTask = stored;
            }
            // eslint-disable-next-line no-console
            console.warn('[WS] handshake task was empty — recovered wizard prompt from sessionStorage');
          }
        } catch {}
      }

      ws.send(JSON.stringify({
        token: token || '',
        projectId: projectId || '',
        modelProvider: modelProvider,
        repoUrl: repoUrl || '',
        repoProvider: repoProvider || '',
        gitToken: gitToken || '',
        branch: branch || '',
        task: resolvedTask,
        lastEventId: this._lastEventId || '',
      }));
      if (resolvedTask) {
        this._handshakeTaskSentFor = projectId;
      }

      // Flush any messages issued while the socket was reconnecting.
      if (this._pendingSends.length > 0) {
        const batch = this._pendingSends.splice(0);
        for (const payload of batch) {
          try { ws.send(payload); } catch (_) {}
        }
      }

      this._emit({ type: '_internal', event: 'connected' });
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        // Track session ID
        if (msg.sessionId) this.sessionId = msg.sessionId;
        // Track Redis Stream cursor — used for delta replay on reconnect
        if (msg.type === '_cursor' && msg.id) {
          this._lastEventId = msg.id;
          return; // internal bookkeeping only, don't forward to listeners
        }
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

  /**
   * Send a JSON message. If the socket is not OPEN (mid-reconnect, etc.)
   * the payload is queued and flushed on the next onopen. Returns true if
   * sent or queued — there is no silent drop for the caller to worry about.
   */
  send(data) {
    const payload = typeof data === 'string' ? data : JSON.stringify(data);
    if (this.isOpen) {
      this.ws.send(payload);
      return true;
    }
    // Cap the queue so a permanently-down socket can't grow it without bound.
    if (this._pendingSends.length < 64) {
      this._pendingSends.push(payload);
    }
    return false;
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
    this._lastEventId = null;
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
