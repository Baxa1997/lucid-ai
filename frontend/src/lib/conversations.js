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
    .single();

  if (error) {
    console.error('Failed to create conversation:', error);
    return null;
  }
  return data;
}

/**
 * List all conversations for the current user.
 * Ordered by most recent first.
 */
export async function listConversations() {
  const supabase = getSupabaseBrowserClient();

  const { data, error } = await supabase
    .from('conversations')
    .select('*')
    .order('updated_at', { ascending: false });

  if (error) {
    console.error('Failed to list conversations:', error);
    return [];
  }
  return data || [];
}

/**
 * Get a single conversation by ID.
 */
export async function getConversation(conversationId) {
  const supabase = getSupabaseBrowserClient();

  const { data, error } = await supabase
    .from('conversations')
    .select('*')
    .eq('id', conversationId)
    .single();

  if (error) {
    console.error('Failed to get conversation:', error);
    return null;
  }
  return data;
}

/**
 * Update conversation metadata (title, status, etc.)
 */
export async function updateConversation(conversationId, updates) {
  const supabase = getSupabaseBrowserClient();

  const { data, error } = await supabase
    .from('conversations')
    .update(updates)
    .eq('id', conversationId)
    .select()
    .single();

  if (error) {
    console.error('Failed to update conversation:', error);
    return null;
  }
  return data;
}

/**
 * Delete a conversation and all its messages (cascade).
 */
export async function deleteConversation(conversationId) {
  const supabase = getSupabaseBrowserClient();

  const { error } = await supabase
    .from('conversations')
    .delete()
    .eq('id', conversationId);

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
    .single();

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
