import { NextResponse } from 'next/server';

const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const MODEL = 'gemini-2.5-flash-lite';
const GEMINI_URL = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${GEMINI_API_KEY}`;

export async function POST(request) {
  try {
    const { message, repoName } = await request.json();

    if (!message) {
      return NextResponse.json({ title: 'New Conversation' });
    }

    if (!GEMINI_API_KEY) {
      console.error('GEMINI_API_KEY is not set');
      return NextResponse.json({ title: fallbackTitle(message) });
    }

    const prompt = `You are a conversation title generator. Generate a short professional title (2-5 words, Title Case) for this user message in a coding assistant chat. Return ONLY the title, nothing else.

User message: ${message}${repoName ? `\nRepository: ${repoName}` : ''}`;

    const res = await fetch(GEMINI_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: {
          temperature: 0.7,
          maxOutputTokens: 30,
        },
      }),
    });

    if (!res.ok) {
      const errBody = await res.text();
      console.error(`Gemini API error (${res.status}):`, errBody);
      return NextResponse.json({ title: fallbackTitle(message) });
    }

    const data = await res.json();
    let title = data.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || '';

    // Clean up: remove quotes, asterisks, trailing punctuation
    title = title.replace(/^["'`*]+|["'`*]+$/g, '').replace(/[.!?]+$/, '').trim();

    if (!title) {
      return NextResponse.json({ title: fallbackTitle(message) });
    }

    return NextResponse.json({ title });
  } catch (error) {
    console.error('Title generation error:', error);
    return NextResponse.json({ title: 'New Conversation' });
  }
}

function fallbackTitle(message) {
  if (message.length > 50) {
    return message.slice(0, 50).replace(/\s+\S*$/, '…');
  }
  return message.replace(/\b\w/g, c => c.toUpperCase());
}
