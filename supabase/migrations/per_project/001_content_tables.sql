-- ─────────────────────────────────────────────────────────────────────────────
--  001_content_tables — Lucid AI per-project content schema
--
--  IMPORTANT: This migration runs on the CUSTOMER's provisioned Supabase
--  project, NOT Lucid's central DB. ai_engine applies it via the Mgmt API
--  immediately after creating the project. Its purpose: turn an empty
--  Supabase into the content backend for one generated app.
--
--  Naming convention: every Lucid-managed table is prefixed `gen_` so it
--  never collides with tables the customer may add later (orders, users,
--  whatever their domain needs).
--
--  Idempotent — re-running is a no-op. Safe to apply multiple times during
--  development or when a re-provisioned project needs to catch up.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Site config ─────────────────────────────────────────────────────────
-- One row per project. Holds brand identity, theme tokens, design system
-- output, classification metadata. Generated app reads this for the
-- header, footer, theme injection, og-image metadata, etc.
CREATE TABLE IF NOT EXISTS public.gen_site_config (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand           JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {name, tagline, description, domain}
    theme           JSONB NOT NULL DEFAULT '{}'::jsonb,   -- shadcn HSL tokens + fonts + radius
    design          JSONB NOT NULL DEFAULT '{}'::jsonb,   -- Design Director output
    archetype       TEXT,                                 -- single_page_landing | consumer_website | admin_dashboard | …
    domain_kind     TEXT,                                 -- saas | ecommerce | food | …
    status_badges   JSONB NOT NULL DEFAULT '{}'::jsonb,
    design_system   JSONB NOT NULL DEFAULT '{}'::jsonb,
    api_config      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Navigation ──────────────────────────────────────────────────────────
-- Header nav, footer nav columns, admin sidebar groups. `kind` discriminates;
-- `position` orders groups within the same kind; `items` is the JSON array
-- of {label, href, icon, badge} entries.
CREATE TABLE IF NOT EXISTS public.gen_navigation (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL CHECK (kind IN ('main', 'footer', 'sidebar')),
    group_name      TEXT NOT NULL DEFAULT 'main',
    position        INT  NOT NULL DEFAULT 0,
    items           JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_gen_navigation_kind_pos ON public.gen_navigation(kind, position);

-- ── Pages ───────────────────────────────────────────────────────────────
-- Route map. Each row corresponds to one src/app/<route>/page.js (Next.js)
-- or src/pages/<Route>.jsx (Vite). Sections live in a separate table and
-- reference page_id.
CREATE TABLE IF NOT EXISTS public.gen_page (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    path            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL DEFAULT '',
    component       TEXT,                                 -- e.g. "AboutPage"
    type            TEXT NOT NULL DEFAULT 'custom'
                    CHECK (type IN ('custom','dashboard','settings','crud_list','crud_form','landing')),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    position        INT  NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_gen_page_position ON public.gen_page(position);

-- ── Sections ────────────────────────────────────────────────────────────
-- Per-page content blocks. `type` selects which React component renders
-- (Hero, Features, Pricing, …). `props` is the prop bag that component
-- consumes. Visible=false soft-hides without losing the row (CMS toggle).
-- This is the single most-edited table — every "change the headline"
-- update lands here.
CREATE TABLE IF NOT EXISTS public.gen_section (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id         UUID NOT NULL REFERENCES public.gen_page(id) ON DELETE CASCADE,
    position        INT  NOT NULL DEFAULT 0,
    type            TEXT NOT NULL,        -- hero | features | testimonials | pricing | faq | cta | …
    props           JSONB NOT NULL DEFAULT '{}'::jsonb,
    visible         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_gen_section_page ON public.gen_section(page_id, position);

-- ── Entities (admin panels) ────────────────────────────────────────────
-- Schema of admin-panel CRUD entities. Templated CRUD pages (step 9) read
-- `fields` to render the table columns + create/edit form fields.
CREATE TABLE IF NOT EXISTS public.gen_entity (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    fields          JSONB NOT NULL DEFAULT '[]'::jsonb,    -- [{name, type, required, inList, inForm, options}]
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Entity rows ────────────────────────────────────────────────────────
-- The actual data behind an entity. Initially seeded with the schema's
-- mock_db rows; the admin UI reads/writes via this table.
CREATE TABLE IF NOT EXISTS public.gen_entity_row (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id       UUID NOT NULL REFERENCES public.gen_entity(id) ON DELETE CASCADE,
    data            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_gen_entity_row_entity ON public.gen_entity_row(entity_id);

-- ── Revisions (edit history / undo) ────────────────────────────────────
-- Every CMS write to gen_section / gen_site_config / gen_entity_row should
-- write one revision row first. Gives free undo and an audit trail. No FK
-- to row_id because rows in the source table may be deleted while we want
-- to keep the revision (for "undo delete").
CREATE TABLE IF NOT EXISTS public.gen_revision (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name      TEXT NOT NULL,
    row_id          UUID NOT NULL,
    before_data     JSONB,
    after_data      JSONB,
    edited_by       UUID,                                 -- usually auth.uid(); not FK so deletes don't cascade
    edited_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_gen_revision_lookup ON public.gen_revision(table_name, row_id, edited_at DESC);

-- ── updated_at triggers ────────────────────────────────────────────────
-- One trigger function shared across every gen_* table. Idempotent.
CREATE OR REPLACE FUNCTION public.gen_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'gen_site_config', 'gen_navigation', 'gen_page',
        'gen_section', 'gen_entity', 'gen_entity_row'
    ] LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I_updated_at ON public.%I; '
            'CREATE TRIGGER %I_updated_at BEFORE UPDATE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public.gen_set_updated_at();',
            t, t, t, t
        );
    END LOOP;
END$$;

-- ── Row-Level Security ─────────────────────────────────────────────────
-- Reads: public for content surfaces (the generated app fetches with the
-- anon key from the browser). Writes: handled exclusively via service_role
-- key (Lucid CMS / generation pipeline) — service_role bypasses RLS, so we
-- intentionally define no INSERT/UPDATE/DELETE policies for anon. Customer
-- apps that need per-user write semantics for *their own domain tables*
-- (orders, comments, etc.) define those tables separately.
ALTER TABLE public.gen_site_config  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gen_navigation   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gen_page         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gen_section      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gen_entity       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gen_entity_row   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gen_revision     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "gen_site_config_read" ON public.gen_site_config;
CREATE POLICY "gen_site_config_read" ON public.gen_site_config FOR SELECT USING (true);

DROP POLICY IF EXISTS "gen_navigation_read" ON public.gen_navigation;
CREATE POLICY "gen_navigation_read" ON public.gen_navigation FOR SELECT USING (true);

DROP POLICY IF EXISTS "gen_page_read" ON public.gen_page;
CREATE POLICY "gen_page_read" ON public.gen_page FOR SELECT USING (true);

-- gen_section: hide soft-deleted (visible=false) rows from anon reads, so
-- a CMS "unpublish" hides without leaking the data.
DROP POLICY IF EXISTS "gen_section_read" ON public.gen_section;
CREATE POLICY "gen_section_read" ON public.gen_section FOR SELECT USING (visible = true);

DROP POLICY IF EXISTS "gen_entity_read" ON public.gen_entity;
CREATE POLICY "gen_entity_read" ON public.gen_entity FOR SELECT USING (true);

-- gen_entity_row + gen_revision: NO public read policy. Admin-panel data
-- and edit history must go through the service_role key (server-side only)
-- — they often contain PII (orders, members, audit trail).
