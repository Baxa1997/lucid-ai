'use client';

// ─────────────────────────────────────────────────────────
//  ToolsDropdown — "+Tools" dropdown menu for ChatInput
//  Image / Video / Figma link / Web search / Context
// ─────────────────────────────────────────────────────────

import { useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';
import {
  Plus, Paperclip, Video, Link2, Camera, Globe, Layers,
} from 'lucide-react';

export default function ToolsDropdown({
  isOpen,
  onToggle,
  onClose,
  onAddFiles,
  onAddVideo,
  showFigmaInput,
  onShowFigma,
  figmaUrl,
  onFigmaUrlChange,
  onFigmaSubmit,
  webSearchEnabled,
  onToggleWebSearch,
  onAddContext,
}) {
  const menuRef = useRef(null);

  // Close on outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        onClose();
      }
    };
    if (isOpen) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, onClose]);

  return (
    <div className="relative" ref={menuRef}>
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          'flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all',
          isOpen
            ? 'bg-slate-200 text-slate-700'
            : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700 cursor-pointer',
        )}
      >
        <Plus className="w-3.5 h-3.5" />
        Tools
      </button>

      {isOpen && (
        <div className="absolute bottom-full left-0 mb-2 w-64 bg-white border border-slate-200 rounded-2xl shadow-2xl overflow-hidden z-30 animate-in fade-in slide-in-from-bottom-2 duration-200">
          {/* Files group */}
          <div className="py-1.5">
            <button
              type="button"
              onClick={onAddFiles}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
            >
              <Paperclip className="w-4 h-4 text-slate-500" />
              <span className="font-medium">Add files or photos</span>
            </button>
            <button
              type="button"
              onClick={onAddVideo}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
            >
              <Video className="w-4 h-4 text-slate-500" />
              <span className="font-medium">Add video</span>
            </button>

            {!showFigmaInput ? (
              <button
                type="button"
                onClick={onShowFigma}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
              >
                <Link2 className="w-4 h-4 text-slate-500" />
                <span className="font-medium">Paste Figma link</span>
              </button>
            ) : (
              <div className="px-3 py-2">
                <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-2 py-1.5 focus-within:ring-2 focus-within:ring-blue-500/20">
                  <input
                    type="url"
                    value={figmaUrl}
                    onChange={(e) => onFigmaUrlChange(e.target.value)}
                    placeholder="https://figma.com/..."
                    className="w-full bg-transparent outline-none text-[13px] text-slate-800 placeholder:text-slate-400"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); onFigmaSubmit(e); }
                    }}
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={onFigmaSubmit}
                    className="text-white bg-blue-500 hover:bg-blue-600 rounded px-2 py-0.5 text-[11px] font-medium transition-colors"
                  >
                    Add
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="h-px bg-slate-100 mx-4" />

          {/* Capture group */}
          <div className="py-1.5">
            <button
              type="button"
              className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
            >
              <Camera className="w-4 h-4 text-slate-500" />
              <span className="font-medium">Take a screenshot</span>
            </button>
          </div>

          <div className="h-px bg-slate-100 mx-4" />

          {/* Context group */}
          <div className="py-1.5">
            <button
              type="button"
              onClick={onToggleWebSearch}
              className="w-full flex items-center justify-between px-4 py-2.5 text-[13px] hover:bg-slate-50 transition-colors"
            >
              <div className="flex items-center gap-3 text-slate-800">
                <Globe className="w-4 h-4 text-slate-500" />
                <span className="font-medium">Web search</span>
              </div>
              <div className={cn(
                'w-7 h-4 rounded-full flex items-center px-0.5 transition-colors',
                webSearchEnabled ? 'bg-blue-500' : 'bg-slate-300',
              )}>
                <div className={cn(
                  'w-3 h-3 bg-white rounded-full shadow-sm transition-transform',
                  webSearchEnabled ? 'translate-x-3' : 'translate-x-0',
                )} />
              </div>
            </button>
            <button
              type="button"
              onClick={onAddContext}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
            >
              <Layers className="w-4 h-4 text-slate-500" />
              <span className="font-medium">Add context</span>
            </button>
          </div>
        </div>
      )}

    </div>
  );
}
