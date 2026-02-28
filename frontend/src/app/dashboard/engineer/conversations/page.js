'use client';

import {
  MessageSquare, Search, Filter,
  ArrowUpDown, Plus, GitBranch,
  CheckCircle2, Loader2, AlertCircle, Pause, ExternalLink,
  Clock, Hash
} from 'lucide-react';
import { useState, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useRouter } from 'next/navigation';

/** Map a chat from the ai_engine API to the shape the UI cards expect. */
function mapChat(chat) {
  return {
    id: chat.id,
    agentSessionId: chat.agentSessionId,
    title: chat.title || 'Untitled Session',
    repo: chat.projectId || '—',
    branch: chat.modelProvider || 'unknown',
    status: chat.isActive ? 'running' : 'completed',
    lastMessage: chat.title || 'AI Engineering Session',
    messageCount: 0,
    createdAt: chat.createdAt,
    updatedAt: chat.updatedAt,
  };
}

const STATUS_CONFIG = {
  completed: {
    label: 'Completed',
    icon: CheckCircle2,
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-400',
    border: 'border-emerald-500/20',
    dot: 'bg-emerald-500',
  },
  running: {
    label: 'Running',
    icon: Loader2,
    bg: 'bg-blue-500/10',
    text: 'text-blue-400',
    border: 'border-blue-500/20',
    dot: 'bg-blue-500',
    animate: true,
  },
  paused: {
    label: 'Paused',
    icon: Pause,
    bg: 'bg-amber-500/10',
    text: 'text-amber-400',
    border: 'border-amber-500/20',
    dot: 'bg-amber-500',
  },
  error: {
    label: 'Error',
    icon: AlertCircle,
    bg: 'bg-red-500/10',
    text: 'text-red-400',
    border: 'border-red-500/20',
    dot: 'bg-red-500',
  },
};

/* ═══════════════════════════════════════════════════
   STATUS BADGE
   ═══════════════════════════════════════════════════ */
function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.completed;
  const Icon = config.icon;

  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider border",
      config.bg, config.text, config.border
    )}>
      <Icon className={cn("w-3 h-3", config.animate && "animate-spin")} />
      {config.label}
    </span>
  );
}

/* ═══════════════════════════════════════════════════
   TIME FORMATTING
   ═══════════════════════════════════════════════════ */
function formatRelativeTime(dateStr) {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/* ═══════════════════════════════════════════════════
   CONVERSATION CARD
   ═══════════════════════════════════════════════════ */
function ConversationCard({ conversation, onClick }) {
  const statusConfig = STATUS_CONFIG[conversation.status];

  return (
    <button
      onClick={onClick}
      className="w-full text-left px-5 py-4 flex items-start gap-4 hover:bg-white/[0.02] transition-all duration-200 group border-b border-slate-700/30 last:border-b-0"
    >
      {/* Status dot */}
      <div className="pt-1.5 shrink-0">
        <div className={cn(
          "w-2.5 h-2.5 rounded-full ring-4 ring-[#151b23]",
          statusConfig?.dot || 'bg-slate-500',
          conversation.status === 'running' && "animate-pulse"
        )} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Title row */}
        <div className="flex items-center gap-3 mb-1.5">
          <h3 className="text-sm font-semibold text-slate-100 truncate group-hover:text-blue-400 transition-colors">
            {conversation.title}
          </h3>
          <StatusBadge status={conversation.status} />
        </div>

        {/* Last message */}
        <p className="text-sm text-slate-400 line-clamp-1 mb-2.5 leading-relaxed">
          {conversation.lastMessage}
        </p>

        {/* Meta row */}
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <span className="inline-flex items-center gap-1.5 font-medium">
            <GitBranch className="w-3 h-3" />
            <span className="text-slate-400">{conversation.repo}</span>
            <span className="text-slate-600">/</span>
            <span className="font-mono text-slate-500">{conversation.branch}</span>
          </span>
          <span className="inline-flex items-center gap-1">
            <Hash className="w-3 h-3" />
            {conversation.messageCount} messages
          </span>
          <span className="inline-flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatRelativeTime(conversation.updatedAt)}
          </span>
        </div>
      </div>

      {/* Arrow on hover */}
      <div className="pt-2 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        <ExternalLink className="w-4 h-4 text-slate-500" />
      </div>
    </button>
  );
}

/* ═══════════════════════════════════════════════════
   MAIN PAGE
   ═══════════════════════════════════════════════════ */
export default function ConversationsPage() {
  const router = useRouter();
  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [showFilters, setShowFilters] = useState(false);

  useEffect(() => {
    fetch('/api/chats')
      .then((r) => r.json())
      .then((data) => {
        if (data.chats) {
          setConversations(data.chats.map(mapChat));
        } else {
          setFetchError('Failed to load conversations.');
        }
      })
      .catch(() => setFetchError('Could not reach the AI service.'))
      .finally(() => setLoading(false));
  }, []);

  // Filter conversations
  const filteredConversations = conversations.filter(conv => {
    const matchesSearch = search === '' || 
      conv.title.toLowerCase().includes(search.toLowerCase()) ||
      conv.repo.toLowerCase().includes(search.toLowerCase()) ||
      conv.lastMessage.toLowerCase().includes(search.toLowerCase());
    
    const matchesStatus = statusFilter === 'all' || conv.status === statusFilter;
    
    return matchesSearch && matchesStatus;
  });

  // Stats
  const stats = {
    total: conversations.length,
    running: conversations.filter(c => c.status === 'running').length,
    completed: conversations.filter(c => c.status === 'completed').length,
    error: conversations.filter(c => c.status === 'error').length,
  };

  return (
    <div className="h-full flex flex-col bg-[#f5f7fa] dark:bg-[#0d1117] overflow-hidden transition-colors duration-200">
      
      {/* ── Sticky Header Section ── */}
      <div className="shrink-0 bg-[#f5f7fa] dark:bg-[#0d1117] px-8 pt-10 pb-0 transition-colors duration-200">
        <div className="max-w-5xl mx-auto">
          
          {/* Page Header */}
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-2xl bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 flex items-center justify-center shadow-sm">
                <MessageSquare className="w-6 h-6 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <h1 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight">Conversations</h1>
                <p className="text-sm text-slate-400 dark:text-slate-500 font-medium mt-0.5">All your AI conversations in one place.</p>
              </div>
            </div>
            <button 
              onClick={() => router.push('/dashboard/engineer')}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all shadow-sm shadow-blue-600/20 active:scale-[0.98]"
            >
              <Plus className="w-4 h-4" strokeWidth={2.5} />
              New Conversation
            </button>
          </div>

          {/* Quick Stats — dark cards matching screenshot */}
          <div className="grid grid-cols-4 gap-3 mb-6">
            {[
              { label: 'TOTAL', value: stats.total, color: 'text-slate-100', dotColor: null },
              { label: 'RUNNING', value: stats.running, color: 'text-blue-400', dotColor: 'bg-blue-500' },
              { label: 'COMPLETED', value: stats.completed, color: 'text-emerald-400', dotColor: 'bg-emerald-500' },
              { label: 'ERRORS', value: stats.error, color: 'text-red-400', dotColor: 'bg-red-500' },
            ].map((stat) => (
              <div key={stat.label} className="px-4 py-3.5 rounded-xl bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 shadow-sm">
                <div className="flex items-center gap-2 mb-1.5">
                  {stat.dotColor && <div className={cn("w-1.5 h-1.5 rounded-full", stat.dotColor)} />}
                  <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.1em]">{stat.label}</span>
                </div>
                <span className={cn("text-2xl font-extrabold", stat.color)}>{stat.value}</span>
              </div>
            ))}
          </div>

          {/* Search & Filters */}
          <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-4 mb-6">
            <div className="relative group">
              <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none">
                <Search className="h-4 w-4 text-slate-400 group-focus-within:text-blue-500 transition-colors" />
              </div>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="block w-full pl-10 pr-3 py-3 bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm placeholder-slate-400 dark:placeholder-slate-500 text-slate-900 dark:text-slate-100 focus:outline-none focus:border-blue-500 dark:focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all shadow-sm"
                placeholder="Search conversations..."
              />
            </div>
            
            <div className="flex items-center gap-3">
              <button 
                onClick={() => setShowFilters(!showFilters)}
                className={cn(
                  "flex items-center gap-2 px-4 py-3 bg-white dark:bg-[#151b23] border rounded-xl text-sm font-semibold transition-all shadow-sm",
                  showFilters 
                    ? "border-blue-300 dark:border-blue-500 text-blue-600 dark:text-blue-400 ring-1 ring-blue-500/20" 
                    : "border-slate-200 dark:border-slate-700/50 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-[#1c2430] hover:border-slate-300 dark:hover:border-slate-600"
                )}
              >
                <Filter className="w-4 h-4" />
                Filter
              </button>
              <button className="flex items-center gap-2 px-4 py-3 bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-[#1c2430] hover:border-slate-300 dark:hover:border-slate-600 transition-all shadow-sm">
                <ArrowUpDown className="w-4 h-4 text-slate-400" />
                Sort
              </button>
            </div>
          </div>

          {/* Filter chips */}
          {showFilters && (
            <div className="flex items-center gap-2 mb-6 animate-in slide-in-from-top-2 fade-in duration-200">
              {['all', 'running', 'completed', 'paused', 'error'].map((status) => (
                <button
                  key={status}
                  onClick={() => setStatusFilter(status)}
                  className={cn(
                    "px-3.5 py-1.5 rounded-lg text-xs font-bold capitalize transition-all border",
                    statusFilter === status
                      ? "bg-blue-600 text-white border-blue-600 shadow-sm shadow-blue-600/20"
                      : "bg-white dark:bg-[#151b23] text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-700/50 hover:bg-slate-50 dark:hover:bg-[#1c2430] hover:border-slate-300 dark:hover:border-slate-600"
                  )}
                >
                  {status === 'all' ? 'All' : status}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Scrollable Conversation List ── */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-5xl mx-auto">
          <div className="bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 rounded-2xl shadow-sm overflow-hidden">
            {loading ? (
              <div className="flex flex-col items-center justify-center p-16 text-center">
                <Loader2 className="w-8 h-8 text-blue-500 animate-spin mb-4" />
                <p className="text-sm text-slate-400 dark:text-slate-500 font-medium">Loading conversations...</p>
              </div>
            ) : fetchError ? (
              <div className="flex flex-col items-center justify-center p-16 text-center">
                <AlertCircle className="w-8 h-8 text-red-400 mb-4" />
                <p className="text-sm text-red-500 dark:text-red-400 font-medium">{fetchError}</p>
              </div>
            ) : filteredConversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-16 text-center">
                <div className="w-20 h-20 bg-slate-50 dark:bg-[#1c2430] rounded-3xl flex items-center justify-center mb-6 relative group">
                  <div className="absolute inset-0 bg-blue-100/50 dark:bg-blue-500/10 rounded-3xl scale-0 group-hover:scale-110 transition-transform duration-500" />
                  <MessageSquare className="w-10 h-10 text-slate-300 dark:text-slate-600 relative z-10 group-hover:text-blue-500 dark:group-hover:text-blue-400 transition-colors duration-300" />
                </div>
                <h3 className="text-xl font-bold text-slate-900 dark:text-slate-100 mb-2">No conversations found</h3>
                <p className="text-slate-400 dark:text-slate-500 max-w-sm mx-auto mb-8 leading-relaxed">
                  Start a new conversation to begin working with the AI on your projects.
                </p>
                <button
                  onClick={() => router.push('/dashboard/engineer')}
                  className="px-8 py-3 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-600/20 hover:shadow-xl hover:-translate-y-0.5"
                >
                  + New Conversation
                </button>
              </div>
            ) : (
              <div>
                {/* List Header */}
                <div className="px-5 py-3 bg-slate-50/80 dark:bg-[#1c2430]/50 border-b border-slate-100 dark:border-slate-700/30 flex items-center justify-between sticky top-0 z-10">
                  <span className="text-xs font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                    {filteredConversations.length} conversation{filteredConversations.length !== 1 ? 's' : ''}
                  </span>
                  <span className="text-xs text-slate-400 dark:text-slate-500">
                    Sorted by most recent
                  </span>
                </div>

                {/* Conversation List */}
                {filteredConversations.map((conv) => (
                  <ConversationCard 
                    key={conv.id}
                    conversation={conv}
                    onClick={() => router.push(`/dashboard/engineer/workspace/${conv.id}`)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
