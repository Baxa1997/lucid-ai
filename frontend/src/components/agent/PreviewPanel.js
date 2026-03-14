'use client';

import { useState } from 'react';
import manager from '@/lib/agentWSManager';

/**
 * PreviewPanel — noVNC iframe with Approve / Reject controls.
 *
 * Props:
 *   previewUrl  — the noVNC URL sent by backend
 *   taskId      — current task identifier
 *   onResolved  — callback after approve/reject cycle completes
 */
export default function PreviewPanel({ previewUrl, taskId, onResolved }) {
  const [state, setState] = useState('preview'); // preview | approving | rejecting | fixing
  const [feedback, setFeedback] = useState('');

  // ── Approve ──────────────────────────────────────
  const handleApprove = () => {
    setState('approving');
    manager?.send({
      type: 'preview_approved',
      task_id: taskId,
    });
    // Parent listens for "complete" to hide this panel
  };

  // ── Reject — show feedback form ─────────────────
  const handleReject = () => {
    setState('rejecting');
  };

  // ── Submit rejection feedback ───────────────────
  const handleSubmitFeedback = () => {
    if (!feedback.trim()) return;
    manager?.send({
      type: 'preview_rejected',
      task_id: taskId,
      feedback: feedback.trim(),
    });
    setState('fixing');
    onResolved?.('rejected');
  };

  // ── Fixing state ───────────────────────────────
  if (state === 'fixing') {
    return (
      <div className="w-full rounded-xl border border-amber-200 bg-amber-50 p-6 text-center">
        <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-4 border-amber-400 border-t-transparent" />
        <p className="text-sm font-semibold text-amber-700">
          Claude is fixing based on your feedback…
        </p>
        <p className="mt-1 text-xs text-amber-500">
          "{feedback}"
        </p>
      </div>
    );
  }

  // ── Approving state (spinner) ──────────────────
  if (state === 'approving') {
    return (
      <div className="w-full rounded-xl border border-emerald-200 bg-emerald-50 p-6 text-center">
        <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-4 border-emerald-500 border-t-transparent" />
        <p className="text-sm font-semibold text-emerald-700">
          Approved! Pushing changes to branch…
        </p>
      </div>
    );
  }

  return (
    <div className="w-full space-y-3">
      {/* ── Preview iframe ────────────────────────── */}
      <div className="overflow-hidden rounded-xl border border-slate-200 shadow-sm">
        <div className="flex items-center gap-2 bg-slate-100 px-4 py-2 border-b border-slate-200">
          <div className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Live Preview
          </span>
        </div>
        <iframe
          src={previewUrl}
          title="Live Preview"
          className="w-full border-0"
          style={{ height: '700px' }}
          sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
        />
      </div>

      {/* ── Action buttons ────────────────────────── */}
      {state === 'preview' && (
        <div className="flex items-center gap-3">
          <button
            onClick={handleApprove}
            className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-emerald-600 px-5 py-3 text-sm font-bold text-white shadow-sm shadow-emerald-600/20 transition-all hover:bg-emerald-700 active:scale-[0.98]"
          >
            ✅ Approve &amp; Push to Branch
          </button>
          <button
            onClick={handleReject}
            className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-red-600 px-5 py-3 text-sm font-bold text-white shadow-sm shadow-red-600/20 transition-all hover:bg-red-700 active:scale-[0.98]"
          >
            ❌ Reject — Fix This
          </button>
        </div>
      )}

      {/* ── Rejection feedback input ──────────────── */}
      {state === 'rejecting' && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 space-y-3">
          <label className="block text-sm font-semibold text-red-700">
            What needs to be fixed?
          </label>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="Describe what's wrong or what should change…"
            rows={3}
            className="w-full rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 outline-none focus:border-red-400 focus:ring-2 focus:ring-red-400/20 resize-none"
            autoFocus
          />
          <div className="flex items-center gap-2">
            <button
              onClick={handleSubmitFeedback}
              disabled={!feedback.trim()}
              className="rounded-lg bg-red-600 px-4 py-2 text-xs font-bold text-white transition-all hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Send Feedback
            </button>
            <button
              onClick={() => setState('preview')}
              className="rounded-lg bg-slate-200 px-4 py-2 text-xs font-bold text-slate-600 transition-all hover:bg-slate-300"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
