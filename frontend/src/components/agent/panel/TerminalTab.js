'use client';

// ─────────────────────────────────────────────────────────
//  TerminalTab — Terminal output + input form
//  Used inside AgentPanel when activeTab === 'terminal'
// ─────────────────────────────────────────────────────────

import { useRef, useEffect, useState } from 'react';
import { cn } from '@/lib/utils';
import { Circle, Send, Trash2 } from 'lucide-react';

const LOG_COLORS = {
  error:         'text-red-400',
  cmd_output:    'text-emerald-400',
  agent_message: 'text-blue-400',
  system:        'text-slate-500',
  user:          'text-amber-400',
  file_write:    'text-purple-400',
  thinking:      'text-slate-600 italic',
};

function getLogColor(type) {
  return LOG_COLORS[type] || 'text-slate-400';
}

export default function TerminalTab({ state, logs, onSendCommand, onClearLogs }) {
  const [terminalInput, setTerminalInput] = useState('');
  const terminalEndRef = useRef(null);
  const isRunning = state === 'running' || state === 'working';

  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const handleSend = (e) => {
    e.preventDefault();
    if (!terminalInput.trim()) return;
    onSendCommand?.(terminalInput.trim());
    setTerminalInput('');
  };

  const canType = state === 'connected' || state === 'ready' || isRunning;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Toolbar */}
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

      {/* Output */}
      <div className="flex-1 overflow-y-auto px-4 py-3 font-mono text-[13px] leading-relaxed bg-slate-900 text-slate-300">
        {logs.length === 0 ? (
          <div className="flex items-center gap-2 text-slate-500">
            <Circle className="w-2 h-2 text-slate-600" />
            <span>Waiting for output...</span>
          </div>
        ) : (
          logs.map((log) => (
            <div key={log.id} className="flex items-start gap-2 mb-0.5 group">
              <span className="text-[10px] text-slate-600 tabular-nums shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity select-none">
                {new Date(log.ts || log.timestamp).toLocaleTimeString('en-US', {
                  hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
                })}
              </span>
              <span className={cn('whitespace-pre-wrap break-all', getLogColor(log.type))}>
                {log.content}
              </span>
            </div>
          ))
        )}
        <div ref={terminalEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSend} className="shrink-0 px-4 py-2.5 border-t border-slate-200 bg-slate-900">
        <div className="flex items-center gap-2">
          <span className="text-emerald-400 text-sm font-mono shrink-0">$</span>
          <input
            value={terminalInput}
            onChange={(e) => setTerminalInput(e.target.value)}
            placeholder="Run a command..."
            disabled={!canType}
            className="flex-1 bg-transparent text-sm text-emerald-300 placeholder:text-slate-600 outline-none font-mono disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!terminalInput.trim() || !canType}
            className="p-1 text-emerald-400 hover:text-emerald-300 disabled:text-slate-600 transition-colors"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      </form>
    </div>
  );
}
