'use client';

import React, { useState, useEffect, useCallback } from 'react';

const PREVIEW_DURATION = 300; // 5 minutes in seconds

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function PreviewPanel({
  previewUrl,
  taskId,
  onApprove,
  onReject,
  isExpired = false,
}) {
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [timeLeft, setTimeLeft] = useState(PREVIEW_DURATION);
  const [localExpired, setLocalExpired] = useState(isExpired);

  // Sync external expired prop
  useEffect(() => {
    if (isExpired) setLocalExpired(true);
  }, [isExpired]);

  // Countdown timer
  useEffect(() => {
    if (localExpired) return;
    if (timeLeft <= 0) {
      setLocalExpired(true);
      return;
    }
    const interval = setInterval(() => {
      setTimeLeft((prev) => {
        if (prev <= 1) {
          setLocalExpired(true);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [localExpired, timeLeft]);

  const handleApprove = useCallback(() => {
    if (onApprove) onApprove();
  }, [onApprove]);

  const handleReject = useCallback(() => {
    if (!feedback.trim()) return;
    if (onReject) onReject(feedback.trim());
    setFeedback('');
    setShowFeedback(false);
  }, [feedback, onReject]);

  if (!previewUrl) return null;

  return (
    <div className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-lg overflow-hidden my-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-800 bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-950/30 dark:to-indigo-950/30">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-500/15 flex items-center justify-center">
            <span className="text-lg">🔍</span>
          </div>
          <div>
            <h3 className="font-bold text-slate-900 dark:text-slate-100 text-sm">
              Preview Changes
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Review before pushing to branch
            </p>
          </div>
        </div>

        {/* Timer */}
        <div
          className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold tracking-wide ${
            localExpired
              ? 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400'
              : timeLeft <= 60
                ? 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'
                : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
          }`}
        >
          <span>⏱️</span>
          {localExpired ? 'Expired' : formatTime(timeLeft)}
        </div>
      </div>

      {/* Expired warning banner */}
      {localExpired && (
        <div className="px-6 py-3 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800/50 flex items-center gap-2">
          <span className="text-amber-500">⚠️</span>
          <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
            Preview expired but changes are saved. Approve to push or reject to
            fix.
          </p>
        </div>
      )}

      {/* Iframe */}
      {!localExpired && (
        <div className="w-full bg-slate-50 dark:bg-slate-950 border-b border-slate-200 dark:border-slate-800">
          <div className="relative w-full" style={{ height: '600px' }}>
            {/* URL bar */}
            <div className="absolute top-0 left-0 right-0 z-10 flex items-center gap-2 px-4 py-2 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm border-b border-slate-200 dark:border-slate-700">
              <div className="flex gap-1.5">
                <div className="w-2.5 h-2.5 rounded-full bg-red-400" />
                <div className="w-2.5 h-2.5 rounded-full bg-amber-400" />
                <div className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
              </div>
              <div className="flex-1 px-3 py-1 bg-slate-100 dark:bg-slate-800 rounded-md text-xs text-slate-500 dark:text-slate-400 font-mono truncate">
                {previewUrl}
              </div>
            </div>
            <iframe
              src={previewUrl}
              title="Live Preview"
              className="w-full h-full border-0 pt-9"
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            />
          </div>
        </div>
      )}

      {/* Action area */}
      <div className="px-6 py-5 space-y-4">
        <p className="text-sm font-semibold text-slate-700 dark:text-slate-300">
          Looks good?
        </p>

        <div className="flex flex-col gap-3">
          {/* Approve button */}
          <button
            onClick={handleApprove}
            className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-sm rounded-xl shadow-md shadow-emerald-600/20 transition-all hover:scale-[1.01] active:scale-[0.99]"
          >
            <span>✅</span>
            Push to Branch
          </button>

          {/* Reject toggle */}
          {!showFeedback ? (
            <button
              onClick={() => setShowFeedback(true)}
              className="w-full flex items-center justify-center gap-2 px-5 py-3 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-bold text-sm rounded-xl transition-all hover:scale-[1.01] active:scale-[0.99]"
            >
              <span>❌</span>
              Something&apos;s wrong
            </button>
          ) : (
            <div className="space-y-3 animate-in fade-in slide-in-from-top-2 duration-300">
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Describe the issue — what should be fixed?"
                rows={3}
                className="w-full px-4 py-3 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 resize-none outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500 transition-all"
                autoFocus
              />
              <div className="flex gap-2">
                <button
                  onClick={handleReject}
                  disabled={!feedback.trim()}
                  className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 font-bold text-sm rounded-xl transition-all ${
                    feedback.trim()
                      ? 'bg-blue-600 hover:bg-blue-700 text-white shadow-md shadow-blue-600/20'
                      : 'bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600 cursor-not-allowed'
                  }`}
                >
                  🔄 Send Fix Request
                </button>
                <button
                  onClick={() => {
                    setShowFeedback(false);
                    setFeedback('');
                  }}
                  className="px-4 py-2.5 text-sm font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
