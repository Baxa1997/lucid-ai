"""Stage 5 — deterministic admin foundation file generator.

Writes every file in the generated admin Next.js project EXCEPT the
per-entity CRUD bodies (those land in Step 3.6 via Claude codegen).
After this step the project should:
  • boot via `npm run dev`
  • render /login wired to Supabase Auth
  • render the sidebar with one nav item per entity
  • render dashboard with per-entity row counts
  • render stub list/new/edit pages explaining Step 3.6

Design choices
--------------
  • JavaScript not TypeScript — matches the rest of the codebase
    (CLAUDE.md: "Frontend uses JavaScript … with Next.js App Router").
  • shadcn-style HSL Tailwind tokens with a slate-neutral palette
    swapped for `--primary` via `admin_plan.branding.primary_color`.
  • @supabase/ssr's `createBrowserClient` (NOT the legacy
    `@supabase/auth-helpers-nextjs`) — Supabase deprecated the
    helpers in 2024 and `ssr` is the supported path.
  • All RPC calls go through migrations 026 (writes) + 027
    (authenticated reads). Anon read RPC (025) is NOT used here —
    admins are authenticated by definition.

Inputs vs side-effects
----------------------
This module's main entry point writes files; the caller is expected
to have ensured `workspace_path` is an empty directory (or that
clobbering existing files is acceptable). The website pipeline's
foundation builder returns a dict and lets the caller write — we
write directly here because the admin foundation is uniformly
overwrite-safe (no merge-with-template step like the website has).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from app.services.data_model import DataModel, TableDefinition

logger = logging.getLogger(__name__)


# ── Slug helpers ─────────────────────────────────────────────────────

def _kebab(name: str) -> str:
    """`purchase_orders` → `purchase-orders`."""
    return (name or "").replace("_", "-")


def _slugify_brand(name: str) -> str:
    """Make a brand name safe for `package.json`'s `name` field."""
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "admin").lower()).strip("-")
    return s or "admin"


# ── Icon mapping (matches admin_plan.py) ─────────────────────────────
# This list is duplicated from admin_plan._ICON_BY_NAME_SUBSTRING so
# the sidebar source can import only the names it actually uses.
# Keep the two in sync when adding entries — they share the same set
# of canonical icon names.

LUCIDE_ICON_NAMES = sorted({
    "home", "user-plus", "users", "graduation-cap", "user-cog",
    "truck", "headphones", "ticket", "shopping-cart", "file-text",
    "credit-card", "arrow-left-right", "package", "boxes", "car",
    "map", "calendar", "calendar-check", "folder", "check-square",
    "flag", "book", "book-open", "award", "trending-up", "activity",
    "message-square", "sticky-note", "wrench", "inbox", "utensils",
    "table",
})


# Lucide ships PascalCase exports — `users` → `Users`, `user-plus` → `UserPlus`.
def _icon_to_pascal(icon_kebab: str) -> str:
    return "".join(part.capitalize() for part in icon_kebab.split("-"))


# ── Tailwind / globals.css ───────────────────────────────────────────

_TAILWIND_CONFIG = """\
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/app/**/*.{js,jsx}",
    "./src/components/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        border:     "hsl(var(--border))",
        input:      "hsl(var(--input))",
        ring:       "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT:    "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        muted: {
          DEFAULT:    "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        card: {
          DEFAULT:    "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        destructive: {
          DEFAULT:    "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
};
"""


_POSTCSS_CONFIG = """\
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""


# Slate-neutral defaults; the primary HSL is derived from the
# admin_plan.branding.primary_color hex via `_hex_to_hsl_string`.
_GLOBALS_CSS_TEMPLATE = """\
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {{
  --background:           0 0% 100%;
  --foreground:           222 47% 11%;
  --card:                 0 0% 100%;
  --card-foreground:      222 47% 11%;
  --muted:                210 40% 96%;
  --muted-foreground:     215 16% 47%;
  --border:               214 32% 91%;
  --input:                214 32% 91%;
  --primary:              {primary_hsl};
  --primary-foreground:   210 40% 98%;
  --destructive:          0 84% 60%;
  --destructive-foreground: 210 40% 98%;
  --ring:                 215 20% 65%;
  --radius:               0.5rem;
}}

body {{
  background-color: hsl(var(--background));
  color: hsl(var(--foreground));
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI",
    Roboto, "Helvetica Neue", Arial, sans-serif;
}}
"""


def _hex_to_hsl_string(hex_color: str) -> str:
    """Convert `#0f172a` → `222 47% 11%` (the Tailwind/shadcn token shape).

    Falls back to slate-900 (`222 47% 11%`) on parse failure so
    globals.css always builds, even if the planner returned a
    malformed brand color.
    """
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return "222 47% 11%"
    try:
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
    except ValueError:
        return "222 47% 11%"

    mx, mn = max(r, g, b), min(r, g, b)
    lum = (mx + mn) / 2
    if mx == mn:
        hue = 0.0
        sat = 0.0
    else:
        d = mx - mn
        sat = d / (2 - mx - mn) if lum > 0.5 else d / (mx + mn)
        if mx == r:
            hue = ((g - b) / d) % 6
        elif mx == g:
            hue = ((b - r) / d) + 2
        else:
            hue = ((r - g) / d) + 4
        hue *= 60
    return f"{int(round(hue))} {int(round(sat * 100))}% {int(round(lum * 100))}%"


# ── package.json / next.config.js / jsconfig.json ────────────────────

def _build_package_json(brand_name: str) -> str:
    pkg = {
        "name":    f"{_slugify_brand(brand_name)}-admin",
        "version": "0.1.0",
        "private": True,
        "scripts": {
            "dev":   "next dev",
            "build": "next build",
            "start": "next start",
            "lint":  "next lint",
        },
        "dependencies": {
            "next":                    "^14.2.0",
            "react":                   "^18.3.0",
            "react-dom":               "^18.3.0",
            "@supabase/ssr":           "^0.5.0",
            "@supabase/supabase-js":   "^2.45.0",
            "react-hook-form":         "^7.53.0",
            "lucide-react":            "^0.440.0",
            "clsx":                    "^2.1.0",
            "tailwind-merge":          "^2.5.0",
        },
        "devDependencies": {
            "autoprefixer": "^10.4.0",
            "postcss":      "^8.4.0",
            "tailwindcss":  "^3.4.0",
        },
    }
    return json.dumps(pkg, indent=2) + "\n"


_NEXT_CONFIG = """\
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};

module.exports = nextConfig;
"""


_JSCONFIG = """\
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src/**/*.js", "src/**/*.jsx"]
}
"""


_GITIGNORE = """\
node_modules/
.next/
out/
.env.local
.env.*.local
.DS_Store
*.log
"""


_ENV_EXAMPLE = """\
# Copy to .env.local and fill in the values from the Supabase Dashboard.
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_PROJECT_ID=
NEXT_PUBLIC_TENANT_SCHEMA=
NEXT_PUBLIC_BRAND_NAME=
NEXT_PUBLIC_PRIMARY_COLOR=
"""


def _build_env_local(
    *,
    supabase_url: str,
    supabase_anon_key: str,
    project_id: str,
    tenant_schema: str,
    brand_name: str,
    primary_color: str,
) -> str:
    return (
        "# AUTO-GENERATED — Lucid AI admin pipeline.\n"
        "# Anon key — safe to ship to the browser; RLS + the\n"
        "# get_tenant_collection_authenticated RPC enforce isolation.\n"
        f"NEXT_PUBLIC_SUPABASE_URL={supabase_url}\n"
        f"NEXT_PUBLIC_SUPABASE_ANON_KEY={supabase_anon_key}\n"
        f"NEXT_PUBLIC_PROJECT_ID={project_id}\n"
        f"NEXT_PUBLIC_TENANT_SCHEMA={tenant_schema}\n"
        f"NEXT_PUBLIC_BRAND_NAME={brand_name}\n"
        f"NEXT_PUBLIC_PRIMARY_COLOR={primary_color}\n"
    )


def _build_readme(brand_name: str, data_model: DataModel) -> str:
    entities = ", ".join(t.plural_label or t.name for t in data_model.tables)
    return (
        f"# {brand_name} — Admin\n\n"
        f"Auto-generated by Lucid AI. Manages: {entities or '(no entities yet)'}.\n\n"
        "## Quickstart\n\n"
        "```bash\n"
        "npm install\n"
        "npm run dev\n"
        "```\n\n"
        "Then open http://localhost:3000/login.\n\n"
        "## Stack\n\n"
        "- Next.js 14 App Router (JavaScript)\n"
        "- Supabase Auth + Postgres (via tenant RPCs)\n"
        "- Tailwind CSS + shadcn-style HSL design tokens\n"
        "- react-hook-form, lucide-react\n\n"
        "## What's here vs. what's coming\n\n"
        "Step 3.5 (this commit) wired up the foundation: auth, sidebar,\n"
        "dashboard, and stub CRUD pages.\n\n"
        "Step 3.6 will replace the stub pages with real list / create /\n"
        "edit UIs.\n"
    )


# ── src/lib ──────────────────────────────────────────────────────────

_LIB_SUPABASE_JS = """\
/* AUTO-GENERATED — Lucid AI admin pipeline. */
import { createBrowserClient } from "@supabase/ssr";

let _client = null;

export function getSupabaseBrowserClient() {
  // Cache the client so React's re-renders don't spawn a new
  // websocket connection on every component mount.
  if (_client) return _client;
  const url     = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "Lucid: NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY is missing.",
    );
  }
  _client = createBrowserClient(url, anonKey);
  return _client;
}
"""


def _build_db_admin_js(data_model: DataModel) -> str:
    """`src/lib/db_admin.js` — list/create/update/delete wrappers around
    migrations 026 + 027. Hits the authenticated RPCs only; the user's
    JWT goes via the browser client we built in supabase.js.
    """
    names = [t.name for t in data_model.tables]
    table_list_comment = (
        "/*\n * Entities exposed by this admin:\n"
        + "".join(f" *   - {n}\n" for n in names)
        + " */"
    )
    return (
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        f'{table_list_comment}\n'
        'import { getSupabaseBrowserClient } from "./supabase.js";\n'
        '\n'
        'const PROJECT_ID = process.env.NEXT_PUBLIC_PROJECT_ID;\n'
        '\n'
        '/**\n'
        ' * List rows for an entity. Uses the authenticated read RPC\n'
        ' * (migration 027) so non-public tables are readable to members.\n'
        ' */\n'
        'export async function listCollection(tableName, options = {}) {\n'
        '  const supabase = getSupabaseBrowserClient();\n'
        '  const { data, error } = await supabase.rpc(\n'
        '    "get_tenant_collection_authenticated",\n'
        '    {\n'
        '      p_project_id:      PROJECT_ID,\n'
        '      p_table_name:      tableName,\n'
        '      p_order_by:        options.orderBy        ?? "created_at",\n'
        '      p_order_direction: options.orderDirection ?? "desc",\n'
        '      p_limit:           options.limit          ?? 100,\n'
        '      p_offset:          options.offset         ?? 0,\n'
        '    },\n'
        '  );\n'
        '  if (error) throw error;\n'
        '  return data || [];\n'
        '}\n'
        '\n'
        '/** Insert one row. Returns the inserted row (with id + timestamps). */\n'
        'export async function createRow(tableName, payload) {\n'
        '  const supabase = getSupabaseBrowserClient();\n'
        '  const { data, error } = await supabase.rpc("set_tenant_row", {\n'
        '    p_project_id: PROJECT_ID,\n'
        '    p_table_name: tableName,\n'
        '    p_payload:    payload,\n'
        '  });\n'
        '  if (error) throw error;\n'
        '  return data;\n'
        '}\n'
        '\n'
        '/** Patch a row by id. Unspecified columns retain their values. */\n'
        'export async function updateRow(tableName, rowId, payload) {\n'
        '  const supabase = getSupabaseBrowserClient();\n'
        '  const { data, error } = await supabase.rpc("update_tenant_row", {\n'
        '    p_project_id: PROJECT_ID,\n'
        '    p_table_name: tableName,\n'
        '    p_row_id:     rowId,\n'
        '    p_payload:    payload,\n'
        '  });\n'
        '  if (error) throw error;\n'
        '  return data;\n'
        '}\n'
        '\n'
        '/** Hard delete a row by id. */\n'
        'export async function deleteRow(tableName, rowId) {\n'
        '  const supabase = getSupabaseBrowserClient();\n'
        '  const { error } = await supabase.rpc("delete_tenant_row", {\n'
        '    p_project_id: PROJECT_ID,\n'
        '    p_table_name: tableName,\n'
        '    p_row_id:     rowId,\n'
        '  });\n'
        '  if (error) throw error;\n'
        '}\n'
    )


_LIB_AUTH_JS = """\
"use client";
/* AUTO-GENERATED — Lucid AI admin pipeline. */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabaseBrowserClient } from "./supabase.js";

export function useAuth() {
  const [user, setUser]       = useState(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();

    supabase.auth.getUser().then(({ data: { user } }) => {
      setUser(user);
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (event, session) => {
        setUser(session?.user || null);
        if (event === "SIGNED_OUT") {
          router.push("/login");
        }
      },
    );

    return () => subscription.unsubscribe();
  }, [router]);

  return { user, loading };
}

export async function signOut() {
  const supabase = getSupabaseBrowserClient();
  await supabase.auth.signOut();
}
"""


_LIB_UTILS_JS = """\
/* AUTO-GENERATED — Lucid AI admin pipeline. */
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind-aware classname merger. */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

/** Format an ISO date string for display in tables / forms. */
export function formatDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}
"""


# ── src/components ───────────────────────────────────────────────────

_COMPONENT_AUTH_GUARD = """\
"use client";
/* AUTO-GENERATED — Lucid AI admin pipeline. */
import { useAuth } from "@/lib/auth.js";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function AuthGuard({ children }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.push("/login");
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-muted-foreground">Loading…</div>
      </div>
    );
  }
  if (!user) return null;
  return children;
}
"""


def _build_sidebar(nav_items: list[dict[str, Any]], brand_name: str) -> str:
    """Emit src/components/Sidebar.jsx wired to admin_plan.navigation."""
    # Collect every distinct icon name so we only import what we use.
    used_icons = sorted({item.get("icon") or "table" for item in nav_items})
    import_list = ", ".join(_icon_to_pascal(i) for i in used_icons)
    icon_map_entries = ",\n  ".join(
        f'"{name}": {_icon_to_pascal(name)}' for name in used_icons
    )

    nav_json = json.dumps(
        [
            {
                "label": item["label"],
                "route": item["route"],
                "icon":  item.get("icon") or "table",
            }
            for item in nav_items
        ],
        indent=2,
    )
    safe_brand = json.dumps(brand_name)

    return (
        '"use client";\n'
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        'import Link from "next/link";\n'
        'import { usePathname } from "next/navigation";\n'
        f'import {{ {import_list} }} from "lucide-react";\n'
        'import { signOut } from "@/lib/auth.js";\n'
        '\n'
        f'const NAV = {nav_json};\n'
        '\n'
        'const ICONS = {\n'
        f'  {icon_map_entries}\n'
        '};\n'
        '\n'
        'export function Sidebar() {\n'
        '  const pathname = usePathname();\n'
        '  return (\n'
        '    <aside className="w-64 bg-slate-900 text-slate-100 min-h-screen p-4 flex flex-col">\n'
        f'      <div className="font-bold text-lg mb-6">{{{safe_brand}}}</div>\n'
        '      <nav className="space-y-1 flex-1">\n'
        '        {NAV.map((item) => {\n'
        '          const Icon = ICONS[item.icon] || ICONS["table"];\n'
        '          const isActive =\n'
        '            item.route === "/"\n'
        '              ? pathname === "/"\n'
        '              : pathname.startsWith(item.route);\n'
        '          return (\n'
        '            <Link\n'
        '              key={item.route}\n'
        '              href={item.route}\n'
        '              className={\n'
        '                "flex items-center gap-3 px-3 py-2 rounded text-sm " +\n'
        '                (isActive ? "bg-slate-700" : "hover:bg-slate-800")\n'
        '              }\n'
        '            >\n'
        '              <Icon size={18} />\n'
        '              <span>{item.label}</span>\n'
        '            </Link>\n'
        '          );\n'
        '        })}\n'
        '      </nav>\n'
        '      <button\n'
        '        onClick={() => signOut()}\n'
        '        className="text-sm text-slate-400 hover:text-slate-200 px-3 py-2 text-left"\n'
        '      >\n'
        '        Sign out\n'
        '      </button>\n'
        '    </aside>\n'
        '  );\n'
        '}\n'
    )


_COMPONENT_HEADER = """\
"use client";
/* AUTO-GENERATED — Lucid AI admin pipeline. */
import { useAuth, signOut } from "@/lib/auth.js";

export function Header() {
  const { user } = useAuth();
  return (
    <header className="border-b border-border bg-card px-6 py-3 flex items-center justify-between">
      <div className="text-sm text-muted-foreground">
        {user?.email || "Loading…"}
      </div>
      <button
        type="button"
        onClick={() => signOut()}
        className="text-sm px-3 py-1 rounded hover:bg-muted"
      >
        Sign out
      </button>
    </header>
  );
}
"""


_COMPONENT_EMPTY_STATE = """\
/* AUTO-GENERATED — Lucid AI admin pipeline. */
import Link from "next/link";

export function EmptyState({ label, createRoute }) {
  return (
    <div className="border border-dashed border-border rounded-lg p-12 text-center">
      <h2 className="text-xl font-semibold mb-2">No {label} yet</h2>
      <p className="text-muted-foreground mb-6">
        Click below to create your first record.
      </p>
      {createRoute ? (
        <Link
          href={createRoute}
          className="inline-flex items-center px-4 py-2 bg-primary text-primary-foreground rounded"
        >
          Add {label.replace(/s$/, "")}
        </Link>
      ) : null}
    </div>
  );
}
"""


_COMPONENT_ENTITY_LIST_SKELETON = """\
/* AUTO-GENERATED — Lucid AI admin pipeline. */
export function EntityListSkeleton() {
  return (
    <div className="p-8 space-y-3">
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className="h-12 bg-muted/60 rounded animate-pulse"
        />
      ))}
    </div>
  );
}
"""


_COMPONENT_TOASTER = """\
"use client";
/* AUTO-GENERATED — Lucid AI admin pipeline. */
// Placeholder wrapper — Step 3.6 swaps this for a real notification
// surface. Kept as a no-op so other components can import it now
// without an unresolved-import build break.
export function Toaster() {
  return null;
}
"""


# ── src/app ──────────────────────────────────────────────────────────

def _build_root_layout(brand_name: str) -> str:
    title = f"{brand_name} — Admin"
    return (
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        'import "./globals.css";\n'
        'import { AuthGuard } from "@/components/AuthGuard.jsx";\n'
        'import { Sidebar } from "@/components/Sidebar.jsx";\n'
        'import { Header } from "@/components/Header.jsx";\n'
        'import { Toaster } from "@/components/Toaster.jsx";\n'
        '\n'
        f'export const metadata = {{ title: "{title}" }};\n'
        '\n'
        'export default function RootLayout({ children }) {\n'
        '  return (\n'
        '    <html lang="en">\n'
        '      <body>\n'
        '        <AuthGuard>\n'
        '          <div className="flex min-h-screen">\n'
        '            <Sidebar />\n'
        '            <div className="flex-1 flex flex-col">\n'
        '              <Header />\n'
        '              <main className="flex-1">{children}</main>\n'
        '            </div>\n'
        '          </div>\n'
        '        </AuthGuard>\n'
        '        <Toaster />\n'
        '      </body>\n'
        '    </html>\n'
        '  );\n'
        '}\n'
    )


# /login lives outside the AuthGuard — own layout, no sidebar.
_LOGIN_LAYOUT = """\
/* AUTO-GENERATED — Lucid AI admin pipeline. */
import "../globals.css";

export default function LoginLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
"""


def _build_login_page(brand_name: str) -> str:
    safe_brand = json.dumps(brand_name)
    return (
        '"use client";\n'
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        'import { useState } from "react";\n'
        'import { useRouter } from "next/navigation";\n'
        'import { getSupabaseBrowserClient } from "@/lib/supabase.js";\n'
        '\n'
        'export default function LoginPage() {\n'
        '  const [email, setEmail]       = useState("");\n'
        '  const [password, setPassword] = useState("");\n'
        '  const [error, setError]       = useState(null);\n'
        '  const [loading, setLoading]   = useState(false);\n'
        '  const router = useRouter();\n'
        '\n'
        '  async function handleLogin(e) {\n'
        '    e.preventDefault();\n'
        '    setLoading(true);\n'
        '    setError(null);\n'
        '    const supabase = getSupabaseBrowserClient();\n'
        '    const { error: authError } = await supabase.auth.signInWithPassword({\n'
        '      email,\n'
        '      password,\n'
        '    });\n'
        '    if (authError) {\n'
        '      setError(authError.message);\n'
        '    } else {\n'
        '      router.push("/");\n'
        '    }\n'
        '    setLoading(false);\n'
        '  }\n'
        '\n'
        '  return (\n'
        '    <div className="min-h-screen flex items-center justify-center bg-muted">\n'
        '      <div className="bg-card p-8 rounded-lg shadow-md w-full max-w-md">\n'
        f'        <h1 className="text-2xl font-bold mb-6">Sign in to {{{safe_brand}}}</h1>\n'
        '        <form onSubmit={handleLogin} className="space-y-4">\n'
        '          <input\n'
        '            type="email"\n'
        '            value={email}\n'
        '            onChange={(e) => setEmail(e.target.value)}\n'
        '            placeholder="Email"\n'
        '            className="w-full px-3 py-2 border border-border rounded bg-background"\n'
        '            required\n'
        '            autoComplete="email"\n'
        '          />\n'
        '          <input\n'
        '            type="password"\n'
        '            value={password}\n'
        '            onChange={(e) => setPassword(e.target.value)}\n'
        '            placeholder="Password"\n'
        '            className="w-full px-3 py-2 border border-border rounded bg-background"\n'
        '            required\n'
        '            autoComplete="current-password"\n'
        '          />\n'
        '          {error ? (\n'
        '            <div className="text-destructive text-sm">{error}</div>\n'
        '          ) : null}\n'
        '          <button\n'
        '            type="submit"\n'
        '            disabled={loading}\n'
        '            className="w-full bg-primary text-primary-foreground py-2 rounded disabled:opacity-50"\n'
        '          >\n'
        '            {loading ? "Signing in…" : "Sign in"}\n'
        '          </button>\n'
        '        </form>\n'
        '      </div>\n'
        '    </div>\n'
        '  );\n'
        '}\n'
    )


def _build_dashboard_page(data_model: DataModel) -> str:
    """Dashboard showing one tile per entity with live row count.

    The tile count is fetched via the authenticated read RPC, so empty
    tables show 0 (truthy), and unreachable tables show "—" (the
    catch arm). Routes match the admin_plan's `/admin/<slug>` shape
    BUT we're already inside the `/admin` mount point — the routes
    on this dashboard are relative to the root, not nested under
    /admin. We default to `/<slug>` here and document the alternative
    in admin_pipeline if a future iteration wants the panel served
    under /admin/.
    """
    tiles = [
        {
            "name":  t.name,
            "label": t.plural_label or t.name,
            "route": f"/{_kebab(t.name)}",
        }
        for t in data_model.tables
    ]
    tiles_json = json.dumps(tiles, indent=2)
    return (
        '"use client";\n'
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        'import { useEffect, useState } from "react";\n'
        'import Link from "next/link";\n'
        'import { listCollection } from "@/lib/db_admin.js";\n'
        'import { EntityListSkeleton } from "@/components/EntityListSkeleton.jsx";\n'
        '\n'
        f'const ENTITIES = {tiles_json};\n'
        '\n'
        'export default function DashboardPage() {\n'
        '  const [counts, setCounts]   = useState({});\n'
        '  const [loading, setLoading] = useState(true);\n'
        '\n'
        '  useEffect(() => {\n'
        '    let cancelled = false;\n'
        '    async function loadCounts() {\n'
        '      const results = {};\n'
        '      for (const entity of ENTITIES) {\n'
        '        try {\n'
        '          const rows = await listCollection(entity.name, { limit: 1000 });\n'
        '          results[entity.name] = rows.length;\n'
        '        } catch {\n'
        '          results[entity.name] = null;\n'
        '        }\n'
        '      }\n'
        '      if (!cancelled) {\n'
        '        setCounts(results);\n'
        '        setLoading(false);\n'
        '      }\n'
        '    }\n'
        '    loadCounts();\n'
        '    return () => { cancelled = true; };\n'
        '  }, []);\n'
        '\n'
        '  if (loading) return <EntityListSkeleton />;\n'
        '\n'
        '  return (\n'
        '    <div className="p-8">\n'
        '      <h1 className="text-3xl font-bold mb-6">Dashboard</h1>\n'
        '      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">\n'
        '        {ENTITIES.map((entity) => (\n'
        '          <Link\n'
        '            key={entity.name}\n'
        '            href={entity.route}\n'
        '            className="bg-card p-6 rounded-lg shadow hover:shadow-md transition border border-border"\n'
        '          >\n'
        '            <div className="text-sm text-muted-foreground">{entity.label}</div>\n'
        '            <div className="text-3xl font-bold mt-2">\n'
        '              {counts[entity.name] ?? "—"}\n'
        '            </div>\n'
        '          </Link>\n'
        '        ))}\n'
        '      </div>\n'
        '    </div>\n'
        '  );\n'
        '}\n'
    )


# ── Stub CRUD pages — Step 3.6 will replace these ────────────────────

def _stub_list_page(table: TableDefinition, seed_count: int) -> str:
    """List view stub. Shows the count so a user can confirm seeding
    worked end-to-end before Step 3.6 lands."""
    label = table.plural_label or table.name
    create_route = f"/{_kebab(table.name)}/new"
    return (
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        '/* Step 3.6 will replace this with the real list view. */\n'
        'import Link from "next/link";\n'
        '\n'
        'export default function Page() {\n'
        '  return (\n'
        '    <div className="p-8">\n'
        f'      <h1 className="text-3xl font-bold mb-4">{label}</h1>\n'
        '      <p className="text-muted-foreground mb-6">\n'
        '        This page will be generated in Step 3.6 (CRUD codegen).<br />\n'
        f'        Seed data: {seed_count} {label} (from Stage 4.7).\n'
        '      </p>\n'
        '      <div className="flex gap-3">\n'
        f'        <Link href="{create_route}" className="px-4 py-2 bg-primary text-primary-foreground rounded">\n'
        f'          Add {table.singular_label or table.name}\n'
        '        </Link>\n'
        '      </div>\n'
        '    </div>\n'
        '  );\n'
        '}\n'
    )


def _stub_create_page(table: TableDefinition) -> str:
    label = table.singular_label or table.name
    list_route = f"/{_kebab(table.name)}"
    return (
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        '/* Step 3.6 will replace this with the real create form. */\n'
        'import Link from "next/link";\n'
        '\n'
        'export default function Page() {\n'
        '  return (\n'
        '    <div className="p-8 max-w-2xl">\n'
        f'      <h1 className="text-3xl font-bold mb-4">Add {label}</h1>\n'
        '      <p className="text-muted-foreground mb-6">\n'
        '        This form will be generated in Step 3.6 (CRUD codegen).\n'
        '      </p>\n'
        f'      <Link href="{list_route}" className="text-sm text-muted-foreground hover:underline">\n'
        '        ← Back to list\n'
        '      </Link>\n'
        '    </div>\n'
        '  );\n'
        '}\n'
    )


def _stub_edit_page(table: TableDefinition) -> str:
    label = table.singular_label or table.name
    list_route = f"/{_kebab(table.name)}"
    return (
        '/* AUTO-GENERATED — Lucid AI admin pipeline. */\n'
        '/* Step 3.6 will replace this with the real edit form. */\n'
        'import Link from "next/link";\n'
        '\n'
        'export default function Page({ params }) {\n'
        '  return (\n'
        '    <div className="p-8 max-w-2xl">\n'
        f'      <h1 className="text-3xl font-bold mb-4">Edit {label}</h1>\n'
        '      <p className="text-muted-foreground mb-6">\n'
        '        This form will be generated in Step 3.6 (CRUD codegen).<br />\n'
        '        Row id: <code className="text-sm">{params.id}</code>\n'
        '      </p>\n'
        f'      <Link href="{list_route}" className="text-sm text-muted-foreground hover:underline">\n'
        '        ← Back to list\n'
        '      </Link>\n'
        '    </div>\n'
        '  );\n'
        '}\n'
    )


# ── Main entry ───────────────────────────────────────────────────────

def _seed_counts_via_admin_client(
    *,
    data_model: DataModel,
    tenant_schema: str,
) -> dict[str, int]:
    """Best-effort: how many seed rows landed per table.

    Returns a {table_name: count} dict; tables not reachable show 0
    (so stub pages emit `0 records` rather than crashing). This is a
    cosmetic signal — used only to color the stub pages and to
    populate the result dict's `tables_with_seed_data` /
    `tables_empty` lists.

    Implementation: read counts via a single SQL through `execute_ddl`.
    Falls back to all-zeros on any error.
    """
    if not tenant_schema or not data_model.tables:
        return {t.name: 0 for t in data_model.tables}

    # Synchronous best-effort. We're inside a sync function so we
    # don't await — instead we use the supabase-py sync client path
    # via a tiny event loop. If anything throws we return zeros.
    import asyncio
    try:
        from app.supabase_client import managed_admin_client
    except Exception:
        return {t.name: 0 for t in data_model.tables}

    async def _run() -> dict[str, int]:
        counts: dict[str, int] = {}
        async with managed_admin_client() as c:
            for table in data_model.tables:
                # Quote the identifiers inline. format(...) is safe
                # because tenant_schema + table.name passed the
                # regex validator in tenant_sql_generator.
                sql = (
                    f'SELECT COUNT(*) AS n FROM "{tenant_schema}"."{table.name}"'
                )
                try:
                    res = await c.rpc(
                        "execute_ddl", {"p_sql": sql},
                    ).execute()
                    # execute_ddl returns NULL on success — no row data.
                    # Fall back to a separate count via the read RPC.
                    counts[table.name] = 0
                except Exception:
                    counts[table.name] = 0
        return counts

    try:
        return asyncio.get_event_loop().run_until_complete(_run())
    except RuntimeError:
        # We're already inside a running loop — caller will skip
        # this best-effort and stub pages just show 0.
        return {t.name: 0 for t in data_model.tables}


def build_admin_foundation(
    *,
    workspace_path: str,
    data_model: DataModel,
    admin_plan: dict,
    tenant_schema: str,
    project_id: str,
    supabase_url: str,
    supabase_anon_key: str,
    seed_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Write every foundation file for the admin project.

    Returns a dict:
      • `files_written`: list of relative paths actually written
      • `tables_with_seed_data`: tables whose seed_count > 0
      • `tables_empty`: tables whose seed_count == 0 (or unknown)

    `seed_counts` is optional and purely cosmetic — used to color
    the stub list pages with the row count from Stage 4.7. Callers
    that don't have it (e.g. dry-run scripts) can pass None and the
    stubs will say "0 rows" without any DB round-trip.
    """
    branding   = admin_plan.get("branding") or {}
    brand_name = branding.get("brand_name") or "Admin"
    primary_color = branding.get("primary_color") or "#0f172a"
    nav_items  = admin_plan.get("navigation") or []

    if seed_counts is None:
        seed_counts = {t.name: 0 for t in data_model.tables}

    files: dict[str, str] = {}

    # ── Project root ────────────────────────────────────────────────
    files["package.json"]      = _build_package_json(brand_name)
    files["next.config.js"]    = _NEXT_CONFIG
    files["tailwind.config.js"] = _TAILWIND_CONFIG
    files["postcss.config.js"] = _POSTCSS_CONFIG
    files["jsconfig.json"]     = _JSCONFIG
    files[".gitignore"]        = _GITIGNORE
    files[".env.example"]      = _ENV_EXAMPLE
    files[".env.local"] = _build_env_local(
        supabase_url=supabase_url,
        supabase_anon_key=supabase_anon_key,
        project_id=project_id,
        tenant_schema=tenant_schema,
        brand_name=brand_name,
        primary_color=primary_color,
    )
    files["README.md"] = _build_readme(brand_name, data_model)

    # ── src/lib ─────────────────────────────────────────────────────
    files["src/lib/supabase.js"]  = _LIB_SUPABASE_JS
    files["src/lib/db_admin.js"]  = _build_db_admin_js(data_model)
    files["src/lib/auth.js"]      = _LIB_AUTH_JS
    files["src/lib/utils.js"]     = _LIB_UTILS_JS

    # ── src/components ──────────────────────────────────────────────
    files["src/components/AuthGuard.jsx"]          = _COMPONENT_AUTH_GUARD
    files["src/components/Sidebar.jsx"]            = _build_sidebar(nav_items, brand_name)
    files["src/components/Header.jsx"]             = _COMPONENT_HEADER
    files["src/components/EmptyState.jsx"]         = _COMPONENT_EMPTY_STATE
    files["src/components/EntityListSkeleton.jsx"] = _COMPONENT_ENTITY_LIST_SKELETON
    files["src/components/Toaster.jsx"]            = _COMPONENT_TOASTER

    # ── src/app ─────────────────────────────────────────────────────
    files["src/app/globals.css"] = _GLOBALS_CSS_TEMPLATE.format(
        primary_hsl=_hex_to_hsl_string(primary_color),
    )
    files["src/app/layout.jsx"] = _build_root_layout(brand_name)
    files["src/app/page.jsx"]   = _build_dashboard_page(data_model)
    files["src/app/login/layout.jsx"] = _LOGIN_LAYOUT
    files["src/app/login/page.jsx"]   = _build_login_page(brand_name)

    # ── Per-entity stub pages ───────────────────────────────────────
    tables_with_seed_data: list[str] = []
    tables_empty:          list[str] = []
    for table in data_model.tables:
        slug = _kebab(table.name)
        count = seed_counts.get(table.name, 0)
        if count > 0:
            tables_with_seed_data.append(table.name)
        else:
            tables_empty.append(table.name)
        files[f"src/app/{slug}/page.jsx"]        = _stub_list_page(table, count)
        files[f"src/app/{slug}/new/page.jsx"]    = _stub_create_page(table)
        files[f"src/app/{slug}/[id]/page.jsx"]   = _stub_edit_page(table)

    # ── Write everything ────────────────────────────────────────────
    written: list[str] = []
    for rel_path, content in files.items():
        abs_path = os.path.join(workspace_path, rel_path)
        os.makedirs(os.path.dirname(abs_path) or workspace_path, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel_path)

    logger.info(
        "admin_foundation_builder: wrote %d files for %d tables "
        "(seeded=%d empty=%d) at %s",
        len(written), len(data_model.tables),
        len(tables_with_seed_data), len(tables_empty),
        workspace_path,
    )

    return {
        "files_written":         written,
        "tables_with_seed_data": tables_with_seed_data,
        "tables_empty":          tables_empty,
    }
