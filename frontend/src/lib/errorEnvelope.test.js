// Tests for the unified error envelope routing (Phase 2 Step 1).
//
// Pure-function tests — no React, no DOM. Cover:
//   • Detection: legacy events fall through (return null)
//   • Severity routing: warn / recoverable / fatal each produce the right Action
//   • Defaults: phase, code, message, hint, retriable all have sensible fallbacks
//   • State actions: setState / setError / setErrorCode
//   • Forward-compat: unknown severity treated as recoverable

import { describe, it, expect } from 'vitest';
import { routeErrorEnvelope } from './errorEnvelope';

describe('routeErrorEnvelope — discrimination', () => {
  it('returns null for non-error messages (caller falls through to other handlers)', () => {
    expect(routeErrorEnvelope({ type: 'progress' })).toBeNull();
    expect(routeErrorEnvelope({ type: 'task_phase', phase: 3 })).toBeNull();
  });

  it('returns null for legacy error events with no severity field', () => {
    // Pre-Phase-2 emissions: { type: 'error', message: '…', code: 'AUTH' }
    expect(routeErrorEnvelope({ type: 'error', message: 'Bad' })).toBeNull();
    expect(routeErrorEnvelope({ type: 'error', message: 'Bad', code: 'AUTH' })).toBeNull();
  });

  it('returns null when msg is null or undefined (defensive)', () => {
    expect(routeErrorEnvelope(null)).toBeNull();
    expect(routeErrorEnvelope(undefined)).toBeNull();
    expect(routeErrorEnvelope({})).toBeNull();
  });

  it('returns null when severity is not a string', () => {
    // Defensive — if backend ever sends severity as a number/object,
    // don't crash, just fall through.
    expect(routeErrorEnvelope({ type: 'error', severity: 1, message: 'x' })).toBeNull();
    expect(routeErrorEnvelope({ type: 'error', severity: null, message: 'x' })).toBeNull();
  });
});

describe('routeErrorEnvelope — severity: warn', () => {
  it('returns a warn Action with no state changes', () => {
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'warn',
      phase: 'research',
      code: 'partial_result',
      message: 'Continuing with reduced research scope.',
      recovery_hint: '',
    });
    expect(action).toMatchObject({
      kind: 'warn',
      chatRole: 'system',
      logLevel: 'system',
      setState: null,
      setError: null,
      setErrorCode: null,
    });
    expect(action.clearAgentStatus).toBeUndefined();
    expect(action.chatMessage).toContain('⚠️');
    expect(action.chatMessage).toContain('reduced research scope');
  });

  it('appends recovery_hint to chat when present', () => {
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'warn',
      message: 'Things took a bit longer than expected.',
      recovery_hint: 'No action needed.',
    });
    expect(action.chatMessage).toContain('Things took a bit longer');
    expect(action.chatMessage).toContain('No action needed.');
  });
});

describe('routeErrorEnvelope — severity: recoverable', () => {
  it('returns a recoverable Action that keeps state ready (default)', () => {
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'recoverable',
      phase: 'code',
      code: 'rate_limit',
      message: 'Try again in a minute.',
      retriable: true,
      recovery_hint: 'Wait a bit and retry.',
    });
    expect(action).toMatchObject({
      kind: 'recoverable',
      chatRole: 'system',
      logLevel: 'error',
      setState: 'ready',
      setErrorCode: 'rate_limit',
      setError: null,
    });
    expect(action.clearAgentStatus).toBeUndefined();
    expect(action.chatMessage).toContain('❌');
    expect(action.chatMessage).toContain('Try again in a minute');
  });

  it('pushes state=error when retriable is explicitly false', () => {
    // recoverable severity + retriable=false means "the task failed and you
    // shouldn't immediately retry" — the UI should reflect this in state.
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'recoverable',
      code: 'content_blocked',
      message: 'The provider blocked this request.',
      retriable: false,
    });
    expect(action.setState).toBe('error');
  });

  it('treats missing retriable as "retry ok" — defaults to state=ready', () => {
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'recoverable',
      message: 'x',
      // retriable omitted → null → treated as not-explicitly-false
    });
    expect(action.setState).toBe('ready');
  });
});

describe('routeErrorEnvelope — severity: fatal', () => {
  it('returns a fatal Action that pushes the session to error state', () => {
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'fatal',
      phase: 'auth',
      code: 'AUTH_EXPIRED',
      message: 'Session expired.',
      recovery_hint: 'Sign in again.',
    });
    expect(action).toMatchObject({
      kind: 'fatal',
      chatRole: 'system',
      logLevel: 'error',
      setState: 'error',
      setError: 'Session expired.',
      setErrorCode: 'AUTH_EXPIRED',
    });
    expect(action.clearAgentStatus).toBeUndefined();
    expect(action.chatMessage).toContain('❌');
    expect(action.chatMessage).toContain('Session expired');
    expect(action.chatMessage).toContain('Sign in again');
  });
});

describe('routeErrorEnvelope — defaults & forward-compat', () => {
  it('defaults message to "Something went wrong." when missing', () => {
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'recoverable',
    });
    expect(action.chatMessage).toContain('Something went wrong');
  });

  it('treats an unknown severity as recoverable (forward-compat)', () => {
    // If the backend ships a new severity value before the frontend knows
    // about it, we should still render something useful instead of dropping.
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'mild',  // not warn/recoverable/fatal
      message: 'x',
    });
    expect(action.kind).toBe('recoverable');
  });

  it('includes phase + code in the log line for telemetry searchability', () => {
    const action = routeErrorEnvelope({
      type: 'error',
      severity: 'recoverable',
      phase: 'preview',
      code: 'network',
      message: 'x',
    });
    expect(action.logMessage).toContain('phase=preview');
    expect(action.logMessage).toContain('code=network');
    expect(action.logMessage).toContain('[recoverable]');
  });
});
