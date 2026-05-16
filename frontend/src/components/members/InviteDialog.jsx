"use client";

// ─────────────────────────────────────────────────────────
//  InviteDialog — modal launched from the Share button on a project.
//  Lets the owner invite collaborators by email and manage existing
//  members + pending invitations. Non-owners see read-only state.
// ─────────────────────────────────────────────────────────

import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { X, Send, Loader2, Check, AlertCircle, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import MembersList from "@/components/members/MembersList";

function isEmailish(s) {
  return typeof s === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s.trim());
}

export default function InviteDialog({
  open,
  onClose,
  projectId,
  projectTitle,
  currentUserId,
}) {
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  // Surfaced when Supabase couldn't send the magic-link email but the
  // invite row was still created — owner can copy the link directly.
  const [fallbackLink, setFallbackLink] = useState(null);
  const [copied, setCopied] = useState(false);

  const [members, setMembers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [loading, setLoading] = useState(false);

  // Computed: is the current user the owner of this project?
  const isOwner = (members || []).some(
    (m) => m.user_id === currentUserId && m.role === "owner",
  );

  const fetchAll = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const [mRes, iRes] = await Promise.all([
        fetch(`/api/projects/${encodeURIComponent(projectId)}/members`),
        fetch(`/api/projects/${encodeURIComponent(projectId)}/invites`),
      ]);
      const mData = mRes.ok ? await mRes.json() : { members: [] };
      // 403 from the invites endpoint just means "you're not the owner" —
      // hide the section rather than surfacing the error.
      const iData = iRes.ok ? await iRes.json() : { invites: [] };
      setMembers(mData.members || []);
      setInvites(iData.invites || []);
    } catch (err) {
      console.error("Failed to load members:", err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // Refetch when the dialog opens.
  useEffect(() => {
    if (open) {
      setError(null);
      setSuccess(null);
      setEmail("");
      setFallbackLink(null);
      setCopied(false);
      fetchAll();
    }
  }, [open, fetchAll]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const handleSubmit = async (e) => {
    e?.preventDefault?.();
    setError(null);
    setSuccess(null);

    const trimmed = email.trim();
    if (!isEmailish(trimmed)) {
      setError("Please enter a valid email address.");
      return;
    }

    setSending(true);
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/invites`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: trimmed }),
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        if (res.status === 409) {
          setError("This person is already invited or a member.");
        } else if (res.status === 403) {
          setError("Only project owners can invite collaborators.");
        } else if (res.status === 400) {
          setError(data.detail || "Please enter a valid email.");
        } else {
          setError(data.detail || `Failed to send invite (${res.status}).`);
        }
        return;
      }

      if (data.delivery === "user_exists") {
        // Recipient already has a Lucid account — no email needed; they
        // see the invitation banner instantly on their dashboard.
        setSuccess(
          `Invite ready for ${trimmed} — they'll see it on their dashboard right away.`,
        );
        setFallbackLink(null);
      } else if (data.warning) {
        setSuccess(
          `Invite created for ${trimmed} — but the email didn't send. Copy the link below and share it directly.`,
        );
        setFallbackLink(data.accept_url || null);
      } else {
        setSuccess(`Invite sent to ${trimmed}.`);
        setFallbackLink(null);
      }
      setCopied(false);
      setEmail("");
      await fetchAll();
    } catch (err) {
      console.error("Invite send failed:", err);
      setError("Network error — please try again.");
    } finally {
      setSending(false);
    }
  };

  const handleRevoke = async (inviteId) => {
    try {
      const res = await fetch(`/api/invites/${encodeURIComponent(inviteId)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || `Failed to revoke (${res.status}).`);
        return;
      }
      await fetchAll();
    } catch (err) {
      console.error("Revoke failed:", err);
      setError("Network error — please try again.");
    }
  };

  const handleRemove = async (userId) => {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || `Failed to remove (${res.status}).`);
        return;
      }
      await fetchAll();
    } catch (err) {
      console.error("Remove failed:", err);
      setError("Network error — please try again.");
    }
  };

  if (!open) return null;
  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
      onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}>
        {/* ── Header ── */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-100">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-slate-900">
              Invite collaborators
            </h2>
            <p className="text-xs text-slate-500 mt-0.5 truncate">
              {projectTitle ? `to ${projectTitle}` : "to this project"}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100"
            aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* ── Invite form (owner only) ── */}
        {isOwner && (
          <form onSubmit={handleSubmit} className="px-5 pt-4">
            <div className="flex gap-2">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="teammate@example.com"
                className="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500"
                disabled={sending}
                autoFocus
              />
              <button
                type="submit"
                disabled={sending || !email.trim()}
                className={cn(
                  "inline-flex items-center gap-1.5 px-3.5 py-2 rounded-md text-sm font-medium",
                  "bg-blue-600 text-white hover:bg-blue-700",
                  "disabled:opacity-50 disabled:cursor-not-allowed",
                )}>
                {sending ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
                Send invite
              </button>
            </div>
          </form>
        )}

        {/* ── Feedback ── */}
        {(error || success) && (
          <div className="px-5 pt-3">
            {error && (
              <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-red-50 border border-red-100 text-sm text-red-700">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}
            {success && (
              <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-emerald-50 border border-emerald-100 text-sm text-emerald-700">
                <Check className="w-4 h-4 mt-0.5 shrink-0" />
                <span>{success}</span>
              </div>
            )}
            {fallbackLink && (
              <div className="mt-2 flex items-center gap-2 px-3 py-2 rounded-md bg-slate-50 border border-slate-200">
                <input
                  readOnly
                  value={fallbackLink}
                  onFocus={(e) => e.target.select()}
                  className="flex-1 bg-transparent text-xs font-mono text-slate-700 outline-none min-w-0 truncate"
                />
                <button
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(fallbackLink);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 2000);
                    } catch {
                      // navigator.clipboard can fail on non-HTTPS or older
                      // browsers — manual select+copy still works from the
                      // input field above.
                    }
                  }}
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200 rounded shrink-0"
                  title="Copy link">
                  {copied ? (
                    <>
                      <Check className="w-3 h-3" /> Copied
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" /> Copy
                    </>
                  )}
                </button>
              </div>
            )}
          </div>
        )}

        {/* ── Members + invites list ── */}
        <div className="px-5 py-4 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-8 text-slate-400">
              <Loader2 className="w-4 h-4 animate-spin" />
            </div>
          ) : (
            <MembersList
              members={members}
              invites={invites}
              currentUserId={currentUserId}
              isOwner={isOwner}
              onRevoke={handleRevoke}
              onRemove={handleRemove}
            />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
