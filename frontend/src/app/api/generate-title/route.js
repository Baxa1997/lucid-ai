import { NextResponse } from 'next/server';

// ─────────────────────────────────────────────────────────
//  POST /api/generate-title
//
//  Generates a short title for a conversation from the first message.
//  The previous Gemini implementation (AI Studio + GEMINI_API_KEY) was
//  disabled and the API key removed from .env. Until this is migrated
//  to Vertex AI like /api/intent-check, the route returns a simple
//  text-derived fallback.
// ─────────────────────────────────────────────────────────

export async function POST(request) {
  try {
    const { message } = await request.json();
    if (!message) {
      return NextResponse.json({ title: 'New Conversation' });
    }
    return NextResponse.json({ title: fallbackTitle(message) });
  } catch (error) {
    console.error('Title generation error:', error);
    return NextResponse.json({ title: 'New Conversation' });
  }
}

function fallbackTitle(message) {
  if (message.length > 50) {
    return message.slice(0, 50).replace(/\s+\S*$/, '…');
  }
  return message.replace(/\b\w/g, (c) => c.toUpperCase());
}
