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
