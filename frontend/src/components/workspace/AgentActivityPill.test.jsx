// Renders + lifecycle tests for the Step 5 agent-activity pill.
// vitest + jsdom + @testing-library/react.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import AgentActivityPill from './AgentActivityPill';

describe('AgentActivityPill', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders nothing when activity is null', () => {
    const { container } = render(<AgentActivityPill activity={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the icon + message when activity is provided', () => {
    render(<AgentActivityPill activity={{ icon: '🖼️', message: '11/12 bound', kind: 'x', ts: 0 }} />);
    const pill = screen.getByTestId('agent-activity-pill');
    expect(pill).toBeTruthy();
    expect(pill.textContent).toContain('🖼️');
    expect(pill.textContent).toContain('11/12 bound');
  });

  it('keeps content visible during the 250ms fade after activity goes null', () => {
    const { rerender } = render(
      <AgentActivityPill activity={{ icon: '🔧', message: 'Running fixers', kind: 'k', ts: 0 }} />,
    );
    expect(screen.getByTestId('agent-activity-pill').textContent).toContain('Running fixers');

    // Activity cleared → content should still be in the DOM (fading out).
    rerender(<AgentActivityPill activity={null} />);
    expect(screen.queryByTestId('agent-activity-pill')).not.toBeNull();
    // opacity-0 class should be applied
    expect(screen.getByTestId('agent-activity-pill').className).toContain('opacity-0');

    // After the fade timeout, content is dropped.
    act(() => { vi.advanceTimersByTime(300); });
    expect(screen.queryByTestId('agent-activity-pill')).toBeNull();
  });

  it('updates content when a fresh activity arrives mid-fade', () => {
    const { rerender } = render(
      <AgentActivityPill activity={{ icon: '🔧', message: 'first', kind: 'k1', ts: 0 }} />,
    );
    expect(screen.getByTestId('agent-activity-pill').textContent).toContain('first');

    // Start a fade by setting null
    rerender(<AgentActivityPill activity={null} />);
    act(() => { vi.advanceTimersByTime(100); });
    // Mid-fade, a new activity shows up — content should swap and stay visible.
    rerender(<AgentActivityPill activity={{ icon: '🖼️', message: 'second', kind: 'k2', ts: 1 }} />);
    expect(screen.getByTestId('agent-activity-pill').textContent).toContain('second');
    expect(screen.getByTestId('agent-activity-pill').className).toContain('opacity-100');
  });

  it('falls back to ⚡ icon and skips render when message is empty', () => {
    render(<AgentActivityPill activity={{ icon: '', message: '', kind: 'k', ts: 0 }} />);
    expect(screen.queryByTestId('agent-activity-pill')).toBeNull();
  });
});
