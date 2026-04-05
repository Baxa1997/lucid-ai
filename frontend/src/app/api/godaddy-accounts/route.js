import { NextResponse } from 'next/server';
import { requireAuth } from '@/lib/gatekeeper';
import { getSupabaseServerClient } from '@/lib/supabase/server';
import { encrypt, decrypt } from '@/lib/crypto';

// ─────────────────────────────────────────────────────────
//  /api/godaddy-accounts
//
//  CRUD for user's GoDaddy DNS accounts (multi-account).
//  GET    — list all accounts (masked keys)
//  POST   — add new account (validates against GoDaddy API)
//  PUT    — update account by id  (body: { id, ...fields })
//  DELETE — remove account by id  (body: { id })
// ─────────────────────────────────────────────────────────

// Validate GoDaddy credentials against their API
async function validateGoDaddyCredentials(apiKey, apiSecret) {
  try {
    const res = await fetch('https://api.godaddy.com/v1/domains?limit=1', {
      headers: {
        'Authorization': `sso-key ${apiKey}:${apiSecret}`,
        'Accept': 'application/json',
      },
      signal: AbortSignal.timeout(10000),
    });

    // 200 = full access, great
    if (res.ok) return { ok: true };

    // 403 ACCESS_DENIED = key is valid but lacks domain-list scope — still valid for DNS ops
    // 422 = authenticated but no domains match — still valid
    if (res.status === 403 || res.status === 422) {
      return { ok: true };
    }

    // 401 = truly invalid credentials
    if (res.status === 401) {
      return { ok: false, error: 'Invalid API key or secret. Check your GoDaddy credentials.' };
    }

    const body = await res.text().catch(() => '');
    return { ok: false, error: `GoDaddy API returned ${res.status}: ${body.slice(0, 120)}` };
  } catch (e) {
    return { ok: false, error: `Failed to reach GoDaddy API: ${e.message}` };
  }
}

// ══════════════════════════════════════════════════════════
//  GET — List accounts
// ══════════════════════════════════════════════════════════
export async function GET() {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  const supabase = await getSupabaseServerClient();
  const { data, error } = await supabase
    .from('godaddy_accounts')
    .select('id, label, domain, record_type, target, api_key_enc, api_secret_enc, created_at, updated_at')
    .eq('user_id', ctx.userId)
    .order('created_at', { ascending: true });

  if (error) {
    console.error('[godaddy-accounts GET]', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  // Return with masked key indicators (never expose encrypted values)
  const accounts = (data || []).map(a => ({
    id: a.id,
    label: a.label,
    domain: a.domain,
    record_type: a.record_type,
    target: a.target,
    has_api_key: !!a.api_key_enc,
    has_api_secret: !!a.api_secret_enc,
    created_at: a.created_at,
    updated_at: a.updated_at,
  }));

  return NextResponse.json({ accounts });
}

// ══════════════════════════════════════════════════════════
//  POST — Create new account
// ══════════════════════════════════════════════════════════
export async function POST(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try { body = await req.json(); } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const { label, domain, record_type, target, api_key, api_secret } = body;

  if (!domain?.trim()) {
    return NextResponse.json({ error: 'Domain is required' }, { status: 400 });
  }
  if (!api_key?.trim() || !api_secret?.trim()) {
    return NextResponse.json({ error: 'API Key and Secret are required' }, { status: 400 });
  }

  // Validate credentials against GoDaddy API
  const validation = await validateGoDaddyCredentials(api_key.trim(), api_secret.trim());
  if (!validation.ok) {
    return NextResponse.json({ error: validation.error }, { status: 400 });
  }

  // Encrypt sensitive fields
  const keyEnc = encrypt(api_key.trim());
  const secretEnc = encrypt(api_secret.trim());

  const supabase = await getSupabaseServerClient();
  const { data, error } = await supabase
    .from('godaddy_accounts')
    .insert({
      user_id: ctx.userId,
      label: (label || '').trim() || 'Default',
      domain: domain.trim(),
      record_type: (record_type || 'A').toUpperCase(),
      target: (target || '').trim(),
      api_key_enc: keyEnc.encrypted,
      api_key_iv: keyEnc.iv,
      api_secret_enc: secretEnc.encrypted,
      api_secret_iv: secretEnc.iv,
    })
    .select('id, label, domain, record_type, target, created_at')
    .single();

  if (error) {
    console.error('[godaddy-accounts POST]', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true, account: data }, { status: 201 });
}

// ══════════════════════════════════════════════════════════
//  PUT — Update existing account
// ══════════════════════════════════════════════════════════
export async function PUT(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try { body = await req.json(); } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const { id, label, domain, record_type, target, api_key, api_secret } = body;

  if (!id) {
    return NextResponse.json({ error: 'Account id is required' }, { status: 400 });
  }

  const supabase = await getSupabaseServerClient();

  // Build update payload
  const update = { updated_at: new Date().toISOString() };
  if (label !== undefined) update.label = label.trim();
  if (domain !== undefined) update.domain = domain.trim();
  if (record_type !== undefined) update.record_type = record_type.toUpperCase();
  if (target !== undefined) update.target = target.trim();

  // If new credentials provided, validate first then encrypt
  if (api_key?.trim() && api_secret?.trim()) {
    const validation = await validateGoDaddyCredentials(api_key.trim(), api_secret.trim());
    if (!validation.ok) {
      return NextResponse.json({ error: validation.error }, { status: 400 });
    }
    const keyEnc = encrypt(api_key.trim());
    const secretEnc = encrypt(api_secret.trim());
    update.api_key_enc = keyEnc.encrypted;
    update.api_key_iv = keyEnc.iv;
    update.api_secret_enc = secretEnc.encrypted;
    update.api_secret_iv = secretEnc.iv;
  }

  const { error } = await supabase
    .from('godaddy_accounts')
    .update(update)
    .eq('id', id)
    .eq('user_id', ctx.userId);

  if (error) {
    console.error('[godaddy-accounts PUT]', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}

// ══════════════════════════════════════════════════════════
//  DELETE — Remove account
// ══════════════════════════════════════════════════════════
export async function DELETE(req) {
  const authResult = await requireAuth();
  if (!authResult.ok) return authResult.response;
  const { ctx } = authResult;

  let body;
  try { body = await req.json(); } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const { id } = body;
  if (!id) {
    return NextResponse.json({ error: 'Account id is required' }, { status: 400 });
  }

  const supabase = await getSupabaseServerClient();
  const { error } = await supabase
    .from('godaddy_accounts')
    .delete()
    .eq('id', id)
    .eq('user_id', ctx.userId);

  if (error) {
    console.error('[godaddy-accounts DELETE]', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
