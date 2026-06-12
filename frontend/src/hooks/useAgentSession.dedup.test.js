// Regression tests for the pushChat user-bubble dedup.
//
// Bug: one typed prompt rendered as two identical user bubbles a few
// seconds apart (wizard echo + a second append during the error/reconnect
// window). All local user-bubble paths funnel through pushChat, which now
// drops an append whose normalized content matches the nearest previous
// user row — looking back across system rows but stopping at agent rows.
import { describe, it, expect } from 'vitest';
import { isDuplicateUserEcho, normUserContent } from './useAgentSession';

const NOW = 1_000_000_000;
const u = (content, tsOffset = -2000, role = 'user') => ({
  role, content, ts: NOW + tsOffset,
});

describe('normUserContent', () => {
  it('strips the [LUCID_PROJECT] header so echo and raw prompt compare equal', () => {
    const raw = 'Build landing page for hotel reservation';
    const headered =
      '[LUCID_PROJECT] description=Build landing page | stack=nextjs | backend=none\n\n' + raw;
    expect(normUserContent(headered)).toBe(normUserContent(raw));
  });

  it('does not strip leading blocks from ordinary multi-paragraph prompts', () => {
    const msg = 'first paragraph\n\nsecond paragraph';
    expect(normUserContent(msg)).toBe('first paragraph second paragraph');
  });
});

describe('isDuplicateUserEcho', () => {
  it('drops an identical user prompt re-appended seconds later', () => {
    const prev = [u('Build landing page for hotel reservation')];
    expect(
      isDuplicateUserEcho(prev, 'user', 'Build landing page for hotel reservation', NOW),
    ).toBe(true);
  });

  it('drops the headered backend echo of a raw local prompt', () => {
    const prev = [u('Build landing page for hotel reservation')];
    const echo =
      '[LUCID_PROJECT] description=x | stack=nextjs | backend=none\n\nBuild landing page for hotel reservation';
    expect(isDuplicateUserEcho(prev, 'user', echo, NOW)).toBe(true);
  });

  it('looks back across system rows (error banner between the two copies)', () => {
    const prev = [
      u('Build landing page for hotel reservation'),
      u('⚠️ An internal error occurred. Please try again.', -1000, 'system'),
    ];
    expect(
      isDuplicateUserEcho(prev, 'user', 'Build landing page for hotel reservation', NOW),
    ).toBe(true);
  });

  it('keeps a deliberate repeat after an agent reply', () => {
    const prev = [
      u('try again'),
      u('Done — anything else?', -1000, 'agent'),
    ];
    expect(isDuplicateUserEcho(prev, 'user', 'try again', NOW)).toBe(false);
  });

  it('keeps a repeat typed after the 30s window', () => {
    const prev = [u('Build landing page for hotel reservation', -60_000)];
    expect(
      isDuplicateUserEcho(prev, 'user', 'Build landing page for hotel reservation', NOW),
    ).toBe(false);
  });

  it('never drops agent or system rows', () => {
    const prev = [u('status', -100, 'system')];
    expect(isDuplicateUserEcho(prev, 'system', 'status', NOW)).toBe(false);
    expect(isDuplicateUserEcho(prev, 'agent', 'status', NOW)).toBe(false);
  });

  it('keeps a different prompt sent right after the first', () => {
    const prev = [u('Build landing page for hotel reservation')];
    expect(isDuplicateUserEcho(prev, 'user', 'Add a booking form', NOW)).toBe(false);
  });
});
