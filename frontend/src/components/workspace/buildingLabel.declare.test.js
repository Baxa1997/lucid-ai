// Tests for declaredPipeline integration in computeBuildLabel (Phase 2 Step 2).
//
// The declared list is purely additive at this step — computeBuildLabel
// accepts it as a parameter but doesn't yet override hardcoded labels
// with declared ones (that's Step 4 cleanup). These tests pin down the
// CURRENT contract:
//   1. Function accepts declaredPipeline without crashing.
//   2. Function ignores it for label decisions for now (backward compat).
//   3. Return shape is unchanged when declaredPipeline is provided.
//
// When Step 4 lands and we actually consume the declared labels, these
// tests will be updated to assert the new precedence rules.

import { describe, it, expect } from 'vitest';
import { computeBuildLabel } from './buildingLabel';

const FAKE_DECLARED = {
  pipelineId: 'new',
  phases: [
    { key: 'validate', label: 'Validating inputs', index: 1 },
    { key: 'setup',    label: 'Setting up workspace', index: 2 },
    { key: 'research', label: 'Researching', index: 3 },
    { key: 'plan',     label: 'Planning', index: 4 },
    { key: 'code',     label: 'Writing code', index: 5 },
    { key: 'verify',   label: 'Verifying build', index: 6 },
  ],
};

const callLabel = (overrides = {}) =>
  computeBuildLabel({
    status: 'running',
    phases: [],
    resolvingInfo: null,
    isWizardMode: false,
    convLoading: false,
    previewLoading: false,
    previewStatusMsg: '',
    declaredPipeline: null,
    ...overrides,
  });

describe('computeBuildLabel — declaredPipeline parameter (Phase 2 Step 2)', () => {
  it('accepts declaredPipeline without crashing', () => {
    expect(() =>
      callLabel({
        declaredPipeline: FAKE_DECLARED,
        phases: [{ phase: 3, title: 'Researching project', status: 'active' }],
      }),
    ).not.toThrow();
  });

  it('returns the same label shape regardless of declared list presence', () => {
    const a = callLabel({
      declaredPipeline: null,
      phases: [{ phase: 3, title: 'Researching project', status: 'active' }],
    });
    const b = callLabel({
      declaredPipeline: FAKE_DECLARED,
      phases: [{ phase: 3, title: 'Researching project', status: 'active' }],
    });
    // Same shape contract — label, subtext, currentPhaseNum.
    expect(Object.keys(a).sort()).toEqual(Object.keys(b).sort());
    expect(typeof a.label).toBe(typeof b.label);
    expect(typeof a.subtext).toBe(typeof b.subtext);
    expect(typeof a.currentPhaseNum).toBe(typeof b.currentPhaseNum);
  });

  it('declared list does NOT yet override the hardcoded label (Step 4 will)', () => {
    // Backward-compat check: until Step 4 consumes the declared labels,
    // a custom declared list MUST NOT change the rendered label. This
    // protects existing visual behavior during the migration.
    const withoutDeclared = callLabel({
      phases: [{ phase: 5, title: 'Writing code', status: 'active' }],
    });
    const withDeclared = callLabel({
      phases: [{ phase: 5, title: 'Writing code', status: 'active' }],
      declaredPipeline: {
        pipelineId: 'new',
        phases: [
          { key: 'code', label: 'Custom Code Label', index: 5 },
        ],
      },
    });
    expect(withDeclared.label).toBe(withoutDeclared.label);
  });

  it('survives an empty declared list', () => {
    const r = callLabel({
      declaredPipeline: { pipelineId: 'new', phases: [] },
      phases: [{ phase: 2, title: 'Setting up workspace', status: 'active' }],
    });
    expect(r.label).toBe('Setting up workspace...');
  });

  it('survives a malformed declared list (missing fields)', () => {
    // Defensive: a partial declare from a buggy backend shouldn't crash
    // the chart.
    expect(() =>
      callLabel({
        declaredPipeline: { pipelineId: 'new', phases: [{ key: 'code' }] },
        phases: [{ phase: 5, title: 'Writing code', status: 'active' }],
      }),
    ).not.toThrow();
  });
});
