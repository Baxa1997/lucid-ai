'use client';

// ─────────────────────────────────────────────────────────
//  ChatPanel — Left-side chat area: message stream + input bar.
//  Extracted from workspace/[projectId]/page.js.
//  Reads shared workspace state via useWorkspace().
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  ArrowDown, ArrowRight, Loader2, Sparkles,
  Settings, Plus, Paperclip, Video, Layers, Globe, Check,
  Mic, MousePointer2, MessageCircle, Lightbulb, X, FileImage,
} from 'lucide-react';
import { useWorkspace } from '@/contexts/WorkspaceContext';
import MessageBubble from '@/components/workspace/MessageBubble';
import {
  addMessage as saveMessage,
  updateConversation,
  saveChatMessage,
} from '@/lib/conversations';

export default function ChatPanel() {
  const {
    messages,
    status,
    isPreparing,
    phases,
    stopSession,
    sendMessage,
    conversation,
    conversationId,
    wizardDesc,
    resolvingProgress,
    setRightPanel,
    panelOverrideRef,
    convLoading,
    isWizardMode,
    setConversation,
    agentStatus,
    // Click-to-edit (Base44) — chip + WS payload field
    editSelection,
    clearEditSelection,
  } = useWorkspace();

  // ── Chat-local state ────────────────────────────────────
  const [chatInput, setChatInput] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [showToolsMenu, setShowToolsMenu] = useState(false);
  const [attachedImages, setAttachedImages] = useState([]);
  const [previewImage, setPreviewImage] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);
  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  const [showFigmaInput, setShowFigmaInput] = useState(false);
  const [figmaUrl, setFigmaUrl] = useState('');
  const [chatMode, setChatMode] = useState('edit');
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef(null);

  const chatEndRef = useRef(null);
  const chatContainerRef = useRef(null);
  const fileInputRef = useRef(null);
  const videoInputRef = useRef(null);
  const toolsMenuRef = useRef(null);
  const userScrolledUpRef = useRef(false);
  const lastScrolledMsgIdRef = useRef(null);

  // ── Auto-scroll on new tail message ────────────────────
  useEffect(() => {
    if (messages.length === 0) return;
    const lastMsg = messages[messages.length - 1];
    if (lastMsg.id === lastScrolledMsgIdRef.current) return;
    lastScrolledMsgIdRef.current = lastMsg.id;
    if (userScrolledUpRef.current) return;
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ── Close tools menu on outside click ──────────────────
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (toolsMenuRef.current && !toolsMenuRef.current.contains(e.target)) {
        setShowToolsMenu(false);
      }
    };
    if (showToolsMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showToolsMenu]);

  // ── Handlers ────────────────────────────────────────────
  const handleSend = async (e) => {
    e.preventDefault();
    if (!chatInput.trim() && attachedImages.length === 0) return;
    panelOverrideRef.current = true;
    const text = chatInput.trim();
    // Attach any pending click-to-edit selection. Backend reads
    // ``editable_target`` to skip extractor vocab and route straight
    // to the direct-edit fast path with a 100%-confidence EditIntent.
    const sendOptions = {
      mode: chatMode,
      webSearch: webSearchEnabled,
      ...(editSelection ? { editableTarget: editSelection } : {}),
    };
    sendMessage(text, attachedImages, sendOptions);
    setChatInput('');
    setAttachedImages([]);
    if (editSelection) clearEditSelection?.();

    if (conversation?.id && text) {
      saveChatMessage(conversationId, { role: 'user', content: text });
      await saveMessage(conversation.id, { role: 'user', content: text });

      if (conversation.title === 'New Conversation') {
        (async () => {
          try {
            const res = await fetch('/api/generate-title', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ message: text, repoName: conversation.repo_name || '' }),
            });
            const { title } = await res.json();
            if (title && title !== 'New Conversation') {
              await updateConversation(conversation.id, { title });
              setConversation((prev) => ({ ...prev, title }));
            }
          } catch {
            const fallback = text.length > 50 ? text.slice(0, 50).replace(/\s+\S*$/, '…') : text;
            await updateConversation(conversation.id, { title: fallback });
            setConversation((prev) => ({ ...prev, title: fallback }));
          }
        })();
      }
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend(e);
    }
  };

  const handleImageSelect = (e) => {
    const files = Array.from(e.target.files || []);
    files.forEach((file) => {
      if (!file.type.startsWith('image/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) => (img.file === file ? { ...img, data: ev.target.result } : img)),
        );
      };
      reader.readAsDataURL(file);
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  const handleVideoSelect = (e) => {
    const files = Array.from(e.target.files || []);
    files.forEach((file) => {
      if (!file.type.startsWith('video/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size, type: 'video' }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) => (img.file === file ? { ...img, data: ev.target.result } : img)),
        );
      };
      reader.readAsDataURL(file);
    });
    if (videoInputRef.current) videoInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  const handleVoice = useCallback(() => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    if (isListening) {
      recognitionRef.current?.stop();
      setIsListening(false);
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';
    recognition.onstart = () => setIsListening(true);
    recognition.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      setChatInput((prev) => prev ? `${prev} ${transcript}` : transcript);
    };
    recognition.onerror = () => setIsListening(false);
    recognition.onend = () => setIsListening(false);
    recognitionRef.current = recognition;
    recognition.start();
  }, [isListening]);

  const handleFigmaSubmit = () => {
    if (!figmaUrl.trim()) return;
    setAttachedImages((prev) => {
      if (prev.length >= 5) return prev;
      return [...prev, { name: 'Figma Design', data: figmaUrl.trim(), size: 0, type: 'figma', url: figmaUrl.trim() }];
    });
    setFigmaUrl('');
    setShowFigmaInput(false);
    setShowToolsMenu(false);
  };

  const removeImage = (index) => {
    setAttachedImages((prev) => prev.filter((_, i) => i !== index));
  };

  const formatFileSize = (bytes) => {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleChatScroll = (e) => {
    if (e.target.scrollLeft !== 0) e.target.scrollLeft = 0;
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    setShowScrollBtn(distFromBottom > 100);
    userScrolledUpRef.current = distFromBottom >= 100;
  };

  // ── Drag handlers ───────────────────────────────────────
  const handleDragEnter = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (e.dataTransfer?.types?.includes('Files')) setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragging(false);
    }
  }, []);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    dragCounterRef.current = 0;
    const files = Array.from(e.dataTransfer?.files || []);
    files.forEach((file) => {
      if (!file.type.startsWith('image/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name, data: null, file, size: file.size }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) => (img.file === file ? { ...img, data: ev.target.result } : img)),
        );
      };
      reader.readAsDataURL(file);
    });
  }, []);

  // ── Paste handler ───────────────────────────────────────
  // Cmd/Ctrl+V of a clipboard image (e.g. a screenshot) attaches the
  // image like the picker / drop paths. preventDefault only when an
  // image item is present so plain-text paste keeps working normally.
  const handlePaste = useCallback((e) => {
    const items = Array.from(e.clipboardData?.items || []);
    const imageItems = items.filter(
      (it) => it.kind === 'file' && it.type.startsWith('image/'),
    );
    if (imageItems.length === 0) return;
    e.preventDefault();
    imageItems.forEach((it) => {
      const file = it.getAsFile();
      if (!file) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev;
        return [...prev, { name: file.name || 'pasted-image', data: null, file, size: file.size }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) => (img.file === file ? { ...img, data: ev.target.result } : img)),
        );
      };
      reader.readAsDataURL(file);
    });
  }, []);

  // ── Render ──────────────────────────────────────────────
  return (
    <>
      {/* Full-page drag-and-drop overlay (fixed, covers whole viewport) */}
      {isDragging && (
        <div
          className="fixed inset-0 z-[100] bg-orange-500/10 dark:bg-orange-500/15 backdrop-blur-[2px] border-2 border-dashed border-orange-400 dark:border-orange-500 flex items-center justify-center"
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}>
          <div className="flex flex-col items-center gap-3 bg-white/90 dark:bg-slate-900/90 rounded-2xl px-8 py-6 shadow-xl border border-orange-200 dark:border-orange-800">
            <div className="w-14 h-14 rounded-2xl bg-orange-50 dark:bg-orange-500/15 border border-orange-200 dark:border-orange-700 flex items-center justify-center">
              <FileImage className="w-6 h-6 text-[#dc5426]" />
            </div>
            <p className="text-sm font-bold text-slate-800 dark:text-slate-200">Drop images here</p>
            <p className="text-xs text-slate-400 dark:text-slate-500">PNG, JPG, GIF up to 5 files</p>
          </div>
        </div>
      )}

      {/* Chat stream */}
      <div
        className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-3 bg-[#f8f9fc] dark:bg-[#0d1117] custom-scrollbar"
        ref={chatContainerRef}
        onScroll={handleChatScroll}
        onDragEnter={handleDragEnter}>
        <div className="space-y-4">
          {convLoading && messages.length === 0 && !isWizardMode && (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-5 h-5 text-slate-300 dark:text-slate-600 animate-spin" />
            </div>
          )}

          {!convLoading && messages.length === 0 && status === 'ready' && !isWizardMode && (
            <div className="flex flex-col items-center justify-center py-20 text-center px-6">
              <div className="w-10 h-10 rounded-2xl bg-gradient-to-br from-[#dc5426] to-orange-600 flex items-center justify-center mb-4 shadow-sm shadow-orange-500/20">
                <Sparkles className="w-5 h-5 text-white" />
              </div>
              <p className="text-[14px] font-medium text-slate-500 dark:text-slate-400">
                Workspace ready. What would you like to build?
              </p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={msg.id} className={msg.fromHistory ? undefined : 'animate-msg-in'}>
              <MessageBubble msg={msg} isLatest={i === messages.length - 1} />
            </div>
          ))}

          <div ref={chatEndRef} className="h-4" />
        </div>
      </div>

      {/* Status bar — pinned above suggestions, always visible when agent is active */}
      {(() => {
        const ALL_ACTIVE = ['connecting', 'preparing', 'cloning', 'installing', 'starting', 'health_check', 'running'];
        if (!ALL_ACTIVE.includes(status)) return null;
        const activePhase = (phases || []).find((p) => p.status === 'active');
        const maxDonePhase = (phases || [])
          .filter((p) => p.status === 'done')
          .reduce((max, p) => Math.max(max, p.phase || 0), 0);
        const currentPhaseNum = activePhase?.phase || maxDonePhase || 0;
        const PHASE_LABELS = {
          0: 'Thinking…',
          1: 'Building app...',
          2: 'Preparing workspace...',
          3: 'Researching your idea...',
          4: activePhase?.title?.toLowerCase().includes('design')
              ? 'Choosing design style...'
              : 'Planning your code...',
          5: 'Writing your code...',
          6: 'Verifying build...',
          7: 'Publishing project...',
        };
        const phaseLabel = currentPhaseNum > 0
          ? (PHASE_LABELS[currentPhaseNum] || PHASE_LABELS[currentPhaseNum >= 8 ? 7 : 0])
          : (wizardDesc
              ? `Designing your ${wizardDesc.length > 28 ? wizardDesc.slice(0, 28) + '…' : wizardDesc.toLowerCase()}...`
              : 'Thinking…');
        const LABELS = {
          connecting: 'Connecting to workspace...',
          preparing: resolvingProgress?.message || 'Preparing workspace...',
          cloning: 'Cloning repository...',
          installing: 'Installing dependencies...',
          starting: 'Starting dev server...',
          health_check: 'Connecting live preview...',
          running: phaseLabel,
        };
        const primary =
          (status === 'running' && agentStatus?.label) ||
          LABELS[status] ||
          'Thinking…';
        const secondary = status === 'running' ? (agentStatus?.subtext || '') : '';
        return (
          <div className="shrink-0 flex items-center gap-2.5 px-4 py-2 border-t border-[#e3e5eb] dark:border-[#1c2128] bg-[#f8f9fc] dark:bg-[#0d1117] animate-in fade-in duration-300">
            <div className="w-5 h-5 rounded-full bg-gradient-to-br from-[#dc5426] to-orange-600 flex items-center justify-center shrink-0 shadow-sm shadow-orange-500/15">
              <Sparkles className="w-2.5 h-2.5 text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-[12px] text-slate-500 dark:text-slate-400 truncate">{primary}</span>
                <div className="flex items-center gap-[3px] shrink-0">
                  {[0, 200, 400].map((delay) => (
                    <span
                      key={delay}
                      className="w-1 h-1 rounded-full bg-orange-400/70 animate-bounce"
                      style={{ animationDelay: `${delay}ms`, animationDuration: '1s' }}
                    />
                  ))}
                </div>
              </div>
              {secondary && (
                <span className="block text-[11px] text-slate-400 dark:text-slate-500 truncate">{secondary}</span>
              )}
            </div>
          </div>
        );
      })()}

      {/* Suggestions bar */}
      {messages.length > 0 && (
        <div className="shrink-0 px-3 py-2 bg-[#f8f9fc] dark:bg-[#0d1117] border-t border-[#e3e5eb] dark:border-[#1c2128] relative">
          {showScrollBtn && (
            <div className="absolute -top-8 left-1/2 -translate-x-1/2 z-10">
              <button
                onClick={() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })}
                className="bg-white dark:bg-[#161b22] text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-[#2d333b] px-3 py-1 rounded-full text-[11px] font-semibold shadow-md flex items-center gap-1.5 hover:bg-slate-50 dark:hover:bg-[#21262d] transition-colors">
                <ArrowDown className="w-3 h-3" /> Latest messages
              </button>
            </div>
          )}
          <div className="flex items-center gap-1.5 mb-1.5">
            <Lightbulb className="w-3 h-3 text-[#9ca3af]" />
            <span className="text-[11px] font-medium text-[#9ca3af] dark:text-slate-500">Suggestions</span>
          </div>
          <div className="flex items-center gap-3 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
            {['Add Admin Dashboard', 'Build Dedicated Menu', 'Improve Mobile Design'].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setChatInput(s)}
                className="text-[12px] text-[#4b5563] dark:text-slate-300 hover:text-[#1f2937] dark:hover:text-white transition-colors whitespace-nowrap shrink-0">
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input bar — also a drop target so dragging an image directly
          onto the textarea triggers the overlay (the chat-stream onDragEnter
          alone misses drops aimed at the input). */}
      <div
        className="shrink-0 bg-[#f8f9fc] dark:bg-[#0d1117] px-3 pb-3"
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}>
        {/* Click-to-edit lives entirely in the preview-side overlay
            (PreviewEditOverlay). We deliberately don't repeat the
            selection as a chip here — it would duplicate the overlay's
            tag badge and confuse the user about where to type. The
            selection is still attached to the next outbound message
            via handleSend reading editSelection from context. */}
        {attachedImages.length > 0 && (
          <div className="mb-2">
            <div className="flex gap-2 flex-wrap">
              {attachedImages.map((img, i) => (
                <div
                  key={i}
                  className="relative group flex items-center gap-2 bg-slate-50 dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-lg px-2 py-1.5">
                  <div className="relative w-8 h-8 rounded overflow-hidden shrink-0 bg-slate-200 dark:bg-[#21262d]">
                    {img.data ? (
                      <img src={img.data} alt={img.name} className="w-full h-full object-cover" />
                    ) : (
                      <Loader2 className="w-3 h-3 text-slate-400 animate-spin m-auto" />
                    )}
                  </div>
                  <span className="text-[10px] text-slate-500 truncate max-w-[60px]">{img.name}</span>
                  <button
                    type="button"
                    onClick={() => removeImage(i)}
                    className="p-0.5 text-slate-400 hover:text-red-500 rounded transition-colors">
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <form onSubmit={handleSend} className="relative">
          <div
            className="rounded-2xl overflow-hidden bg-white dark:bg-[#1c2128] shadow-sm"
            style={{ border: '1px solid #e5e7eb' }}>
            <textarea
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder="What would you like to change? (paste or drop images)"
              className="w-full px-4 pt-3.5 pb-8 min-h-[80px] max-h-[240px] outline-none text-[13px] text-[#1f2937] dark:text-slate-100 placeholder:text-[#9ca3af] dark:placeholder:text-slate-500 resize-none leading-relaxed break-words"
              style={{ background: 'transparent', wordBreak: 'break-word', overflowWrap: 'break-word' }}
              rows={2}
            />
          </div>

          <div className="flex items-center justify-between px-1 pt-2.5">
            <div className="flex items-center gap-1">
              {/* Tools menu */}
              <div className="relative" ref={toolsMenuRef}>
                <button
                  type="button"
                  onClick={() => { setShowToolsMenu(!showToolsMenu); setShowFigmaInput(false); }}
                  className="p-1.5 text-[#6b7280] hover:text-[#374151] dark:text-slate-400 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/[0.06] rounded-md transition-colors"
                  title="Tools">
                  <Settings className="w-4 h-4" />
                </button>
                {showToolsMenu && (
                  <div className="absolute bottom-full left-0 mb-2 w-56 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl shadow-2xl overflow-hidden z-30 animate-in fade-in slide-in-from-bottom-2 duration-200">
                    <div className="py-1">
                      <button type="button" onClick={() => { fileInputRef.current?.click(); setShowToolsMenu(false); }} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left">
                        <Paperclip className="w-4 h-4 text-slate-400" />
                        <span className="font-medium">Add files or photos</span>
                      </button>
                      <button type="button" onClick={() => { videoInputRef.current?.click(); setShowToolsMenu(false); }} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left">
                        <Video className="w-4 h-4 text-slate-400" />
                        <span className="font-medium">Add video</span>
                      </button>
                      <button type="button" onClick={() => setShowFigmaInput(!showFigmaInput)} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] text-left">
                        <Layers className="w-4 h-4 text-slate-400" />
                        <span className="font-medium">Paste Figma link</span>
                      </button>
                    </div>
                    <div className="border-t border-slate-100 dark:border-[#2d333b]" />
                    <div className="py-1">
                      <button type="button" onClick={() => setWebSearchEnabled(!webSearchEnabled)} className="w-full flex items-center gap-3 px-3 py-2.5 text-[13px] text-left hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                        <Globe className={cn('w-4 h-4', webSearchEnabled ? 'text-[#dc5426]' : 'text-slate-400')} />
                        <span className={cn('flex-1 font-medium', webSearchEnabled ? 'text-[#dc5426] dark:text-orange-400' : 'text-slate-700 dark:text-slate-300')}>
                          Web search
                        </span>
                        {webSearchEnabled && <Check className="w-4 h-4 text-[#dc5426]" />}
                      </button>
                    </div>
                    {showFigmaInput && (
                      <div className="px-3 pb-2 pt-1 border-t border-slate-100 dark:border-[#2d333b]">
                        <input
                          type="url"
                          value={figmaUrl}
                          onChange={(e) => setFigmaUrl(e.target.value)}
                          placeholder="https://figma.com/file/..."
                          className="w-full text-[12px] px-2.5 py-1.5 rounded-lg border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] text-slate-700 dark:text-slate-300 outline-none focus:border-[#dc5426]"
                          onKeyDown={(e) => e.key === 'Enter' && handleFigmaSubmit()}
                        />
                        <button
                          type="button"
                          onClick={handleFigmaSubmit}
                          className="mt-1.5 w-full text-[12px] font-semibold bg-[#dc5426] text-white rounded-lg py-1.5 hover:bg-[#b8421e] transition-colors">
                          Add Figma link
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Plus button */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="p-1.5 text-[#6b7280] hover:text-[#374151] dark:text-slate-400 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/[0.06] rounded-md transition-colors"
                title="Add files">
                <Plus className="w-4 h-4" />
              </button>

              <div className="h-4 w-px bg-[#e5e7eb] dark:bg-[#2d333b] mx-0.5" />

              {/* Edit mode toggle */}
              <button
                type="button"
                onClick={() => setChatMode('edit')}
                className={cn(
                  'flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] font-medium transition-all',
                  chatMode !== 'discuss'
                    ? 'bg-[#f3f4f6] dark:bg-[#21262d] text-[#111827] dark:text-white'
                    : 'text-[#6b7280] dark:text-slate-400 hover:text-[#374151] dark:hover:text-slate-300 hover:bg-[#f3f4f6]/60 dark:hover:bg-white/[0.04]',
                )}>
                <MousePointer2 className="w-3.5 h-3.5" />
                Edit
              </button>

              {/* Discuss mode toggle */}
              <button
                type="button"
                onClick={() => setChatMode(chatMode === 'discuss' ? 'edit' : 'discuss')}
                className={cn(
                  'flex items-center gap-1 px-2.5 py-1 rounded-md text-[12px] font-medium transition-all',
                  chatMode === 'discuss'
                    ? 'bg-[#f3f4f6] dark:bg-[#21262d] text-[#111827] dark:text-white'
                    : 'text-[#6b7280] dark:text-slate-400 hover:text-[#374151] dark:hover:text-slate-300 hover:bg-[#f3f4f6]/60 dark:hover:bg-white/[0.04]',
                )}>
                <MessageCircle className="w-3.5 h-3.5" />
                Discuss
              </button>
            </div>

            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={handleVoice}
                className={cn(
                  'p-1.5 rounded-md transition-colors',
                  isListening
                    ? 'text-red-500 bg-red-50 dark:bg-red-900/20 animate-pulse'
                    : 'text-[#6b7280] hover:text-[#374151] dark:text-slate-400 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/[0.06]',
                )}
                title={isListening ? 'Stop listening' : 'Voice input'}>
                <Mic className="w-4 h-4" />
              </button>

              {status === 'running' || isPreparing ? (
                <button
                  type="button"
                  onClick={stopSession}
                  className="w-8 h-8 rounded-lg bg-[#111827] text-white hover:bg-slate-900 transition-all flex items-center justify-center shrink-0"
                  title="Stop processing">
                  <div className="w-2.5 h-2.5 bg-white rounded-sm" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!chatInput.trim() && attachedImages.length === 0}
                  className={cn(
                    'w-8 h-8 rounded-lg transition-all flex items-center justify-center shrink-0',
                    chatInput.trim() || attachedImages.length > 0
                      ? 'bg-[#111827] text-white hover:bg-slate-900'
                      : 'bg-[#d1d5db] text-[#9ca3af] cursor-not-allowed',
                  )}>
                  <ArrowRight className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          <input ref={fileInputRef} type="file" accept="image/*" multiple onChange={handleImageSelect} className="hidden" />
          <input ref={videoInputRef} type="file" accept="video/*" multiple onChange={handleVideoSelect} className="hidden" />
        </form>

        {previewImage && (
          <div
            className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 animate-in fade-in duration-200"
            onClick={() => setPreviewImage(null)}>
            <div className="relative max-w-[80vw] max-h-[80vh]" onClick={(e) => e.stopPropagation()}>
              <img src={previewImage.data} alt={previewImage.name} className="max-w-full max-h-[80vh] rounded-xl shadow-2xl" />
              <button
                onClick={() => setPreviewImage(null)}
                className="absolute -top-3 -right-3 w-8 h-8 bg-white text-slate-600 rounded-full flex items-center justify-center shadow-lg hover:bg-slate-100 transition-colors">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
