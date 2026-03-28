// ─────────────────────────────────────────────────────────
//  Lucid AI — Supabase Project Provisioning
//
//  Uses the official @supabase/mcp-server-supabase to
//  generate a full backend for the user's project.
//
//  Pipeline:
//    1. Check Supabase MCP availability
//    2. Connect to official Supabase MCP → create project,
//       apply migrations, configure auth, get API keys
//    3. Fallback to Supabase Management REST API if MCP fails
//    4. Configure Tier 1 auth (email + magic link)
//    5. Encrypt + store credentials in supabase_projects table
//    6. Return credentials + generated file contents
// ─────────────────────────────────────────────────────────

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { checkSupabaseMcp } from '@/lib/integrations/check';
import { encrypt } from '@/lib/crypto';
import { getSupabaseServerClient } from '@/lib/supabase/server';

const SUPABASE_MGMT_API = 'https://api.supabase.com/v1';

// ═══════════════════════════════════════════════════════════
//  Auth provider presets by project type
// ═══════════════════════════════════════════════════════════

const AUTH_PRESETS = {
  ecommerce: { providers: ['email', 'google', 'facebook'], suggested: ['google', 'facebook'], label: 'Email + Google + Facebook OAuth' },
  website:   { providers: ['email', 'google', 'facebook'], suggested: ['google', 'facebook'], label: 'Email + Google + Facebook OAuth' },
  admin:     { providers: ['email', 'google'],             suggested: ['google'],              label: 'Email + Google OAuth' },
  saas:      { providers: ['email', 'google', 'github'],   suggested: ['google', 'github'],    label: 'Email + Google + GitHub OAuth' },
  app:       { providers: ['email', 'google', 'github'],   suggested: ['google', 'github'],    label: 'Email + Google + GitHub OAuth' },
  default:   { providers: ['email', 'google'],             suggested: ['google'],              label: 'Email + Google OAuth' },
};

// ═══════════════════════════════════════════════════════════
//  SQL migrations by project type
// ═══════════════════════════════════════════════════════════

function getMigrations(projectType) {
  const type = projectType?.toLowerCase() || 'default';

  // Common base: profiles table with trigger
  const baseMigration = {
    name: '001_profiles',
    sql: `
      CREATE TABLE IF NOT EXISTS public.profiles (
        id         UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
        email      TEXT,
        full_name  TEXT,
        avatar_url TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
      );

      ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

      CREATE POLICY "Users can view own profile"
        ON public.profiles FOR SELECT USING (auth.uid() = id);
      CREATE POLICY "Users can update own profile"
        ON public.profiles FOR UPDATE USING (auth.uid() = id);
      CREATE POLICY "Users can insert own profile"
        ON public.profiles FOR INSERT WITH CHECK (auth.uid() = id);

      CREATE OR REPLACE FUNCTION public.handle_new_user()
      RETURNS TRIGGER AS $$
      BEGIN
        INSERT INTO public.profiles (id, email, full_name, avatar_url)
        VALUES (
          NEW.id,
          NEW.email,
          COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name'),
          COALESCE(NEW.raw_user_meta_data->>'avatar_url', NEW.raw_user_meta_data->>'picture')
        )
        ON CONFLICT (id) DO NOTHING;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

      CREATE OR REPLACE TRIGGER on_auth_user_created
        AFTER INSERT ON auth.users
        FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
    `,
  };

  const migrations = [baseMigration];

  if (type === 'ecommerce') {
    migrations.push({
      name: '002_ecommerce',
      sql: `
        CREATE TABLE IF NOT EXISTS public.categories (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name        TEXT NOT NULL,
          slug        TEXT UNIQUE NOT NULL,
          description TEXT,
          image_url   TEXT,
          created_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.products (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name        TEXT NOT NULL,
          slug        TEXT UNIQUE NOT NULL,
          description TEXT,
          price       DECIMAL(10,2) NOT NULL DEFAULT 0,
          compare_price DECIMAL(10,2),
          image_url   TEXT,
          images      JSONB DEFAULT '[]',
          category_id UUID REFERENCES public.categories(id),
          stock       INT DEFAULT 0,
          is_active   BOOLEAN DEFAULT TRUE,
          metadata    JSONB DEFAULT '{}',
          created_at  TIMESTAMPTZ DEFAULT NOW(),
          updated_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.orders (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     UUID REFERENCES auth.users(id) ON DELETE SET NULL,
          status      TEXT DEFAULT 'pending' CHECK (status IN ('pending','confirmed','shipped','delivered','cancelled')),
          total       DECIMAL(10,2) NOT NULL DEFAULT 0,
          subtotal    DECIMAL(10,2) NOT NULL DEFAULT 0,
          shipping    DECIMAL(10,2) DEFAULT 0,
          tax         DECIMAL(10,2) DEFAULT 0,
          shipping_address JSONB,
          billing_address  JSONB,
          notes       TEXT,
          created_at  TIMESTAMPTZ DEFAULT NOW(),
          updated_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.order_items (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          order_id    UUID REFERENCES public.orders(id) ON DELETE CASCADE,
          product_id  UUID REFERENCES public.products(id),
          quantity    INT NOT NULL DEFAULT 1,
          unit_price  DECIMAL(10,2) NOT NULL,
          total_price DECIMAL(10,2) NOT NULL
        );

        CREATE TABLE IF NOT EXISTS public.cart_items (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     UUID REFERENCES auth.users(id) ON DELETE CASCADE,
          product_id  UUID REFERENCES public.products(id) ON DELETE CASCADE,
          quantity    INT NOT NULL DEFAULT 1,
          created_at  TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(user_id, product_id)
        );

        ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.categories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.order_items ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.cart_items ENABLE ROW LEVEL SECURITY;

        CREATE POLICY "Products are viewable by everyone" ON public.products FOR SELECT USING (true);
        CREATE POLICY "Categories are viewable by everyone" ON public.categories FOR SELECT USING (true);
        CREATE POLICY "Users can view own orders" ON public.orders FOR SELECT USING (auth.uid() = user_id);
        CREATE POLICY "Users can insert own orders" ON public.orders FOR INSERT WITH CHECK (auth.uid() = user_id);
        CREATE POLICY "Users can view own order items" ON public.order_items FOR SELECT
          USING (order_id IN (SELECT id FROM public.orders WHERE user_id = auth.uid()));
        CREATE POLICY "Users can manage own cart" ON public.cart_items FOR ALL USING (auth.uid() = user_id);
      `,
    });
  } else if (type === 'admin') {
    migrations.push({
      name: '002_admin',
      sql: `
        CREATE TABLE IF NOT EXISTS public.settings (
          id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          key        TEXT UNIQUE NOT NULL,
          value      JSONB NOT NULL DEFAULT '{}',
          updated_at TIMESTAMPTZ DEFAULT NOW(),
          updated_by UUID REFERENCES auth.users(id)
        );

        CREATE TABLE IF NOT EXISTS public.audit_log (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     UUID REFERENCES auth.users(id),
          action      TEXT NOT NULL,
          resource    TEXT,
          resource_id TEXT,
          details     JSONB DEFAULT '{}',
          ip_address  TEXT,
          created_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.roles (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name        TEXT UNIQUE NOT NULL,
          permissions JSONB DEFAULT '[]',
          created_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.user_roles (
          user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
          role_id UUID REFERENCES public.roles(id) ON DELETE CASCADE,
          PRIMARY KEY (user_id, role_id)
        );

        ALTER TABLE public.settings ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;

        CREATE POLICY "Settings are viewable by authenticated" ON public.settings FOR SELECT USING (auth.uid() IS NOT NULL);
        CREATE POLICY "Audit log viewable by authenticated" ON public.audit_log FOR SELECT USING (auth.uid() IS NOT NULL);
        CREATE POLICY "Roles are viewable by authenticated" ON public.roles FOR SELECT USING (auth.uid() IS NOT NULL);
        CREATE POLICY "User roles are viewable by authenticated" ON public.user_roles FOR SELECT USING (auth.uid() IS NOT NULL);

        INSERT INTO public.roles (name, permissions) VALUES
          ('admin', '["*"]'),
          ('editor', '["read", "write"]'),
          ('viewer', '["read"]')
        ON CONFLICT (name) DO NOTHING;
      `,
    });
  } else if (type === 'saas' || type === 'app') {
    migrations.push({
      name: '002_saas',
      sql: `
        CREATE TABLE IF NOT EXISTS public.organizations (
          id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name       TEXT NOT NULL,
          slug       TEXT UNIQUE NOT NULL,
          logo_url   TEXT,
          plan       TEXT DEFAULT 'free' CHECK (plan IN ('free','pro','enterprise')),
          metadata   JSONB DEFAULT '{}',
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.org_members (
          id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id  UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
          user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
          role    TEXT DEFAULT 'member' CHECK (role IN ('owner','admin','member')),
          joined_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(org_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS public.projects (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
          name        TEXT NOT NULL,
          description TEXT,
          status      TEXT DEFAULT 'active' CHECK (status IN ('active','archived','deleted')),
          settings    JSONB DEFAULT '{}',
          created_at  TIMESTAMPTZ DEFAULT NOW(),
          updated_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.invitations (
          id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id     UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
          email      TEXT NOT NULL,
          role       TEXT DEFAULT 'member',
          token      TEXT UNIQUE NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL,
          accepted   BOOLEAN DEFAULT FALSE,
          created_at TIMESTAMPTZ DEFAULT NOW()
        );

        ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.org_members ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.invitations ENABLE ROW LEVEL SECURITY;

        CREATE POLICY "Org members can view org" ON public.organizations FOR SELECT
          USING (id IN (SELECT org_id FROM public.org_members WHERE user_id = auth.uid()));
        CREATE POLICY "Members can view org members" ON public.org_members FOR SELECT
          USING (org_id IN (SELECT org_id FROM public.org_members WHERE user_id = auth.uid()));
        CREATE POLICY "Members can view projects" ON public.projects FOR SELECT
          USING (org_id IN (SELECT org_id FROM public.org_members WHERE user_id = auth.uid()));
        CREATE POLICY "Members can manage projects" ON public.projects FOR ALL
          USING (org_id IN (SELECT org_id FROM public.org_members WHERE user_id = auth.uid()));
        CREATE POLICY "Invitations viewable by org members" ON public.invitations FOR SELECT
          USING (org_id IN (SELECT org_id FROM public.org_members WHERE user_id = auth.uid()));
      `,
    });
  } else {
    // website / default — simple content tables
    migrations.push({
      name: '002_content',
      sql: `
        CREATE TABLE IF NOT EXISTS public.pages (
          id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          title      TEXT NOT NULL,
          slug       TEXT UNIQUE NOT NULL,
          content    TEXT,
          metadata   JSONB DEFAULT '{}',
          is_published BOOLEAN DEFAULT FALSE,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS public.contacts (
          id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name       TEXT NOT NULL,
          email      TEXT NOT NULL,
          message    TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW()
        );

        ALTER TABLE public.pages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.contacts ENABLE ROW LEVEL SECURITY;

        CREATE POLICY "Published pages are viewable by everyone" ON public.pages FOR SELECT USING (is_published = true);
        CREATE POLICY "Anyone can submit contact" ON public.contacts FOR INSERT WITH CHECK (true);
      `,
    });
  }

  // Storage bucket for uploads
  migrations.push({
    name: '003_storage',
    sql: `
      INSERT INTO storage.buckets (id, name, public)
      VALUES ('uploads', 'uploads', true)
      ON CONFLICT (id) DO NOTHING;

      CREATE POLICY "Authenticated users can upload" ON storage.objects
        FOR INSERT WITH CHECK (bucket_id = 'uploads' AND auth.uid() IS NOT NULL);
      CREATE POLICY "Anyone can view uploads" ON storage.objects
        FOR SELECT USING (bucket_id = 'uploads');
    `,
  });

  return migrations;
}

// ═══════════════════════════════════════════════════════════
//  Main provisioning function
// ═══════════════════════════════════════════════════════════

/**
 * Provision a full Supabase backend for a wizard build.
 *
 * @param {object} opts
 * @param {string} opts.projectName
 * @param {string} opts.projectSlug — used for naming + URLs
 * @param {string} opts.projectType — 'ecommerce'|'admin'|'saas'|'app'|'website'
 * @param {string} opts.userId — platform user ID
 * @param {string} opts.platformProjectId — platform project ID
 * @returns {{ ok, supabaseUrl, supabaseAnonKey, supabaseRef, method, tables, authProviders, suggestedProviders, clientFileContent, authFileContent, authButtonsFileContent, error? }}
 */
export async function provisionSupabase({
  projectName,
  projectSlug,
  projectType = 'default',
  userId,
  platformProjectId,
}) {
  // Build the canonical project name: {userId_prefix}-{projectSlug}
  const canonicalName = `${userId.substring(0, 8)}-${(projectSlug || projectName).toLowerCase().replace(/[^a-z0-9-]/g, '-')}`;
  const mcpCheck = checkSupabaseMcp();
  let result = null;

  // ── Try official Supabase MCP first ────────────────────
  if (mcpCheck.available && mcpCheck.hasToken) {
    console.log('[supabase-provision] Attempting provisioning via official Supabase MCP…');
    result = await provisionViaMcp({ projectName: canonicalName, projectType, serverConfig: mcpCheck.serverConfig });
    if (result) {
      console.log('[supabase-provision] ✓ Supabase provisioned via MCP');
    }
  } else {
    console.log('[supabase-provision] MCP not available:', mcpCheck.reason || 'no token');
  }

  // ── Fallback to REST API ───────────────────────────────
  if (!result) {
    console.log('[supabase-provision] Attempting REST API fallback…');
    result = await provisionViaRestApi({ projectName: canonicalName, projectType, projectSlug });
    if (result) {
      console.log('[supabase-provision] ✓ Supabase provisioned via REST API');
    }
  }

  if (!result) {
    return {
      ok: false,
      error: 'All provisioning methods failed. Ensure SUPABASE_ACCESS_TOKEN is set.',
    };
  }

  // ── Configure Tier 1 auth (email + magic link) ─────────
  try {
    await configureTier1Auth({
      ref: result.ref,
      projectSlug: projectSlug || projectName,
    });
    console.log('[supabase-provision] ✓ Tier 1 auth configured');
  } catch (err) {
    console.warn('[supabase-provision] Tier 1 auth config failed (non-fatal):', err.message);
  }

  // ── Determine suggested OAuth providers (Tier 2) ───────
  const preset = AUTH_PRESETS[projectType?.toLowerCase()] || AUTH_PRESETS.default;
  const suggestedProviders = preset.suggested.map((p) => ({
    provider: p,
    enabled: false,
    client_id: null,
    client_secret: null,
  }));

  // ── Store credentials in supabase_projects table ───────
  try {
    await storeSupabaseProject({
      userId,
      platformProjectId,
      projectSlug: projectSlug || projectName,
      supabaseRef: result.ref,
      supabaseUrl: result.supabaseUrl,
      supabaseAnonKey: result.supabaseAnonKey,
      supabaseServiceKey: result.supabaseServiceKey,
      dbPassword: result.dbPassword,
      region: 'us-east-1',
      suggestedProviders,
    });
  } catch (err) {
    console.error('[supabase-provision] Credential storage error (non-fatal):', err.message);
  }

  // ── Generate client files for the user's project ───────
  const clientFileContent = generateSupabaseClientFile();
  const authFileContent = generateSupabaseAuthFile();
  const authButtonsFileContent = generateAuthButtonsFile();
  const envFileContent = generateEnvProductionFile({
    supabaseUrl: result.supabaseUrl,
    supabaseAnonKey: result.supabaseAnonKey,
  });

  return {
    ok: true,
    method: result.method,
    supabaseUrl: result.supabaseUrl,
    supabaseAnonKey: result.supabaseAnonKey,
    supabaseRef: result.ref,
    tables: result.tables || [],
    authProviders: preset.providers,
    suggestedProviders,
    clientFileContent,
    authFileContent,
    authButtonsFileContent,
    envFileContent,
    // Never expose service key to frontend
  };
}

// ═══════════════════════════════════════════════════════════
//  Tier 1 Auth Configuration
//  Enabled immediately — no user credentials needed
// ═══════════════════════════════════════════════════════════

async function configureTier1Auth({ ref, projectSlug }) {
  const managementKey =
    process.env.SUPABASE_ACCESS_TOKEN ||
    process.env.SUPABASE_MANAGEMENT_API_KEY;

  if (!managementKey?.trim()) return;

  const slug = projectSlug.toLowerCase().replace(/[^a-z0-9-]/g, '-');

  const authConfig = {
    site_url: `https://${slug}.udevs.io`,
    additional_redirect_urls: [
      `https://${slug}.udevs.io/auth/callback`,
      `https://${slug}.staging.udevs.io/auth/callback`,
    ],
    email_confirm: false,
    enable_signup: true,
    external_email_enabled: true,
    mailer_otp_enabled: true,
  };

  const res = await fetch(`${SUPABASE_MGMT_API}/projects/${ref}/config/auth`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${managementKey}`,
    },
    body: JSON.stringify(authConfig),
    signal: AbortSignal.timeout(15000),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Auth config PATCH failed (${res.status}): ${err}`);
  }
}

// ═══════════════════════════════════════════════════════════
//  MCP provisioning (official @supabase/mcp-server-supabase)
// ═══════════════════════════════════════════════════════════

async function provisionViaMcp({ projectName, projectType, serverConfig }) {
  let client = null;
  let transport = null;

  try {
    const token = process.env.SUPABASE_ACCESS_TOKEN || process.env.SUPABASE_MANAGEMENT_API_KEY;

    // Use the official binary from @supabase/mcp-server-supabase
    const command = serverConfig?.command || 'npx';
    const defaultArgs = command === 'npx'
      ? ['-y', '@supabase/mcp-server-supabase', '--access-token', token]
      : [];
    const args = serverConfig?.args || defaultArgs;

    const serverEnv = { ...process.env };
    if (serverConfig?.env) {
      for (const [key, val] of Object.entries(serverConfig.env)) {
        serverEnv[key] = val.replace(/\$\{(\w+)\}/g, (_, v) => process.env[v] || '');
      }
    }

    transport = new StdioClientTransport({ command, args, env: serverEnv });
    client = new Client(
      { name: 'lucid-ai', version: '1.0.0' },
      { capabilities: {} }
    );

    await client.connect(transport);
    console.log('[supabase-provision] ✓ Connected to Supabase MCP');

    // Discover available tools
    const toolsList = await client.listTools();
    const toolNames = (toolsList.tools || []).map((t) => t.name);
    console.log('[supabase-provision] MCP tools:', toolNames.join(', '));

    // ── 1. Get organization ID ───────────────────────────
    let orgId = process.env.SUPABASE_ORG_ID;
    if (!orgId && toolNames.includes('list_organizations')) {
      const orgsResult = await client.callTool({
        name: 'list_organizations',
        arguments: {},
      });
      const orgs = parseToolResult(orgsResult);
      if (Array.isArray(orgs) && orgs.length > 0) {
        orgId = orgs[0].id;
        console.log('[supabase-provision] Using org:', orgs[0].name || orgId);
      }
    }

    if (!orgId) {
      console.warn('[supabase-provision] No organization ID available');
      return null;
    }

    // ── 2. Create project ────────────────────────────────
    if (!toolNames.includes('create_project')) {
      console.warn('[supabase-provision] create_project tool not available');
      return null;
    }

    const dbPassword = generateDbPassword();
    const createResult = await client.callTool({
      name: 'create_project',
      arguments: {
        name: projectName,
        organization_id: orgId,
        plan: 'free',
        region: 'us-east-1',
        db_pass: dbPassword,
      },
    });

    const project = parseToolResult(createResult);
    if (!project?.id) {
      console.error('[supabase-provision] MCP: create_project returned no ID');
      return null;
    }

    const ref = project.id;
    console.log('[supabase-provision] ✓ Project created:', ref);

    // Wait for project initialization (3 minute timeout)
    await waitForProject(client, toolNames, ref);

    // ── 3. Apply migrations (tables + RLS) ───────────────
    const migrations = getMigrations(projectType);
    const tablesCreated = [];

    for (const migration of migrations) {
      const sqlTool = toolNames.includes('apply_migration') ? 'apply_migration' : 'execute_sql';
      try {
        const sqlArgs = sqlTool === 'apply_migration'
          ? { project_id: ref, name: migration.name, query: migration.sql }
          : { project_id: ref, query: migration.sql };

        await client.callTool({ name: sqlTool, arguments: sqlArgs });
        tablesCreated.push(migration.name);
        console.log(`[supabase-provision] ✓ Migration applied: ${migration.name}`);
      } catch (err) {
        console.warn(`[supabase-provision] Migration ${migration.name} failed:`, err.message);
      }
    }

    // ── 4. Get API keys ──────────────────────────────────
    let supabaseUrl = `https://${ref}.supabase.co`;
    let supabaseAnonKey = '';
    let supabaseServiceKey = '';

    if (toolNames.includes('get_project')) {
      try {
        const projResult = await client.callTool({
          name: 'get_project',
          arguments: { project_id: ref },
        });
        const projData = parseToolResult(projResult);
        if (projData?.endpoint) supabaseUrl = projData.endpoint;
        if (projData?.anon_key) supabaseAnonKey = projData.anon_key;
        if (projData?.service_role_key) supabaseServiceKey = projData.service_role_key;
      } catch { /* keys will be fetched differently */ }
    }

    // If keys not from get_project, try list_api_keys or get_api_keys
    if (!supabaseAnonKey) {
      const keyTools = ['list_api_keys', 'get_api_keys'];
      for (const kt of keyTools) {
        if (toolNames.includes(kt)) {
          try {
            const keysResult = await client.callTool({
              name: kt,
              arguments: { project_id: ref },
            });
            const keys = parseToolResult(keysResult);
            if (Array.isArray(keys)) {
              supabaseAnonKey = keys.find((k) => k.name === 'anon')?.api_key || '';
              supabaseServiceKey = keys.find((k) => k.name === 'service_role')?.api_key || '';
            }
          } catch { /* continue */ }
          break;
        }
      }
    }

    return {
      method: 'MCP',
      ref,
      supabaseUrl,
      supabaseAnonKey,
      supabaseServiceKey,
      dbPassword,
      tables: tablesCreated,
    };
  } catch (err) {
    console.error('[supabase-provision] MCP provisioning failed:', err.message);
    return null;
  } finally {
    try { if (client) await client.close(); } catch { /* ok */ }
    try { if (transport) await transport.close(); } catch { /* ok */ }
  }
}

/**
 * Wait for a newly created project to be ready.
 * Polls every 5 seconds, timeout after 3 minutes (36 iterations).
 */
async function waitForProject(client, toolNames, ref) {
  if (!toolNames.includes('get_project')) {
    await sleep(10000); // Blind wait
    return;
  }

  for (let i = 0; i < 36; i++) {
    await sleep(5000);
    try {
      const result = await client.callTool({
        name: 'get_project',
        arguments: { project_id: ref },
      });
      const data = parseToolResult(result);
      if (data?.status === 'ACTIVE_HEALTHY' || data?.status === 'ACTIVE') {
        console.log('[supabase-provision] ✓ Project is ready');
        return;
      }
      console.log('[supabase-provision] Project status:', data?.status || 'unknown');
    } catch { /* continue polling */ }
  }
  console.warn('[supabase-provision] Project readiness timeout — proceeding anyway');
}

function parseToolResult(result) {
  if (!result) return null;
  for (const block of (result.content || [])) {
    if (block.type === 'text' && block.text) {
      try { return JSON.parse(block.text); } catch { return null; }
    }
  }
  return null;
}

// ═══════════════════════════════════════════════════════════
//  REST API provisioning (fallback)
// ═══════════════════════════════════════════════════════════

async function provisionViaRestApi({ projectName, projectType, projectSlug }) {
  const managementKey =
    process.env.SUPABASE_ACCESS_TOKEN ||
    process.env.SUPABASE_MANAGEMENT_API_KEY;

  if (!managementKey?.trim()) {
    console.warn('[supabase-provision] No management key for REST fallback');
    return null;
  }

  const orgId = process.env.SUPABASE_ORG_ID || '';
  if (!orgId) {
    // Try to get org from API
    try {
      const orgsRes = await fetch(`${SUPABASE_MGMT_API}/organizations`, {
        headers: { Authorization: `Bearer ${managementKey}` },
        signal: AbortSignal.timeout(10000),
      });
      if (orgsRes.ok) {
        const orgs = await orgsRes.json();
        if (orgs.length > 0) {
          return await doRestProvision(managementKey, orgs[0].id, projectName, projectType);
        }
      }
    } catch { /* fall through */ }
    console.warn('[supabase-provision] No org ID available for REST provisioning');
    return null;
  }

  return await doRestProvision(managementKey, orgId, projectName, projectType);
}

async function doRestProvision(managementKey, orgId, projectName, projectType) {
  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${managementKey}`,
  };

  try {
    // ── Create project ───────────────────────────────────
    const dbPassword = generateDbPassword();
    const createRes = await fetch(`${SUPABASE_MGMT_API}/projects`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        name: projectName,
        organization_id: orgId,
        plan: 'free',
        region: 'us-east-1',
        db_pass: dbPassword,
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (!createRes.ok) {
      console.error('[supabase-provision] REST: Create failed:', createRes.status);
      return null;
    }

    const project = await createRes.json();
    const ref = project.id;
    const supabaseUrl = `https://${ref}.supabase.co`;

    // ── Poll until ACTIVE_HEALTHY (3 min timeout) ────────
    let supabaseAnonKey = '';
    let supabaseServiceKey = '';
    for (let i = 0; i < 36; i++) {
      await sleep(5000);

      // Check project status
      try {
        const statusRes = await fetch(`${SUPABASE_MGMT_API}/projects/${ref}`, {
          headers,
          signal: AbortSignal.timeout(10000),
        });
        if (statusRes.ok) {
          const statusData = await statusRes.json();
          if (statusData.status === 'ACTIVE_HEALTHY' || statusData.status === 'ACTIVE') {
            console.log('[supabase-provision] ✓ Project is ACTIVE_HEALTHY');
          }
        }
      } catch { /* continue */ }

      // Try to get API keys
      try {
        const keysRes = await fetch(`${SUPABASE_MGMT_API}/projects/${ref}/api-keys`, {
          headers,
          signal: AbortSignal.timeout(10000),
        });
        if (keysRes.ok) {
          const keys = await keysRes.json();
          const anon = keys.find((k) => k.name === 'anon');
          const service = keys.find((k) => k.name === 'service_role');
          if (anon?.api_key) {
            supabaseAnonKey = anon.api_key;
            supabaseServiceKey = service?.api_key || '';
            break;
          }
        }
      } catch { /* continue */ }
    }

    if (!supabaseAnonKey) {
      console.error('[supabase-provision] REST: API keys timeout');
      return null;
    }

    // ── Run migrations via REST SQL ──────────────────────
    const migrations = getMigrations(projectType);
    const tablesCreated = [];
    for (const migration of migrations) {
      try {
        const sqlRes = await fetch(`${SUPABASE_MGMT_API}/projects/${ref}/database/query`, {
          method: 'POST', headers,
          body: JSON.stringify({ query: migration.sql }),
          signal: AbortSignal.timeout(15000),
        });
        if (sqlRes.ok) tablesCreated.push(migration.name);
      } catch { /* non-fatal */ }
    }

    return {
      method: 'REST API',
      ref,
      supabaseUrl,
      supabaseAnonKey,
      supabaseServiceKey,
      dbPassword,
      tables: tablesCreated,
    };
  } catch (err) {
    console.error('[supabase-provision] REST provisioning failed:', err.message);
    return null;
  }
}

// ═══════════════════════════════════════════════════════════
//  Credential storage (supabase_projects table)
// ═══════════════════════════════════════════════════════════

async function storeSupabaseProject({
  userId,
  platformProjectId,
  projectSlug,
  supabaseRef,
  supabaseUrl,
  supabaseAnonKey,
  supabaseServiceKey,
  dbPassword,
  region,
  suggestedProviders,
}) {
  const supabase = await getSupabaseServerClient();

  // Encrypt sensitive values
  let anonKeyEnc = null, anonKeyIv = null;
  if (supabaseAnonKey) {
    const { encrypted, iv } = encrypt(supabaseAnonKey);
    anonKeyEnc = encrypted;
    anonKeyIv = iv;
  }

  let serviceKeyEnc = null, serviceKeyIv = null;
  if (supabaseServiceKey) {
    const { encrypted, iv } = encrypt(supabaseServiceKey);
    serviceKeyEnc = encrypted;
    serviceKeyIv = iv;
  }

  let dbPassEnc = null, dbPassIv = null;
  if (dbPassword) {
    const { encrypted, iv } = encrypt(dbPassword);
    dbPassEnc = encrypted;
    dbPassIv = iv;
  }

  const { error } = await supabase
    .from('supabase_projects')
    .upsert({
      user_id: userId,
      project_id: platformProjectId,
      project_slug: projectSlug,
      supabase_ref: supabaseRef,
      supabase_url: supabaseUrl,
      anon_key_enc: anonKeyEnc,
      anon_key_iv: anonKeyIv,
      service_key_enc: serviceKeyEnc,
      service_key_iv: serviceKeyIv,
      db_password_enc: dbPassEnc,
      db_password_iv: dbPassIv,
      region,
      status: 'ACTIVE',
      auth_providers: [],
      suggested_providers: suggestedProviders,
      created_at: new Date().toISOString(),
    }, { onConflict: 'user_id,project_id' });

  if (error) throw error;
  console.log('[supabase-provision] ✓ Credentials stored in supabase_projects');
}

// ═══════════════════════════════════════════════════════════
//  Generated project file templates
// ═══════════════════════════════════════════════════════════

/**
 * Generate /lib/supabase/client.js for the user's project.
 * Uses Vite-style env variables.
 */
export function generateSupabaseClientFile() {
  return `import { createClient } from '@supabase/supabase-js'

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
)
`;
}

/**
 * Generate /lib/supabase/auth.js for the user's project.
 */
export function generateSupabaseAuthFile() {
  return `import { supabase } from './client'

export const signUp = (email, password) =>
  supabase.auth.signUp({ email, password })

export const signIn = (email, password) =>
  supabase.auth.signInWithPassword({ email, password })

export const signInMagicLink = (email) =>
  supabase.auth.signInWithOtp({ email })

export const signInOAuth = (provider) =>
  supabase.auth.signInWithOAuth({
    provider,
    options: {
      redirectTo: \`\${window.location.origin}/auth/callback\`
    }
  })

export const signOut = () =>
  supabase.auth.signOut()

export const getUser = () =>
  supabase.auth.getUser()
`;
}

/**
 * Generate /components/AuthButtons.jsx for the user's project.
 * Conditionally renders OAuth buttons based on env vars.
 */
export function generateAuthButtonsFile() {
  return `import { signIn, signUp, signInMagicLink, signInOAuth, signOut } from '../lib/supabase/auth'
import { useState } from 'react'

const googleEnabled = import.meta.env.VITE_GOOGLE_ENABLED === 'true'
const githubEnabled = import.meta.env.VITE_GITHUB_ENABLED === 'true'

export default function AuthButtons() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState('signin') // 'signin' | 'signup' | 'magic'
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setMessage('')

    try {
      if (mode === 'magic') {
        const { error } = await signInMagicLink(email)
        if (error) throw error
        setMessage('Check your email for a magic link!')
      } else if (mode === 'signup') {
        const { error } = await signUp(email, password)
        if (error) throw error
        setMessage('Account created! Check your email to confirm.')
      } else {
        const { error } = await signIn(email, password)
        if (error) throw error
      }
    } catch (err) {
      setMessage(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-container">
      <form onSubmit={handleSubmit} className="auth-form">
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        {mode !== 'magic' && (
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        )}
        <button type="submit" disabled={loading}>
          {loading ? 'Loading...' : mode === 'magic' ? 'Send Magic Link' : mode === 'signup' ? 'Sign Up' : 'Sign In'}
        </button>
      </form>

      <div className="auth-toggle">
        <button type="button" onClick={() => setMode('signin')}>Sign In</button>
        <button type="button" onClick={() => setMode('signup')}>Sign Up</button>
        <button type="button" onClick={() => setMode('magic')}>Magic Link</button>
      </div>

      {/* OAuth Providers */}
      <div className="auth-oauth">
        {googleEnabled && (
          <button type="button" onClick={() => signInOAuth('google')} className="oauth-btn google">
            Continue with Google
          </button>
        )}
        {githubEnabled && (
          <button type="button" onClick={() => signInOAuth('github')} className="oauth-btn github">
            Continue with GitHub
          </button>
        )}
      </div>

      {message && <p className="auth-message">{message}</p>}
    </div>
  )
}
`;
}

/**
 * Generate a .env.production file with Supabase credentials baked in.
 * This file is committed to the repo so the build has access at build-time.
 * CI/CD variables remain clean for K8s/infra only.
 */
export function generateEnvProductionFile({ supabaseUrl, supabaseAnonKey }) {
  return `# ─────────────────────────────────────────────────
# Auto-generated by Lucid AI — Supabase credentials
# ─────────────────────────────────────────────────
VITE_SUPABASE_URL=${supabaseUrl}
VITE_SUPABASE_ANON_KEY=${supabaseAnonKey}
VITE_GOOGLE_ENABLED=false
VITE_GITHUB_ENABLED=false
`;
}

// ═══════════════════════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════════════════════

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Generate a strong 32-character database password.
 */
function generateDbPassword() {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%&*';
  let pw = '';
  for (let i = 0; i < 32; i++) pw += chars[Math.floor(Math.random() * chars.length)];
  return pw;
}
