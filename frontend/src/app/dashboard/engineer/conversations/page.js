'use client';

import {
  MessageSquare, Search, Filter,
  ArrowUpDown, Plus, GitBranch,
  CheckCircle2, Loader2, AlertCircle, Pause, ExternalLink,
  Clock, Hash, Github, Trash2, X, AlertTriangle
} from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { useRouter } from 'next/navigation';
import { listConversations, deleteConversation } from '@/lib/conversations';

/* GitLab SVG icon */
function GitLabIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}

const STATUS_CONFIG = {
  active: {
    label: 'Active',
    icon: Loader2,
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-400',
    border: 'border-emerald-500/20',
    dot: 'bg-emerald-500',
  },
  completed: {
    label: 'Completed',
    icon: CheckCircle2,
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-400',
    border: 'border-emerald-500/20',
    dot: 'bg-emerald-500',
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

function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.active;
  return (
    <span className={cn(
      'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider border',
      config.bg, config.text, config.border
    )}>
      <span className={cn('w-1.5 h-1.5 rounded-full', config.dot)} />
      {config.label}
    </span>
  );
}

function formatRelativeTime(dateStr) {
  if (!dateStr) return '';
  const now = new Date();
  const date = new Date(dateStr);
  const diff = Math.floor((now - date) / 1000);

  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return date.toLocaleDateString();
}

/* ═══════════════════════════════════════════════════
   DELETE CONFIRMATION MODAL
   ═══════════════════════════════════════════════════ */
function DeleteModal({ conversation, onConfirm, onCancel, loading }) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={(e) => e.stopPropagation()}>
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onCancel}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 shadow-2xl dark:shadow-black/40 overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-6 pb-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-500" />
            </div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100">Delete Conversation</h3>
          </div>
          <button
            onClick={onCancel}
            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5">
          <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
            Are you sure you want to delete{' '}
            <span className="font-semibold text-slate-700 dark:text-slate-200">
              &ldquo;{conversation?.title || 'this conversation'}&rdquo;
            </span>
            ? This will permanently remove all messages and cannot be undone.
          </p>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 pb-6">
          <button
            onClick={onCancel}
            disabled={loading}
            className="px-4 py-2.5 text-sm font-semibold text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] rounded-xl border border-slate-200 dark:border-slate-700/50 transition-all"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2.5 text-sm font-bold text-white bg-red-600 hover:bg-red-700 rounded-xl shadow-sm shadow-red-600/20 transition-all active:scale-[0.98] disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
            {loading ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════
   CONVERSATION CARD
   ═══════════════════════════════════════════════════ */
function ConversationCard({ conversation, onClick, onDelete }) {
  const [deleting, setDeleting] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);

  const handleDeleteClick = (e) => {
    e.stopPropagation();
    setShowDeleteModal(true);
  };

  const handleConfirmDelete = async () => {
    setDeleting(true);
    await onDelete(conversation.id);
    setShowDeleteModal(false);
  };

  return (
    <div
      onClick={onClick}
      className="group bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 px-5 py-4 hover:border-blue-300 dark:hover:border-blue-500/30 hover:shadow-md dark:hover:shadow-black/20 transition-all duration-200 cursor-pointer"
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-xl bg-slate-50 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 flex items-center justify-center shrink-0">
            {conversation.repo_provider === 'github'
              ? <Github className="w-5 h-5 text-slate-700 dark:text-slate-300" />
              : conversation.repo_provider === 'gitlab'
                ? <GitLabIcon className="w-5 h-5 text-orange-500" />
                : <MessageSquare className="w-5 h-5 text-blue-500" />
            }
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 truncate">
              {conversation.title || conversation.repo_name || 'Conversation'}
            </h3>
            <div className="flex items-center gap-2 mt-0.5">
              {conversation.branch && (
                <span className="flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500 font-medium">
                  <GitBranch className="w-3 h-3" />
                  {conversation.branch}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <StatusBadge status={conversation.status} />
          {showDeleteModal && (
            <DeleteModal
              conversation={conversation}
              onConfirm={handleConfirmDelete}
              onCancel={() => setShowDeleteModal(false)}
              loading={deleting}
            />
          )}
          <button
            onClick={handleDeleteClick}
            disabled={deleting}
            className="opacity-0 group-hover:opacity-100 p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-all"
          >
            {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      <div className="flex items-center gap-4 text-[11px] text-slate-400 dark:text-slate-500 font-medium">
        <span className="flex items-center gap-1">
          <Hash className="w-3 h-3" />
          {conversation.message_count || 0} messages
        </span>
        <span className="flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {formatRelativeTime(conversation.updated_at)}
        </span>
        {conversation.repo_name && (
          <span className="flex items-center gap-1 truncate">
            {conversation.repo_provider === 'github' ? <Github className="w-3 h-3" /> : <GitBranch className="w-3 h-3" />}
            {conversation.repo_name}
          </span>
        )}
      </div>
    </div>
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

  const loadConversations = useCallback(async () => {
    try {
      const data = await listConversations();
      setConversations(data);
    } catch {
      setFetchError('Failed to load conversations.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  const handleDelete = async (id) => {
    await deleteConversation(id);
    setConversations(prev => prev.filter(c => c.id !== id));
  };

  // Filter
  const filtered = conversations.filter(conv => {
    const matchesSearch = search === '' || 
      (conv.title || '').toLowerCase().includes(search.toLowerCase()) ||
      (conv.repo_name || '').toLowerCase().includes(search.toLowerCase()) ||
      (conv.last_message || '').toLowerCase().includes(search.toLowerCase());
    
    const matchesStatus = statusFilter === 'all' || conv.status === statusFilter;
    
    return matchesSearch && matchesStatus;
  });

  // Stats
  const stats = {
    total: conversations.length,
    active: conversations.filter(c => c.status === 'active').length,
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

          {/* Quick Stats */}
          <div className="grid grid-cols-4 gap-3 mb-6">
            {[
              { label: 'TOTAL', value: stats.total, color: 'text-slate-100', dotColor: null },
              { label: 'ACTIVE', value: stats.active, color: 'text-blue-400', dotColor: 'bg-blue-500' },
              { label: 'COMPLETED', value: stats.completed, color: 'text-emerald-400', dotColor: 'bg-emerald-500' },
              { label: 'ERRORS', value: stats.error, color: 'text-red-400', dotColor: 'bg-red-500' },
            ].map((stat) => (
              <div key={stat.label} className="bg-white dark:bg-[#151b23] rounded-xl px-4 py-3 border border-slate-200 dark:border-slate-700/50">
                <div className="flex items-center gap-2">
                  {stat.dotColor && <span className={cn('w-2 h-2 rounded-full', stat.dotColor)} />}
                  <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">{stat.label}</span>
                </div>
                <span className={cn('text-2xl font-extrabold mt-1 block', stat.color)}>{stat.value}</span>
              </div>
            ))}
          </div>

          {/* Search & Filters */}
          <div className="flex items-center gap-3 mb-5">
            <div className="relative flex-1">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search conversations..."
                className="w-full pl-10 pr-4 py-2.5 bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 outline-none focus:border-blue-500 transition-all"
              />
            </div>
            <div className="relative">
              <button 
                onClick={() => setShowFilters(!showFilters)}
                className={cn(
                  "flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium border transition-all",
                  statusFilter !== 'all'
                    ? "bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-500/20"
                    : "bg-white dark:bg-[#151b23] text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-700/50 hover:border-blue-300"
                )}
              >
                <Filter className="w-4 h-4" />
                {statusFilter === 'all' ? 'Filter' : STATUS_CONFIG[statusFilter]?.label}
              </button>
              {showFilters && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setShowFilters(false)} />
                  <div className="absolute right-0 top-full mt-1.5 w-40 bg-white dark:bg-[#151b23] rounded-xl border border-slate-200 dark:border-slate-700/50 overflow-hidden z-50 shadow-lg">
                    {['all', 'active', 'completed', 'paused', 'error'].map(s => (
                      <button
                        key={s}
                        onClick={() => { setStatusFilter(s); setShowFilters(false); }}
                        className={cn(
                          "w-full text-left px-3.5 py-2.5 text-sm hover:bg-slate-50 dark:hover:bg-white/[0.03] transition-colors",
                          statusFilter === s ? "text-blue-600 dark:text-blue-400 font-medium" : "text-slate-600 dark:text-slate-300"
                        )}
                      >
                        {s === 'all' ? 'All' : STATUS_CONFIG[s]?.label}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Scrollable Content ── */}
      <div className="flex-1 overflow-y-auto px-8 pb-10 min-h-0">
        <div className="max-w-5xl mx-auto">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="w-6 h-6 text-blue-500 animate-spin" />
            </div>
          ) : fetchError ? (
            <div className="bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 rounded-2xl p-8 text-center">
              <AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-3" />
              <p className="text-sm text-red-600 dark:text-red-400 font-medium">{fetchError}</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-20">
              <div className="w-16 h-16 rounded-2xl bg-slate-100 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 flex items-center justify-center mx-auto mb-4">
                <MessageSquare className="w-8 h-8 text-slate-300 dark:text-slate-600" />
              </div>
              <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 mb-1">
                {conversations.length === 0 ? 'No conversations yet' : 'No matching conversations'}
              </h3>
              <p className="text-sm text-slate-400 dark:text-slate-500 mb-4">
                {conversations.length === 0 
                  ? 'Start a new conversation by selecting a repository.' 
                  : 'Try adjusting your search or filters.'
                }
              </p>
              {conversations.length === 0 && (
                <button
                  onClick={() => router.push('/dashboard/engineer')}
                  className="px-5 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all"
                >
                  Start First Conversation
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-2.5">
              {filtered.map((conv) => (
                <ConversationCard
                  key={conv.id}
                  conversation={conv}
                  onClick={() => router.push(`/dashboard/engineer/workspace/${conv.id}`)}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
