'use client';

// ─────────────────────────────────────────────────────────
//  WorkspaceContext — shared state for the 3-panel workspace.
//
//  page.js provides all state via <WorkspaceContext.Provider>.
//  ChatPanel, RightPanel, and WorkspaceHeader consume it
//  through the useWorkspace() hook — no prop drilling.
// ─────────────────────────────────────────────────────────

import { createContext, useContext } from 'react';

export const WorkspaceContext = createContext(null);

/**
 * useWorkspace() — consume workspace state from any child component.
 * Must be called inside a <WorkspaceContext.Provider> tree.
 */
export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace must be used inside WorkspaceContext.Provider');
  return ctx;
}
