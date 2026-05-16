// ─────────────────────────────────────────────────────────
//  Lucid AI — Conversations helper (Supabase)
//  CRUD operations for conversations & messages
//  All scoped to the authenticated user via RLS
// ─────────────────────────────────────────────────────────

import { getSupabaseBrowserClient } from '@/lib/supabase/client';

/**
 * Create a new conversation when user launches a workspace.
 * Returns the created conversation object.
 */
export async function createConversation({ repoName, repoProvider, repoUrl, branch, title }) {
  const supabase = getSupabaseBrowserClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  const { data, error } = await supabase
    .from('conversations')
    .insert({
      user_id: user.id,
      title: title || repoName || 'New Conversation',
      repo_name: repoName || null,
      repo_provider: repoProvider || null,
      repo_url: repoUrl || null,
      branch: branch || null,
      status: 'active',
    })
    .select()
    .maybeSingle();

  if (error) {
    console.error('Failed to create conversation:', error);
    return null;
  }
  return data;
}

/**
 * List all conversations for the current user.
 * Reads from chat_sessions (the real data layer), deduplicates by project_id.
 */
export async function listConversations() {
  const supabase = getSupabaseBrowserClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return [];

  // No .eq('user_id', user.id) — the RLS policy on chat_sessions (from
  // migration 020) already restricts SELECT to project members, so this
  // returns both owned projects AND projects the user was invited to.
  // Adding an explicit user_id filter would hide invited projects.
  const { data, error } = await supabase
    .from('chat_sessions')
    .select('project_id, user_repo_url, user_repo_provider, title, created_at, updated_at, is_active')
    .order('updated_at', { ascending: false })
    .limit(100);

  if (error) return [];

  // Deduplicate by project_id — keep the most recent session per project
  const seen = new Set();
  return (data || [])
    .filter(row => {
      if (!row.project_id) return false;
      if (row.is_active === false) return false;
      if (seen.has(row.project_id)) return false;
      seen.add(row.project_id);
      return true;
    })
    .map(row => {
      const repoUrl = row.user_repo_url || null;
      const repoName = repoUrl
        ? repoUrl.replace(/\.git$/, '').split('/').slice(-2).join('/')
        : null;
      const fallbackTitle = repoName?.split('/').pop() || row.title || 'Project';
      return {
        id: row.project_id,
        title: fallbackTitle,
        repo_name: repoName,
        repo_provider: row.user_repo_provider || null,
        repo_url: repoUrl,
        status: 'active',
        created_at: row.created_at,
        updated_at: row.updated_at,
      };
    });
}

/**
 * Get a single conversation by ID.
 * Reads from chat_sessions (the real data layer) and shapes the result
 * to match what the workspace page expects.
 */
export async function getConversation(conversationId) {
  const supabase = getSupabaseBrowserClient();

  // Multiple chat_sessions rows can share the same project_id slug —
  // each WS connect creates a fresh row (with an empty/placeholder
  // title). The ORIGINAL row created by the project owner has the real
  // project title; later rows are placeholders. Prefer rows with a
  // non-null title; fall back to the oldest one as a tiebreaker
  // (oldest = the owner's first session = the real project metadata).
  const { data: rows, error } = await supabase
    .from('chat_sessions')
    .select('id, project_id, title, user_repo_url, user_repo_provider, platform_repo_url, platform_repo_branch, created_at')
    .eq('project_id', conversationId)
    .order('created_at', { ascending: true })
    .limit(20);

  if (error || !rows || rows.length === 0) return null;

  const data =
    rows.find((r) => r.title && r.title.trim().length > 0) || rows[0];

  // Prefer user_repo_url (user linked their own repo).
  // Fall back to platform_repo_url (wizard/scratch project — repo created by Lucid).
  const repoUrl = data.user_repo_url || data.platform_repo_url || null;
  const repoName = repoUrl
    ? repoUrl.replace(/\.git$/, '').split('/').slice(-2).join('/')
    : null;

  return {
    // Historical: `id` here is the URL/project slug, NOT chat_sessions.id.
    // Most callers depend on that aliasing, so we keep it. `db_id` exposes
    // the actual chat_sessions PK so features that interact with related
    // tables (project_members, project_invites) can use the right key.
    id: conversationId,
    db_id: data.id,
    // DB title is the brand name set by the landing pipeline — prefer it over
    // the repo slug so landing projects show "The Bali Haven" not "lucid-ws-...".
    title: data.title || repoName?.split('/').pop() || 'Project',
    repo_name: repoName,
    repo_provider: data.user_repo_provider || null,
    repo_url: repoUrl,
    branch: data.platform_repo_branch || 'main',
    is_platform_owned: !data.user_repo_url && !!data.platform_repo_url,
  };
}

/**
 * Update conversation metadata (title, status, etc.)
 * Writes to chat_sessions — the conversations table no longer exists.
 */
export async function updateConversation(conversationId, updates) {
  // Status updates are ephemeral — not persisted anywhere critical.
  // Silently succeed so callers don't see 406 errors in the console.
  return null;
}

/**
 * Hide a conversation from the conversations list without deleting the project.
 * Sets is_active = false so it no longer appears in listConversations,
 * but the chat_sessions row (and its project data) remains intact.
 */
export async function deleteConversation(conversationId) {
  const supabase = getSupabaseBrowserClient();

  const { error } = await supabase
    .from('chat_sessions')
    .update({ is_active: false })
    .eq('project_id', conversationId);

  return !error;
}

/**
 * Hide multiple conversations at once without deleting the projects.
 */
export async function deleteConversations(conversationIds) {
  const supabase = getSupabaseBrowserClient();

  const { error } = await supabase
    .from('chat_sessions')
    .update({ is_active: false })
    .in('project_id', conversationIds);

  return !error;
}

// ─────────────────────────────────────────────────────────
//  Messages
// ─────────────────────────────────────────────────────────

/**
 * Add a message to a conversation.
 * Also updates the conversation's last_message and message_count.
 */
export async function addMessage(conversationId, { role, content }) {
  const supabase = getSupabaseBrowserClient();

  // Insert the message
  const { data: msg, error: msgError } = await supabase
    .from('messages')
    .insert({
      conversation_id: conversationId,
      role,
      content,
    })
    .select()
    .maybeSingle();

  if (msgError) {
    console.error('Failed to add message:', msgError);
    return null;
  }

  // Update conversation metadata
  await supabase
    .from('conversations')
    .update({
      last_message: content.slice(0, 200),
      message_count: await getMessageCount(conversationId),
    })
    .eq('id', conversationId);

  return msg;
}

/**
 * Get all messages for a conversation, ordered chronologically.
 */
export async function getMessages(conversationId) {
  const supabase = getSupabaseBrowserClient();

  const { data, error } = await supabase
    .from('messages')
    .select('*')
    .eq('conversation_id', conversationId)
    .order('created_at', { ascending: true });

  if (error) {
    console.error('Failed to get messages:', error);
    return [];
  }
  return data || [];
}

/**
 * Get message count for a conversation.
 */
async function getMessageCount(conversationId) {
  const supabase = getSupabaseBrowserClient();

  const { count, error } = await supabase
    .from('messages')
    .select('*', { count: 'exact', head: true })
    .eq('conversation_id', conversationId);

  return error ? 0 : count;
}

// ─────────────────────────────────────────────────────────
//  Unified Chat History (reads from chat_messages table,
//  the same table the backend writes to)
// ─────────────────────────────────────────────────────────

/**
 * Get chat history for a workspace session.
 * Reads from chat_messages via chat_sessions.project_id match.
 * This is the SAME table the backend WS handler writes to,
 * so history survives across refreshes and reconnects.
 *
 * @param {string} projectId — the conversationId (UUID from URL)
 */
export async function getChatHistory(projectId) {
  const supabase = getSupabaseBrowserClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return [];

  try {
    // 1. Find ALL chat sessions for this project (not just the latest)
    const { data: sessions, error: sessErr } = await supabase
      .from('chat_sessions')
      .select('id')
      .eq('project_id', projectId)
      .eq('user_id', user.id)
      .order('created_at', { ascending: false });

    if (sessErr || !sessions?.length) return [];

    const sessionIds = sessions.map(s => s.id);

    // 2. Read all messages from ALL chat sessions for this project
    const { data: messages, error: msgErr } = await supabase
      .from('chat_messages')
      .select('*')
      .in('session_id', sessionIds)
      .order('created_at', { ascending: true });

    if (msgErr) {
      console.error('Failed to load chat history:', msgErr);
      return [];
    }

    return messages || [];
  } catch (err) {
    console.error('getChatHistory error:', err);
    return [];
  }
}

/**
 * Save a user message to the chat_messages table (same table backend uses).
 *
 * @param {string} projectId — conversationId to find the chat_session
 * @param {object} msg — { role, content }
 */
export async function saveChatMessage(projectId, { role, content }) {
  const supabase = getSupabaseBrowserClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  try {
    // Find the LATEST chat_session for this project
    const { data: sessions } = await supabase
      .from('chat_sessions')
      .select('id')
      .eq('project_id', projectId)
      .eq('user_id', user.id)
      .order('created_at', { ascending: false })
      .limit(1);

    if (!sessions?.length) {
      console.warn('No chat session found for project:', projectId);
      return null;
    }

    const chatSessionId = sessions[0].id;

    const { data, error } = await supabase
      .from('chat_messages')
      .insert({
        session_id: chatSessionId,
        role,
        content,
        event_type: role === 'user' ? 'UserMessage' : 'AssistantMessage',
      })
      .select()
      .single();

    if (error) {
      console.error('Failed to save chat message:', error);
      return null;
    }
    return data;
  } catch (err) {
    console.error('saveChatMessage error:', err);
    return null;
  }
}
