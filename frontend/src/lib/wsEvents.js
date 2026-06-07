// wsEvents.js — pure formatters for typed structured events.
//
// Phase 2 Step 4 replaces free-form `type: "progress"` text messages with
// typed events (e.g. `image_binder.summary`) carrying structured data.
// The hook dispatches each typed event; this module owns the
// purely-computational rendering of those payloads into user-facing
// strings, so tests can pin behavior without React or DOM.

/**
 * Format an image_binder.summary payload into a one-line log entry + a
 * short status subtext. Pure function — no side effects.
 *
 * @param {object} msg — { requested, bound, unbound, geo_rejected,
 *                         subject_rejected, retry_used }
 * @returns {{ summary: string, detail: string, log: string, subtext: string, rate: number }}
 */
export function formatImageBinderSummary(msg = {}) {
  const requested = Number(msg.requested) || 0;
  const bound = Number(msg.bound) || 0;
  const geo = Number(msg.geo_rejected) || 0;
  const subject = Number(msg.subject_rejected) || 0;
  const retries = Number(msg.retry_used) || 0;

  // Rate is bound/requested as a percentage. Edge cases: a 0-request run
  // is treated as 100% success (nothing to bind, nothing failed).
  const rate = requested ? Math.round((bound / requested) * 100) : 100;

  const summary = `${bound}/${requested} images bound (${rate}%)`;
  const detail = (geo || subject || retries)
    ? `geo-rejected=${geo} subject-rejected=${subject} retries=${retries}`
    : '';

  return {
    summary,
    detail,
    log: detail
      ? `[image_binder] ${summary} · ${detail}`
      : `[image_binder] ${summary}`,
    subtext: `🖼️ ${summary}`,
    rate,
  };
}

/**
 * Format a quality.summary payload into a one-line log + status subtext.
 * Pure function — no side effects.
 *
 * The full `quality_report` event already carries the per-check chart
 * data; this just renders the aggregate counts so we can show a single
 * status chip instead of dumping the same numbers into chat.
 *
 * @param {object} msg — { passed, total, blockers, warnings, purpose }
 * @returns {{ summary: string, log: string, subtext: string, status: 'pass'|'warn'|'fail' }}
 */
export function formatQualitySummary(msg = {}) {
  const passed = Number(msg.passed) || 0;
  const total = Number(msg.total) || 0;
  const blockers = Number(msg.blockers) || 0;
  const warnings = Number(msg.warnings) || 0;
  const purpose = typeof msg.purpose === 'string' ? msg.purpose : '';

  // Status discriminator — blockers > warnings > pass.
  let status = 'pass';
  if (blockers > 0) status = 'fail';
  else if (warnings > 0) status = 'warn';

  const icon = status === 'fail' ? '⚠️' : status === 'warn' ? '✓' : '✅';
  const tail = blockers
    ? ` · ${blockers} blocker${blockers === 1 ? '' : 's'}`
    : warnings
      ? ` · ${warnings} warning${warnings === 1 ? '' : 's'}`
      : '';

  const summary = `${passed}/${total} checks passed${tail}`;
  const purposeTag = purpose ? ` [${purpose}]` : '';

  return {
    summary,
    log: `[quality]${purposeTag} ${summary}`,
    subtext: `${icon} Quality: ${summary}`,
    status,
  };
}

/**
 * Format a build.start payload into a one-line log + status subtext.
 * Pure function — no side effects.
 *
 * Two phases share this event:
 *   - phase === "install"  → "📦 Installing dependencies (pnpm)..."
 *   - phase === "build"    → "🔍 Running production build (npm run build)..."
 *
 * @param {object} msg — { phase, package_manager, command }
 * @returns {{ summary: string, log: string, subtext: string, phase: string }}
 */
export function formatBuildStart(msg = {}) {
  const phase = msg.phase === 'install' ? 'install' : 'build';
  const pm = typeof msg.package_manager === 'string' ? msg.package_manager : '';
  const command = typeof msg.command === 'string' ? msg.command : '';

  const icon = phase === 'install' ? '📦' : '🔍';
  const verb = phase === 'install' ? 'Installing dependencies' : 'Running production build';
  const tail = phase === 'install'
    ? (pm ? ` (${pm})` : '')
    : (command ? ` (${command})` : '');

  const summary = `${verb}${tail}`;
  return {
    summary,
    log: `[build] ${phase}: ${summary}`,
    subtext: `${icon} ${summary}…`,
    phase,
  };
}

/**
 * Format a build.result payload into a one-line log + status subtext.
 * Pure function — no side effects.
 *
 * Status discriminator:
 *   - success === true                 → 'pass'
 *   - install_failed || timed_out      → 'fail' (with reason tag)
 *   - success === false                → 'fail'
 *
 * @param {object} msg — { success, attempts, error_count, fixed_count,
 *                          install_failed, timed_out, needs_fix }
 * @returns {{ summary: string, log: string, subtext: string, status: 'pass'|'fail', reason: string }}
 */
export function formatBuildResult(msg = {}) {
  const success = Boolean(msg.success);
  const attempts = Number(msg.attempts) || 0;
  const errorCount = Number(msg.error_count) || 0;
  const fixedCount = Number(msg.fixed_count) || 0;
  const installFailed = Boolean(msg.install_failed);
  const timedOut = Boolean(msg.timed_out);

  let status = success ? 'pass' : 'fail';
  let reason = '';
  if (!success) {
    if (timedOut) reason = 'timeout';
    else if (installFailed) reason = 'install';
    else reason = 'errors';
  }

  let summary;
  if (success) {
    summary = attempts > 0
      ? `Build passed (fixed in ${attempts} attempt${attempts === 1 ? '' : 's'})`
      : 'Build passed';
  } else if (timedOut) {
    summary = 'Build timed out';
  } else if (installFailed) {
    summary = 'Install-time failure';
  } else {
    summary = `${errorCount} error${errorCount === 1 ? '' : 's'} remain after ${attempts} attempt${attempts === 1 ? '' : 's'}`;
  }

  const fixedTail = fixedCount > 0 ? ` · ${fixedCount} file${fixedCount === 1 ? '' : 's'} auto-fixed` : '';
  const icon = success ? '✅' : '⚠️';

  return {
    summary,
    log: `[build] result: ${summary}${fixedTail}`,
    subtext: `${icon} ${summary}`,
    status,
    reason,
  };
}

/**
 * Format a repo.create_started payload into a one-line log + status subtext.
 * Pure function — no side effects.
 *
 * @param {object} msg — { provider, repo_name, owner, platform_owned }
 * @returns {{ summary: string, log: string, subtext: string, provider: string, platformOwned: boolean }}
 */
export function formatRepoCreateStarted(msg = {}) {
  const provider = typeof msg.provider === 'string' ? msg.provider : 'github';
  const repoName = typeof msg.repo_name === 'string' ? msg.repo_name : '';
  const owner = typeof msg.owner === 'string' ? msg.owner : '';
  const platformOwned = Boolean(msg.platform_owned);

  const providerLabel = provider === 'gitlab' ? 'GitLab' : 'GitHub';
  const slug = owner && repoName ? `${owner}/${repoName}` : (repoName || providerLabel);
  const summary = `Creating ${providerLabel} repo: ${slug}`;

  return {
    summary,
    log: `[repo] create_started: ${slug}`,
    subtext: `📦 ${summary}…`,
    provider,
    platformOwned,
  };
}

/**
 * Format a repo.create_done payload into a one-line log + status subtext.
 * Pure function — no side effects.
 *
 * The follow-up `type: "repo_created"` event already carries the URL the
 * FE uses for navigation; this formatter just renders the chart chip.
 *
 * @param {object} msg — { success, provider, repo_url, repo_name,
 *                          platform_owned, error }
 * @returns {{ summary: string, log: string, subtext: string, status: 'pass'|'fail', repoUrl: string }}
 */
export function formatRepoCreateDone(msg = {}) {
  const success = Boolean(msg.success);
  const provider = typeof msg.provider === 'string' ? msg.provider : 'github';
  const repoUrl = typeof msg.repo_url === 'string' ? msg.repo_url : '';
  const repoName = typeof msg.repo_name === 'string' ? msg.repo_name : '';
  const error = typeof msg.error === 'string' ? msg.error : '';

  const providerLabel = provider === 'gitlab' ? 'GitLab' : 'GitHub';
  const status = success ? 'pass' : 'fail';
  const icon = success ? '✅' : '⚠️';

  const summary = success
    ? `${providerLabel} repo created: ${repoUrl || repoName}`
    : `${providerLabel} repo creation failed${error ? ` (${error.slice(0, 80)})` : ''}`;

  return {
    summary,
    log: `[repo] create_done: ${success ? 'ok' : 'fail'} ${repoUrl || repoName}`.trim(),
    subtext: `${icon} ${summary}`,
    status,
    repoUrl,
  };
}

/**
 * Format a research.started payload into a one-line log + status subtext.
 * Pure function — no side effects.
 *
 * The medium-impact tier reuses one event for all research kinds
 * (requirements/structure/design/products/domain/conversion); the
 * formatter picks an icon + label per kind.
 *
 * @param {object} msg — { kind, label }
 * @returns {{ summary: string, log: string, subtext: string, kind: string }}
 */
export function formatResearchStarted(msg = {}) {
  const kind = typeof msg.kind === 'string' ? msg.kind : '';
  const label = typeof msg.label === 'string' ? msg.label : '';

  const fallback = kind ? `Researching ${kind}` : 'Researching';
  const summary = label || fallback;

  return {
    summary,
    log: `[research]${kind ? ` ${kind}` : ''} ${summary}`,
    subtext: `🔬 ${summary}…`,
    kind,
  };
}

/**
 * Format a code.write_started payload into a one-line log + subtext.
 * Pure function — no side effects.
 *
 * @param {object} msg — { task_type, model }
 * @returns {{ summary: string, log: string, subtext: string, taskType: string }}
 */
export function formatCodeWriteStarted(msg = {}) {
  const taskType = typeof msg.task_type === 'string' ? msg.task_type : '';
  const model = typeof msg.model === 'string' ? msg.model : '';
  const tail = model ? ` (${model})` : '';
  const summary = `Writing code${tail}`;
  return {
    summary,
    log: `[code] write_started${taskType ? ` task=${taskType}` : ''}${model ? ` model=${model}` : ''}`,
    subtext: `🤖 ${summary}…`,
    taskType,
  };
}

/**
 * Phase 2 Step 5 — Map a typed event payload into the AgentActivityPill
 * shape. Pure function. Returns null when the event has no activity-pill
 * counterpart (e.g. ack/pong).
 *
 * The pill shows ONE compact line of "what the agent is doing right now,"
 * decoupled from the TaskProgress phase chart (which renders task_phase).
 *
 * @param {object} msg — { type, ...payload }
 * @returns {{ icon: string, message: string, kind: string } | null}
 */
export function formatAgentActivity(msg = {}) {
  const t = typeof msg.type === 'string' ? msg.type : '';
  if (!t) return null;

  if (t === 'image_binder.summary') {
    const f = formatImageBinderSummary(msg);
    return { icon: '🖼️', message: f.summary, kind: t };
  }
  if (t === 'quality.summary') {
    const f = formatQualitySummary(msg);
    const icon = f.status === 'fail' ? '⚠️' : f.status === 'warn' ? '✓' : '✅';
    return { icon, message: `Quality: ${f.summary}`, kind: t };
  }
  if (t === 'build.start') {
    const f = formatBuildStart(msg);
    return { icon: f.phase === 'install' ? '📦' : '🔍', message: f.summary, kind: t };
  }
  if (t === 'build.result') {
    const f = formatBuildResult(msg);
    return { icon: f.status === 'pass' ? '✅' : '⚠️', message: f.summary, kind: t };
  }
  if (t === 'repo.create_started') {
    const f = formatRepoCreateStarted(msg);
    return { icon: '📦', message: f.summary, kind: t };
  }
  if (t === 'repo.create_done') {
    const f = formatRepoCreateDone(msg);
    return { icon: f.status === 'pass' ? '✅' : '⚠️', message: f.summary, kind: t };
  }
  if (t === 'fixers.run_started') {
    return { icon: '🔧', message: 'Running landing fixers', kind: t };
  }
  if (t === 'brief.distill_started') {
    return { icon: '📋', message: 'Distilling research', kind: t };
  }
  if (t === 'research.started') {
    const f = formatResearchStarted(msg);
    return { icon: '🔬', message: f.summary, kind: t };
  }
  if (t === 'cli.session_started') {
    const model = typeof msg.model === 'string' && msg.model ? ` (${msg.model})` : '';
    return { icon: '🤖', message: `Claude CLI ready${model}`, kind: t };
  }
  if (t === 'code.write_started') {
    const f = formatCodeWriteStarted(msg);
    return { icon: '🤖', message: f.summary, kind: t };
  }
  // agent_event — thoughts / tool use
  if (t === 'agent_event') {
    const thought = typeof msg.thought === 'string' ? msg.thought.trim() : '';
    if (thought) {
      return { icon: '💭', message: thought.slice(0, 140), kind: 'agent_event.thought' };
    }
    const toolName = typeof msg.toolName === 'string' ? msg.toolName : '';
    if (toolName) {
      return { icon: '🛠️', message: `Using ${toolName}`, kind: 'agent_event.tool' };
    }
    return null;
  }
  return null;
}

/**
 * Returns true when the given progress message is a legacy free-form
 * counterpart of a typed event we now handle structurally.
 *
 * The dual-emit migration window closed after Phase 2 Step 4 — the
 * backend no longer emits these legacy strings. This function is kept
 * as a safety net for stale clients or in-flight backends mid-deploy;
 * once a release has shipped without any reports, it can be deleted
 * along with this docstring.
 */
export function isLegacyDuplicateOfTypedEvent(message) {
  if (typeof message !== 'string') return false;
  return false;
}
