import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { decrypt } from '@/lib/crypto';

const SUPABASE_MGMT_API = 'https://api.supabase.com/v1';

// ─────────────────────────────────────────────────────────
//  POST /api/supabase-wakeup
//  Cron endpoint — pings all active Supabase projects to
//  prevent free-tier pausing (1 week inactivity).
//
//  Run every 3 days via external cron (e.g. cron-job.org).
//  Protected by X-Cron-Secret header.
//
//  For each active project:
//    GET https://{ref}.supabase.co/rest/v1/
//    If 503 → POST /v1/projects/{ref}/restore → poll until active
// ─────────────────────────────────────────────────────────

export async function POST(req) {
  // ── Verify cron secret ─────────────────────────────
  const cronSecret = req.headers.get('x-cron-secret');
  const expectedSecret = process.env.CRON_SECRET || process.env.SUPABASE_SERVICE_KEY;

  if (!cronSecret || cronSecret !== expectedSecret) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  // ── Use service role client to bypass RLS ──────────
  const supabase = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY,
    { auth: { persistSession: false } }
  );

  const managementKey =
    process.env.SUPABASE_ACCESS_TOKEN ||
    process.env.SUPABASE_MANAGEMENT_API_KEY;

  // ── Load all active Supabase projects ──────────────
  const { data: projects, error } = await supabase
    .from('supabase_projects')
    .select('id, supabase_ref, supabase_url, anon_key_enc, anon_key_iv, status')
    .eq('status', 'ACTIVE')
    .is('deleted_at', null);

  if (error) {
    console.error('[supabase-wakeup] DB error:', error.message);
    return NextResponse.json({ error: 'Failed to load projects' }, { status: 500 });
  }

  if (!projects?.length) {
    return NextResponse.json({ ok: true, pinged: 0, message: 'No active projects' });
  }

  const results = [];

  for (const project of projects) {
    const { id, supabase_ref, supabase_url, anon_key_enc, anon_key_iv } = project;
    let anonKey = '';

    try {
      if (anon_key_enc && anon_key_iv) {
        anonKey = decrypt(anon_key_enc, anon_key_iv);
      }
    } catch {
      results.push({ ref: supabase_ref, status: 'error', reason: 'decrypt_failed' });
      continue;
    }

    if (!anonKey) {
      results.push({ ref: supabase_ref, status: 'skipped', reason: 'no_anon_key' });
      continue;
    }

    // ── Ping the project ──────────────────────────────
    try {
      const pingRes = await fetch(`${supabase_url}/rest/v1/`, {
        headers: {
          apikey: anonKey,
          Authorization: `Bearer ${anonKey}`,
        },
        signal: AbortSignal.timeout(10000),
      });

      if (pingRes.ok || pingRes.status === 200) {
        // Project is alive — update last_ping_at
        await supabase
          .from('supabase_projects')
          .update({ last_ping_at: new Date().toISOString() })
          .eq('id', id);

        results.push({ ref: supabase_ref, status: 'alive' });
      } else if (pingRes.status === 503) {
        // Project is paused — attempt restore
        console.log(`[supabase-wakeup] Project ${supabase_ref} is paused (503), attempting restore…`);

        if (managementKey) {
          try {
            const restoreRes = await fetch(`${SUPABASE_MGMT_API}/projects/${supabase_ref}/restore`, {
              method: 'POST',
              headers: {
                Authorization: `Bearer ${managementKey}`,
              },
              signal: AbortSignal.timeout(15000),
            });

            if (restoreRes.ok) {
              // Poll until active (max 3 minutes)
              let restored = false;
              for (let i = 0; i < 36; i++) {
                await sleep(5000);
                try {
                  const statusRes = await fetch(`${SUPABASE_MGMT_API}/projects/${supabase_ref}`, {
                    headers: { Authorization: `Bearer ${managementKey}` },
                    signal: AbortSignal.timeout(10000),
                  });
                  if (statusRes.ok) {
                    const data = await statusRes.json();
                    if (data.status === 'ACTIVE_HEALTHY' || data.status === 'ACTIVE') {
                      restored = true;
                      break;
                    }
                  }
                } catch { /* continue */ }
              }

              await supabase
                .from('supabase_projects')
                .update({
                  status: restored ? 'ACTIVE' : 'PAUSED',
                  last_ping_at: new Date().toISOString(),
                })
                .eq('id', id);

              results.push({
                ref: supabase_ref,
                status: restored ? 'restored' : 'restore_timeout',
              });
            } else {
              results.push({ ref: supabase_ref, status: 'restore_failed' });
            }
          } catch (err) {
            results.push({ ref: supabase_ref, status: 'restore_error', reason: err.message });
          }
        } else {
          // Mark as paused in DB
          await supabase
            .from('supabase_projects')
            .update({ status: 'PAUSED' })
            .eq('id', id);

          results.push({ ref: supabase_ref, status: 'paused_no_mgmt_key' });
        }
      } else {
        results.push({ ref: supabase_ref, status: 'ping_error', httpStatus: pingRes.status });
      }
    } catch (err) {
      results.push({ ref: supabase_ref, status: 'timeout', reason: err.message });
    }
  }

  const summary = {
    ok: true,
    pinged: projects.length,
    alive: results.filter((r) => r.status === 'alive').length,
    restored: results.filter((r) => r.status === 'restored').length,
    paused: results.filter((r) => r.status === 'paused_no_mgmt_key' || r.status === 'restore_failed').length,
    errors: results.filter((r) => r.status === 'error' || r.status === 'timeout' || r.status === 'restore_error').length,
    details: results,
  };

  console.log(`[supabase-wakeup] Done: ${summary.alive} alive, ${summary.restored} restored, ${summary.paused} paused, ${summary.errors} errors`);
  return NextResponse.json(summary);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
