// Behavioral tests for computeBuildLabel — the function that decides the
// status label and subtext shown above the chat input and in BuildingScreen.
//
// The post-refactor contract is HONEST EVENT-DRIVEN LABELS:
//   • Each label maps to a specific backend event (workspace_state /
//     task_phase / preview_status).
//   • There is no anticipation: wizard mode no longer pre-empts the first
//     task_phase with "Analyzing…" while the workspace is just cloning.
//   • status=ready always reads "Workspace ready" — the chat panel's own
//     isWaiting override switches that to "Waiting for your message…" when
//     messages exist.

import { describe, it, expect } from 'vitest';
import { computeBuildLabel } from './buildingLabel';

const callLabel = (overrides = {}) =>
  computeBuildLabel({
    status: 'idle',
    phases: [],
    resolvingInfo: null,
    isWizardMode: false,
    convLoading: false,
    previewLoading: false,
    previewStatusMsg: '',
    ...overrides,
  });

describe('computeBuildLabel — connection-phase states', () => {
  it('idle + edit mode shows the loading conversation label', () => {
    const { label, subtext } = callLabel({ status: 'idle' });
    expect(label).toBe('Loading your conversation...');
    expect(subtext).toMatch(/chat history/);
  });

  it('connecting shows the connecting label regardless of mode', () => {
    expect(callLabel({ status: 'connecting' }).label).toBe('Connecting...');
    expect(callLabel({ status: 'connecting', isWizardMode: true }).label).toBe(
      'Connecting...',
    );
  });

  it('preparing reads "Preparing workspace…" in BOTH modes (no wizard lie)', () => {
    // Pre-refactor this collapsed to "Analyzing your request…" in wizard
    // mode — but during preparing the agent isn't analysing anything;
    // the workspace is just doing its setup.
    expect(callLabel({ status: 'preparing', isWizardMode: true }).label).toBe(
      'Preparing workspace...',
    );
    expect(callLabel({ status: 'preparing', isWizardMode: false }).label).toBe(
      'Preparing workspace...',
    );
  });

  it('cloning reads "Cloning template…" by default (both modes)', () => {
    expect(callLabel({ status: 'cloning', isWizardMode: true }).label).toBe(
      'Cloning template...',
    );
    expect(callLabel({ status: 'cloning', isWizardMode: false }).label).toBe(
      'Cloning template...',
    );
  });

  it('cloning with existing_repo info shows the repo display name', () => {
    const { label } = callLabel({
      status: 'cloning',
      resolvingInfo: { path: 'existing_repo', repoDisplay: 'acme/site' },
    });
    expect(label).toBe('Cloning acme/site...');
  });

  it('installing shows the dependency install label', () => {
    expect(callLabel({ status: 'installing' }).label).toMatch(/Installing/);
  });

  it('starting reads "Starting dev server…" (no wizard lie)', () => {
    expect(callLabel({ status: 'starting', isWizardMode: true }).label).toBe(
      'Starting dev server...',
    );
  });

  it('health_check reads "Waiting for dev server…"', () => {
    expect(callLabel({ status: 'health_check' }).label).toBe(
      'Waiting for dev server...',
    );
  });
});

describe('computeBuildLabel — ready-state copy is honest in every mode', () => {
  it('wizard mode + ready + no phases now shows "Workspace ready" (no longer lies "Analyzing...")', () => {
    const { label } = callLabel({ status: 'ready', phases: [], isWizardMode: true });
    expect(label).toBe('Workspace ready...');
  });

  it('edit mode + ready + no phases shows "Workspace ready..."', () => {
    const { label } = callLabel({ status: 'ready', phases: [], isWizardMode: false });
    expect(label).toBe('Workspace ready...');
  });
});

describe('computeBuildLabel — new-project intake overrides workspace setup', () => {
  it('shows the same checking status even while the socket is preparing', () => {
    const { label, subtext } = callLabel({
      status: 'preparing',
      previewLoading: true,
      previewStage: 'preparing',
      previewStatusMsg: 'Preparing preview...',
      projectIntakeStatus: 'checking',
    });
    expect(label).toBe('Analyzing your prompt...');
    expect(subtext).toMatch(/enough information/);
  });

  it('keeps analyzing visible until the backend acknowledges the prompt', () => {
    const { label, subtext } = callLabel({
      status: 'ready',
      projectIntakeStatus: 'handoff',
    });
    expect(label).toBe('Analyzing your prompt...');
    expect(subtext).toBe('Starting your project');
  });

  it('waits for more details instead of claiming the preview is preparing', () => {
    const { label, subtext } = callLabel({
      status: 'preparing',
      previewLoading: true,
      previewStage: 'preparing',
      previewStatusMsg: 'Preparing preview...',
      projectIntakeStatus: 'clarifying',
    });
    expect(label).toBe('Waiting for project details...');
    expect(subtext).toMatch(/question in chat/);
  });
});

describe('computeBuildLabel — phase-driven labels (backend = source of truth)', () => {
  it('running with no canonical status or phase uses a neutral fallback', () => {
    const { label } = callLabel({ status: 'running', phases: [] });
    expect(label).toBe('Working on your request...');
  });

  it('running + phase 1 uses the phase title verbatim (no wizard lie)', () => {
    const { label, subtext } = callLabel({
      status: 'running',
      phases: [{ phase: 1, title: 'Preparing workspace', description: 'Cloning the starter template', status: 'active' }],
      isWizardMode: true,
    });
    expect(label).toBe('Preparing workspace...');
    expect(subtext).toBe('Cloning the starter template');
  });

  it('running + phase 2 uses the phase title verbatim (no wizard lie)', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 2, title: 'Setting up workspace', status: 'active' }],
      isWizardMode: true,
    });
    expect(label).toBe('Setting up workspace...');
  });

  it('renders an understanding phase title verbatim', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 3, title: 'Understanding project', status: 'active' }],
    });
    expect(label).toBe('Understanding project...');
  });

  it('renders classifying task verbatim instead of guessing a status', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 3, title: 'Classifying task', status: 'active' }],
    });
    expect(label).toBe('Classifying task...');
  });

  it('phase 3 research title shows the researching copy', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 3, title: 'Researching project', status: 'active' }],
    });
    expect(label).toBe('Researching project...');
  });

  it('phase 4 design path shows "Choosing design style..."', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 4, title: 'Choosing design', status: 'active' }],
    });
    expect(label).toBe('Choosing design...');
  });

  it('phase 5 → writing code', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 5, title: 'Writing code', status: 'active' }],
    });
    expect(label).toBe('Writing code...');
  });

  it('uses the actual planning title even when its phase number changes', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 7, title: 'Planning code', status: 'active' }],
    });
    expect(label).toBe('Planning code...');
  });

  it('phase 6 → verifying build', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 6, title: 'Verifying build', status: 'active' }],
    });
    expect(label).toBe('Verifying build...');
  });

  it('phase 7 → publishing', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 7, title: 'Publishing project', status: 'active' }],
    });
    expect(label).toBe('Publishing project...');
  });

  it('phase 8 → deploying', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 8, title: 'Deploying', status: 'active' }],
    });
    expect(label).toBe('Deploying...');
  });

  it('unknown future phase falls back to the phase title verbatim', () => {
    // Forward-compat: a backend that adds phase 9 with a brand-new title
    // should render as that title, not crash or pick a wrong label.
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 9, title: 'Optimizing assets', status: 'active' }],
    });
    expect(label).toBe('Optimizing assets...');
  });
});

describe('computeBuildLabel — completion bridge logic', () => {
  it('does not invent a next status after research completes', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [
        { phase: 3, title: 'Researching project', status: 'done' },
      ],
    });
    expect(label).toBe('Researching project...');
  });

  it('does not invent a next status after design completes', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [
        { phase: 4, title: 'Choosing design', status: 'done' },
      ],
    });
    expect(label).toBe('Choosing design...');
  });
});

describe('computeBuildLabel — preview overlay precedence', () => {
  it('preview cloning reads "Setting up workspace…" (NOT "Building your app…")', () => {
    // Pre-refactor lie: the preview-pipeline cloning step said "Building
    // your app…" which sounded like the AGENT was generating code. It
    // wasn't — it was just cloning the template.
    const { label } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStatusMsg: 'Cloning template...',
    });
    expect(label).toBe('Setting up workspace...');
  });

  it('preview install message overrides idle label', () => {
    const { label } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStatusMsg: 'Installing dependencies...',
    });
    expect(label).toBe('Installing dependencies...');
  });

  it('preview loading does NOT override running label (running wins)', () => {
    const { label } = callLabel({
      status: 'running',
      phases: [{ phase: 5, title: 'Writing code', status: 'active' }],
      previewLoading: true,
      previewStatusMsg: 'Starting dev server...',
    });
    expect(label).toBe('Writing code...');
  });
});

describe('computeBuildLabel — canonical agent status', () => {
  it('renders the prompt-router status verbatim over phases and preview setup', () => {
    const { label, subtext } = callLabel({
      status: 'running',
      phases: [{ phase: 5, title: 'Writing code', status: 'active' }],
      previewLoading: true,
      previewStage: 'starting',
      agentStatus: {
        label: 'Researching education competitors...',
        description: 'Gemini Flash selected the landing-page workflow',
        state: 'active',
      },
    });
    expect(label).toBe('Researching education competitors...');
    expect(subtext).toBe('Gemini Flash selected the landing-page workflow');
  });

  it('immediately replaces the local intake handoff status', () => {
    const { label, subtext } = callLabel({
      status: 'running',
      projectIntakeStatus: 'handoff',
      agentStatus: {
        label: 'Researching project...',
        description: 'Finding relevant references',
        state: 'active',
      },
    });
    expect(label).toBe('Researching project...');
    expect(subtext).toBe('Finding relevant references');
  });
});

describe('computeBuildLabel — structured previewStage (no string-matching)', () => {
  // The backend's preview_status event carries `msg.status` (the
  // structured stage). When present, we trust it over the human-readable
  // previewStatusMsg. This is the fix for the "grepped 'clone' in any
  // message → wrong label" class of bug.
  it('previewStage="cloning" maps to "Setting up workspace..."', () => {
    const { label, subtext } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStage: 'cloning',
      previewStatusMsg: 'irrelevant freetext from old backend',
    });
    expect(label).toBe('Setting up workspace...');
    expect(subtext).toBe('irrelevant freetext from old backend');
  });

  it('previewStage="installing" maps to "Installing dependencies..."', () => {
    const { label } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStage: 'installing',
    });
    expect(label).toBe('Installing dependencies...');
  });

  it('previewStage="starting" maps to "Starting the dev server..."', () => {
    const { label } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStage: 'starting',
    });
    expect(label).toBe('Starting the dev server...');
  });

  it('previewStage="health_check" maps to "Waiting for dev server..."', () => {
    const { label } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStage: 'health_check',
    });
    expect(label).toBe('Waiting for dev server...');
  });

  it('structured stage wins over the message-grep fallback', () => {
    // If a future backend uses stage="installing" but ALSO sends a
    // message containing "clone" (e.g. "Installing deps for cloned repo"),
    // the structured stage must win — otherwise we revert to the old bug.
    const { label } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStage: 'installing',
      previewStatusMsg: 'Installing for the cloned template',
    });
    expect(label).toBe('Installing dependencies...');
  });

  it('without previewStage, falls back to message-grep for old backends', () => {
    const { label } = callLabel({
      status: 'idle',
      previewLoading: true,
      previewStage: '',
      previewStatusMsg: 'Cloning template now',
    });
    expect(label).toBe('Setting up workspace...');
  });
});

describe('computeBuildLabel — return shape contract', () => {
  it('always returns { label, subtext, currentPhaseNum }', () => {
    const result = callLabel({});
    expect(result).toHaveProperty('label');
    expect(result).toHaveProperty('subtext');
    expect(result).toHaveProperty('currentPhaseNum');
    expect(typeof result.label).toBe('string');
    expect(typeof result.subtext).toBe('string');
    expect(typeof result.currentPhaseNum).toBe('number');
  });

  it('currentPhaseNum reflects highest done phase when no active phase exists', () => {
    const result = callLabel({
      status: 'running',
      phases: [
        { phase: 2, status: 'done', title: 'Setting up workspace' },
        { phase: 3, status: 'done', title: 'Researching' },
      ],
    });
    expect(result.currentPhaseNum).toBe(3);
  });

  it('currentPhaseNum reflects the active phase over the done phases', () => {
    const result = callLabel({
      status: 'running',
      phases: [
        { phase: 2, status: 'done', title: 'Setting up workspace' },
        { phase: 3, status: 'active', title: 'Researching' },
      ],
    });
    expect(result.currentPhaseNum).toBe(3);
  });
});
