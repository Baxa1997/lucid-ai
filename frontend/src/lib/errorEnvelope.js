// errorEnvelope.js — pure routing for the unified error envelope.
//
// Phase 2 Step 1 of the status/error refactor introduces a single
// `type: "error"` shape carrying `severity`. This module is the pure
// translation layer: given an incoming WS message, decide what side
// effects the session hook should apply.
//
// Why pure: tests can pin down behavior without mocking `useAgentSession`
// or React's set-state plumbing. The hook calls `routeErrorEnvelope(msg)`
// and applies the returned actions.
//
// Severity contract:
//   • warn        — informational; no state change, soft chat note.
//   • recoverable — task aborted but retriable; show error banner,
//                   keep session ready (the activity pill clears
//                   automatically via the state-transition effect).
//   • fatal       — session unusable; push to error state, surface
//                   recovery_hint prominently.
//   • legacy      — message did NOT carry severity → caller must fall
//                   through to the pre-Phase-2 handler.

/**
 * Returns null when the message is NOT a unified error envelope (caller
 * should fall through). Returns a structured Action when it is.
 *
 * @param {object} msg — raw WS payload
 * @returns {object|null}
 */
export function routeErrorEnvelope(msg) {
  if (!msg || msg.type !== 'error' || typeof msg.severity !== 'string') {
    return null; // legacy path
  }

  const severity = msg.severity;
  const phase = msg.phase || 'unknown';
  const code = msg.code || null;
  const errMsg = msg.message || 'Something went wrong.';
  const hint = msg.recovery_hint || '';
  const retriable = msg.retriable !== undefined ? msg.retriable : null;

  const logLine = `[${severity}] phase=${phase} code=${code} retriable=${retriable} — ${errMsg}`;

  if (severity === 'warn') {
    return {
      kind: 'warn',
      chatRole: 'system',
      chatMessage: `⚠️ ${errMsg}${hint ? `\n${hint}` : ''}`,
      logMessage: logLine,
      logLevel: 'system',
      // No state changes for warnings — they're informational.
      setState: null,
      setError: null,
      setErrorCode: null,
    };
  }

  if (severity === 'fatal') {
    return {
      kind: 'fatal',
      chatRole: 'system',
      chatMessage: `❌ **${errMsg}**${hint ? `\n${hint}` : ''}`,
      logMessage: logLine,
      logLevel: 'error',
      setState: 'error',
      setError: errMsg,
      setErrorCode: code,
    };
  }

  // severity === 'recoverable' — anything else falls into this bucket
  // (we accept unknown severities as recoverable for forward-compat with
  // future severity values the backend may add).
  return {
    kind: 'recoverable',
    chatRole: 'system',
    chatMessage: `❌ **${errMsg}**${hint ? `\n${hint}` : ''}`,
    logMessage: logLine,
    logLevel: 'error',
    // retriable === false means "don't try this again" — push to error.
    setState: retriable === false ? 'error' : 'ready',
    setError: null,
    setErrorCode: code,
  };
}
