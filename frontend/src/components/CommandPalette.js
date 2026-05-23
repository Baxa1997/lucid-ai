'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Command Palette (⌘K)
//  Global search: Projects, Conversations, Settings, Actions
// ─────────────────────────────────────────────────────────

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import {
  Search, X, ArrowRight, Sparkles, MessageSquare,
  Settings, Grid2X2, BookOpen, FileText, Home,
  Layers, FolderGit2, Zap, Plus,
} from 'lucide-react';
import { cn } from '@/lib/utils';

// ── Static navigation items ──────────────────
const NAV_ACTIONS = [
  { id: 'home', label: 'Go to Home', icon: Home, href: '/dashboard', section: 'Navigation' },
  { id: 'conversations', label: 'Go to Conversations', icon: MessageSquare, href: '/dashboard/conversations', section: 'Navigation' },
  { id: 'integrations', label: 'Go to Integrations', icon: Grid2X2, href: '/dashboard/integrations', section: 'Navigation' },
  { id: 'settings', label: 'Go to Settings', icon: Settings, href: '/dashboard/settings', section: 'Navigation' },
  { id: 'usage-guide', label: 'Go to Usage Guide', icon: BookOpen, href: '/dashboard/usage-docs', section: 'Navigation' },
  { id: 'docs', label: 'Go to Documentation', icon: FileText, href: '/dashboard/doc-setup', section: 'Navigation' },
];

const QUICK_ACTIONS = [
  { id: 'new-project', label: 'New Project', icon: Plus, action: 'wizard', section: 'Actions', badge: '⌘N' },
];

export default function CommandPalette({ isOpen, onClose, projects = [], conversations = [], onWizard }) {
  const router = useRouter();
  const inputRef = useRef(null);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);

  // Focus input on open
  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // ── Build results ──────────────────────────
  const getResults = useCallback(() => {
    const q = query.toLowerCase().trim();
    const results = [];

    // Quick actions always first when no query
    QUICK_ACTIONS.forEach(a => {
      if (!q || a.label.toLowerCase().includes(q)) {
        results.push({ type: 'action', ...a });
      }
    });

    // Projects
    const matchedProjects = projects.filter(p =>
      !q || (p.projectName || '').toLowerCase().includes(q) || (p.repoName || '').toLowerCase().includes(q)
    ).slice(0, 5);
    matchedProjects.forEach(p => {
      results.push({
        type: 'project',
        id: p.projectId,
        label: p.projectName || p.repoName,
        sublabel: p.repoName,
        icon: Layers,
        href: `/dashboard/workspace/${p.projectId}`,
        section: 'Projects',
      });
    });

    // Conversations
    const matchedConvos = conversations.filter(c =>
      !q || (c.title || '').toLowerCase().includes(q) || (c.repo_name || '').toLowerCase().includes(q)
    ).slice(0, 5);
    matchedConvos.forEach(c => {
      results.push({
        type: 'conversation',
        id: c.id,
        label: c.title || c.repo_name || 'Conversation',
        sublabel: c.repo_name,
        icon: MessageSquare,
        href: `/dashboard/workspace/${c.id}`,
        section: 'Conversations',
      });
    });

    // Navigation
    NAV_ACTIONS.forEach(a => {
      if (!q || a.label.toLowerCase().includes(q)) {
        results.push({ type: 'nav', ...a });
      }
    });

    return results;
  }, [query, projects, conversations]);

  const results = getResults();

  // Reset selection when results change
  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  // ── Execute selection ──────────────────────
  const executeResult = useCallback((result) => {
    onClose();
    if (result.action === 'wizard') {
      onWizard?.();
    } else if (result.href) {
      router.push(result.href);
    }
  }, [onClose, onWizard, router]);

  // ── Keyboard navigation ────────────────────
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e) => {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setSelectedIndex(i => Math.min(i + 1, results.length - 1));
          break;
        case 'ArrowUp':
          e.preventDefault();
          setSelectedIndex(i => Math.max(i - 1, 0));
          break;
        case 'Enter':
          e.preventDefault();
          if (results[selectedIndex]) {
            executeResult(results[selectedIndex]);
          }
          break;
        case 'Escape':
          e.preventDefault();
          onClose();
          break;
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, results, selectedIndex, executeResult, onClose]);

  if (!isOpen) return null;

  // ── Group results by section ───────────────
  const sections = {};
  results.forEach(r => {
    if (!sections[r.section]) sections[r.section] = [];
    sections[r.section].push(r);
  });

  let globalIndex = 0;

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[15vh]">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/30 dark:bg-black/50 backdrop-command"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="relative w-full max-w-[560px] mx-4 bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-slate-700/50 shadow-2xl dark:shadow-black/60 animate-scale-in overflow-hidden">
        {/* Search Input */}
        <div className="flex items-center gap-3 px-5 border-b border-slate-100 dark:border-slate-800">
          <Search className="w-4 h-4 text-slate-400 dark:text-slate-500 shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search projects, pages, actions..."
            className="flex-1 py-4 text-sm bg-transparent text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 outline-none"
          />
          <div className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 text-[10px] font-medium text-slate-400 bg-slate-100 dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700">
              ESC
            </kbd>
          </div>
        </div>

        {/* Results */}
        <div className="max-h-[360px] overflow-y-auto py-2">
          {results.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <Search className="w-8 h-8 text-slate-200 dark:text-slate-700 mb-2" />
              <p className="text-sm font-medium text-slate-400 dark:text-slate-500">No results found</p>
              <p className="text-[11px] text-slate-300 dark:text-slate-600 mt-0.5">Try a different search term</p>
            </div>
          ) : (
            Object.entries(sections).map(([sectionName, items]) => (
              <div key={sectionName}>
                <div className="px-5 pt-2 pb-1">
                  <span className="text-[10px] font-bold text-slate-400 dark:text-slate-600 uppercase tracking-wider">
                    {sectionName}
                  </span>
                </div>
                {items.map((item) => {
                  const idx = globalIndex++;
                  const isSelected = idx === selectedIndex;
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.id}
                      onClick={() => executeResult(item)}
                      onMouseEnter={() => setSelectedIndex(idx)}
                      className={cn(
                        "w-full flex items-center gap-3 px-5 py-2.5 text-left transition-colors",
                        isSelected
                          ? "bg-emerald-50 dark:bg-emerald-500/[0.08]"
                          : "hover:bg-slate-50 dark:hover:bg-white/[0.02]"
                      )}
                    >
                      <div className={cn(
                        "w-7 h-7 rounded-lg flex items-center justify-center shrink-0 transition-colors",
                        isSelected
                          ? "bg-emerald-100 dark:bg-emerald-500/20"
                          : "bg-slate-100 dark:bg-white/[0.04]"
                      )}>
                        <Icon className={cn(
                          "w-3.5 h-3.5",
                          isSelected ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400 dark:text-slate-500"
                        )} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className={cn(
                          "text-[13px] font-medium truncate",
                          isSelected ? "text-emerald-700 dark:text-emerald-300" : "text-slate-700 dark:text-slate-300"
                        )}>
                          {item.label}
                        </p>
                        {item.sublabel && (
                          <p className="text-[11px] text-slate-400 dark:text-slate-500 truncate">{item.sublabel}</p>
                        )}
                      </div>
                      {item.badge && (
                        <kbd className="px-1.5 py-0.5 text-[10px] font-medium text-slate-400 bg-slate-100 dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 shrink-0">
                          {item.badge}
                        </kbd>
                      )}
                      <ArrowRight className={cn(
                        "w-3.5 h-3.5 shrink-0 transition-colors",
                        isSelected ? "text-emerald-400 dark:text-emerald-500" : "text-slate-300 dark:text-slate-700"
                      )} />
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-2.5 border-t border-slate-100 dark:border-slate-800 text-[10px] text-slate-400 dark:text-slate-600">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="px-1 py-0.5 bg-slate-100 dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 text-[9px]">↑↓</kbd>
              Navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1 py-0.5 bg-slate-100 dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 text-[9px]">↵</kbd>
              Open
            </span>
          </div>
          <span className="font-medium">Lucid AI</span>
        </div>
      </div>
    </div>
  );
}
