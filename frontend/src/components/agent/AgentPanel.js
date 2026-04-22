"use client";

// ─────────────────────────────────────────────────────────
//  Lucid AI — AgentPanel (Production)
//  Tab switcher: Chat ↔ Terminal
//  All heavy rendering delegated to panel/ sub-components.
// ─────────────────────────────────────────────────────────

import {useState} from "react";
import {cn} from "@/lib/utils";
import {MessageSquare, Terminal, Circle, StopCircle} from "lucide-react";
import StatusBadge from "./panel/StatusBadge";
import ChatTab from "./panel/ChatTab";
import TerminalTab from "./panel/TerminalTab";
import TabBar from "@/components/ui/TabBar";

/**
 * AgentPanel — Right-side panel with Chat and Terminal tabs.
 *
 * Props:
 * @param {string}   state          - Agent connection state
 * @param {Array}    chatMessages   - Chat message history
 * @param {Array}    logs           - Terminal log entries
 * @param {Array}    phases         - Task pipeline phases
 * @param {Function} onSendMessage  - Send chat message
 * @param {Function} onSendCommand  - Run terminal command
 * @param {Function} onStop         - Stop the session
 * @param {Function} onClearLogs    - Clear terminal log
 * @param {string}   error          - Error message if any
 */
export default function AgentPanel({
  state = "idle",
  chatMessages = [],
  logs = [],
  phases = [],
  onSendMessage,
  onSendCommand,
  onStop,
  onClearLogs,
  error,
}) {
  const [activeTab, setActiveTab] = useState("chat");
  const isRunning = state === "running" || state === "working";

  const tabs = [
    {
      id: "chat",
      label: "Chat",
      icon: MessageSquare,
      badge: chatMessages.length,
    },
    {id: "terminal", label: "Terminal", icon: Terminal, badge: logs.length},
  ];

  return (
    <div className="h-full flex flex-col bg-white border-l border-slate-200">
      <div className="shrink-0 flex items-center justify-between px-3 py-2 border-b border-slate-200 bg-slate-50/80">
        <TabBar
          tabs={tabs}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          activeColor={activeTab === "terminal" ? "emerald" : "blue"}
        />

        <div className="flex items-center gap-2">
          <StatusBadge state={state} />
          {(state === "connected" || state === "ready" || isRunning) && (
            <button
              onClick={onStop}
              className="p-1 text-red-500 hover:text-red-700 hover:bg-red-50 rounded transition-colors"
              title="Stop session">
              <StopCircle className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* ── Error Banner ─────────────────────────────────── */}
      {error && (
        <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-red-50 border-b border-red-200">
          <Circle className="w-2 h-2 text-red-500 fill-red-500" />
          <p className="text-[11px] text-red-600 font-medium flex-1 truncate">
            {error}
          </p>
        </div>
      )}

      {/* ── Tab Content ─────────────────────────────────── */}
      {activeTab === "chat" && (
        <ChatTab
          state={state}
          chatMessages={chatMessages}
          phases={phases}
          onSendMessage={onSendMessage}
          isRunning={isRunning}
        />
      )}
      {activeTab === "terminal" && (
        <TerminalTab
          state={state}
          logs={logs}
          onSendCommand={onSendCommand}
          onClearLogs={onClearLogs}
        />
      )}
    </div>
  );
}
