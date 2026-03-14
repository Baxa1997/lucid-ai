import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';

// ─────────────────────────────────────────────────────────
//  Supported providers and models (mirrors the frontend UI)
// ─────────────────────────────────────────────────────────
const VALID_PROVIDERS = ['google', 'anthropic'];

const VALID_MODELS = {
  google: [
    'gemini/gemini-3-flash-preview',
    'gemini/gemini-3.1-pro-preview',
  ],
  anthropic: [
    'anthropic/claude-3-5-sonnet-20241022',
    'anthropic/claude-3-5-opus-20241022',
    'anthropic/claude-sonnet-4-6',
    'anthropic/claude-opus-4-6',
  ],
};

/**
 * GET /api/settings
 * Returns the authenticated user's LLM settings.
 * If no row exists yet, returns the default configuration.
 */
export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const supabase = await getSupabaseServerClient();

  const { data, error } = await supabase
    .from('user_settings')
    .select('llm_provider, llm_model, api_key_enc, api_key_iv')
    .eq('user_id', ctx.userId)
    .maybeSingle();

  if (error) {
    console.error('[settings GET] Supabase error:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  // Return defaults if no row yet
  if (!data) {
    return NextResponse.json({
      llm_provider: 'google',
      llm_model: 'gemini/gemini-3-flash-preview',
      has_api_key: false,
    });
  }

  const legacyMappings = {
    'gemini/gemini-2.5-flash-preview': 'gemini/gemini-3-flash-preview',
    'gemini/gemini-2.5-pro-preview': 'gemini/gemini-3.1-pro-preview',
  };
  const resolvedModel = legacyMappings[data.llm_model] || data.llm_model;

  return NextResponse.json({
    llm_provider: data.llm_provider,
    llm_model: resolvedModel,
    // Never return the raw key — just let the client know one exists
    has_api_key: !!(data.api_key_enc && data.api_key_iv),
  });
}

/**
 * PUT /api/settings
 * Upserts the authenticated user's LLM settings.
 *
 * Body: { llm_provider, llm_model, api_key? }
 *
 * The api_key is encrypted on the server before storage using
 * AES-GCM with the ENCRYPTION_KEY environment variable.
 * If api_key is omitted/null, the existing encrypted value is preserved.
 */
export async function PUT(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }

  const { llm_provider, llm_model, api_key } = body;


  if (!VALID_PROVIDERS.includes(llm_provider)) {
    return NextResponse.json(
      { error: `Invalid provider. Must be one of: ${VALID_PROVIDERS.join(', ')}` },
      { status: 400 }
    );
  }


  if (!VALID_MODELS[llm_provider]?.includes(llm_model)) {
    return NextResponse.json(
      { error: `Invalid model '${llm_model}' for provider '${llm_provider}'.` },
      { status: 400 }
    );
  }

  const supabase = await getSupabaseServerClient();


  const { data: existing, error: fetchError } = await supabase
    .from('user_settings')
    .select('api_key_enc, api_key_iv')
    .eq('user_id', ctx.userId)
    .maybeSingle();

  if (fetchError) {
    console.error('[settings PUT] Supabase fetch error:', fetchError);
    return NextResponse.json({ error: fetchError.message }, { status: 500 });
  }

  let updatePayload = {
    user_id: ctx.userId,
    llm_provider,
    llm_model,
    api_key_enc: existing?.api_key_enc || null,
    api_key_iv: existing?.api_key_iv || null,
  };
  if (api_key && api_key.trim()) {
    try {
      const { encrypt } = await import('@/lib/crypto');
      const { encrypted, iv } = encrypt(api_key.trim());
      updatePayload.api_key_enc = encrypted;
      updatePayload.api_key_iv = iv;
    } catch (encErr) {
      console.error('[settings PUT] Encryption error:', encErr);
      return NextResponse.json(
        { error: 'Failed to encrypt API key' },
        { status: 500 }
      );
    }
  }

  const { error: upsertError } = await supabase
    .from('user_settings')
    .upsert(updatePayload, { onConflict: 'user_id' });

  if (upsertError) {
    console.error('[settings PUT] Supabase upsert error:', upsertError);
    return NextResponse.json({ error: upsertError.message }, { status: 500 });
  }

  return NextResponse.json({ success: true });
}
