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
  const handleImageSelect = (e) => {
    Array.from(e.target.files || []).forEach((file) => {
      if (!file.type.startsWith('image/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size, type: 'image' }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => setAttachedImages((prev) =>
        prev.map((img) => img.file === file ? { ...img, data: ev.target.result } : img),
      );
      reader.readAsDataURL(file);
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  const handleVideoSelect = (e) => {
    Array.from(e.target.files || []).forEach((file) => {
      if (!file.type.startsWith('video/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size, type: 'video' }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => setAttachedImages((prev) =>
        prev.map((img) => img.file === file ? { ...img, data: ev.target.result } : img),
      );
      reader.readAsDataURL(file);
    });
    if (videoInputRef.current) videoInputRef.current.value = '';
    setShowToolsMenu(false);
  };

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
    <form onSubmit={handleSend} className="shrink-0 border-t border-slate-200 bg-white">
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
            placeholder={
              isRunning ? 'Agent is working...'
                : (state === 'connected' || state === 'ready' || state === 'idle') ? 'What do you want to build?'
                : 'Waiting for connection...'
            }
            rows={1}
            className="flex-1 px-3 py-2 text-sm bg-transparent outline-none text-slate-800 placeholder:text-slate-400 resize-none min-h-[36px] max-h-[120px]"
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
