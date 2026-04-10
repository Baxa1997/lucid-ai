'use client';

// ─────────────────────────────────────────────────────────
//  ChatTab — Messages list, typing indicator, scroll button
//  Used inside AgentPanel when activeTab === 'chat'
// ─────────────────────────────────────────────────────────

import { useRef, useEffect, useState } from 'react';
import { cn } from '@/lib/utils';
import { Bot, User, Cpu, ArrowDown, Loader2 } from 'lucide-react';
import { MessageRenderer } from '../MessageRenderer';
import TaskProgress from '../TaskProgress';
import ChatInput from './ChatInput';

export default function ChatTab({ state, chatMessages, phases, onSendMessage, isRunning }) {
  const chatEndRef = useRef(null);
  const chatContainerRef = useRef(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  // Auto-scroll when messages arrive (only if already near bottom)
  useEffect(() => {
    if (!showScrollBtn) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, phases, showScrollBtn]);

  const handleChatScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    setShowScrollBtn(scrollHeight - scrollTop - clientHeight > 100);
  };

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    setShowScrollBtn(false);
  };

  const isEmpty = chatMessages.length === 0 && phases.length === 0;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Messages */}
      <div
        ref={chatContainerRef}
        onScroll={handleChatScroll}
        className="flex-1 overflow-y-auto px-4 py-4 space-y-4 bg-[#f5f7fa]"
      >
        {isEmpty ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-6">
            <div className="w-14 h-14 rounded-2xl bg-blue-50 border border-blue-200 flex items-center justify-center mb-4">
              <Bot className="w-6 h-6 text-blue-600" />
            </div>
            <p className="text-sm font-semibold text-slate-700">
              {state === 'connecting' || isRunning ? 'Connecting to agent...' : 'Start a conversation'}
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
                  'flex gap-3 animate-in fade-in slide-in-from-bottom-1 duration-200',
                  msg.role === 'user' ? 'justify-end' : 'justify-start',
                )}
              >
                {/* Agent/system avatar */}
                {msg.role !== 'user' && (
                  <div className={cn(
                    'w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 border',
                    msg.role === 'agent' ? 'bg-blue-50 border-blue-200'
                      : msg.role === 'push_result' ? 'bg-emerald-50 border-emerald-200'
                      : 'bg-slate-50 border-slate-200',
                  )}>
                    {msg.role === 'agent'
                      ? <Bot className="w-3.5 h-3.5 text-blue-600" />
                      : <Cpu className="w-3.5 h-3.5 text-slate-400" />}
                  </div>
                )}

                {/* Message bubble */}
                <div className={cn(
                  'max-w-[85%] rounded-2xl overflow-hidden',
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white rounded-br-md shadow-sm shadow-blue-600/20 px-3.5 py-2.5'
                    : msg.role === 'agent'
                    ? 'bg-white border border-slate-200 text-slate-700 rounded-bl-md shadow-sm px-3.5 py-2.5'
                    : msg.role === 'push_result'
                    ? 'bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-bl-md shadow-sm px-3.5 py-2.5'
                    : 'bg-slate-100 text-slate-500 text-xs italic rounded-bl-md px-3.5 py-2.5',
                )}>
                  {/* Attached images */}
                  {msg.images?.length > 0 && (
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
                  {/* Content */}
                  {msg.role === 'agent' ? (
                    <MessageRenderer content={msg.content} toolCalls={msg.toolCalls || []} />
                  ) : (
                    <span className="text-sm leading-relaxed">{msg.content}</span>
                  )}
                </div>

                {/* User avatar */}
                {msg.role === 'user' && (
                  <div className="w-7 h-7 rounded-lg bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0 mt-0.5">
                    <User className="w-3.5 h-3.5 text-slate-500" />
                  </div>
                )}
              </div>
            ))}

            {/* Pipeline progress */}
            {phases.length > 0 && <TaskProgress phases={phases} />}

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

      {/* Chat input */}
      <ChatInput state={state} onSendMessage={onSendMessage} isRunning={isRunning} />
    </div>
  );
}
