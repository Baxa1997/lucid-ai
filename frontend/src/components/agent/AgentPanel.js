'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — AgentPanel (Production)
//  Chat + Terminal tabs with rich message rendering,
//  pipeline progress, typing indicator, and image upload.
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';
import {
  Send, Terminal, MessageSquare, Bot, User, Cpu,
  StopCircle, Trash2, Circle, ArrowDown, Paperclip, X,
  Image as ImageIcon, Loader2, Settings, FileImage, Eye,
  Video, Globe, Camera, Link2, Layers, Plus
} from 'lucide-react';
import { MessageRenderer } from './MessageRenderer';
import TaskProgress from './TaskProgress';

/**
 * AgentPanel — Right-side panel with Chat and Terminal tabs.
 * Features: markdown rendering, tool call bubbles, pipeline progress,
 * typing indicator, image attachments.
 */
export default function AgentPanel({
  state = 'idle',
  chatMessages = [],
  logs = [],
  phases = [],
  onSendMessage,
  onSendCommand,
  onStop,
  onClearLogs,
  error,
}) {
  const [activeTab, setActiveTab] = useState('chat');
  const [chatInput, setChatInput] = useState('');
  const [terminalInput, setTerminalInput] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [attachedImages, setAttachedImages] = useState([]);
  const [showToolsMenu, setShowToolsMenu] = useState(false);
  const [previewImage, setPreviewImage] = useState(null);
  
  // New Attachment States
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [showFigmaInput, setShowFigmaInput] = useState(false);
  const [figmaUrl, setFigmaUrl] = useState('');
  
  const toolsMenuRef = useRef(null);

  const chatEndRef = useRef(null);
  const terminalEndRef = useRef(null);
  const chatContainerRef = useRef(null);
  const fileInputRef = useRef(null);
  const videoInputRef = useRef(null);

  const isRunning = state === 'running' || state === 'working';

  // ── Auto-scroll chat ──────────────────────────────────
  useEffect(() => {
    if (!showScrollBtn) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, phases, showScrollBtn]);

  // ── Auto-scroll terminal ──────────────────────────────
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // ── Track scroll position ─────────────────────────────
  const handleChatScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
    setShowScrollBtn(!isNearBottom);
  };

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    setShowScrollBtn(false);
  };

  // ── Send chat message ─────────────────────────────────
  const handleChatSend = (e) => {
    e.preventDefault();
    if (!chatInput.trim() && attachedImages.length === 0) return;
    onSendMessage?.(chatInput.trim(), attachedImages);
    setChatInput('');
    setAttachedImages([]);
  };

  // ── Send terminal command ─────────────────────────────
  const handleTerminalSend = (e) => {
    e.preventDefault();
    if (!terminalInput.trim()) return;
    onSendCommand?.(terminalInput.trim());
    setTerminalInput('');
  };

  // ── Chat keyboard handler ─────────────────────────────
  const handleChatKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleChatSend(e);
    }
  };

  const handleImageSelect = (e) => {
    const files = Array.from(e.target.files || []);
    files.forEach((file) => {
      if (!file.type.startsWith('image/')) return;
      setAttachedImages((prev) => {
        if (prev.length >= 5) return prev; // Max 5 items
        return [...prev, { name: file.name, data: null, file, size: file.size, type: 'image' }];
      });
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) =>
            img.file === file ? { ...img, data: ev.target.result } : img
          )
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
      // For video, we don't need a huge data URL immediately just for display,
      // but if the backend expects it parsed, we can read it:
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages((prev) =>
          prev.map((img) =>
            img.file === file ? { ...img, data: ev.target.result } : img
          )
        );
      };
      reader.readAsDataURL(file);
    });
    if (videoInputRef.current) videoInputRef.current.value = '';
    setShowToolsMenu(false);
  };

  const handleFigmaSubmit = (e) => {
    e.preventDefault();
    if (!figmaUrl.trim()) return;
    setAttachedImages((prev) => {
      if (prev.length >= 5) return prev;
      return [...prev, { name: 'Figma Design', url: figmaUrl, size: 0, type: 'figma' }];
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

  // ── Status badge ──────────────────────────────────────
  const statusConfig = {
    idle:       { dot: 'bg-slate-400', text: 'text-slate-500', label: 'Idle' },
    connecting: { dot: 'bg-amber-500 animate-pulse', text: 'text-amber-600', label: 'Connecting...' },
    preparing:  { dot: 'bg-amber-500 animate-pulse', text: 'text-amber-600', label: 'Preparing...' },
    connected:  { dot: 'bg-emerald-500', text: 'text-emerald-600', label: 'Connected' },
    ready:      { dot: 'bg-emerald-500', text: 'text-emerald-600', label: 'Ready' },
    running:    { dot: 'bg-blue-500 animate-pulse', text: 'text-blue-600', label: 'Working...' },
    working:    { dot: 'bg-blue-500 animate-pulse', text: 'text-blue-600', label: 'Working...' },
    error:      { dot: 'bg-red-500', text: 'text-red-600', label: 'Error' },
    stopped:    { dot: 'bg-slate-400', text: 'text-slate-500', label: 'Stopped' },
  };
  const currentStatus = statusConfig[state] || statusConfig.idle;

  // ── Log type colors ───────────────────────────────────
  const logColor = (type) => {
    const colors = {
      error: 'text-red-400',
      cmd_output: 'text-emerald-400',
      agent_message: 'text-blue-400',
      system: 'text-slate-500',
      user: 'text-amber-400',
      file_write: 'text-purple-400',
      thinking: 'text-slate-600 italic',
    };
    return colors[type] || 'text-slate-400';
  };

  return (
    <div className="h-full flex flex-col bg-white border-l border-slate-200">

      {/* ── Header ──────────────────────────────────────── */}
      <div className="shrink-0 flex items-center justify-between px-3 py-2 border-b border-slate-200 bg-slate-50/80">
        {/* Tabs */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => setActiveTab('chat')}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all",
              activeTab === 'chat'
                ? "bg-blue-50 text-blue-600 border border-blue-200"
                : "text-slate-400 hover:text-slate-600 hover:bg-slate-100"
            )}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            Chat
            {chatMessages.length > 0 && (
              <span className="ml-0.5 px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10px] tabular-nums text-slate-500">
                {chatMessages.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setActiveTab('terminal')}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all",
              activeTab === 'terminal'
                ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                : "text-slate-400 hover:text-slate-600 hover:bg-slate-100"
            )}
          >
            <Terminal className="w-3.5 h-3.5" />
            Terminal
            {logs.length > 0 && (
              <span className="ml-0.5 px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10px] tabular-nums text-slate-500">
                {logs.length}
              </span>
            )}
          </button>
        </div>

        {/* Status + Actions */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5">
            <div className={cn("w-2 h-2 rounded-full", currentStatus.dot)} />
            <span className={cn("text-[10px] font-bold uppercase tracking-wider", currentStatus.text)}>
              {currentStatus.label}
            </span>
          </div>

          {(state === 'connected' || state === 'ready' || isRunning) && (
            <button
              onClick={onStop}
              className="p-1 text-red-500 hover:text-red-700 hover:bg-red-50 rounded transition-colors"
              title="Stop session"
            >
              <StopCircle className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* ── Error Banner ────────────────────────────────── */}
      {error && (
        <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-red-50 border-b border-red-200">
          <Circle className="w-2 h-2 text-red-500 fill-red-500" />
          <p className="text-[11px] text-red-600 font-medium flex-1 truncate">{error}</p>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          CHAT TAB
      ═══════════════════════════════════════════════════ */}
      {activeTab === 'chat' && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Messages */}
          <div
            ref={chatContainerRef}
            onScroll={handleChatScroll}
            className="flex-1 overflow-y-auto px-4 py-4 space-y-4 bg-[#f5f7fa]"
          >
            {chatMessages.length === 0 && phases.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center px-6">
                <div className="w-14 h-14 rounded-2xl bg-blue-50 border border-blue-200 flex items-center justify-center mb-4">
                  <Bot className="w-6 h-6 text-blue-600" />
                </div>
                <p className="text-sm font-semibold text-slate-700">
                  {state === 'connecting' || isRunning
                    ? 'Connecting to agent...'
                    : 'Start a conversation'}
                </p>
                <p className="text-xs text-slate-400 mt-1.5 max-w-[260px] leading-relaxed">
                  Describe what you want to build, fix, or improve. The agent will analyze and execute.
                </p>
              </div>
            ) : (
              <>
                {chatMessages.map((msg) => (
                  <div
                    key={msg.id}
                    className={cn(
                      "flex gap-3 animate-in fade-in slide-in-from-bottom-1 duration-200",
                      msg.role === 'user' ? "justify-end" : "justify-start"
                    )}
                  >
                    {/* Avatar */}
                    {msg.role !== 'user' && (
                      <div className={cn(
                        "w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 border",
                        msg.role === 'agent'
                          ? "bg-blue-50 border-blue-200"
                          : msg.role === 'push_result'
                          ? "bg-emerald-50 border-emerald-200"
                          : "bg-slate-50 border-slate-200"
                      )}>
                        {msg.role === 'agent'
                          ? <Bot className="w-3.5 h-3.5 text-blue-600" />
                          : <Cpu className="w-3.5 h-3.5 text-slate-400" />}
                      </div>
                    )}

                    {/* Bubble */}
                    <div className={cn(
                      "max-w-[85%] rounded-2xl overflow-hidden",
                      msg.role === 'user'
                        ? "bg-blue-600 text-white rounded-br-md shadow-sm shadow-blue-600/20 px-3.5 py-2.5"
                        : msg.role === 'agent'
                        ? "bg-white border border-slate-200 text-slate-700 rounded-bl-md shadow-sm px-3.5 py-2.5"
                        : msg.role === 'push_result'
                        ? "bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-bl-md shadow-sm px-3.5 py-2.5"
                        : "bg-slate-100 text-slate-500 text-xs italic rounded-bl-md px-3.5 py-2.5"
                    )}>
                      {/* Images in message */}
                      {msg.images && msg.images.length > 0 && (
                        <div className="flex gap-2 mb-2 flex-wrap">
                          {msg.images.map((img, i) => (
                            <img
                              key={i}
                              src={img.data || img}
                              alt={img.name || 'attached'}
                              className="w-24 h-24 object-cover rounded-lg border border-white/20"
                            />
                          ))}
                        </div>
                      )}

                      {/* Content — rich rendering for agent, plain for user */}
                      {msg.role === 'agent' ? (
                        <MessageRenderer
                          content={msg.content}
                          toolCalls={msg.toolCalls || []}
                        />
                      ) : (
                        <span className="text-sm leading-relaxed">{msg.content}</span>
                      )}
                    </div>

                    {/* User Avatar */}
                    {msg.role === 'user' && (
                      <div className="w-7 h-7 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0 mt-0.5">
                        <User className="w-3.5 h-3.5 text-slate-500" />
                      </div>
                    )}
                  </div>
                ))}

                {/* Pipeline progress */}
                {phases.length > 0 && (
                  <TaskProgress phases={phases} />
                )}

                {/* Typing indicator */}
                {isRunning && (
                  <div className="flex gap-3 justify-start animate-in fade-in duration-300">
                    <div className="w-7 h-7 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center shrink-0 mt-0.5">
                      <Bot className="w-3.5 h-3.5 text-blue-600" />
                    </div>
                    <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-md shadow-sm px-4 py-3 flex items-center gap-2">
                      <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin" />
                      <span className="text-xs text-slate-500">Agent is working...</span>
                    </div>
                  </div>
                )}
              </>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Scroll-to-bottom FAB */}
          {showScrollBtn && (
            <div className="absolute bottom-20 left-1/2 -translate-x-1/2 z-10">
              <button
                onClick={scrollToBottom}
                className="p-2 bg-white border border-slate-200 rounded-full shadow-lg hover:bg-slate-50 transition-colors"
              >
                <ArrowDown className="w-4 h-4 text-slate-600" />
              </button>
            </div>
          )}

          {/* ── Chat Input (Claude-style) ─────────────────── */}
          <form onSubmit={handleChatSend} className="shrink-0 border-t border-slate-200 bg-white">
            {/* Image previews — Claude-style cards inside the input area */}
            {attachedImages.length > 0 && (
              <div className="px-4 pt-3 pb-1">
                <div className="flex gap-2 flex-wrap">
                  {attachedImages.map((img, i) => (
                    <div
                      key={i}
                      className="relative group flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-2 py-1.5 hover:bg-slate-100 transition-colors"
                    >
                      {/* Thumbnail */}
                      <div className="relative w-10 h-10 rounded-lg overflow-hidden shrink-0 bg-slate-200">
                        {img.data ? (
                          <img
                            src={img.data}
                            alt={img.name}
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center">
                            <Loader2 className="w-4 h-4 text-slate-400 animate-spin" />
                          </div>
                        )}
                        {/* Hover preview button */}
                        {img.data && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); setPreviewImage(img); }}
                            className="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          >
                            <Eye className="w-3.5 h-3.5 text-white" />
                          </button>
                        )}
                      </div>
                      {/* File info */}
                      <div className="flex flex-col min-w-0">
                        <span className="text-[11px] font-medium text-slate-700 truncate max-w-[100px]">
                          {img.name}
                        </span>
                        <span className="text-[10px] text-slate-400">
                          {formatFileSize(img.size)}
                        </span>
                      </div>
                      {/* Remove button */}
                      <button
                        type="button"
                        onClick={() => removeImage(i)}
                        className="ml-1 p-0.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-md transition-colors"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                  {/* Add more images indicator */}
                  {attachedImages.length < 5 && (
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      className="flex items-center justify-center w-10 h-[52px] border border-dashed border-slate-300 rounded-xl text-slate-400 hover:text-slate-600 hover:border-slate-400 transition-colors"
                      title="Add more images"
                    >
                      <span className="text-lg font-light">+</span>
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Input row */}
            <div className="px-3 pt-2 pb-1">
              <div className="flex items-end gap-1.5 bg-slate-50 border border-slate-200 rounded-2xl px-1 py-1 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500/20 transition-all">
                <textarea
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={handleChatKeyDown}
                  placeholder={
                    isRunning
                      ? 'Agent is working...'
                      : state === 'connected' || state === 'ready'
                      ? 'What do you want to build?'
                      : state === 'idle'
                      ? 'What do you want to build?'
                      : 'Waiting for connection...'
                  }
                  rows={1}
                  className="flex-1 px-3 py-2 text-sm bg-transparent outline-none text-slate-800 placeholder:text-slate-400 resize-none min-h-[36px] max-h-[120px]"
                />
                <button
                  type="submit"
                  disabled={!chatInput.trim() && attachedImages.length === 0}
                  className={cn(
                    "p-2 rounded-xl transition-all shrink-0 mb-0.5",
                    (chatInput.trim() || attachedImages.length > 0)
                      ? "bg-slate-600 text-white hover:bg-slate-700"
                      : "bg-transparent text-slate-300 cursor-not-allowed"
                  )}
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Toolbar row — Tools + status */}
            <div className="flex items-center gap-2 px-4 pb-2.5 pt-1">
              {/* Tools dropdown */}
              <div className="relative" ref={toolsMenuRef}>
                <button
                  type="button"
                  onClick={() => { setShowToolsMenu(!showToolsMenu); setShowFigmaInput(false); }}
                  className={cn(
                    "flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all",
                    showToolsMenu
                      ? "bg-slate-200 text-slate-700"
                      : "text-slate-500 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                  )}
                >
                  <Plus className="w-3.5 h-3.5" />
                  Tools
                </button>

                {/* Dropdown menu */}
                {showToolsMenu && (
                  <div className="absolute bottom-full left-0 mb-2 w-64 bg-white border border-slate-200 rounded-2xl shadow-2xl overflow-hidden z-30 animate-in fade-in slide-in-from-bottom-2 duration-200">
                    {/* Group 1: Files */}
                    <div className="py-1.5">
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
                      >
                        <Paperclip className="w-4 h-4 text-slate-500" />
                        <span className="font-medium">Add files or photos</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => videoInputRef.current?.click()}
                        className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
                      >
                        <Video className="w-4 h-4 text-slate-500" />
                        <span className="font-medium">Add video</span>
                      </button>
                      
                      {!showFigmaInput ? (
                        <button
                          type="button"
                          onClick={() => setShowFigmaInput(true)}
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
                              onChange={(e) => setFigmaUrl(e.target.value)}
                              placeholder="https://figma.com/..."
                              className="w-full bg-transparent outline-none text-[13px] text-slate-800 placeholder:text-slate-400"
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault();
                                  handleFigmaSubmit(e);
                                }
                              }}
                              autoFocus
                            />
                            <button
                              type="button"
                              onClick={handleFigmaSubmit}
                              className="text-white bg-blue-500 hover:bg-blue-600 rounded px-2 py-0.5 text-[11px] font-medium transition-colors"
                            >
                              Add
                            </button>
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="h-px bg-slate-100 mx-4" />

                    {/* Group 2: Capture */}
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

                    {/* Group 3: Context */}
                    <div className="py-1.5">
                      <button
                        type="button"
                        onClick={() => {
                          setWebSearchEnabled(!webSearchEnabled);
                          setShowToolsMenu(false);
                        }}
                        className="w-full flex items-center justify-between px-4 py-2.5 text-[13px] hover:bg-slate-50 transition-colors"
                      >
                        <div className="flex items-center gap-3 text-slate-800">
                          <Globe className="w-4 h-4 text-slate-500" />
                          <span className="font-medium">Web search</span>
                        </div>
                        <div className={cn(
                          "w-7 h-4 rounded-full flex items-center px-0.5 transition-colors",
                          webSearchEnabled ? "bg-blue-500" : "bg-slate-300"
                        )}>
                          <div className={cn(
                            "w-3 h-3 bg-white rounded-full shadow-sm transition-transform",
                            webSearchEnabled ? "translate-x-3" : "translate-x-0"
                          )} />
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setChatInput(prev => prev + (prev.endsWith(' ') || !prev ? '' : ' ') + '@');
                          setShowToolsMenu(false);
                        }}
                        className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] text-slate-800 hover:bg-slate-50 transition-colors text-left"
                      >
                        <Layers className="w-4 h-4 text-slate-500" />
                        <span className="font-medium">Add context</span>
                      </button>
                    </div>

                  </div>
                )}
              </div>

              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                onChange={handleImageSelect}
                className="hidden"
              />
              <input
                ref={videoInputRef}
                type="file"
                accept="video/*"
                multiple
                onChange={handleVideoSelect}
                className="hidden"
              />

              <div className="flex-1" />

              {/* Status */}
              <div className="flex items-center gap-1.5">
                <div className={cn("w-1.5 h-1.5 rounded-full", currentStatus.dot)} />
                <span className="text-[10px] text-slate-400">
                  {currentStatus.label}
                </span>
              </div>

              {/* Send hint */}
              <span className="text-[10px] text-slate-300">↵</span>
            </div>
          </form>

          {/* ── Image Preview Modal ─────────────────────── */}
          {previewImage && (
            <div
              className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 animate-in fade-in duration-200"
              onClick={() => setPreviewImage(null)}
            >
              <div className="relative max-w-[80vw] max-h-[80vh]" onClick={(e) => e.stopPropagation()}>
                <img
                  src={previewImage.data}
                  alt={previewImage.name}
                  className="max-w-full max-h-[80vh] rounded-xl shadow-2xl"
                />
                <button
                  onClick={() => setPreviewImage(null)}
                  className="absolute -top-3 -right-3 w-8 h-8 bg-white text-slate-600 rounded-full flex items-center justify-center shadow-lg hover:bg-slate-100 transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent rounded-b-xl px-4 py-3">
                  <p className="text-white text-sm font-medium">{previewImage.name}</p>
                  <p className="text-white/70 text-xs">{formatFileSize(previewImage.size)}</p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          TERMINAL TAB
      ═══════════════════════════════════════════════════ */}
      {activeTab === 'terminal' && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Terminal toolbar */}
          <div className="shrink-0 flex items-center justify-between px-4 py-1.5 border-b border-slate-200 bg-slate-50/80">
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-400" />
              <div className="w-2.5 h-2.5 rounded-full bg-amber-400" />
              <div className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
            </div>
            <button
              onClick={onClearLogs}
              className="p-1 text-slate-400 hover:text-slate-600 transition-colors"
              title="Clear terminal"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </div>

          {/* Terminal output */}
          <div className="flex-1 overflow-y-auto px-4 py-3 font-mono text-[13px] leading-relaxed bg-slate-900 text-slate-300">
            {logs.length === 0 ? (
              <div className="flex items-center gap-2 text-slate-500">
                <Circle className="w-2 h-2 text-slate-600" />
                <span>Waiting for output...</span>
              </div>
            ) : (
              logs.map((log) => (
                <div key={log.id} className="flex items-start gap-2 mb-0.5 group">
                  {/* Timestamp — shown on hover */}
                  <span className="text-[10px] text-slate-600 tabular-nums shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity select-none">
                    {new Date(log.ts || log.timestamp).toLocaleTimeString('en-US', {
                      hour12: false,
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                    })}
                  </span>
                  {/* Content */}
                  <span className={cn("whitespace-pre-wrap break-all", logColor(log.type))}>
                    {log.content}
                  </span>
                </div>
              ))
            )}
            <div ref={terminalEndRef} />
          </div>

          {/* Terminal input */}
          <form onSubmit={handleTerminalSend} className="shrink-0 px-4 py-2.5 border-t border-slate-200 bg-slate-900">
            <div className="flex items-center gap-2">
              <span className="text-emerald-400 text-sm font-mono shrink-0">$</span>
              <input
                value={terminalInput}
                onChange={(e) => setTerminalInput(e.target.value)}
                placeholder="Run a command..."
                disabled={state !== 'connected' && state !== 'ready' && !isRunning}
                className="flex-1 bg-transparent text-sm text-emerald-300 placeholder:text-slate-600 outline-none font-mono disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={!terminalInput.trim() || (state !== 'connected' && state !== 'ready' && !isRunning)}
                className="p-1 text-emerald-400 hover:text-emerald-300 disabled:text-slate-600 transition-colors"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
