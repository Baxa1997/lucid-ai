'use client';

// ─────────────────────────────────────────────────────────
//  ChatInput — Textarea + send button + tools + attachments
//  Used inside AgentPanel's chat tab
// ─────────────────────────────────────────────────────────

import { useRef, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Send } from 'lucide-react';
import ToolsDropdown from './ToolsDropdown';
import AttachmentPreview from './AttachmentPreview';
import { getStatusConfig } from './StatusBadge';

export default function ChatInput({ state, onSendMessage, isRunning }) {
  const [chatInput, setChatInput] = useState('');
  const [attachedImages, setAttachedImages] = useState([]);
  const [showToolsMenu, setShowToolsMenu] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [showFigmaInput, setShowFigmaInput] = useState(false);
  const [figmaUrl, setFigmaUrl] = useState('');
  // Visual feedback while a file is being dragged over the composer.
  const [isDragging, setIsDragging] = useState(false);

  const fileInputRef = useRef(null);
  const videoInputRef = useRef(null);

  const currentStatus = getStatusConfig(state);

  // ── Send ──────────────────────────────────────────────
  const handleSend = (e) => {
    e.preventDefault();
    if (!chatInput.trim() && attachedImages.length === 0) return;
    onSendMessage?.(chatInput.trim(), attachedImages);
    setChatInput('');
    setAttachedImages([]);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(e); }
  };

  // ── File handlers ─────────────────────────────────────
  // Shared file-ingest path used by the picker, clipboard paste, and
  // drag-and-drop. `kind` is the attachment type label we tag on each
  // entry so AttachmentPreview can render the right icon.
  const ingestFiles = useCallback((fileList, kind /* "image" | "video" */) => {
    const mimePrefix = kind === 'video' ? 'video/' : 'image/';
    Array.from(fileList || []).forEach((file) => {
      if (!file?.type?.startsWith(mimePrefix)) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size, type: kind }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => setAttachedImages((prev) =>
        prev.map((img) => img.file === file ? { ...img, data: ev.target.result } : img),
      );
      reader.readAsDataURL(file);
    });
  }, []);

  const handleImageSelect = (e) => {
    ingestFiles(e.target.files, 'image');
    if (fileInputRef.current) fileInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  const handleVideoSelect = (e) => {
    ingestFiles(e.target.files, 'video');
    if (videoInputRef.current) videoInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  // ── Paste handler ─────────────────────────────────────
  // Cmd/Ctrl+V of a screenshot (or any image on the clipboard) should
  // attach the same way the file picker does. preventDefault only when
  // we actually find image items so plain-text paste keeps working.
  const handlePaste = useCallback((e) => {
    const items = Array.from(e.clipboardData?.items || []);
    const imageItems = items.filter(it => it.kind === 'file' && it.type.startsWith('image/'));
    if (imageItems.length === 0) return;
    e.preventDefault();
    const files = imageItems.map(it => it.getAsFile()).filter(Boolean);
    ingestFiles(files, 'image');
  }, [ingestFiles]);

  // ── Drag-and-drop handlers ────────────────────────────
  // Files dropped anywhere on the composer get attached. We toggle a
  // visual border on dragenter/leave so the user sees a valid drop zone.
  const handleDragOver = useCallback((e) => {
    // dataTransfer.types is the only thing we're allowed to inspect during
    // a drag (security restriction in some browsers). Bail early when the
    // drag obviously isn't a file (text selection, link, etc.) so we don't
    // hijack drags meant for other surfaces.
    const types = e.dataTransfer?.types;
    if (!types || ![...types].includes('Files')) return;
    e.preventDefault();
    if (!isDragging) setIsDragging(true);
  }, [isDragging]);

  const handleDragLeave = useCallback((e) => {
    // Only clear when the cursor leaves the form, not when crossing
    // between child elements (which fires spurious leave events).
    if (e.currentTarget.contains(e.relatedTarget)) return;
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    setIsDragging(false);
    const files = Array.from(e.dataTransfer.files);
    const images = files.filter(f => f.type.startsWith('image/'));
    const videos = files.filter(f => f.type.startsWith('video/'));
    if (images.length) ingestFiles(images, 'image');
    if (videos.length) ingestFiles(videos, 'video');
  }, [ingestFiles]);

  const handleFigmaSubmit = (e) => {
    if (e?.preventDefault) e.preventDefault();
    if (!figmaUrl.trim()) return;
    setAttachedImages((prev) => {
      if (prev.length >= 5) return prev;
      return [...prev, { name: 'Figma Design', url: figmaUrl, size: 0, type: 'figma' }];
    });
    setFigmaUrl('');
    setShowFigmaInput(false);
    setShowToolsMenu(false);
  };

  const removeImage = useCallback((index) => {
    setAttachedImages((prev) => prev.filter((_, i) => i !== index));
  }, []);

  return (
    <form
      onSubmit={handleSend}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        'shrink-0 border-t bg-white transition-colors relative',
        isDragging ? 'border-blue-400 bg-blue-50/50' : 'border-slate-200',
      )}
    >
      {/* Drop-zone overlay shown while files are being dragged over */}
      {isDragging && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-blue-50/80 z-10 rounded-t-lg">
          <span className="text-sm font-medium text-blue-700">Drop image to attach</span>
        </div>
      )}

      {/* Attachment previews */}
      <AttachmentPreview
        attachedImages={attachedImages}
        onRemove={removeImage}
        onAddMore={() => fileInputRef.current?.click()}
      />

      {/* Textarea row */}
      <div className="px-3 pt-2 pb-1">
        <div className="flex items-end gap-1.5 bg-slate-50 border border-slate-200 rounded-2xl px-1 py-1 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500/20 transition-all">
          <textarea
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={
              isRunning ? 'Agent is working — your message will be queued or answered…'
                : (state === 'connected' || state === 'ready' || state === 'idle') ? 'What do you want to build? (paste or drop images)'
                : 'Waiting for connection...'
            }
            rows={1}
            className="flex-1 min-w-0 px-3 py-2 text-sm bg-transparent outline-none text-slate-800 placeholder:text-slate-400 resize-none min-h-[36px] max-h-[120px]"
            style={{ overflowWrap: 'anywhere', wordBreak: 'break-word' }}
          />
          <button
            type="submit"
            disabled={!chatInput.trim() && attachedImages.length === 0}
            className={cn(
              'p-2 rounded-xl transition-all shrink-0 mb-0.5',
              (chatInput.trim() || attachedImages.length > 0)
                ? 'bg-slate-600 text-white hover:bg-slate-700'
                : 'bg-transparent text-slate-300 cursor-not-allowed',
            )}
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Toolbar row */}
      <div className="flex items-center gap-2 px-4 pb-2.5 pt-1">
        <ToolsDropdown
          isOpen={showToolsMenu}
          onToggle={() => { setShowToolsMenu((v) => !v); setShowFigmaInput(false); }}
          onClose={() => setShowToolsMenu(false)}
          onAddFiles={() => fileInputRef.current?.click()}
          onAddVideo={() => videoInputRef.current?.click()}
          showFigmaInput={showFigmaInput}
          onShowFigma={() => setShowFigmaInput(true)}
          figmaUrl={figmaUrl}
          onFigmaUrlChange={setFigmaUrl}
          onFigmaSubmit={handleFigmaSubmit}
          webSearchEnabled={webSearchEnabled}
          onToggleWebSearch={() => { setWebSearchEnabled((v) => !v); setShowToolsMenu(false); }}
          onAddContext={() => { setChatInput((prev) => prev + (prev.endsWith(' ') || !prev ? '' : ' ') + '@'); setShowToolsMenu(false); }}
        />

        <input ref={fileInputRef} type="file" accept="image/*" multiple onChange={handleImageSelect} className="hidden" />
        <input ref={videoInputRef} type="file" accept="video/*" multiple onChange={handleVideoSelect} className="hidden" />

        <div className="flex-1" />

        {/* Status */}
        <div className="flex items-center gap-1.5">
          <div className={cn('w-1.5 h-1.5 rounded-full', currentStatus.dot)} />
          <span className="text-[10px] text-slate-400">{currentStatus.label}</span>
        </div>
        <span className="text-[10px] text-slate-300">↵</span>
      </div>
    </form>
  );
}
