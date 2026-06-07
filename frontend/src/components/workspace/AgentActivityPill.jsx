'use client';

// ─────────────────────────────────────────────────────────
//  AgentActivityPill — Phase 2 Step 5
//  Renders the live agent-activity signal as a transient pill
//  above the chat input. Decoupled from TaskProgress (which
//  renders task_phase). Driven by the typed sub-step events
//  + agent_event thoughts/tool use via the agentActivity
//  state slot on useAgentSession.
// ─────────────────────────────────────────────────────────

import { useEffect, useRef, useState } from 'react';

/**
 * @param {object} props
 * @param {{ icon: string, message: string, kind: string, ts: number } | null} props.activity
 */
export default function AgentActivityPill({ activity }) {
  // Local "visible" mirror so we can fade out *after* the activity
  // object is gone — without this the pill snaps off, which feels worse
  // than the 200ms fade you get from React + a CSS transition class.
  const [visible, setVisible] = useState(false);
  const [lastShown, setLastShown] = useState(null);
  const fadeTimerRef = useRef(null);

  useEffect(() => {
    if (activity) {
      setLastShown(activity);
      setVisible(true);
      if (fadeTimerRef.current) {
        clearTimeout(fadeTimerRef.current);
        fadeTimerRef.current = null;
      }
      return;
    }
    // activity went null — start fade-out, then drop content.
    setVisible(false);
    fadeTimerRef.current = setTimeout(() => {
      setLastShown(null);
      fadeTimerRef.current = null;
    }, 250);
    return () => {
      if (fadeTimerRef.current) {
        clearTimeout(fadeTimerRef.current);
        fadeTimerRef.current = null;
      }
    };
  }, [activity]);

  if (!lastShown) return null;

  const { icon = '⚡', message = '' } = lastShown;
  if (!message) return null;

  return (
    <div
      aria-live="polite"
      role="status"
      data-testid="agent-activity-pill"
      className={[
        'mx-3 mb-2 flex items-center gap-2 px-3 py-1.5',
        'rounded-full text-[12px] leading-tight',
        'bg-[#f3f4f6] dark:bg-[#1c2128] text-[#374151] dark:text-slate-300',
        'border border-[#e5e7eb] dark:border-[#2d333b]',
        'shadow-sm transition-opacity duration-200',
        visible ? 'opacity-100' : 'opacity-0',
      ].join(' ')}
    >
      <span aria-hidden className="text-[13px] leading-none">{icon}</span>
      <span className="truncate font-medium">{message}</span>
    </div>
  );
}
