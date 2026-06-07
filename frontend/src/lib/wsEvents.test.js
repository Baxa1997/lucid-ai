// Tests for typed-event formatters (Phase 2 Step 4).

import { describe, it, expect } from 'vitest';
import {
  formatImageBinderSummary,
  formatQualitySummary,
  formatBuildStart,
  formatBuildResult,
  formatRepoCreateStarted,
  formatRepoCreateDone,
  formatResearchStarted,
  formatCodeWriteStarted,
  formatAgentActivity,
  isLegacyDuplicateOfTypedEvent,
} from './wsEvents';

describe('formatImageBinderSummary', () => {
  it('formats the happy-path payload with all counts', () => {
    const f = formatImageBinderSummary({
      requested: 12, bound: 11, unbound: 1,
      geo_rejected: 3, subject_rejected: 2, retry_used: 4,
    });
    expect(f.summary).toBe('11/12 images bound (92%)');
    expect(f.detail).toBe('geo-rejected=3 subject-rejected=2 retries=4');
    expect(f.log).toContain('[image_binder]');
    expect(f.log).toContain('11/12');
    expect(f.subtext).toContain('🖼️');
    expect(f.rate).toBe(92);
  });

  it('omits the detail segment when there are no rejections or retries', () => {
    const f = formatImageBinderSummary({ requested: 5, bound: 5 });
    expect(f.detail).toBe('');
    expect(f.log).toBe('[image_binder] 5/5 images bound (100%)');
  });

  it('treats 0 requested as 100% (nothing to bind, nothing failed)', () => {
    const f = formatImageBinderSummary({ requested: 0, bound: 0 });
    expect(f.rate).toBe(100);
    expect(f.summary).toBe('0/0 images bound (100%)');
  });

  it('rounds rate to the nearest whole percent', () => {
    // 7/9 = 77.77... → 78
    const f = formatImageBinderSummary({ requested: 9, bound: 7 });
    expect(f.rate).toBe(78);
    expect(f.summary).toBe('7/9 images bound (78%)');
  });

  it('coerces non-numeric inputs to 0 (defensive)', () => {
    // A future emitter could accidentally send strings or null; don't crash.
    const f = formatImageBinderSummary({
      requested: '10', bound: undefined, geo_rejected: null,
    });
    expect(f.summary).toBe('0/10 images bound (0%)');
  });

  it('returns a stable shape for any input (subtext + log are always strings)', () => {
    const f = formatImageBinderSummary({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.detail).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(typeof f.rate).toBe('number');
  });
});

describe('formatQualitySummary', () => {
  it('all-pass payload renders as ✅ chip with no tail', () => {
    const f = formatQualitySummary({ passed: 10, total: 10, blockers: 0, warnings: 0 });
    expect(f.summary).toBe('10/10 checks passed');
    expect(f.log).toBe('[quality] 10/10 checks passed');
    expect(f.subtext).toBe('✅ Quality: 10/10 checks passed');
    expect(f.status).toBe('pass');
  });

  it('warnings-only payload renders as ✓ with warn tail', () => {
    const f = formatQualitySummary({ passed: 9, total: 10, warnings: 1 });
    expect(f.summary).toBe('9/10 checks passed · 1 warning');
    expect(f.status).toBe('warn');
    expect(f.subtext).toContain('✓');
  });

  it('blockers take precedence over warnings (fail status)', () => {
    const f = formatQualitySummary({ passed: 7, total: 10, blockers: 2, warnings: 1 });
    expect(f.status).toBe('fail');
    expect(f.summary).toBe('7/10 checks passed · 2 blockers');
    expect(f.subtext).toContain('⚠️');
  });

  it('pluralizes blockers/warnings correctly', () => {
    expect(formatQualitySummary({ passed: 9, total: 10, blockers: 1 }).summary)
      .toBe('9/10 checks passed · 1 blocker');
    expect(formatQualitySummary({ passed: 8, total: 10, warnings: 2 }).summary)
      .toBe('8/10 checks passed · 2 warnings');
  });

  it('appends purpose tag in the log line when present', () => {
    const f = formatQualitySummary({ passed: 5, total: 5, purpose: 'apply-form' });
    expect(f.log).toBe('[quality] [apply-form] 5/5 checks passed');
  });

  it('coerces non-numeric inputs to 0 (defensive)', () => {
    const f = formatQualitySummary({ passed: undefined, total: '10', blockers: null });
    expect(f.summary).toBe('0/10 checks passed');
    expect(f.status).toBe('pass');
  });

  it('returns a stable shape for any input', () => {
    const f = formatQualitySummary({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(['pass', 'warn', 'fail']).toContain(f.status);
  });
});

describe('formatBuildStart', () => {
  it('install phase renders with 📦 icon and package manager', () => {
    const f = formatBuildStart({ phase: 'install', package_manager: 'pnpm', command: 'pnpm install' });
    expect(f.phase).toBe('install');
    expect(f.summary).toBe('Installing dependencies (pnpm)');
    expect(f.subtext).toBe('📦 Installing dependencies (pnpm)…');
    expect(f.log).toContain('[build] install:');
  });

  it('build phase renders with 🔍 icon and command tail', () => {
    const f = formatBuildStart({ phase: 'build', package_manager: 'npm', command: 'npm run build' });
    expect(f.phase).toBe('build');
    expect(f.summary).toBe('Running production build (npm run build)');
    expect(f.subtext).toContain('🔍');
  });

  it('defaults to build phase when phase is missing', () => {
    const f = formatBuildStart({ command: 'npm run build' });
    expect(f.phase).toBe('build');
    expect(f.log).toContain('[build] build:');
  });

  it('omits the parenthetical tail when nothing was provided', () => {
    expect(formatBuildStart({ phase: 'install' }).summary).toBe('Installing dependencies');
    expect(formatBuildStart({ phase: 'build' }).summary).toBe('Running production build');
  });

  it('returns a stable shape for any input', () => {
    const f = formatBuildStart({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(['install', 'build']).toContain(f.phase);
  });
});

describe('formatBuildResult', () => {
  it('success payload renders pass status with no fix-tail', () => {
    const f = formatBuildResult({ success: true, attempts: 0 });
    expect(f.status).toBe('pass');
    expect(f.reason).toBe('');
    expect(f.summary).toBe('Build passed');
    expect(f.subtext).toBe('✅ Build passed');
    expect(f.log).toBe('[build] result: Build passed');
  });

  it('success after retries reports attempt count (plural)', () => {
    expect(formatBuildResult({ success: true, attempts: 1 }).summary)
      .toBe('Build passed (fixed in 1 attempt)');
    expect(formatBuildResult({ success: true, attempts: 3 }).summary)
      .toBe('Build passed (fixed in 3 attempts)');
  });

  it('success with fixed_count appends a tail to the log line only', () => {
    const f = formatBuildResult({ success: true, attempts: 1, fixed_count: 2 });
    expect(f.log).toContain('2 files auto-fixed');
    expect(f.subtext).not.toContain('auto-fixed');
  });

  it('timeout flag takes precedence over generic error count', () => {
    const f = formatBuildResult({ success: false, timed_out: true, error_count: 5 });
    expect(f.status).toBe('fail');
    expect(f.reason).toBe('timeout');
    expect(f.summary).toBe('Build timed out');
    expect(f.subtext).toContain('⚠️');
  });

  it('install_failed flag renders distinct reason', () => {
    const f = formatBuildResult({ success: false, install_failed: true });
    expect(f.reason).toBe('install');
    expect(f.summary).toBe('Install-time failure');
  });

  it('generic failure reports error + attempt counts with pluralization', () => {
    expect(formatBuildResult({ success: false, attempts: 1, error_count: 1 }).summary)
      .toBe('1 error remain after 1 attempt');
    expect(formatBuildResult({ success: false, attempts: 2, error_count: 3 }).summary)
      .toBe('3 errors remain after 2 attempts');
  });

  it('coerces non-numeric inputs defensively', () => {
    const f = formatBuildResult({ success: false, attempts: '2', error_count: null });
    expect(f.summary).toBe('0 errors remain after 2 attempts');
    expect(f.status).toBe('fail');
  });

  it('returns a stable shape for any input', () => {
    const f = formatBuildResult({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(['pass', 'fail']).toContain(f.status);
    expect(typeof f.reason).toBe('string');
  });
});

describe('formatRepoCreateStarted', () => {
  it('user-owned path renders provider + name without owner slug', () => {
    const f = formatRepoCreateStarted({ provider: 'github', repo_name: 'my-project' });
    expect(f.summary).toBe('Creating GitHub repo: my-project');
    expect(f.subtext).toBe('📦 Creating GitHub repo: my-project…');
    expect(f.log).toBe('[repo] create_started: my-project');
    expect(f.platformOwned).toBe(false);
  });

  it('platform-owned path includes owner/name slug', () => {
    const f = formatRepoCreateStarted({
      provider: 'github',
      repo_name: 'proj-12345-abc',
      owner: 'lucid-ai-platform',
      platform_owned: true,
    });
    expect(f.summary).toBe('Creating GitHub repo: lucid-ai-platform/proj-12345-abc');
    expect(f.platformOwned).toBe(true);
  });

  it('gitlab provider renders as GitLab', () => {
    const f = formatRepoCreateStarted({ provider: 'gitlab', repo_name: 'x' });
    expect(f.summary).toContain('GitLab');
  });

  it('defaults to github when provider missing', () => {
    expect(formatRepoCreateStarted({}).provider).toBe('github');
  });

  it('returns a stable shape for any input', () => {
    const f = formatRepoCreateStarted({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(typeof f.platformOwned).toBe('boolean');
  });
});

describe('formatRepoCreateDone', () => {
  it('success payload renders pass status with repo URL', () => {
    const f = formatRepoCreateDone({
      success: true,
      provider: 'github',
      repo_url: 'https://github.com/owner/proj',
      repo_name: 'proj',
    });
    expect(f.status).toBe('pass');
    expect(f.summary).toBe('GitHub repo created: https://github.com/owner/proj');
    expect(f.subtext).toContain('✅');
    expect(f.repoUrl).toBe('https://github.com/owner/proj');
  });

  it('falls back to repo_name when no URL is provided', () => {
    const f = formatRepoCreateDone({ success: true, repo_name: 'proj' });
    expect(f.summary).toContain('proj');
  });

  it('failure payload renders fail status with error tag', () => {
    const f = formatRepoCreateDone({
      success: false,
      provider: 'github',
      error: 'create_github_repo returned no result',
    });
    expect(f.status).toBe('fail');
    expect(f.summary).toContain('failed');
    expect(f.summary).toContain('create_github_repo');
    expect(f.subtext).toContain('⚠️');
  });

  it('truncates very long error messages in the summary', () => {
    const long = 'x'.repeat(200);
    const f = formatRepoCreateDone({ success: false, error: long });
    // 80 chars + parens + ellipsis-ish
    expect(f.summary.length).toBeLessThan(150);
  });

  it('returns a stable shape for any input', () => {
    const f = formatRepoCreateDone({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(['pass', 'fail']).toContain(f.status);
    expect(typeof f.repoUrl).toBe('string');
  });
});

describe('formatResearchStarted', () => {
  it('prefers explicit label over generated fallback', () => {
    const f = formatResearchStarted({ kind: 'requirements', label: 'Researching project requirements' });
    expect(f.summary).toBe('Researching project requirements');
    expect(f.kind).toBe('requirements');
    expect(f.subtext).toContain('🔬');
    expect(f.log).toContain('[research] requirements');
  });

  it('falls back to "Researching <kind>" when label is missing', () => {
    const f = formatResearchStarted({ kind: 'design' });
    expect(f.summary).toBe('Researching design');
  });

  it('falls back to "Researching" when kind and label are both missing', () => {
    const f = formatResearchStarted({});
    expect(f.summary).toBe('Researching');
  });

  it('returns a stable shape for any input', () => {
    const f = formatResearchStarted({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(typeof f.kind).toBe('string');
  });
});

describe('formatCodeWriteStarted', () => {
  it('renders model in parentheses when provided', () => {
    const f = formatCodeWriteStarted({ task_type: 'ui_simple', model: 'sonnet' });
    expect(f.summary).toBe('Writing code (sonnet)');
    expect(f.taskType).toBe('ui_simple');
    expect(f.log).toContain('task=ui_simple');
    expect(f.log).toContain('model=sonnet');
  });

  it('omits parens when model is missing', () => {
    const f = formatCodeWriteStarted({ task_type: 'feature_simple' });
    expect(f.summary).toBe('Writing code');
  });

  it('returns a stable shape for any input', () => {
    const f = formatCodeWriteStarted({});
    expect(typeof f.summary).toBe('string');
    expect(typeof f.log).toBe('string');
    expect(typeof f.subtext).toBe('string');
    expect(typeof f.taskType).toBe('string');
  });
});

describe('formatAgentActivity', () => {
  it('returns null for unknown / non-activity types', () => {
    expect(formatAgentActivity({})).toBeNull();
    expect(formatAgentActivity({ type: '' })).toBeNull();
    expect(formatAgentActivity({ type: 'workspace_state' })).toBeNull();
    expect(formatAgentActivity({ type: 'pong' })).toBeNull();
    // task_phase must NOT drive the pill — that's the whole point of Step 5
    expect(formatAgentActivity({ type: 'task_phase', phase: 2, status: 'active' })).toBeNull();
  });

  it('maps image_binder.summary to the 🖼️ icon + summary text', () => {
    const a = formatAgentActivity({
      type: 'image_binder.summary', requested: 12, bound: 11,
    });
    expect(a).toEqual({ icon: '🖼️', message: '11/12 images bound (92%)', kind: 'image_binder.summary' });
  });

  it('maps quality.summary to a status-discriminator icon', () => {
    expect(formatAgentActivity({ type: 'quality.summary', passed: 10, total: 10 }).icon).toBe('✅');
    expect(formatAgentActivity({ type: 'quality.summary', passed: 9, total: 10, warnings: 1 }).icon).toBe('✓');
    expect(formatAgentActivity({ type: 'quality.summary', passed: 7, total: 10, blockers: 2 }).icon).toBe('⚠️');
  });

  it('maps build.start to phase-specific icon (📦 install / 🔍 build)', () => {
    expect(formatAgentActivity({ type: 'build.start', phase: 'install' }).icon).toBe('📦');
    expect(formatAgentActivity({ type: 'build.start', phase: 'build' }).icon).toBe('🔍');
  });

  it('maps build.result to ✅ on success, ⚠️ otherwise', () => {
    expect(formatAgentActivity({ type: 'build.result', success: true }).icon).toBe('✅');
    expect(formatAgentActivity({ type: 'build.result', success: false, error_count: 3, attempts: 2 }).icon).toBe('⚠️');
  });

  it('maps repo.create_started / repo.create_done', () => {
    const start = formatAgentActivity({ type: 'repo.create_started', repo_name: 'x' });
    expect(start.icon).toBe('📦');
    expect(start.message).toContain('x');
    expect(formatAgentActivity({ type: 'repo.create_done', success: true }).icon).toBe('✅');
    expect(formatAgentActivity({ type: 'repo.create_done', success: false }).icon).toBe('⚠️');
  });

  it('maps medium-tier sub-step markers', () => {
    expect(formatAgentActivity({ type: 'fixers.run_started' })).toEqual({
      icon: '🔧', message: 'Running landing fixers', kind: 'fixers.run_started',
    });
    expect(formatAgentActivity({ type: 'brief.distill_started' })).toEqual({
      icon: '📋', message: 'Distilling research', kind: 'brief.distill_started',
    });
    expect(formatAgentActivity({ type: 'research.started', kind: 'design' }).icon).toBe('🔬');
    expect(formatAgentActivity({ type: 'cli.session_started', model: 'sonnet' }).message)
      .toBe('Claude CLI ready (sonnet)');
    expect(formatAgentActivity({ type: 'code.write_started', model: 'sonnet' }).icon).toBe('🤖');
  });

  it('maps agent_event thoughts (💭) and tool use (🛠️)', () => {
    expect(formatAgentActivity({ type: 'agent_event', thought: '  let me think  ' }))
      .toEqual({ icon: '💭', message: 'let me think', kind: 'agent_event.thought' });
    expect(formatAgentActivity({ type: 'agent_event', toolName: 'Edit' }))
      .toEqual({ icon: '🛠️', message: 'Using Edit', kind: 'agent_event.tool' });
  });

  it('truncates very long agent_event thoughts to keep the pill compact', () => {
    const long = 'a'.repeat(500);
    const a = formatAgentActivity({ type: 'agent_event', thought: long });
    expect(a.message.length).toBeLessThanOrEqual(140);
  });

  it('returns null for agent_event with neither thought nor toolName', () => {
    expect(formatAgentActivity({ type: 'agent_event', content: 'plain text' })).toBeNull();
  });
});

describe('isLegacyDuplicateOfTypedEvent — post Step 2 cleanup', () => {
  // The dual-emit migration closed in Phase 2 Step 2. The backend no
  // longer emits the legacy progress strings, so this matcher should
  // return false for everything (safety net for stale clients only).
  it('returns false for every input — there is no longer a legacy emit to suppress', () => {
    expect(isLegacyDuplicateOfTypedEvent('✅ Bound 11/12 images')).toBe(false);
    expect(isLegacyDuplicateOfTypedEvent('✅ Quality gate: 10/10 checks passed.')).toBe(false);
    expect(isLegacyDuplicateOfTypedEvent('📦 Creating GitHub repository...')).toBe(false);
    expect(isLegacyDuplicateOfTypedEvent('🔧 Running landing fixers...')).toBe(false);
    expect(isLegacyDuplicateOfTypedEvent('🤖 Writing code...')).toBe(false);
  });

  it('handles non-string input defensively', () => {
    expect(isLegacyDuplicateOfTypedEvent(null)).toBe(false);
    expect(isLegacyDuplicateOfTypedEvent(undefined)).toBe(false);
    expect(isLegacyDuplicateOfTypedEvent(42)).toBe(false);
    expect(isLegacyDuplicateOfTypedEvent({})).toBe(false);
  });
});
