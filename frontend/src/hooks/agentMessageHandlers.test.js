// Focused tests for the extracted WS-message dispatcher.
//
// The dispatcher is a pure module — given a deps bag (state setters as
// spies, refs as mutable objects, helpers as spies), each call exercises
// exactly one handler branch and we assert which setters / helpers fired.
//
// Coverage strategy: we do NOT test all 54 message types. We test
//   • the gating logic (stop suppression, activity pill auto-route)
//   • the canonical state-transition handlers (workspace_state, status)
//   • the trickier or higher-blast-radius branches (chat_history dedup,
//     error envelope routing, legacy-error fatal codes, agent_event
//     task_start reset, file_write_event metric accumulation, _internal
//     reconnect path)
//   • one representative typed event per Phase-2-Step-4 category
// That's enough to lock in the wiring without growing the test file
// proportionally with every new message type.

import { describe, it, expect, vi, beforeEach } from 'vitest';

// agentWSManager + supabase client are imported at module load but only
// USED inside the _internal close handler. The simple-handler tests below
// don't reach them; the reconnect test mocks manager state explicitly.
vi.mock('@/lib/agentWSManager', () => ({
  default: {
    isOpen: false,
    isConnecting: false,
    projectId: null,
    connect: vi.fn(),
    subscribe: vi.fn(),
  },
}));
vi.mock('@/lib/supabase/client', () => ({
  getSupabaseBrowserClient: () => ({
    auth: {
      refreshSession: vi.fn().mockResolvedValue({
        data: { session: { access_token: 'fresh-token' } },
        error: null,
      }),
    },
  }),
}));

import { createMessageDispatcher } from './agentMessageHandlers';

// ─── Test helpers ──────────────────────────────────────────

/**
 * Build a deps bag where every setter / helper is a spy and every ref is
 * a plain mutable object. Returned bag can be inspected after each call.
 *
 * @param {object} overrides — any field can be replaced (e.g. preset
 *   stopRequestedRef.current to true to test stop suppression).
 */
function makeDeps(overrides = {}) {
  const refs = {
    chatMessagesRef: { current: [] },
    stepsRef: { current: [] },
    flushedRef: { current: false },
    phasesRef: { current: [] },
    stopRequestedRef: { current: false },
    reconnectCount: { current: 0 },
    authRefreshAttemptedRef: { current: false },
    tokenRef: { current: 'tok' },
    projectIdRef: { current: 'proj-1' },
    repoUrlRef: { current: '' },
    repoProviderRef: { current: '' },
    gitTokenRef: { current: '' },
    branchRef: { current: 'main' },
    historyLoadedRef: { current: false },
  };
  // 30 setters as fresh spies — listed once here so every test starts
  // from a clean state without copy/paste in each it().
  const setters = [
    'setState', 'setSessionId', 'setError', 'setErrorCode', 'setErrorStage',
    'setSteps', 'setFinishSummary', 'setCompletionSummary',
    'setPhases', 'setDeclaredPipeline', 'setAgentStatus', 'setResolvingInfo', 'setResolvingProgress',
    'setPreviewUrl', 'setPreviewTaskId', 'setPreviewLoading', 'setPreviewStatusMsg',
    'setPreviewStage', 'setPreviewStartedAt', 'setPreviewEverReady', 'setPreviewFileMap',
    'setPreviewError', 'setDeployUrl', 'setQualityReport', 'setWrittenFiles',
    'setFileContents', 'setFileMetrics', 'setPlanAwaiting', 'setCurrentPlanData',
    'setChatMessages', 'setFiles',
    'setAwaitingResponse',
  ].reduce((acc, name) => { acc[name] = vi.fn(); return acc; }, {});
  const helpers = {
    pushChat: vi.fn(),
    pushLog: vi.fn(),
    pushAgentActivity: vi.fn(),
    flushPhasesToChat: vi.fn(),
    getFreshToken: vi.fn().mockResolvedValue('fresh-token'),
    uid: () => 'evt_test_id',
    _previewStorageKey: 'ws_preview_proj-1',
  };
  return { ...refs, ...setters, ...helpers, ...overrides };
}

function dispatch(msg, deps) {
  const d = deps ?? makeDeps();
  const fn = createMessageDispatcher(() => d);
  fn(msg);
  return d;
}

// ─── Stop suppression ──────────────────────────────────────

describe('dispatcher — stop suppression', () => {
  it('drops in-flight events when stopRequestedRef is true', () => {
    const deps = makeDeps({
      stopRequestedRef: { current: true },
    });
    dispatch({ type: 'file_write_event', filename: 'a.tsx' }, deps);
    dispatch({ type: 'agent_event', content: 'x', event: 'task_start' }, deps);
    dispatch({ type: 'chat_message', role: 'agent', content: 'hi' }, deps);
    expect(deps.setWrittenFiles).not.toHaveBeenCalled();
    expect(deps.setState).not.toHaveBeenCalled();
    expect(deps.setChatMessages).not.toHaveBeenCalled();
    expect(deps.pushChat).not.toHaveBeenCalled();
  });

  it('still routes connection / status / stopped / error events through', () => {
    const deps = makeDeps({
      stopRequestedRef: { current: true },
    });
    dispatch({ type: 'workspace_state', state: 'ready' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('ready');

    dispatch({ type: 'stopped', message: 'done' }, deps);
    expect(deps.pushChat).toHaveBeenCalled();
  });
});

// ─── Agent activity pill auto-route (Phase 2 Step 5) ──────

describe('dispatcher — activity pill auto-route', () => {
  it('pushes a pill update for typed sub-step events', () => {
    const deps = makeDeps();
    dispatch({ type: 'image_binder.summary', bound: 11, requested: 12 }, deps);
    expect(deps.pushAgentActivity).toHaveBeenCalled();
    const arg = deps.pushAgentActivity.mock.calls[0][0];
    expect(arg.kind).toBe('image_binder.summary');
    expect(arg.icon).toBeTruthy();
  });

  it('does NOT push a pill for task_phase (Step 5 contract — chart owns it)', () => {
    const deps = makeDeps();
    dispatch({ type: 'task_phase', phase: 1, status: 'active' }, deps);
    expect(deps.pushAgentActivity).not.toHaveBeenCalled();
  });
});

describe('dispatcher — canonical agent status', () => {
  it('stores the backend status verbatim and marks active work running', () => {
    const deps = makeDeps();
    dispatch({
      type: 'agent.status',
      key: 'research',
      label: 'Researching education competitors...',
      description: 'Gemini is comparing reference sites',
      state: 'active',
      source: 'gemini_flash',
      workflow_id: 'landing_generation',
    }, deps);
    expect(deps.setAgentStatus).toHaveBeenCalledWith({
      key: 'research',
      label: 'Researching education competitors...',
      description: 'Gemini is comparing reference sites',
      state: 'active',
      source: 'gemini_flash',
      workflowId: 'landing_generation',
    });
    expect(deps.setState).toHaveBeenCalledWith('running');
  });
});

// ─── workspace_state (canonical transitions) ──────────────

describe('dispatcher — workspace_state', () => {
  it('maps backend states to FE states via stateMap', () => {
    const deps = makeDeps();
    dispatch({ type: 'workspace_state', state: 'cloning' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('cloning');

    dispatch({ type: 'workspace_state', state: 'updating' }, deps);
    expect(deps.setState).toHaveBeenLastCalledWith('running');
  });

  it('clears stale phases + flush guard on a fresh updating transition', () => {
    const deps = makeDeps();
    deps.flushedRef.current = true;  // simulate previous run flushed
    dispatch({ type: 'workspace_state', state: 'updating' }, deps);
    expect(deps.flushedRef.current).toBe(false);
    expect(deps.setCompletionSummary).toHaveBeenCalledWith('');
    expect(deps.setPhases).toHaveBeenCalledWith([]);
    expect(deps.setWrittenFiles).toHaveBeenCalledWith([]);
  });

  it('resets reconnectCount + clears stop flag on ready', () => {
    const deps = makeDeps({
      reconnectCount: { current: 5 },
      stopRequestedRef: { current: true },
    });
    dispatch({ type: 'workspace_state', state: 'ready' }, deps);
    expect(deps.reconnectCount.current).toBe(0);
    expect(deps.stopRequestedRef.current).toBe(false);
  });

  it('surfaces error_stage when state=error', () => {
    const deps = makeDeps();
    dispatch({ type: 'workspace_state', state: 'error', message: 'boom', error_stage: 'clone' }, deps);
    expect(deps.setError).toHaveBeenCalledWith('boom');
    expect(deps.setErrorStage).toHaveBeenCalledWith('clone');
  });

  it('clears errorStage on any non-error transition (workspace recovered)', () => {
    const deps = makeDeps();
    dispatch({ type: 'workspace_state', state: 'ready' }, deps);
    // First call is null when there was no error_stage in payload — still
    // important: an in-flight clone error shouldn't visually persist after
    // the workspace flips back to ready.
    expect(deps.setErrorStage).toHaveBeenCalledWith(null);
  });
});

// ─── status (legacy state path) ───────────────────────────

describe('dispatcher — status', () => {
  it('initializing → preparing (not cloning)', () => {
    const deps = makeDeps();
    dispatch({ type: 'status', status: 'initializing' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('preparing');
  });

  it('ready clears the stop flag and resets reconnectCount', () => {
    const deps = makeDeps({
      stopRequestedRef: { current: true },
      reconnectCount: { current: 3 },
    });
    dispatch({ type: 'status', status: 'ready' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('ready');
    expect(deps.stopRequestedRef.current).toBe(false);
    expect(deps.reconnectCount.current).toBe(0);
  });

  it('persists session id to sessionStorage on ready', () => {
    const deps = makeDeps();
    dispatch({ type: 'status', status: 'ready', sessionId: 'sess-xyz' }, deps);
    expect(deps.setSessionId).toHaveBeenCalledWith('sess-xyz');
    expect(sessionStorage.getItem('ws_session_proj-1')).toBe('sess-xyz');
  });
});

// ─── agent_event task_start (resets per-task state) ──────

describe('dispatcher — agent_event', () => {
  it('task_start clears steps / phases / written files / flush guard', () => {
    const deps = makeDeps();
    deps.flushedRef.current = true;
    dispatch({
      type: 'agent_event', event: 'task_start', content: 'Starting…',
    }, deps);
    expect(deps.setState).toHaveBeenCalledWith('running');
    expect(deps.setSteps).toHaveBeenCalledWith([]);
    expect(deps.setPhases).toHaveBeenCalledWith([]);
    expect(deps.setWrittenFiles).toHaveBeenCalledWith([]);
    expect(deps.flushedRef.current).toBe(false);
    expect(deps.setCompletionSummary).toHaveBeenCalledWith('');
    expect(deps.setFinishSummary).toHaveBeenCalledWith('');
  });

  it('logs thoughts as thinking-class entries', () => {
    const deps = makeDeps();
    dispatch({ type: 'agent_event', thought: 'planning the next step' }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith(
      expect.stringContaining('planning the next step'), 'thinking'
    );
  });

  it('routes terminal ActionEvent content to cmd_output channel', () => {
    const deps = makeDeps();
    dispatch({
      type: 'agent_event', eventType: 'ActionEvent', toolName: 'terminal', content: '$ ls',
    }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith('$ ls', 'cmd_output');
  });
});

// ─── file_write_event (metrics accumulator) ───────────────

describe('dispatcher — file_write_event', () => {
  it('appends filename to writtenFiles (idempotent setter)', () => {
    const deps = makeDeps();
    dispatch({ type: 'file_write_event', filename: 'src/app/page.tsx', action: 'write' }, deps);
    expect(deps.setWrittenFiles).toHaveBeenCalled();
    // The setter is an updater — invoke it with [] and check the result
    const updater = deps.setWrittenFiles.mock.calls[0][0];
    expect(updater([])).toEqual(['src/app/page.tsx']);
    // Idempotent: should not append a second time
    expect(updater(['src/app/page.tsx'])).toEqual(['src/app/page.tsx']);
  });

  it('seeds file metrics on first write and increments on subsequent writes', () => {
    const deps = makeDeps();
    dispatch({
      type: 'file_write_event', filename: 'a.tsx', action: 'write',
      content: 'hello', size: 100, phase: 'gen',
    }, deps);
    const updater = deps.setFileMetrics.mock.calls[0][0];
    const seeded = updater({});
    expect(seeded['a.tsx'].writes).toBe(1);
    expect(seeded['a.tsx'].lastAction).toBe('write');
    expect(seeded['a.tsx'].lastPhase).toBe('gen');
    expect(seeded['a.tsx'].firstWriteTs).toBeTruthy();

    const next = updater(seeded);
    expect(next['a.tsx'].writes).toBe(2);
    // firstWriteTs is preserved across writes — used for elapsed time.
    expect(next['a.tsx'].firstWriteTs).toBe(seeded['a.tsx'].firstWriteTs);
  });

  it('snapshots content (current + previous) for DiffViewer', () => {
    const deps = makeDeps();
    dispatch({ type: 'file_write_event', filename: 'a.tsx', content: 'v2' }, deps);
    const updater = deps.setFileContents.mock.calls[0][0];
    const seeded = updater({ 'a.tsx': { current: 'v1', previous: '' } });
    expect(seeded['a.tsx'].current).toBe('v2');
    expect(seeded['a.tsx'].previous).toBe('v1');
  });
});

// ─── task_phase (TaskProgress chart driver) ────────────────

describe('dispatcher — task_phase', () => {
  it('phase=1 active resets state to running and seeds phases with this entry', () => {
    const deps = makeDeps();
    deps.flushedRef.current = true;
    dispatch({ type: 'task_phase', phase: 1, status: 'active', label: 'Boot' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('running');
    expect(deps.flushedRef.current).toBe(false);
    expect(deps.setCompletionSummary).toHaveBeenCalledWith('');
    expect(deps.setPhases).toHaveBeenCalled();
  });

  it('updates existing phase entries via setPhases updater', () => {
    const deps = makeDeps();
    dispatch({ type: 'task_phase', phase: 2, status: 'done', label: 'Plan' }, deps);
    const updater = deps.setPhases.mock.calls[0][0];
    const result = updater([
      { phase: 1, status: 'done' },
      { phase: 2, status: 'active' },
    ]);
    expect(result[1].status).toBe('done');
  });
});

// ─── pipeline_failure ─────────────────────────────────────

describe('dispatcher — pipeline_failure', () => {
  it('non-retriable failure pushes state back to ready (user is unblocked)', () => {
    const deps = makeDeps();
    dispatch({
      type: 'pipeline_failure', message: 'Blocked', code: 'content_blocked', retriable: false,
    }, deps);
    expect(deps.setState).toHaveBeenCalledWith('ready');
    expect(deps.pushChat).toHaveBeenCalledWith('system', expect.stringContaining('Blocked'));
  });

  it('retriable failure does NOT push state (user can retry from running)', () => {
    const deps = makeDeps();
    dispatch({
      type: 'pipeline_failure', message: 'Timeout', code: 'timeout', retriable: true,
    }, deps);
    expect(deps.setState).not.toHaveBeenCalled();
  });
});

// ─── error envelope (Phase 2 Step 1 routing) ──────────────

describe('dispatcher — error envelope', () => {
  it('fatal severity flips to error state with code + message', () => {
    const deps = makeDeps();
    dispatch({
      type: 'error', severity: 'fatal', code: 'AUTH_EXPIRED',
      message: 'session expired', phase: 'auth',
    }, deps);
    expect(deps.setState).toHaveBeenCalledWith('error');
    expect(deps.setError).toHaveBeenCalledWith('session expired');
    expect(deps.setErrorCode).toHaveBeenCalledWith('AUTH_EXPIRED');
  });

  it('warn severity makes no state changes (just chat + log)', () => {
    const deps = makeDeps();
    dispatch({
      type: 'error', severity: 'warn', message: 'soft', phase: 'research',
    }, deps);
    expect(deps.setState).not.toHaveBeenCalled();
    expect(deps.setError).not.toHaveBeenCalled();
    expect(deps.pushLog).toHaveBeenCalled();
    expect(deps.pushChat).toHaveBeenCalled();
  });

  it('recoverable retriable=false flips to error state', () => {
    const deps = makeDeps();
    dispatch({
      type: 'error', severity: 'recoverable', message: 'rate-limited',
      code: 'rate_limit', retriable: false,
    }, deps);
    expect(deps.setState).toHaveBeenCalledWith('error');
  });
});

// ─── legacy error path (pre-envelope) ─────────────────────

describe('dispatcher — legacy error', () => {
  it('fatal code (AUTH_EXPIRED) escalates to error state', () => {
    const deps = makeDeps();
    dispatch({ type: 'error', message: 'Auth expired', code: 'AUTH_EXPIRED' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('error');
    expect(deps.setError).toHaveBeenCalledWith('Auth expired');
    expect(deps.setErrorCode).toHaveBeenCalledWith('AUTH_EXPIRED');
  });

  it('non-fatal error keeps state=ready (user can try again)', () => {
    const deps = makeDeps();
    dispatch({ type: 'error', message: 'Soft failure' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('ready');
  });

  it('gibberish-detection rescue renders as assistant message, not red banner', () => {
    const deps = makeDeps();
    dispatch({ type: 'error', message: 'Please describe your project in a few words' }, deps);
    // The chat path is the warm assistant copy, NOT the ⚠️ system error
    expect(deps.pushChat).toHaveBeenCalledWith('assistant', expect.stringContaining("describe what"));
    expect(deps.setState).toHaveBeenCalledWith('ready');
    expect(deps.setPhases).toHaveBeenCalledWith([]);
  });
});

// ─── chat_history (dedup logic) ───────────────────────────

describe('dispatcher — chat_history', () => {
  it('hydrates plain text messages when not already loaded', () => {
    const deps = makeDeps();
    dispatch({
      type: 'chat_history',
      messages: [
        { id: 'h1', role: 'user', content: 'Hello', created_at: '2026-06-06T00:00:00Z' },
        { id: 'h2', role: 'assistant', content: 'Hi there', created_at: '2026-06-06T00:00:01Z' },
      ],
    }, deps);
    expect(deps.historyLoadedRef.current).toBe(true);
    expect(deps.setChatMessages).toHaveBeenCalled();
  });

  it('skips hydration on the second invocation (historyLoadedRef is sticky)', () => {
    const deps = makeDeps({ historyLoadedRef: { current: true } });
    dispatch({
      type: 'chat_history',
      messages: [{ id: 'h1', role: 'user', content: 'Hello' }],
    }, deps);
    expect(deps.setChatMessages).not.toHaveBeenCalled();
  });

  it('detects persisted plan JSON and rebuilds it as a plan card', () => {
    const deps = makeDeps();
    const planJson = JSON.stringify({
      messageType: 'plan', planData: { brandName: 'Acme', sections: [{ id: 'hero' }] },
    });
    dispatch({
      type: 'chat_history',
      messages: [{ id: 'p1', role: 'agent', content: planJson }],
    }, deps);
    const updater = deps.setChatMessages.mock.calls[0][0];
    const result = updater([]);
    expect(result[0].messageType).toBe('plan');
    expect(result[0].planData.brandName).toBe('Acme');
  });

  it('strips the [LUCID_PROJECT] wizard header from user messages', () => {
    const deps = makeDeps();
    dispatch({
      type: 'chat_history',
      messages: [{
        id: 'u1', role: 'user',
        content: '[LUCID_PROJECT] meta\n\nReal user prompt',
      }],
    }, deps);
    const updater = deps.setChatMessages.mock.calls[0][0];
    const result = updater([]);
    expect(result[0].content).toBe('Real user prompt');
  });
});

// ─── chat_message dedup ────────────────────────────────────

describe('dispatcher — chat_message', () => {
  it('drops an immediate echo of the same role+content (handshake bind_chat case)', () => {
    const deps = makeDeps({
      chatMessagesRef: {
        current: [
          { id: 'a', role: 'user', content: 'Build me a thing' },
        ],
      },
    });
    dispatch({
      type: 'chat_message', role: 'user', content: 'Build me a thing',
    }, deps);
    expect(deps.pushChat).not.toHaveBeenCalled();
  });

  it('keeps a fresh agent message even when user prompt is in tail', () => {
    const deps = makeDeps({
      chatMessagesRef: {
        current: [{ id: 'a', role: 'user', content: 'Build me a thing' }],
      },
    });
    dispatch({
      type: 'chat_message', role: 'agent', content: 'Sure, here\'s the plan',
    }, deps);
    expect(deps.pushChat).toHaveBeenCalledWith('agent', "Sure, here's the plan");
  });

  it('keeps a repeated agent message when the user typed something in between', () => {
    // Regression: previously the dedup checked the last 3 entries, so the
    // follow-up guard's identical "I couldn't make out…" response was
    // dropped on every retry after the first. The user typed, no reply
    // came back, typed again, no reply, etc. — chat went silent.
    const deps = makeDeps({
      chatMessagesRef: {
        current: [
          { id: 'u1', role: 'user', content: 'tree apple' },
          { id: 'a1', role: 'agent', content: 'I couldn\'t make out what you\'d like me to change.' },
          { id: 'u2', role: 'user', content: 'banana keyboard' },
          // ← last entry is the user's new attempt
        ],
      },
    });
    dispatch({
      type: 'chat_message', role: 'agent',
      content: 'I couldn\'t make out what you\'d like me to change.',
    }, deps);
    expect(deps.pushChat).toHaveBeenCalledWith(
      'agent',
      'I couldn\'t make out what you\'d like me to change.',
    );
  });

  it('dedups plan messages by stable signature', () => {
    const planData = { brandName: 'Acme', tagline: 't', sections: [{ id: 'hero' }] };
    const sig = `Acme::t::hero`;
    const deps = makeDeps({
      chatMessagesRef: {
        current: [{ id: 'p1', messageType: 'plan', _planSig: sig }],
      },
    });
    dispatch({
      type: 'chat_message', messageType: 'plan', planData,
    }, deps);
    // currentPlanData still updates (so confirm/reject affordances stay
    // wired to the latest plan), but setChatMessages does NOT add a card.
    expect(deps.setCurrentPlanData).toHaveBeenCalledWith(planData);
    expect(deps.setChatMessages).not.toHaveBeenCalled();
  });
});

// ─── preview lifecycle ────────────────────────────────────

describe('dispatcher — preview_ready', () => {
  it('persists URL to sessionStorage and clears loading state', () => {
    const deps = makeDeps();
    dispatch({
      type: 'preview_ready', preview_url: 'https://abc.local',
      task_id: 'task-1', message: 'live',
    }, deps);
    expect(deps.setPreviewUrl).toHaveBeenCalledWith('https://abc.local');
    expect(deps.setPreviewLoading).toHaveBeenCalledWith(false);
    expect(deps.setPreviewError).toHaveBeenCalledWith(null);
    expect(deps.setPreviewEverReady).toHaveBeenCalledWith(true);
    expect(sessionStorage.getItem('ws_preview_proj-1')).toBe('https://abc.local');
  });
});

describe('dispatcher — preview_error', () => {
  it('sets error + clears URL and storage so the iframe never points at a dead port', () => {
    const deps = makeDeps();
    sessionStorage.setItem('ws_preview_proj-1', 'https://stale');
    dispatch({
      type: 'preview_error', message: 'dev server crashed', error_stage: 'start',
    }, deps);
    expect(deps.setPreviewError).toHaveBeenCalledWith({
      stage: 'start',
      message: 'dev server crashed',
    });
    expect(deps.setPreviewUrl).toHaveBeenCalledWith(null);
    expect(sessionStorage.getItem('ws_preview_proj-1')).toBeNull();
  });
});

// ─── unhandled-type audit: 5 backend emits added in Phase 2 cleanup ──

describe('dispatcher — terminal_log (Bash stream from claude_cli)', () => {
  it('routes stdin lines to the cmd_input channel', () => {
    const deps = makeDeps();
    dispatch({ type: 'terminal_log', stream: 'stdin', data: '$ ls -la' }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith('$ ls -la', 'cmd_input');
  });

  it('routes stdout (default stream) to the cmd_output channel', () => {
    const deps = makeDeps();
    dispatch({ type: 'terminal_log', stream: 'stdout', data: 'total 4' }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith('total 4', 'cmd_output');
  });

  it('drops empty payloads silently', () => {
    const deps = makeDeps();
    dispatch({ type: 'terminal_log', stream: 'stdout' }, deps);
    expect(deps.pushLog).not.toHaveBeenCalled();
  });
});

describe('dispatcher — codex_message (parallel to claude_message)', () => {
  it('logs trimmed content as agent_message', () => {
    const deps = makeDeps();
    dispatch({ type: 'codex_message', content: 'Fixed missing import' }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith(
      expect.stringContaining('Codex'), 'agent_message',
    );
  });

  it('drops empty content silently', () => {
    const deps = makeDeps();
    dispatch({ type: 'codex_message', content: '' }, deps);
    expect(deps.pushLog).not.toHaveBeenCalled();
  });
});

describe('dispatcher — classification (auto-selected task params)', () => {
  it('summarises the chosen knobs on one line', () => {
    const deps = makeDeps();
    dispatch({
      type: 'classification',
      developer: 'senior', model: 'opus', task_type: 'feature_complex',
      intent: 'add a stripe checkout flow with webhooks',
    }, deps);
    const call = deps.pushLog.mock.calls[0];
    expect(call[0]).toContain('senior');
    expect(call[0]).toContain('opus');
    expect(call[0]).toContain('feature_complex');
    expect(call[0]).toContain('stripe');
    expect(call[1]).toBe('system');
  });

  it('falls back to "auto" / "feature_simple" defaults when fields missing', () => {
    const deps = makeDeps();
    dispatch({ type: 'classification' }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith(
      expect.stringContaining('auto · auto · feature_simple'), 'system',
    );
  });
});

describe('dispatcher — project_indexed (connected-repo metadata)', () => {
  it('renders the friendly message as a system chat note', () => {
    const deps = makeDeps();
    dispatch({
      type: 'project_indexed', framework: 'nextjs', language: 'typescript',
      packageManager: 'pnpm', routeCount: 12,
      message: 'Connected project indexed: nextjs (12 routes)',
    }, deps);
    expect(deps.pushChat).toHaveBeenCalledWith(
      'system', 'Connected project indexed: nextjs (12 routes)',
    );
    expect(deps.pushLog).toHaveBeenCalledWith(
      expect.stringContaining('nextjs'), 'system',
    );
  });

  it('skips the chat note when no message is provided (log still fires)', () => {
    const deps = makeDeps();
    dispatch({ type: 'project_indexed', framework: 'vite' }, deps);
    expect(deps.pushChat).not.toHaveBeenCalled();
    expect(deps.pushLog).toHaveBeenCalled();
  });
});

describe('dispatcher — preview_stopped (backend confirms shutdown)', () => {
  it('clears every piece of preview state and the sessionStorage URL', () => {
    const deps = makeDeps();
    sessionStorage.setItem('ws_preview_proj-1', 'https://stale.local');
    dispatch({ type: 'preview_stopped' }, deps);
    expect(deps.setPreviewUrl).toHaveBeenCalledWith(null);
    expect(deps.setPreviewLoading).toHaveBeenCalledWith(false);
    expect(deps.setPreviewStatusMsg).toHaveBeenCalledWith('');
    expect(deps.setPreviewError).toHaveBeenCalledWith(null);
    expect(deps.setPreviewEverReady).toHaveBeenCalledWith(false);
    expect(sessionStorage.getItem('ws_preview_proj-1')).toBeNull();
  });
});

// ─── typed event log lines (Phase 2 Step 4) ───────────────

describe('dispatcher — typed event log lines', () => {
  beforeEach(() => { sessionStorage.clear(); });

  it('image_binder.summary writes a system log entry', () => {
    const deps = makeDeps();
    dispatch({ type: 'image_binder.summary', bound: 10, requested: 12 }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith(expect.stringContaining('image_binder'), 'system');
  });

  it('build.result with pass goes to system log', () => {
    const deps = makeDeps();
    dispatch({ type: 'build.result', success: true, attempts: 2 }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith(expect.any(String), 'system');
  });

  it('build.result with fail goes to warning log', () => {
    const deps = makeDeps();
    dispatch({ type: 'build.result', success: false, error_count: 3, attempts: 2 }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith(expect.any(String), 'warning');
  });

  it('repo.create_done logs at system on success, warning on failure', () => {
    const ok = makeDeps();
    dispatch({ type: 'repo.create_done', success: true, repo_url: 'https://x' }, ok);
    expect(ok.pushLog).toHaveBeenCalledWith(expect.any(String), 'system');

    const fail = makeDeps();
    dispatch({ type: 'repo.create_done', success: false, error: 'forbidden' }, fail);
    expect(fail.pushLog).toHaveBeenCalledWith(expect.any(String), 'warning');
  });
});

// ─── clarification flow ───────────────────────────────────

describe('dispatcher — clarification', () => {
  it('clarify renders as a warm assistant message and resets state', () => {
    const deps = makeDeps();
    dispatch({ type: 'clarify', message: 'Tell me more' }, deps);
    expect(deps.setPhases).toHaveBeenCalledWith([]);
    expect(deps.setState).toHaveBeenCalledWith('ready');
    expect(deps.pushChat).toHaveBeenCalledWith('assistant', 'Tell me more');
  });

  it('gibberish_detected is handled identically to clarify (state must unstick)', () => {
    // Bug observed: landing_pipeline.py emits {type: "gibberish_detected"}
    // instead of the canonical {type: "clarify"} when the prompt is keyboard
    // mash. Without a handler the state stayed 'running' forever and the
    // UI showed "Analyzing your request…" until the user reloaded.
    const deps = makeDeps();
    dispatch({
      type: 'gibberish_detected', kind: 'gibberish',
      message: 'Looks like random characters — what would you like to build?',
    }, deps);
    expect(deps.setPhases).toHaveBeenCalledWith([]);
    expect(deps.setState).toHaveBeenCalledWith('ready');
    expect(deps.pushChat).toHaveBeenCalledWith(
      'assistant',
      expect.stringContaining('random characters'),
    );
  });

  it('clarification_needed appends a structured question card', () => {
    const deps = makeDeps();
    dispatch({
      type: 'clarification_needed', kind: 'archetype',
      question: 'Landing or full app?', options: ['Landing', 'App'],
      original_task: 'do something',
    }, deps);
    const updater = deps.setChatMessages.mock.calls[0][0];
    const result = updater([]);
    expect(result[0].messageType).toBe('clarification');
    expect(result[0].clarification.question).toBe('Landing or full app?');
    expect(result[0].clarification.options).toEqual(['Landing', 'App']);
  });
});

// ─── stop / pong / unknown fallback ───────────────────────

describe('dispatcher — terminal events', () => {
  it('stopped pushes chat + sets state ready', () => {
    const deps = makeDeps();
    dispatch({ type: 'stopped', message: 'user clicked stop' }, deps);
    expect(deps.setState).toHaveBeenCalledWith('ready');
    expect(deps.pushChat).toHaveBeenCalledWith('system', expect.stringContaining('user clicked stop'));
    expect(deps.setSteps).toHaveBeenCalledWith([]);
  });

  it('pong / ack early-return without any setter activity', () => {
    const deps = makeDeps();
    dispatch({ type: 'pong' }, deps);
    dispatch({ type: 'ack' }, deps);
    expect(deps.setState).not.toHaveBeenCalled();
    expect(deps.pushLog).not.toHaveBeenCalled();
  });

  it('unknown types fall through to the JSON-dump log line', () => {
    const deps = makeDeps();
    dispatch({ type: 'this_type_does_not_exist', foo: 'bar' }, deps);
    expect(deps.pushLog).toHaveBeenCalledWith(
      expect.stringContaining('this_type_does_not_exist'), 'system'
    );
  });
});

// ─── Architectural invariant (Phase 2 honest-status refactor) ─────────
//
// The dispatcher's job is event → action. Every state mutation must be
// triggered by a SPECIFIC message type. If a state setter ever fires
// from a fall-through path, the UI is reacting to nothing, which is
// exactly the bug class that produced "Analyzing your request…" stuck
// status this session.
//
// These tests guard the invariant by sending plausibly-shaped but
// unrouted messages and asserting no setter is called.

describe('dispatcher — event-driven invariant', () => {
  // Every setter EXCEPT setAwaitingResponse must be driven by a specific
  // msg.type branch. setAwaitingResponse is intentionally released on
  // every backend response (any type that isn't pong/ack/_internal),
  // so it's tested separately in the awaitingResponse-release block.
  const ALL_SETTERS = [
    'setState', 'setSessionId', 'setError', 'setErrorCode', 'setErrorStage',
    'setSteps', 'setFinishSummary', 'setCompletionSummary',
    'setPhases', 'setDeclaredPipeline', 'setResolvingInfo', 'setResolvingProgress',
    'setPreviewUrl', 'setPreviewTaskId', 'setPreviewLoading', 'setPreviewStatusMsg',
    'setPreviewStage', 'setPreviewStartedAt', 'setPreviewEverReady', 'setPreviewFileMap',
    'setPreviewError', 'setDeployUrl', 'setQualityReport', 'setWrittenFiles',
    'setFileContents', 'setFileMetrics', 'setPlanAwaiting', 'setCurrentPlanData',
    'setChatMessages', 'setFiles',
  ];
  const assertNoSetters = (deps) => {
    for (const name of ALL_SETTERS) {
      expect(deps[name], `${name} was unexpectedly invoked`).not.toHaveBeenCalled();
    }
  };

  it('unknown type triggers no setters (fall-through goes to log only)', () => {
    const deps = makeDeps();
    dispatch({ type: 'this_type_will_never_exist_at_runtime' }, deps);
    assertNoSetters(deps);
    expect(deps.pushLog).toHaveBeenCalled();
  });

  it('pong / ack early-return triggers no setters', () => {
    const deps = makeDeps();
    dispatch({ type: 'pong' }, deps);
    dispatch({ type: 'ack' }, deps);
    assertNoSetters(deps);
  });

  it('message without content triggers no setters (informational log only)', () => {
    const deps = makeDeps();
    dispatch({ type: 'message', content: '' }, deps);
    assertNoSetters(deps);
  });

  it('warning without code triggers chat + log but no state mutation', () => {
    const deps = makeDeps();
    dispatch({ type: 'warning', message: 'soft heads-up' }, deps);
    expect(deps.setState).not.toHaveBeenCalled();
    expect(deps.setError).not.toHaveBeenCalled();
    // Warnings DO call pushChat + pushLog by design — assert those, not setters.
    expect(deps.pushChat).toHaveBeenCalled();
    expect(deps.pushLog).toHaveBeenCalled();
  });
});

describe('dispatcher — awaitingResponse release (Phase 2 honest-status)', () => {
  // sendMessageInternal sets awaitingResponse=true so the chat status
  // pill can say "Sending…". The dispatcher must release it on the next
  // concrete backend response — chat, state change, error, plan, clarify
  // — but NOT on connection-level noise (pong/ack/_internal).

  it('chat_message clears the flag', () => {
    const deps = makeDeps({ chatMessagesRef: { current: [{ role: 'user', content: 'hi' }] } });
    dispatch({ type: 'chat_message', role: 'agent', content: 'reply' }, deps);
    expect(deps.setAwaitingResponse).toHaveBeenCalledWith(false);
  });

  it('clarification_needed clears the flag', () => {
    const deps = makeDeps();
    dispatch({
      type: 'clarification_needed', kind: 'archetype',
      question: 'Q?', options: ['A', 'B'], original_task: 'x',
    }, deps);
    expect(deps.setAwaitingResponse).toHaveBeenCalledWith(false);
  });

  it('clarify (canonical) clears the flag', () => {
    const deps = makeDeps();
    dispatch({ type: 'clarify', message: 'Tell me more' }, deps);
    expect(deps.setAwaitingResponse).toHaveBeenCalledWith(false);
  });

  it('workspace_state clears the flag', () => {
    const deps = makeDeps();
    dispatch({ type: 'workspace_state', state: 'updating' }, deps);
    expect(deps.setAwaitingResponse).toHaveBeenCalledWith(false);
  });

  it('error envelope clears the flag', () => {
    const deps = makeDeps();
    dispatch({
      type: 'error', severity: 'fatal', code: 'AUTH_EXPIRED',
      message: 'bye', phase: 'auth',
    }, deps);
    expect(deps.setAwaitingResponse).toHaveBeenCalledWith(false);
  });

  it('pong does NOT clear the flag (connection noise, not a response)', () => {
    const deps = makeDeps();
    dispatch({ type: 'pong' }, deps);
    expect(deps.setAwaitingResponse).not.toHaveBeenCalled();
  });

  it('ack does NOT clear the flag', () => {
    const deps = makeDeps();
    dispatch({ type: 'ack' }, deps);
    expect(deps.setAwaitingResponse).not.toHaveBeenCalled();
  });

  it('_internal connection events do NOT clear the flag', () => {
    const deps = makeDeps();
    dispatch({ type: '_internal', event: 'connected' }, deps);
    expect(deps.setAwaitingResponse).not.toHaveBeenCalled();
  });
});
