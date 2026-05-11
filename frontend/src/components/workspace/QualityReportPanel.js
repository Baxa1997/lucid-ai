'use client';

// ─────────────────────────────────────────────────────────
//  QualityReportPanel — surfaces backend quality_gate findings.
//
//  The backend (ai_engine/app/services/landing_quality_gate.py) emits
//  one `quality_report` event after a generation finishes. The shape is:
//    {
//      purpose: 'hiring' | 'lead_generation' | 'ecommerce' | 'booking',
//      checks: [{ name, label, passed, severity, suggestion, where }],
//      summary: { total, passed, blockers, warnings }
//    }
//
//  This panel collapses to a one-line summary by default and expands to
//  show the failed checks with a per-check "Regenerate this section"
//  button. The button is wired up in A6 — for now it's a placeholder
//  that emits the section name as a chat message so the user can act.
//
//  Renders nothing when:
//    - report is null (no generation finished yet, or unsupported purpose)
//    - all checks passed AND there are no warnings
// ─────────────────────────────────────────────────────────
import { useState } from 'react';
import { CheckCircle2, AlertTriangle, XCircle, ChevronDown, ChevronUp, X } from 'lucide-react';
import { cn } from '@/lib/utils';

const SEVERITY_META = {
  blocker: {
    label: 'Blocker',
    badge: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-800/50',
    icon: XCircle,
    iconClass: 'text-red-500 dark:text-red-400',
  },
  warning: {
    label: 'Warning',
    badge: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800/50',
    icon: AlertTriangle,
    iconClass: 'text-amber-500 dark:text-amber-400',
  },
  info: {
    label: 'Info',
    badge: 'bg-slate-50 text-slate-600 border-slate-200 dark:bg-slate-800/40 dark:text-slate-300 dark:border-slate-700',
    icon: AlertTriangle,
    iconClass: 'text-slate-400 dark:text-slate-500',
  },
};

export default function QualityReportPanel({ report, onDismiss, onRegenerate }) {
  const [expanded, setExpanded] = useState(true);

  if (!report || !Array.isArray(report.checks) || report.checks.length === 0) {
    return null;
  }

  const failed = report.checks.filter((c) => !c.passed);
  if (failed.length === 0) return null;

  const summary = report.summary || {};
  const blockerCount = summary.blockers || failed.filter((c) => c.severity === 'blocker').length;
  const warningCount = summary.warnings || failed.filter((c) => c.severity === 'warning').length;

  const headerTone = blockerCount > 0
    ? 'border-red-200 bg-red-50 dark:border-red-900/40 dark:bg-red-900/10'
    : 'border-amber-200 bg-amber-50 dark:border-amber-900/40 dark:bg-amber-900/10';

  const headerIcon = blockerCount > 0 ? XCircle : AlertTriangle;
  const headerIconClass = blockerCount > 0
    ? 'text-red-500 dark:text-red-400'
    : 'text-amber-500 dark:text-amber-400';
  const HeaderIcon = headerIcon;

  return (
    <div className={cn(
      'mx-3 mb-2 rounded-xl border overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-300',
      headerTone,
    )}>
      {/* Header — collapsible summary */}
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-black/[0.02] dark:hover:bg-white/[0.02] transition-colors cursor-pointer"
      >
        <HeaderIcon className={cn('w-4 h-4 shrink-0', headerIconClass)} />
        <div className="flex-1 min-w-0">
          <div className="text-[12px] font-semibold text-slate-800 dark:text-slate-100 leading-tight">
            Quality check — {failed.length} {failed.length === 1 ? 'item' : 'items'} need attention
          </div>
          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            {report.purpose ? `${formatPurpose(report.purpose)} · ` : ''}
            {blockerCount > 0 && `${blockerCount} blocker${blockerCount === 1 ? '' : 's'}`}
            {blockerCount > 0 && warningCount > 0 && ' · '}
            {warningCount > 0 && `${warningCount} warning${warningCount === 1 ? '' : 's'}`}
          </div>
        </div>
        {expanded
          ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" />
          : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        <span
          role="button"
          tabIndex={0}
          onClick={(e) => { e.stopPropagation(); onDismiss?.(); }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.stopPropagation();
              onDismiss?.();
            }
          }}
          className="ml-1 p-1 rounded hover:bg-black/5 dark:hover:bg-white/10 cursor-pointer"
          title="Dismiss"
        >
          <X className="w-3.5 h-3.5 text-slate-400" />
        </span>
      </button>

      {/* Failed checks */}
      {expanded && (
        <div className="border-t border-current/10 bg-white dark:bg-[#0d1117]">
          <ul className="divide-y divide-slate-100 dark:divide-[#2d333b]">
            {failed.map((check) => {
              const meta = SEVERITY_META[check.severity] || SEVERITY_META.info;
              const Icon = meta.icon;
              return (
                <li key={check.name} className="px-3 py-2.5 flex items-start gap-2.5">
                  <Icon className={cn('w-3.5 h-3.5 mt-0.5 shrink-0', meta.iconClass)} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <span className="text-[12.5px] font-medium text-slate-800 dark:text-slate-100">
                        {check.label || check.name}
                      </span>
                      <span className={cn(
                        'inline-flex items-center px-1.5 py-0 rounded border text-[10px] font-medium uppercase tracking-wide',
                        meta.badge,
                      )}>
                        {meta.label}
                      </span>
                    </div>
                    {check.suggestion && (
                      <p className="text-[12px] leading-snug text-slate-600 dark:text-slate-400">
                        {check.suggestion}
                      </p>
                    )}
                    {Array.isArray(check.where) && check.where.length > 0 && (
                      <p className="text-[10.5px] text-slate-400 dark:text-slate-500 mt-1 truncate">
                        Files: {check.where.slice(0, 3).join(', ')}
                      </p>
                    )}
                  </div>
                  {onRegenerate && (
                    <button
                      type="button"
                      onClick={() => onRegenerate(check)}
                      className="shrink-0 self-center px-2.5 py-1 rounded-md text-[11.5px] font-medium border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#161b22] hover:border-orange-300 hover:bg-orange-50 dark:hover:border-orange-700/60 dark:hover:bg-orange-900/10 text-slate-700 dark:text-slate-200 transition-colors cursor-pointer"
                      title="Re-run section codegen for this check"
                    >
                      Regenerate
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
          {/* Pass-count footer for context */}
          {summary.passed > 0 && (
            <div className="px-3 py-1.5 border-t border-slate-100 dark:border-[#2d333b] bg-slate-50/50 dark:bg-[#161b22]/50">
              <div className="flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
                <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                <span>
                  {summary.passed} of {summary.total} purpose checks passed.
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatPurpose(p) {
  if (!p) return '';
  return p.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
