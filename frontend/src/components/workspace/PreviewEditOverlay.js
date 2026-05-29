'use client';

// ─────────────────────────────────────────────────────────
//  PreviewEditOverlay — Base44-style inline editing UI.
//
//  Renders absolutely-positioned visuals over the preview iframe:
//
//    • Hover outline that tracks the cursor inside the iframe
//    • Persistent selection box around the picked element
//    • "tag" badge in the top-left of the selection (h1 / button / img …)
//    • Floating action toolbar below the selection — three modes:
//        (1) Quick actions: Manual / AI + X (close)
//        (2) Manual edit modal: deterministic form fields + Apply
//        (3) Inline AI input: ← back, "What to change?" textarea, ↑ submit, X
//
//  Coordinates flow:
//
//    iframe getBoundingClientRect → child element rect (iframe-local
//    viewport coords) → posted to parent via lucid_element_* messages
//    → parent positions overlay at (iframeRect.{top,left} + childRect)
//
//  Cross-origin safe: parent never touches the iframe DOM; iframe never
//  touches the parent DOM. Everything is a structured postMessage.
// ─────────────────────────────────────────────────────────

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  Sparkles,
  ArrowLeft,
  X,
  Pencil,
  Check,
  Image as ImageIcon,
  Link2,
  Type,
} from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * @param {object}  props
 * @param {object}  props.iframeRef   - the workspace iframe ref (mutable)
 * @param {object?} props.selection   - persistent selection payload from postMessage
 * @param {object?} props.hover       - transient hover payload {rect, tag, hasEditablePath}
 * @param {boolean} props.active      - true when edit-select mode is enabled
 * @param {Function} props.onClose    - clear selection + leave edit-select mode
 * @param {Function} props.onSubmit   - (text) => void; called when inline input is submitted
 * @param {Function} props.onManualApply - (patch) => void; no-LLM manual edit
 */
export default function PreviewEditOverlay({
  iframeRef,
  selection,
  hover,
  active,
  onClose,
  onSubmit,
  onManualApply,
}) {
  // Iframe's bounding rect — we need it to translate the iframe-local
  // coordinates the listener sent into the parent's viewport space.
  // Tracked as state so the overlay re-renders when the iframe layout
  // changes (panel resize, dev-tools open, etc.).
  const [frame, setFrame] = useState(null);
  const rafRef = useRef(0);

  const measureFrame = useCallback(() => {
    const el = iframeRef?.current;
    if (!el) {
      setFrame(null);
      return;
    }
    const r = el.getBoundingClientRect();
    setFrame({ top: r.top, left: r.left, width: r.width, height: r.height });
  }, [iframeRef]);

  useEffect(() => {
    measureFrame();
    const onResizeOrScroll = () => {
      if (rafRef.current) return;
      rafRef.current = window.requestAnimationFrame(() => {
        rafRef.current = 0;
        measureFrame();
      });
    };
    window.addEventListener('resize', onResizeOrScroll);
    window.addEventListener('scroll', onResizeOrScroll, true);
    // ResizeObserver catches workspace panel drags — much more
    // responsive than waiting for the next "resize" event.
    let ro;
    if (iframeRef?.current && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(onResizeOrScroll);
      ro.observe(iframeRef.current);
    }
    return () => {
      window.removeEventListener('resize', onResizeOrScroll);
      window.removeEventListener('scroll', onResizeOrScroll, true);
      if (ro) ro.disconnect();
      if (rafRef.current) window.cancelAnimationFrame(rafRef.current);
    };
  }, [iframeRef, measureFrame]);

  // ── Local UI state — toolbar mode + inline/manual drafts ───
  const [mode, setMode] = useState('actions'); // 'actions' | 'inline' | 'manual'
  const [draft, setDraft] = useState('');
  const [manualText, setManualText] = useState('');
  const [manualSrc, setManualSrc] = useState('');
  const [manualAlt, setManualAlt] = useState('');
  const [manualHref, setManualHref] = useState('');
  const inputRef = useRef(null);

  // Switching to inline mode focuses the input on the next paint so
  // the user can start typing immediately.
  useEffect(() => {
    if (mode === 'inline') {
      const id = window.requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
      return () => window.cancelAnimationFrame(id);
    }
    return undefined;
  }, [mode]);

  // New selection resets the toolbar shape.
  //   • With a rect → compact icon toolbar (user can see the element
  //     they picked, "Edit Element" button is a natural CTA).
  //   • Without a rect (legacy listener) → jump straight to the inline
  //     input — without a selection box to point at, the icon toolbar
  //     would look orphaned.
  useEffect(() => {
    if (!selection) return;
    setMode(selection.rect ? 'actions' : 'inline');
    setDraft('');
    setManualText(selection.text || '');
    setManualSrc(selection.src || '');
    setManualAlt(selection.alt || selection.text || '');
    setManualHref(selection.href || '');
  }, [selection?.path, selection?.file]);

  const manualKind = useMemo(() => {
    const rawType = (selection?.type || '').toLowerCase();
    const tag = (selection?.tag || '').toLowerCase();
    if (rawType === 'image' || rawType === 'image_url' || tag === 'img') return 'image';
    if (rawType === 'url' || rawType === 'link' || tag === 'a') return 'link';
    return 'text';
  }, [selection?.type, selection?.tag]);

  const manualSupported = useMemo(() => (
    Boolean(selection?.path)
    && !selection?.fuzzy
    && ['text', 'image', 'link'].includes(manualKind)
  ), [selection?.path, selection?.fuzzy, manualKind]);

  const ManualIcon = manualKind === 'image'
    ? ImageIcon
    : manualKind === 'link'
      ? Link2
      : Type;

  // Esc clears the selection. Keyboard handler lives on the parent
  // because the iframe also forwards its own Esc keypresses up here.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && (selection || active)) {
        onClose?.();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selection, active, onClose]);

  // ── Render position math ───────────────────────────────────
  // Convert iframe-local viewport coords (sent by the listener) into
  // the parent's viewport coords by offsetting with the iframe's
  // bounding rect. ``position: fixed`` then sits the overlay over
  // exactly the right pixels, no matter the iframe's location.
  const selectionStyle = useMemo(() => {
    if (!selection?.rect || !frame) return null;
    return {
      position: 'fixed',
      top: frame.top + selection.rect.y,
      left: frame.left + selection.rect.x,
      width: selection.rect.width,
      height: selection.rect.height,
      pointerEvents: 'none',
      zIndex: 60,
    };
  }, [selection, frame]);

  const hoverStyle = useMemo(() => {
    if (!hover?.rect || !frame) return null;
    // Skip the hover layer when it's over the currently-selected box,
    // otherwise we double up outlines.
    if (
      selection?.rect
      && hover.rect.x === selection.rect.x
      && hover.rect.y === selection.rect.y
      && hover.rect.width === selection.rect.width
      && hover.rect.height === selection.rect.height
    ) return null;
    return {
      position: 'fixed',
      top: frame.top + hover.rect.y,
      left: frame.left + hover.rect.x,
      width: hover.rect.width,
      height: hover.rect.height,
      pointerEvents: 'none',
      zIndex: 55,
    };
  }, [hover, selection, frame]);

  // Tag badge sits just above the top-left corner of the selection.
  // We pin it INSIDE the iframe rect even when the selection is
  // flush against the top — keeps it visible.
  const tagBadgeStyle = useMemo(() => {
    if (!selection?.rect || !frame) return null;
    const topPx = Math.max(
      frame.top + 4,
      frame.top + selection.rect.y - 32,
    );
    return {
      position: 'fixed',
      top: topPx,
      left: frame.left + selection.rect.x,
      zIndex: 65,
    };
  }, [selection, frame]);

  // Toolbar sits just below the selection. If there's no room, we
  // flip it above the selection. When rect is missing (legacy iframe
  // listener without rect support), we fall back to a centered modal
  // over the iframe so the user can still type their edit.
  const toolbarStyle = useMemo(() => {
    if (!selection || !frame) return null;
    const toolbarApproxWidth = mode === 'inline' ? 460 : mode === 'manual' ? 420 : 290;
    const toolbarApproxHeight = mode === 'manual' ? 250 : 56;
    // Centered-modal fallback when no rect — sits in the middle of
    // the iframe area, vertically biased toward the top third so it
    // doesn't cover the page content the user is editing.
    if (!selection.rect) {
      return {
        position: 'fixed',
        top: frame.top + Math.max(48, frame.height / 3),
        left: frame.left + (frame.width - toolbarApproxWidth) / 2,
        zIndex: 70,
      };
    }
    const wantTop = frame.top + selection.rect.y + selection.rect.height + 8;
    const fitsBelow = wantTop + toolbarApproxHeight < frame.top + frame.height;
    const top = fitsBelow
      ? wantTop
      : Math.max(frame.top + 4, frame.top + selection.rect.y - toolbarApproxHeight - 8);
    // Clamp left so the toolbar never bleeds past the right edge of the iframe.
    const desiredLeft = frame.left + selection.rect.x;
    const maxLeft = frame.left + frame.width - toolbarApproxWidth - 4;
    const left = Math.max(frame.left + 4, Math.min(desiredLeft, maxLeft));
    return {
      position: 'fixed',
      top,
      left,
      zIndex: 70,
    };
  }, [selection, frame, mode]);

  // ── Banner when in select mode but nothing picked yet ──────
  // Shown in lieu of the in-iframe banner the v2 listener used to
  // render — keeps the affordance visible without polluting the
  // generated site.
  const showBanner = active && !selection && frame;
  const bannerStyle = useMemo(() => {
    if (!frame) return null;
    return {
      position: 'fixed',
      top: frame.top + 12,
      left: frame.left + frame.width / 2,
      transform: 'translateX(-50%)',
      zIndex: 70,
      pointerEvents: 'none',
    };
  }, [frame]);

  // ── Submit / cancel handlers ───────────────────────────────
  const handleSubmit = useCallback(
    (e) => {
      e?.preventDefault?.();
      const text = draft.trim();
      if (!text) return;
      onSubmit?.(text);
      setDraft('');
      setMode('actions');
    },
    [draft, onSubmit],
  );

  const handleInputKey = useCallback(
    (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit(e);
      }
    },
    [handleSubmit],
  );

  const handleManualSubmit = useCallback(
    (e) => {
      e?.preventDefault?.();
      if (!manualSupported || !selection) return;

      const patch = {
        kind: manualKind,
        path: selection.path,
        type: selection.type || '',
        tag: selection.tag || '',
      };

      if (manualKind === 'image') {
        patch.src = manualSrc.trim();
        patch.alt = manualAlt;
      } else if (manualKind === 'link') {
        patch.text = manualText;
        patch.href = manualHref.trim();
      } else {
        patch.text = manualText;
      }

      onManualApply?.(patch);
      setMode('actions');
    },
    [
      manualSupported,
      selection,
      manualKind,
      manualSrc,
      manualAlt,
      manualText,
      manualHref,
      onManualApply,
    ],
  );

  // ── Output ─────────────────────────────────────────────────
  // We need ``frame`` (iframe bounding rect) to position anything.
  // Beyond that, we render whenever the user is in edit-select mode,
  // currently has a selection, or is hovering — covering both the
  // "pick something" state and the post-pick toolbar state.
  if (!frame || (!active && !selection && !hover)) return null;

  return (
    <>
      {/* Hover preview outline (transient) */}
      {hoverStyle && (
        <div
          style={hoverStyle}
          className="rounded-[2px] outline outline-2 outline-blue-500/60 outline-offset-2"
        />
      )}

      {/* Persistent selection outline */}
      {selectionStyle && (
        <div
          style={selectionStyle}
          className="rounded-[2px] outline outline-2 outline-blue-600 outline-offset-2 bg-blue-500/5"
        />
      )}

      {/* Tag badge — top-left of the selection */}
      {selection && tagBadgeStyle && (
        <div style={tagBadgeStyle} className="pointer-events-auto">
          <div className="inline-flex items-center gap-1 px-2 h-6 rounded-md bg-blue-600 text-white text-[11px] font-mono font-semibold shadow-sm select-none">
            {selection.tag || 'element'}
          </div>
        </div>
      )}

      {/* Floating action toolbar */}
      {selection && toolbarStyle && (
        <div style={toolbarStyle} className="pointer-events-auto">
          {mode === 'actions' ? (
            <div className="inline-flex items-center gap-1 h-11 px-1.5 rounded-2xl bg-white dark:bg-[#1c2128] border border-slate-200 dark:border-[#2d333b] shadow-[0_8px_24px_rgba(15,23,42,0.12)]">
              <button
                type="button"
                onClick={() => manualSupported && setMode('manual')}
                disabled={!manualSupported}
                className={cn(
                  'inline-flex items-center gap-1.5 h-8 px-3 rounded-xl text-[12px] font-semibold transition-colors',
                  manualSupported
                    ? 'bg-slate-100 dark:bg-[#21262d] hover:bg-slate-200 dark:hover:bg-[#2d333b] text-slate-800 dark:text-slate-100'
                    : 'bg-slate-50 dark:bg-[#161b22] text-slate-400 cursor-not-allowed',
                )}
                title={manualSupported ? 'Manual edit' : 'Manual edit is available for editable content'}>
                <Pencil className="w-3.5 h-3.5 text-blue-600" />
                Manual
              </button>
              <button
                type="button"
                onClick={() => setMode('inline')}
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-xl bg-slate-100 dark:bg-[#21262d] hover:bg-slate-200 dark:hover:bg-[#2d333b] text-[12px] font-semibold text-slate-800 dark:text-slate-100 transition-colors"
                title="Describe what to change with AI">
                <Sparkles className="w-3.5 h-3.5 text-blue-600" />
                AI
              </button>
              <button
                type="button"
                onClick={() => onClose?.()}
                className="w-8 h-8 inline-flex items-center justify-center rounded-xl text-slate-500 hover:text-slate-800 hover:bg-slate-100 dark:hover:bg-[#21262d] transition-colors"
                title="Close">
                <X className="w-4 h-4" />
              </button>
            </div>
          ) : mode === 'manual' ? (
            <form
              onSubmit={handleManualSubmit}
              className="w-[420px] max-w-[calc(100vw-32px)] rounded-2xl bg-white dark:bg-[#1c2128] border border-slate-200 dark:border-[#2d333b] shadow-[0_10px_32px_rgba(15,23,42,0.16)] p-3">
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="flex items-center gap-2 min-w-0">
                  <button
                    type="button"
                    onClick={() => setMode('actions')}
                    className="w-8 h-8 shrink-0 inline-flex items-center justify-center rounded-xl text-slate-500 hover:text-slate-800 hover:bg-slate-100 dark:hover:bg-[#21262d] transition-colors"
                    title="Back to actions">
                    <ArrowLeft className="w-4 h-4" />
                  </button>
                  <div className="w-8 h-8 shrink-0 rounded-xl bg-blue-50 dark:bg-blue-950/30 text-blue-600 dark:text-blue-300 inline-flex items-center justify-center">
                    <ManualIcon className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-[13px] font-semibold text-slate-900 dark:text-slate-100 leading-tight">
                      Manual edit
                    </p>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400 truncate">
                      {selection.path}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => onClose?.()}
                  className="w-8 h-8 shrink-0 inline-flex items-center justify-center rounded-xl text-slate-500 hover:text-slate-800 hover:bg-slate-100 dark:hover:bg-[#21262d] transition-colors"
                  title="Close">
                  <X className="w-4 h-4" />
                </button>
              </div>

              {manualKind === 'image' ? (
                <div className="space-y-3">
                  <label className="block">
                    <span className="block mb-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase">
                      Image URL
                    </span>
                    <input
                      value={manualSrc}
                      onChange={(e) => setManualSrc(e.target.value)}
                      placeholder="https://..."
                      className="w-full h-9 px-3 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] outline-none text-[13px] text-slate-800 dark:text-slate-100 placeholder:text-slate-400 focus:border-blue-500"
                    />
                  </label>
                  <label className="block">
                    <span className="block mb-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase">
                      Alt Text
                    </span>
                    <input
                      value={manualAlt}
                      onChange={(e) => setManualAlt(e.target.value)}
                      className="w-full h-9 px-3 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] outline-none text-[13px] text-slate-800 dark:text-slate-100 focus:border-blue-500"
                    />
                  </label>
                </div>
              ) : manualKind === 'link' ? (
                <div className="space-y-3">
                  <label className="block">
                    <span className="block mb-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase">
                      Label
                    </span>
                    <input
                      value={manualText}
                      onChange={(e) => setManualText(e.target.value)}
                      className="w-full h-9 px-3 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] outline-none text-[13px] text-slate-800 dark:text-slate-100 focus:border-blue-500"
                    />
                  </label>
                  <label className="block">
                    <span className="block mb-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase">
                      URL
                    </span>
                    <input
                      value={manualHref}
                      onChange={(e) => setManualHref(e.target.value)}
                      placeholder="/contact"
                      className="w-full h-9 px-3 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] outline-none text-[13px] text-slate-800 dark:text-slate-100 placeholder:text-slate-400 focus:border-blue-500"
                    />
                  </label>
                </div>
              ) : (
                <label className="block">
                  <span className="block mb-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase">
                    Text
                  </span>
                  <textarea
                    value={manualText}
                    onChange={(e) => setManualText(e.target.value)}
                    rows={4}
                    className="w-full resize-none px-3 py-2 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] outline-none text-[13px] leading-5 text-slate-800 dark:text-slate-100 focus:border-blue-500"
                  />
                </label>
              )}

              <div className="flex items-center justify-end gap-2 mt-3">
                <button
                  type="button"
                  onClick={() => setMode('actions')}
                  className="h-8 px-3 rounded-xl text-[12px] font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-[#21262d] transition-colors">
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!manualSupported}
                  className={cn(
                    'h-8 px-3 inline-flex items-center gap-1.5 rounded-xl text-[12px] font-semibold transition-colors',
                    manualSupported
                      ? 'bg-blue-600 hover:bg-blue-700 text-white'
                      : 'bg-slate-100 dark:bg-[#21262d] text-slate-400 cursor-not-allowed',
                  )}>
                  <Check className="w-3.5 h-3.5" />
                  Apply
                </button>
              </div>
            </form>
          ) : (
            <form
              onSubmit={handleSubmit}
              className="flex items-center gap-2 h-11 pl-2 pr-1.5 rounded-2xl bg-white dark:bg-[#1c2128] border border-slate-200 dark:border-[#2d333b] shadow-[0_8px_24px_rgba(15,23,42,0.12)]"
              style={{ width: 440 }}>
              <button
                type="button"
                onClick={() => setMode('actions')}
                className="w-8 h-8 inline-flex items-center justify-center rounded-xl text-slate-500 hover:text-slate-800 hover:bg-slate-100 dark:hover:bg-[#21262d] transition-colors"
                title="Back to actions">
                <ArrowLeft className="w-4 h-4" />
              </button>
              <input
                ref={inputRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleInputKey}
                placeholder="What to change?"
                className="flex-1 h-8 bg-transparent outline-none text-[13px] text-slate-800 dark:text-slate-100 placeholder:text-slate-400"
              />
              <button
                type="submit"
                disabled={!draft.trim()}
                className={cn(
                  'w-8 h-8 inline-flex items-center justify-center rounded-xl transition-colors',
                  draft.trim()
                    ? 'bg-blue-600 hover:bg-blue-700 text-white'
                    : 'bg-slate-100 dark:bg-[#21262d] text-slate-400 cursor-not-allowed',
                )}
                title="Send to agent">
                <ArrowUp className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={() => onClose?.()}
                className="w-8 h-8 inline-flex items-center justify-center rounded-xl text-slate-500 hover:text-slate-800 hover:bg-slate-100 dark:hover:bg-[#21262d] transition-colors"
                title="Close">
                <X className="w-4 h-4" />
              </button>
            </form>
          )}
        </div>
      )}

      {/* "Click any element" banner when select mode is on but nothing
          has been picked yet. Hidden the moment a selection arrives. */}
      {showBanner && bannerStyle && (
        <div style={bannerStyle}>
          <div className="px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-semibold shadow-lg">
            Click any element to edit it
          </div>
        </div>
      )}
    </>
  );
}
