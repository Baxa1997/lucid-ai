"use client";

import {
  deleteConversation,
  deleteConversations,
  listConversations,
} from "@/lib/conversations";
import {cn} from "@/lib/utils";
import {
  AlertCircle,
  AlertTriangle,
  Check,
  GitBranch,
  Github,
  Loader2,
  MessageSquare,
  Search,
  Trash2,
  X,
} from "lucide-react";
import {useRouter} from "next/navigation";
import {useCallback, useEffect, useState} from "react";

/* GitLab SVG icon */
function GitLabIcon({className}) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}

function formatRelativeTime(dateStr) {
  if (!dateStr) return "";
  const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

/* ═══════════════════════════════════════════════════
   BULK DELETE CONFIRMATION MODAL
   ═══════════════════════════════════════════════════ */
function BulkDeleteModal({count, onConfirm, onCancel, loading}) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onCancel}
      />
      <div className="relative w-full max-w-md bg-white dark:bg-[#151b23] rounded-2xl border border-slate-200 dark:border-slate-700/50 shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between px-6 pt-6 pb-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-500" />
            </div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100">
              Delete {count} conversation{count !== 1 ? "s" : ""}
            </h3>
          </div>
          <button
            onClick={onCancel}
            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-6 py-5">
          <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
            This will permanently delete{" "}
            <span className="font-semibold text-slate-700 dark:text-slate-200">
              {count} conversation{count !== 1 ? "s" : ""}
            </span>{" "}
            and all their messages. This cannot be undone.
          </p>
        </div>
        <div className="flex items-center justify-end gap-3 px-6 pb-6">
          <button
            onClick={onCancel}
            disabled={loading}
            className="px-4 py-2.5 text-sm font-semibold text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-white/[0.06] hover:bg-slate-200 dark:hover:bg-white/[0.1] rounded-xl border border-slate-200 dark:border-slate-700/50 transition-all">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2.5 text-sm font-bold text-white bg-red-600 hover:bg-red-700 rounded-xl shadow-sm shadow-red-600/20 transition-all disabled:opacity-50">
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
            {loading ? "Deleting…" : `Delete ${count}`}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════
   CONVERSATION ROW
   ═══════════════════════════════════════════════════ */
function ConversationRow({
  conversation,
  selected,
  onSelect,
  onClick,
  onDelete,
}) {
  const [deleting, setDeleting] = useState(false);

  const handleDeleteClick = async (e) => {
    e.stopPropagation();
    setDeleting(true);
    await onDelete(conversation.id);
    setDeleting(false);
  };

  const handleCheckbox = (e) => {
    e.stopPropagation();
    onSelect(conversation.id);
  };

  return (
    <div
      onClick={onClick}
      className={cn(
        "group flex items-center gap-4 px-4 py-3.5 border-b border-slate-100 dark:border-[#21262d] hover:bg-slate-50 dark:hover:bg-white/[0.02] transition-colors cursor-pointer",
        selected &&
          "bg-blue-50/50 dark:bg-blue-500/5 hover:bg-blue-50 dark:hover:bg-blue-500/5",
      )}>
      {/* Checkbox */}
      <div
        onClick={handleCheckbox}
        className={cn(
          "shrink-0 w-4 h-4 rounded border-2 flex items-center justify-center transition-colors cursor-pointer",
          selected
            ? "bg-blue-600 border-blue-600"
            : "border-slate-300 dark:border-slate-600 group-hover:border-slate-400 dark:group-hover:border-slate-500",
        )}>
        {selected && <Check className="w-2.5 h-2.5 text-white" />}
      </div>

      {/* Icon */}
      <div className="shrink-0 w-8 h-8 rounded-lg bg-slate-100 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 flex items-center justify-center">
        {conversation.repo_provider === "github" ? (
          <Github className="w-4 h-4 text-slate-600 dark:text-slate-300" />
        ) : conversation.repo_provider === "gitlab" ? (
          <GitLabIcon className="w-4 h-4 text-orange-500" />
        ) : (
          <MessageSquare className="w-4 h-4 text-blue-500" />
        )}
      </div>

      {/* Title + repo */}
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 truncate">
          {conversation.title || conversation.repo_name || "Project"}
        </p>
        {conversation.repo_name && (
          <p className="text-[11px] text-slate-400 dark:text-slate-500 truncate mt-0.5 flex items-center gap-1">
            <GitBranch className="w-3 h-3 shrink-0" />
            {conversation.repo_name}
          </p>
        )}
      </div>

      {/* Time */}
      <span className="shrink-0 text-[11px] text-slate-400 dark:text-slate-500 font-medium">
        {formatRelativeTime(conversation.updated_at)}
      </span>

      {/* Delete button */}
      <button
        onClick={handleDeleteClick}
        disabled={deleting}
        className="shrink-0 opacity-0 group-hover:opacity-100 p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-all">
        {deleting ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Trash2 className="w-3.5 h-3.5" />
        )}
      </button>
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
  const [search, setSearch] = useState("");

  // Multi-select
  const [selected, setSelected] = useState(new Set());
  const [showBulkModal, setShowBulkModal] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const loadConversations = useCallback(async () => {
    try {
      const data = await listConversations();
      setConversations(data);
    } catch {
      setFetchError("Failed to load conversations.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Single delete
  const handleDelete = async (id) => {
    await deleteConversation(id);
    setConversations((prev) => prev.filter((c) => c.id !== id));
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  // Bulk delete
  const handleBulkDelete = async () => {
    setBulkDeleting(true);
    const ids = [...selected];
    await deleteConversations(ids);
    setConversations((prev) => prev.filter((c) => !ids.includes(c.id)));
    setSelected(new Set());
    setShowBulkModal(false);
    setBulkDeleting(false);
  };

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const filtered = conversations.filter(
    (conv) =>
      search === "" ||
      (conv.title || "").toLowerCase().includes(search.toLowerCase()) ||
      (conv.repo_name || "").toLowerCase().includes(search.toLowerCase()),
  );

  const allSelected =
    filtered.length > 0 && filtered.every((c) => selected.has(c.id));
  const someSelected = filtered.some((c) => selected.has(c.id));

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filtered.map((c) => c.id)));
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#f5f7fa] dark:bg-[#0d1117] overflow-hidden">
      {showBulkModal && (
        <BulkDeleteModal
          count={selected.size}
          onConfirm={handleBulkDelete}
          onCancel={() => setShowBulkModal(false)}
          loading={bulkDeleting}
        />
      )}

      {/* Header */}
      <div className="shrink-0 bg-[#f5f7fa] dark:bg-[#0d1117] px-6 lg:px-8 pt-6 pb-4">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-4">
            <div className="w-11 h-11 rounded-2xl bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 flex items-center justify-center shadow-sm">
              <MessageSquare className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h1 className="text-xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight">
                Conversations
              </h1>
              <p className="text-[12px] text-slate-400 dark:text-slate-500 font-medium mt-0.5">
                {conversations.length} workspace
                {conversations.length !== 1 ? "s" : ""}
              </p>
            </div>
          </div>
          <button
            onClick={() => router.push("/dashboard")}
            className="flex items-center gap-2 px-4 py-2 text-[13px] font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 border border-slate-200 dark:border-slate-700 rounded-xl hover:bg-white dark:hover:bg-slate-800 transition-all">
            ← Back
          </button>
        </div>

        {/* Search + bulk action bar */}
        <div className="flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search conversations..."
              className="w-full pl-10 pr-4 py-2.5 bg-white dark:bg-[#151b23] border border-slate-200 dark:border-slate-700/50 rounded-xl text-[13px] text-slate-700 dark:text-slate-200 placeholder-slate-400 outline-none focus:border-blue-500 transition-all"
            />
          </div>

          {/* Bulk delete button — visible when something is selected */}
          {selected.size > 0 && (
            <button
              onClick={() => setShowBulkModal(true)}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-500/20 text-[13px] font-semibold hover:bg-red-100 dark:hover:bg-red-500/20 transition-all">
              <Trash2 className="w-4 h-4" />
              Delete {selected.size}
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto min-h-0 px-6 lg:px-8 pb-8">
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-6 h-6 text-blue-500 animate-spin" />
          </div>
        ) : fetchError ? (
          <div className="bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 rounded-2xl p-8 text-center">
            <AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-3" />
            <p className="text-sm text-red-600 dark:text-red-400 font-medium">
              {fetchError}
            </p>
          </div>
        ) : (
          <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] overflow-hidden">
            {/* Table header */}
            {filtered.length > 0 && (
              <div className="flex items-center gap-4 px-4 py-2.5 border-b border-slate-100 dark:border-[#21262d] bg-slate-50 dark:bg-[#0f1118]">
                {/* Select all checkbox */}
                <div
                  onClick={toggleSelectAll}
                  className={cn(
                    "shrink-0 w-4 h-4 rounded border-2 flex items-center justify-center transition-colors cursor-pointer",
                    allSelected
                      ? "bg-blue-600 border-blue-600"
                      : someSelected
                        ? "bg-blue-200 border-blue-400 dark:bg-blue-500/30 dark:border-blue-500"
                        : "border-slate-300 dark:border-slate-600 hover:border-slate-400",
                  )}>
                  {allSelected && <Check className="w-2.5 h-2.5 text-white" />}
                  {someSelected && !allSelected && (
                    <div className="w-2 h-0.5 bg-blue-600 dark:bg-blue-400 rounded-full" />
                  )}
                </div>
                <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider flex-1">
                  {selected.size > 0
                    ? `${selected.size} selected`
                    : `${filtered.length} conversation${filtered.length !== 1 ? "s" : ""}`}
                </span>
                <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider shrink-0">
                  Updated
                </span>
                <div className="w-[52px]" />
              </div>
            )}

            {/* Rows */}
            {filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-center px-6">
                <div className="w-14 h-14 rounded-2xl bg-slate-100 dark:bg-white/[0.04] border border-slate-200 dark:border-slate-700/50 flex items-center justify-center mx-auto mb-4">
                  <MessageSquare className="w-7 h-7 text-slate-300 dark:text-slate-600" />
                </div>
                <h3 className="text-[15px] font-bold text-slate-900 dark:text-slate-100 mb-1">
                  {conversations.length === 0
                    ? "No conversations yet"
                    : "No matching results"}
                </h3>
                <p className="text-[13px] text-slate-400 dark:text-slate-500 mb-4">
                  {conversations.length === 0
                    ? "Start a new conversation by selecting a repository."
                    : "Try adjusting your search."}
                </p>
                {conversations.length === 0 && (
                  <button
                    onClick={() => router.push("/dashboard")}
                    className="px-5 py-2.5 bg-blue-600 text-white rounded-xl text-[13px] font-bold hover:bg-blue-700 transition-all">
                    Start First Conversation
                  </button>
                )}
              </div>
            ) : (
              filtered.map((conv) => (
                <ConversationRow
                  key={conv.id}
                  conversation={conv}
                  selected={selected.has(conv.id)}
                  onSelect={toggleSelect}
                  onClick={() =>
                    router.push(`/dashboard/workspace/${conv.id}`)
                  }
                  onDelete={handleDelete}
                />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
