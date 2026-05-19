"""Stage 5 - deterministic React/Vite admin foundation generator.

Writes every file in the generated admin React app except the final
per-entity CRUD bodies, which Stage 6 may replace with Claude output.
After this step the project should:
  - boot via `npm run dev`
  - render `/login` with Supabase Auth
  - render a protected sidebar shell with one nav item per entity
  - render a dashboard with per-entity row counts
  - render stub list/new/edit pages that Stage 6 can overwrite

Admin panels deliberately use the local `admin-react`/Vite shape,
while websites and landing pages continue to use Next.js.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from app.services.data_model import DataModel, TableDefinition

logger = logging.getLogger(__name__)


# -- Slug/component helpers -------------------------------------------------

def _kebab(name: str) -> str:
    """`purchase_orders` -> `purchase-orders`."""
    return (name or "").replace("_", "-")


def _pascal(name: str) -> str:
    """`purchase_orders` -> `PurchaseOrders`."""
    return "".join(part.capitalize() for part in re.split(r"[_\-\s]+", name or "") if part) or "Entity"


def _slugify_brand(name: str) -> str:
    """Make a brand name safe for `package.json`'s `name` field."""
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "admin").lower()).strip("-")
    return s or "admin"


def _react_route(route: str) -> str:
    """Normalize older `/admin/...` plan routes to standalone app routes."""
    route = (route or "/").strip() or "/"
    if route == "/admin":
        route = "/"
    elif route.startswith("/admin/"):
        route = route[len("/admin"):] or "/"
    return route.replace("[id]", ":id")


# -- Icon mapping (matches admin_plan.py) -----------------------------------

LUCIDE_ICON_NAMES = sorted({
    "home", "user-plus", "users", "graduation-cap", "user-cog",
    "truck", "headphones", "ticket", "shopping-cart", "file-text",
    "credit-card", "arrow-left-right", "package", "boxes", "car",
    "map", "calendar", "calendar-check", "folder", "check-square",
    "flag", "book", "book-open", "award", "trending-up", "activity",
    "message-square", "sticky-note", "wrench", "inbox", "utensils",
    "table",
})


def _icon_to_pascal(icon_kebab: str) -> str:
    return "".join(part.capitalize() for part in icon_kebab.split("-"))


# -- Tailwind / Vite root files ---------------------------------------------

_TAILWIND_CONFIG = """\
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
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
const config = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};

export default config;
"""


_VITE_CONFIG = """\
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
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
dist/
.vite/
.env.local
.env.*.local
.DS_Store
*.log
"""


_ENV_EXAMPLE = """\
# Copy to .env.local and fill in the values from the Supabase Dashboard.
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_PROJECT_ID=
VITE_TENANT_SCHEMA=
VITE_BRAND_NAME=
VITE_PRIMARY_COLOR=
"""


_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Lucid Admin</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""


_INDEX_CSS_TEMPLATE = """\
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {{
  --background: 0 0% 100%;
  --foreground: 222 47% 11%;
  --card: 0 0% 100%;
  --card-foreground: 222 47% 11%;
  --muted: 210 40% 96%;
  --muted-foreground: 215 16% 47%;
  --border: 214 32% 91%;
  --input: 214 32% 91%;
  --primary: {primary_hsl};
  --primary-foreground: 210 40% 98%;
  --destructive: 0 84% 60%;
  --destructive-foreground: 210 40% 98%;
  --ring: 215 20% 65%;
  --radius: 0.5rem;

  /* visual_dna-driven design tokens — components read these via
     Tailwind arbitrary values like p-[var(--admin-padding-y)_var(--admin-padding-x)]
     or font-[var(--font-heading)]. */
  --font-body: {body_font};
  --font-heading: {heading_font};
  --body-weight: {body_weight};
  --admin-padding-x: {density_padding_x};
  --admin-padding-y: {density_padding_y};
}}

* {{
  box-sizing: border-box;
}}

html {{
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}}

body {{
  margin: 0;
  min-height: 100vh;
  background-color: hsl(var(--background));
  color: hsl(var(--foreground));
  font-family: var(--font-body);
  font-weight: var(--body-weight);
}}

h1, h2, h3, h4, h5, h6 {{
  font-family: var(--font-heading);
}}

button,
input,
select,
textarea {{
  font: inherit;
}}

a {{
  color: inherit;
  text-decoration: none;
}}
"""


def _hex_to_hsl_string(hex_color: str) -> str:
    """Convert `#0f172a` -> `222 47% 11%`.

    Falls back to slate-900 on parse failure so generated CSS always builds.
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


# ── visual_dna → foundation knobs ──────────────────────────────────

_FONT_STACKS: dict[str, tuple[str, str]] = {
    # voice: (body_stack, heading_stack)
    "professional": (
        "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    ),
    "friendly": (
        "ui-rounded, 'SF Pro Rounded', system-ui, sans-serif",
        "ui-rounded, 'SF Pro Rounded', system-ui, sans-serif",
    ),
    "minimal": (
        "ui-sans-serif, system-ui, sans-serif",
        "ui-sans-serif, system-ui, sans-serif",
    ),
    "editorial": (
        "ui-sans-serif, system-ui, sans-serif",
        "ui-serif, Georgia, 'Times New Roman', serif",
    ),
    "playful": (
        "ui-rounded, 'SF Pro Rounded', 'Comic Sans MS', system-ui, sans-serif",
        "ui-rounded, 'SF Pro Rounded', system-ui, sans-serif",
    ),
    "technical": (
        "ui-sans-serif, system-ui, sans-serif",
        "ui-monospace, 'SF Mono', Menlo, Consolas, monospace",
    ),
    "soft": (
        "ui-rounded, system-ui, sans-serif",
        "ui-rounded, system-ui, sans-serif",
    ),
}


# (padding_x, padding_y, body_font_weight) per density tier. Components
# read --admin-padding-x / --admin-padding-y via Tailwind arbitrary
# values like `p-[var(--admin-padding-y)_var(--admin-padding-x)]`.
_DENSITY_SPACING: dict[str, dict[str, str]] = {
    "compact":     {"padding_x": "0.75rem", "padding_y": "0.5rem",  "body_weight": "400"},
    "comfortable": {"padding_x": "1rem",    "padding_y": "0.75rem", "body_weight": "400"},
    "spacious":    {"padding_x": "1.5rem",  "padding_y": "1rem",    "body_weight": "400"},
}


def _font_stack_for_voice(voice: str) -> tuple[str, str]:
    """Return (body_stack, heading_stack) CSS font-family strings."""
    return _FONT_STACKS.get((voice or "").strip().lower(),
                             _FONT_STACKS["professional"])


def _density_spacing(density: str) -> dict[str, str]:
    return _DENSITY_SPACING.get((density or "").strip().lower(),
                                 _DENSITY_SPACING["comfortable"])


def _adjust_hsl_for_intensity(hsl_str: str, intensity: str) -> str:
    """Tweak the primary color's saturation according to cultural_intensity.

    - ``calm``: reduce saturation by 15 points (more muted brand color).
    - ``energetic``: boost saturation by 15 points (brighter accents).
    - ``editorial`` (or anything else): keep saturation, drop lightness
      slightly so headings + button accents read more 'authored'.

    Caps S / L at [0, 100] so out-of-range Tailwind/CSS values never leak.
    """
    parts = (hsl_str or "").strip().replace("%", "").split()
    if len(parts) != 3:
        return hsl_str
    try:
        h_, s_, l_ = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return hsl_str

    norm = (intensity or "").strip().lower()
    if norm == "calm":
        s_ = max(0, s_ - 15)
    elif norm == "energetic":
        s_ = min(100, s_ + 15)
    elif norm == "editorial":
        l_ = max(0, l_ - 3)

    return f"{h_} {s_}% {l_}%"


def _build_package_json(brand_name: str) -> str:
    pkg = {
        "name": f"{_slugify_brand(brand_name)}-admin",
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview",
        },
        "dependencies": {
            "@supabase/supabase-js": "^2.45.0",
            "clsx": "^2.1.1",
            "lucide-react": "^0.460.0",
            "react": "^19.0.0",
            "react-dom": "^19.0.0",
            "react-hook-form": "^7.53.0",
            "react-router-dom": "^7.0.0",
            "tailwind-merge": "^2.5.4",
        },
        "devDependencies": {
            "@vitejs/plugin-react": "^4.3.0",
            "autoprefixer": "^10.4.20",
            "postcss": "^8.4.49",
            "tailwindcss": "^3.4.17",
            "vite": "^6.0.0",
        },
    }
    return json.dumps(pkg, indent=2) + "\n"


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
        "# AUTO-GENERATED - Lucid AI admin pipeline.\n"
        "# Anon key is safe to ship to the browser; RLS and tenant RPCs enforce isolation.\n"
        f"VITE_SUPABASE_URL={supabase_url}\n"
        f"VITE_SUPABASE_ANON_KEY={supabase_anon_key}\n"
        f"VITE_PROJECT_ID={project_id}\n"
        f"VITE_TENANT_SCHEMA={tenant_schema}\n"
        f"VITE_BRAND_NAME={brand_name}\n"
        f"VITE_PRIMARY_COLOR={primary_color}\n"
    )


def _build_readme(brand_name: str, data_model: DataModel) -> str:
    entities = ", ".join(t.plural_label or t.name for t in data_model.tables)
    return (
        f"# {brand_name} - Admin\n\n"
        f"Auto-generated by Lucid AI. Manages: {entities or '(no entities yet)'}.\n\n"
        "## Quickstart\n\n"
        "```bash\n"
        "npm install\n"
        "npm run dev\n"
        "```\n\n"
        "Then open http://localhost:5173/login.\n\n"
        "## Stack\n\n"
        "- React + Vite\n"
        "- React Router\n"
        "- Supabase Auth + tenant RPCs\n"
        "- Tailwind CSS + HSL design tokens\n"
        "- react-hook-form, lucide-react\n"
    )


# -- src/lib ---------------------------------------------------------------

_LIB_SUPABASE_JS = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
import { createClient } from "@supabase/supabase-js";

let client = null;

export function getSupabaseBrowserClient() {
  if (client) return client;

  const url = import.meta.env.VITE_SUPABASE_URL;
  const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    throw new Error(
      "Lucid: VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY is missing.",
    );
  }

  client = createClient(url, anonKey);
  return client;
}
"""


def _build_db_admin_js(data_model: DataModel) -> str:
    names = [t.name for t in data_model.tables]
    table_list_comment = (
        "/*\n * Entities exposed by this admin:\n"
        + "".join(f" *   - {n}\n" for n in names)
        + " */"
    )
    return (
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        f"{table_list_comment}\n"
        'import { getSupabaseBrowserClient } from "./supabase.js";\n'
        "\n"
        "const PROJECT_ID = import.meta.env.VITE_PROJECT_ID;\n"
        "\n"
        "function projectId() {\n"
        "  if (!PROJECT_ID) throw new Error(\"Lucid: VITE_PROJECT_ID is missing.\");\n"
        "  return PROJECT_ID;\n"
        "}\n"
        "\n"
        "export async function listCollection(tableName, options = {}) {\n"
        "  const supabase = getSupabaseBrowserClient();\n"
        "  const { data, error } = await supabase.rpc(\n"
        '    "get_tenant_collection_authenticated",\n'
        "    {\n"
        "      p_project_id: projectId(),\n"
        "      p_table_name: tableName,\n"
        '      p_order_by: options.orderBy ?? "created_at",\n'
        '      p_order_direction: options.orderDirection ?? "desc",\n'
        "      p_limit: options.limit ?? 100,\n"
        "      p_offset: options.offset ?? 0,\n"
        "    },\n"
        "  );\n"
        "  if (error) throw error;\n"
        "  return data || [];\n"
        "}\n"
        "\n"
        "export async function createRow(tableName, payload) {\n"
        "  const supabase = getSupabaseBrowserClient();\n"
        '  const { data, error } = await supabase.rpc("set_tenant_row", {\n'
        "    p_project_id: projectId(),\n"
        "    p_table_name: tableName,\n"
        "    p_payload: payload,\n"
        "  });\n"
        "  if (error) throw error;\n"
        "  return data;\n"
        "}\n"
        "\n"
        "export async function updateRow(tableName, rowId, payload) {\n"
        "  const supabase = getSupabaseBrowserClient();\n"
        '  const { data, error } = await supabase.rpc("update_tenant_row", {\n'
        "    p_project_id: projectId(),\n"
        "    p_table_name: tableName,\n"
        "    p_row_id: rowId,\n"
        "    p_payload: payload,\n"
        "  });\n"
        "  if (error) throw error;\n"
        "  return data;\n"
        "}\n"
        "\n"
        "export async function deleteRow(tableName, rowId) {\n"
        "  const supabase = getSupabaseBrowserClient();\n"
        '  const { error } = await supabase.rpc("delete_tenant_row", {\n'
        "    p_project_id: projectId(),\n"
        "    p_table_name: tableName,\n"
        "    p_row_id: rowId,\n"
        "  });\n"
        "  if (error) throw error;\n"
        "}\n"
    )


_LIB_AUTH_JS = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
import { useEffect, useState } from "react";
import { getSupabaseBrowserClient } from "./supabase.js";

export function useAuth() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    let mounted = true;

    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!mounted) return;
      setSession(session);
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setSession(session);
        setLoading(false);
      },
    );

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, []);

  return { session, user: session?.user || null, loading };
}

export async function signOut() {
  const supabase = getSupabaseBrowserClient();
  await supabase.auth.signOut();
}
"""


_LIB_UTILS_JS = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Intl.DateTimeFormat("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}
"""


# -- src/components --------------------------------------------------------

_COMPONENT_AUTH_GUARD = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
import { Navigate } from "react-router-dom";
import { useAuth } from "@/lib/auth.js";

export function AuthGuard({ children }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-sm text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
"""


def _build_layout(nav_items: list[dict[str, Any]], brand_name: str) -> str:
    used_icons = sorted({item.get("icon") or "table" for item in nav_items} | {"home", "table"})
    import_list = ", ".join(_icon_to_pascal(i) for i in used_icons)
    icon_map_entries = ",\n  ".join(
        f'"{name}": {_icon_to_pascal(name)}' for name in used_icons
    )
    nav_json = json.dumps(
        [
            {
                "label": item["label"],
                "route": _react_route(item["route"]),
                "icon": item.get("icon") or "table",
            }
            for item in nav_items
        ],
        indent=2,
    )
    return (
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        'import { useState } from "react";\n'
        'import { NavLink, useNavigate } from "react-router-dom";\n'
        f'import {{ {import_list}, LogOut, Menu, X }} from "lucide-react";\n'
        'import { signOut } from "@/lib/auth.js";\n'
        "\n"
        f"const BRAND_NAME = {json.dumps(brand_name)};\n"
        f"const NAV = {nav_json};\n"
        "const ICONS = {\n"
        f"  {icon_map_entries}\n"
        "};\n"
        "\n"
        "export default function Layout({ children }) {\n"
        "  const [sidebarOpen, setSidebarOpen] = useState(true);\n"
        "  const navigate = useNavigate();\n"
        "\n"
        "  async function handleLogout() {\n"
        "    await signOut();\n"
        '    navigate("/login", { replace: true });\n'
        "  }\n"
        "\n"
        "  return (\n"
        '    <div className="flex min-h-screen bg-muted/30 text-foreground">\n'
        '      <aside className={(sidebarOpen ? "w-64" : "w-16") + " flex shrink-0 flex-col border-r border-border bg-slate-950 text-slate-100 transition-all"}>\n'
        '        <div className="flex h-14 items-center justify-between border-b border-slate-800 px-3">\n'
        '          {sidebarOpen ? <span className="truncate text-sm font-semibold">{BRAND_NAME}</span> : null}\n'
        '          <button type="button" className="rounded p-2 hover:bg-slate-800" onClick={() => setSidebarOpen((v) => !v)} aria-label="Toggle sidebar">\n'
        '            {sidebarOpen ? <X size={17} /> : <Menu size={17} />}\n'
        "          </button>\n"
        "        </div>\n"
        '        <nav className="flex-1 space-y-1 p-3">\n'
        "          {NAV.map((item) => {\n"
        "            const Icon = ICONS[item.icon] || ICONS.table;\n"
        "            return (\n"
        "              <NavLink\n"
        "                key={item.route}\n"
        "                to={item.route}\n"
        "                end={item.route === \"/\"}\n"
        "                className={({ isActive }) =>\n"
        '                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition " +\n'
        '                  (isActive ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-900 hover:text-white")\n'
        "                }\n"
        "              >\n"
        "                <Icon size={18} />\n"
        "                {sidebarOpen ? <span className=\"truncate\">{item.label}</span> : null}\n"
        "              </NavLink>\n"
        "            );\n"
        "          })}\n"
        "        </nav>\n"
        '        <div className="border-t border-slate-800 p-3">\n'
        '          <button type="button" onClick={handleLogout} className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm text-slate-300 hover:bg-slate-900 hover:text-white">\n'
        "            <LogOut size={18} />\n"
        "            {sidebarOpen ? <span>Sign out</span> : null}\n"
        "          </button>\n"
        "        </div>\n"
        "      </aside>\n"
        '      <main className="min-w-0 flex-1">\n'
        '        <div className="mx-auto w-full max-w-7xl p-6">{children}</div>\n'
        "      </main>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )


_COMPONENT_EMPTY_STATE = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
import { Link } from "react-router-dom";

export function EmptyState({ label, createRoute }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-card p-12 text-center">
      <h2 className="mb-2 text-xl font-semibold">No {label} yet</h2>
      <p className="mb-6 text-sm text-muted-foreground">
        Create the first record to start managing this collection.
      </p>
      {createRoute ? (
        <Link
          to={createRoute}
          className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Add {label.replace(/s$/, "")}
        </Link>
      ) : null}
    </div>
  );
}
"""


_COMPONENT_ENTITY_LIST_SKELETON = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
export function EntityListSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="h-12 animate-pulse rounded-md bg-muted" />
      ))}
    </div>
  );
}
"""


_COMPONENT_TOASTER = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
export function Toaster() {
  return null;
}
"""


# -- src/pages and app entry -----------------------------------------------

_MAIN_JSX = """\
/* AUTO-GENERATED - Lucid AI admin pipeline. */
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
"""


def _build_app_jsx(data_model: DataModel) -> str:
    imports = [
        'import { Navigate, Route, Routes } from "react-router-dom";',
        'import { AuthGuard } from "@/components/AuthGuard.jsx";',
        'import { Toaster } from "@/components/Toaster.jsx";',
        'import Layout from "@/components/Layout.jsx";',
        'import Login from "@/pages/Login.jsx";',
        'import Dashboard from "@/pages/Dashboard.jsx";',
    ]
    routes = [
        '        <Route path="/" element={<Dashboard />} />',
    ]
    for table in data_model.tables:
        base = _pascal(table.name)
        slug = _kebab(table.name)
        imports.extend([
            f'import {base}ListPage from "@/pages/{base}List.jsx";',
            f'import {base}CreatePage from "@/pages/{base}Create.jsx";',
            f'import {base}EditPage from "@/pages/{base}Edit.jsx";',
        ])
        routes.extend([
            f'        <Route path="/{slug}" element={{<{base}ListPage />}} />',
            f'        <Route path="/{slug}/new" element={{<{base}CreatePage />}} />',
            f'        <Route path="/{slug}/:id" element={{<{base}EditPage />}} />',
        ])
    routes.append('        <Route path="*" element={<Navigate to="/" replace />} />')

    return (
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        + "\n".join(imports)
        + "\n\n"
        "function ProtectedApp() {\n"
        "  return (\n"
        "    <AuthGuard>\n"
        "      <Layout>\n"
        "        <Routes>\n"
        + "\n".join(routes)
        + "\n"
        "        </Routes>\n"
        "      </Layout>\n"
        "    </AuthGuard>\n"
        "  );\n"
        "}\n\n"
        "export default function App() {\n"
        "  return (\n"
        "    <>\n"
        "      <Routes>\n"
        '        <Route path="/login" element={<Login />} />\n'
        '        <Route path="/*" element={<ProtectedApp />} />\n'
        "      </Routes>\n"
        "      <Toaster />\n"
        "    </>\n"
        "  );\n"
        "}\n"
    )


def _build_login_page(brand_name: str) -> str:
    return (
        '"use client";\n'
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        'import { useEffect, useState } from "react";\n'
        'import { useNavigate } from "react-router-dom";\n'
        'import { useAuth } from "@/lib/auth.js";\n'
        'import { getSupabaseBrowserClient } from "@/lib/supabase.js";\n'
        "\n"
        f"const BRAND_NAME = {json.dumps(brand_name)};\n"
        "\n"
        "export default function Login() {\n"
        '  const [email, setEmail] = useState("");\n'
        '  const [password, setPassword] = useState("");\n'
        "  const [error, setError] = useState(null);\n"
        "  const [loading, setLoading] = useState(false);\n"
        "  const { user, loading: authLoading } = useAuth();\n"
        "  const navigate = useNavigate();\n"
        "\n"
        "  useEffect(() => {\n"
        "    if (!authLoading && user) navigate(\"/\", { replace: true });\n"
        "  }, [authLoading, user, navigate]);\n"
        "\n"
        "  async function handleLogin(event) {\n"
        "    event.preventDefault();\n"
        "    setLoading(true);\n"
        "    setError(null);\n"
        "    const supabase = getSupabaseBrowserClient();\n"
        "    const { error: authError } = await supabase.auth.signInWithPassword({\n"
        "      email,\n"
        "      password,\n"
        "    });\n"
        "    if (authError) {\n"
        "      setError(authError.message);\n"
        "      setLoading(false);\n"
        "      return;\n"
        "    }\n"
        '    navigate("/", { replace: true });\n'
        "  }\n"
        "\n"
        "  return (\n"
        '    <div className="flex min-h-screen items-center justify-center bg-muted/60 px-4">\n'
        '      <div className="w-full max-w-md rounded-lg border border-border bg-card p-8 shadow-sm">\n'
        '        <h1 className="mb-2 text-2xl font-semibold">Sign in</h1>\n'
        '        <p className="mb-6 text-sm text-muted-foreground">{BRAND_NAME} admin</p>\n'
        '        <form onSubmit={handleLogin} className="space-y-4">\n'
        "          <input\n"
        '            type="email"\n'
        "            value={email}\n"
        "            onChange={(event) => setEmail(event.target.value)}\n"
        '            placeholder="Email"\n'
        '            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"\n'
        "            required\n"
        '            autoComplete="email"\n'
        "          />\n"
        "          <input\n"
        '            type="password"\n'
        "            value={password}\n"
        "            onChange={(event) => setPassword(event.target.value)}\n"
        '            placeholder="Password"\n'
        '            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"\n'
        "            required\n"
        '            autoComplete="current-password"\n'
        "          />\n"
        '          {error ? <div className="text-sm text-destructive">{error}</div> : null}\n'
        "          <button\n"
        '            type="submit"\n'
        "            disabled={loading}\n"
        '            className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"\n'
        "          >\n"
        '            {loading ? "Signing in..." : "Sign in"}\n'
        "          </button>\n"
        "        </form>\n"
        "      </div>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )


def _build_dashboard_page(data_model: DataModel) -> str:
    tiles = [
        {
            "name": t.name,
            "label": t.plural_label or t.name,
            "route": f"/{_kebab(t.name)}",
        }
        for t in data_model.tables
    ]
    return (
        '"use client";\n'
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        'import { useEffect, useState } from "react";\n'
        'import { Link } from "react-router-dom";\n'
        'import { listCollection } from "@/lib/db_admin.js";\n'
        'import { EntityListSkeleton } from "@/components/EntityListSkeleton.jsx";\n'
        "\n"
        f"const ENTITIES = {json.dumps(tiles, indent=2)};\n"
        "\n"
        "export default function Dashboard() {\n"
        "  const [counts, setCounts] = useState({});\n"
        "  const [loading, setLoading] = useState(true);\n"
        "\n"
        "  useEffect(() => {\n"
        "    let cancelled = false;\n"
        "    async function loadCounts() {\n"
        "      const results = {};\n"
        "      for (const entity of ENTITIES) {\n"
        "        try {\n"
        "          const rows = await listCollection(entity.name, { limit: 1000 });\n"
        "          results[entity.name] = rows.length;\n"
        "        } catch {\n"
        "          results[entity.name] = null;\n"
        "        }\n"
        "      }\n"
        "      if (!cancelled) {\n"
        "        setCounts(results);\n"
        "        setLoading(false);\n"
        "      }\n"
        "    }\n"
        "    loadCounts();\n"
        "    return () => { cancelled = true; };\n"
        "  }, []);\n"
        "\n"
        "  if (loading) return <EntityListSkeleton />;\n"
        "\n"
        "  return (\n"
        '    <div className="space-y-6">\n'
        "      <div>\n"
        '        <h1 className="text-3xl font-semibold">Dashboard</h1>\n'
        '        <p className="text-sm text-muted-foreground">Live overview of your admin collections.</p>\n'
        "      </div>\n"
        '      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">\n'
        "        {ENTITIES.map((entity) => (\n"
        "          <Link\n"
        "            key={entity.name}\n"
        "            to={entity.route}\n"
        '            className="rounded-lg border border-border bg-card p-5 shadow-sm transition hover:shadow-md"\n'
        "          >\n"
        '            <div className="text-sm text-muted-foreground">{entity.label}</div>\n'
        '            <div className="mt-2 text-3xl font-semibold">{counts[entity.name] ?? "-"}</div>\n'
        "          </Link>\n"
        "        ))}\n"
        "      </div>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )


def _stub_list_page(table: TableDefinition, seed_count: int) -> str:
    label = table.plural_label or table.name
    singular = table.singular_label or table.name
    slug = _kebab(table.name)
    base = _pascal(table.name)
    return (
        '"use client";\n'
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        "/* Step 3.6 will replace this with the real list view. */\n"
        'import { Link } from "react-router-dom";\n'
        'import { AuthGuard } from "@/components/AuthGuard.jsx";\n'
        "\n"
        f"export default function {base}ListPage() {{\n"
        "  return (\n"
        "    <AuthGuard>\n"
        '      <div className="space-y-4">\n'
        f'        <h1 className="text-3xl font-semibold">{label}</h1>\n'
        '        <p className="text-sm text-muted-foreground">\n'
        "          This page will be generated in Step 3.6 (CRUD codegen).<br />\n"
        f"          Seed data: {seed_count} {label}.\n"
        "        </p>\n"
        f'        <Link to="/{slug}/new" className="inline-flex rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">Add {singular}</Link>\n'
        "      </div>\n"
        "    </AuthGuard>\n"
        "  );\n"
        "}\n"
    )


def _stub_create_page(table: TableDefinition) -> str:
    label = table.singular_label or table.name
    slug = _kebab(table.name)
    base = _pascal(table.name)
    return (
        '"use client";\n'
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        "/* Step 3.6 will replace this with the real create form. */\n"
        'import { Link } from "react-router-dom";\n'
        'import { AuthGuard } from "@/components/AuthGuard.jsx";\n'
        "\n"
        f"export default function {base}CreatePage() {{\n"
        "  return (\n"
        "    <AuthGuard>\n"
        '      <div className="max-w-2xl space-y-4">\n'
        f'        <h1 className="text-3xl font-semibold">Add {label}</h1>\n'
        '        <p className="text-sm text-muted-foreground">This form will be generated in Step 3.6 (CRUD codegen).</p>\n'
        f'        <Link to="/{slug}" className="text-sm text-muted-foreground hover:underline">Back to list</Link>\n'
        "      </div>\n"
        "    </AuthGuard>\n"
        "  );\n"
        "}\n"
    )


def _stub_edit_page(table: TableDefinition) -> str:
    label = table.singular_label or table.name
    slug = _kebab(table.name)
    base = _pascal(table.name)
    return (
        '"use client";\n'
        "/* AUTO-GENERATED - Lucid AI admin pipeline. */\n"
        "/* Step 3.6 will replace this with the real edit form. */\n"
        'import { Link, useParams } from "react-router-dom";\n'
        'import { AuthGuard } from "@/components/AuthGuard.jsx";\n'
        "\n"
        f"export default function {base}EditPage() {{\n"
        "  const { id } = useParams();\n"
        "  return (\n"
        "    <AuthGuard>\n"
        '      <div className="max-w-2xl space-y-4">\n'
        f'        <h1 className="text-3xl font-semibold">Edit {label}</h1>\n'
        '        <p className="text-sm text-muted-foreground">\n'
        "          This form will be generated in Step 3.6 (CRUD codegen).<br />\n"
        '          Row id: <code className="text-xs">{id}</code>\n'
        "        </p>\n"
        f'        <Link to="/{slug}" className="text-sm text-muted-foreground hover:underline">Back to list</Link>\n'
        "      </div>\n"
        "    </AuthGuard>\n"
        "  );\n"
        "}\n"
    )


# -- Main entry -------------------------------------------------------------

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
    """Write every foundation file for the React/Vite admin project."""
    branding = admin_plan.get("branding") or {}
    brand_name = branding.get("brand_name") or "Admin"
    primary_color = branding.get("primary_color") or "#0f172a"
    typography_voice = branding.get("typography_voice") or "professional"
    layout_density = branding.get("layout_density") or "comfortable"
    cultural_intensity = branding.get("cultural_intensity") or "calm"
    nav_items = admin_plan.get("navigation") or []

    body_font, heading_font = _font_stack_for_voice(typography_voice)
    density = _density_spacing(layout_density)
    primary_hsl = _adjust_hsl_for_intensity(
        _hex_to_hsl_string(primary_color), cultural_intensity,
    )

    if seed_counts is None:
        seed_counts = {t.name: 0 for t in data_model.tables}

    files: dict[str, str] = {}

    files["package.json"] = _build_package_json(brand_name)
    files["index.html"] = _INDEX_HTML.replace("Lucid Admin", f"{brand_name} Admin")
    files["vite.config.js"] = _VITE_CONFIG
    files["tailwind.config.js"] = _TAILWIND_CONFIG
    files["postcss.config.mjs"] = _POSTCSS_CONFIG
    files["jsconfig.json"] = _JSCONFIG
    files[".gitignore"] = _GITIGNORE
    files[".env.example"] = _ENV_EXAMPLE
    files[".env.local"] = _build_env_local(
        supabase_url=supabase_url,
        supabase_anon_key=supabase_anon_key,
        project_id=project_id,
        tenant_schema=tenant_schema,
        brand_name=brand_name,
        primary_color=primary_color,
    )
    files["README.md"] = _build_readme(brand_name, data_model)

    files["src/index.css"] = _INDEX_CSS_TEMPLATE.format(
        primary_hsl=primary_hsl,
        body_font=body_font,
        heading_font=heading_font,
        body_weight=density["body_weight"],
        density_padding_x=density["padding_x"],
        density_padding_y=density["padding_y"],
    )
    files["src/main.jsx"] = _MAIN_JSX
    files["src/App.jsx"] = _build_app_jsx(data_model)

    files["src/lib/supabase.js"] = _LIB_SUPABASE_JS
    files["src/lib/db_admin.js"] = _build_db_admin_js(data_model)
    files["src/lib/auth.js"] = _LIB_AUTH_JS
    files["src/lib/utils.js"] = _LIB_UTILS_JS

    files["src/components/AuthGuard.jsx"] = _COMPONENT_AUTH_GUARD
    files["src/components/Layout.jsx"] = _build_layout(nav_items, brand_name)
    files["src/components/EmptyState.jsx"] = _COMPONENT_EMPTY_STATE
    files["src/components/EntityListSkeleton.jsx"] = _COMPONENT_ENTITY_LIST_SKELETON
    files["src/components/Toaster.jsx"] = _COMPONENT_TOASTER

    files["src/pages/Login.jsx"] = _build_login_page(brand_name)
    files["src/pages/Dashboard.jsx"] = _build_dashboard_page(data_model)

    tables_with_seed_data: list[str] = []
    tables_empty: list[str] = []
    for table in data_model.tables:
        count = seed_counts.get(table.name, 0)
        if count > 0:
            tables_with_seed_data.append(table.name)
        else:
            tables_empty.append(table.name)
        base = _pascal(table.name)
        files[f"src/pages/{base}List.jsx"] = _stub_list_page(table, count)
        files[f"src/pages/{base}Create.jsx"] = _stub_create_page(table)
        files[f"src/pages/{base}Edit.jsx"] = _stub_edit_page(table)

    written: list[str] = []
    for rel_path, content in files.items():
        abs_path = os.path.join(workspace_path, rel_path)
        os.makedirs(os.path.dirname(abs_path) or workspace_path, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel_path)

    logger.info(
        "admin_foundation_builder: wrote %d React/Vite files for %d tables "
        "(seeded=%d empty=%d) at %s",
        len(written), len(data_model.tables),
        len(tables_with_seed_data), len(tables_empty),
        workspace_path,
    )

    return {
        "files_written": written,
        "tables_with_seed_data": tables_with_seed_data,
        "tables_empty": tables_empty,
    }
