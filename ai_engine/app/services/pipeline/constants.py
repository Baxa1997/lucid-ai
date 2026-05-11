"""
pipeline/constants.py — module-level constants shared across all pipeline steps.

Extracted verbatim from task_pipeline.py (lines 32–86, 92–96, 293–311).
Zero logic changes.
"""
from __future__ import annotations

import os
import pathlib
import logging

logger = logging.getLogger(__name__)

# ── Centralized Gemini Models ─────────────────────────────────
# All Gemini calls use Pro. Live testing showed Flash 2.5 burning its
# entire 16k output budget on thinking tokens (15,726 thought / 654 output)
# and returning a truncated 3.4k-char spec — unrecoverable downstream.
# Pro is consistent: ~50s research + ~45s blueprint, ~$0.25 per generation
# vs ~$0.05 on Flash. The reliability gap dwarfs the cost gap for SaaS.
GEMINI_MODEL           = "gemini-3.1-pro-preview"   # classify, explore, implementation plan
GEMINI_RESEARCH_MODEL  = "gemini-3.1-pro-preview"   # product research (gemini_research / gemini_deep_research)
GEMINI_BLUEPRINT_MODEL = "gemini-3.1-pro-preview"   # blueprint → plan.json (gemini_create_plan)

# ── PLATFORM_GITHUB_TOKEN — loaded ONCE at module startup ────────────────────
# Load from environment first; fall back to the nearest .env file on disk.
# This avoids repeated lazy dotenv reads scattered across the pipeline and
# surfaces misconfiguration immediately when the server starts.
def _load_platform_token() -> str:
    """Return PLATFORM_GITHUB_TOKEN from env or the nearest .env file."""
    # 1. Already in environment (Docker / systemd / cloud-run injected it)
    token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
    if token:
        return token

    # 2. Walk up from this file to find a .env file
    _candidates = [
        pathlib.Path(__file__).resolve().parents[4] / ".env",
        pathlib.Path(__file__).resolve().parents[3] / ".env",
        pathlib.Path(__file__).resolve().parents[2] / ".env",
        pathlib.Path("/app/.env"),
        pathlib.Path.cwd() / ".env",
    ]
    for _candidate in _candidates:
        if _candidate.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(_candidate, override=False)
                token = os.environ.get("PLATFORM_GITHUB_TOKEN", "").strip()
                if token:
                    logger.info(
                        "PLATFORM_GITHUB_TOKEN loaded from %s (prefix: %s...)",
                        _candidate,
                        token[:7],
                    )
                    return token
            except Exception as _e:
                logger.warning("dotenv load failed for %s: %s", _candidate, _e)

    logger.warning(
        "PLATFORM_GITHUB_TOKEN not found in environment or any .env file. "
        "New-project template cloning and GitHub repo creation will be disabled."
    )
    return ""


PLATFORM_GITHUB_TOKEN: str = _load_platform_token()

# ── File-tree exclude set ─────────────────────────────────────
_FILE_TREE_EXCLUDE = {
    ".git", "node_modules", "__pycache__", ".next",
    ".venv", "venv", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".tox", ".eggs", ".claude",
}

# ── Template Registry ─────────────────────────────────────────────────────────
# Maps stack names (from [LUCID_PROJECT] header) to the LucidSoftware-tech
# template repos. The backend clones these DIRECTLY — no intermediate repo needed.
_TEMPLATE_REGISTRY: dict[str, str] = {
    "nextjs":          "LucidSoftware-tech/lucid-template-nextjs-website",
    "nextjs-website":  "LucidSoftware-tech/lucid-template-nextjs-website",
    "react":           "LucidSoftware-tech/lucid-template-react-admin",
    "react-admin":     "LucidSoftware-tech/lucid-template-react-admin",
    "vue":             "LucidSoftware-tech/lucid-template-vue-admin",
    "vue-admin":       "LucidSoftware-tech/lucid-template-vue-admin",
}

_PLATFORM_ORG = "LucidSoftware-tech"


# ── Available UI components per template ────────────────────────────────
# Source of truth: the index.js barrel file in each template's
# src/components/ui/. Telling Claude exactly which component names exist
# prevents it from inventing imports like `<Carousel />`, `<Command />`,
# `<Calendar />`, or `<Form />` that aren't shipped — these cause the
# Next.js build to fail with "Unsupported Server Component type: undefined".
#
# When a template adds or removes a component, update the matching block
# here AND its barrel file. Drift causes confident-but-broken imports.
_SHADCN_COMPONENTS: dict[str, str] = {
    "nextjs": (
        "PRIMITIVES INVENTORY — these are your Lego bricks, not the design.\n\n"
        "From '@/components/ui':\n"
        "  Button, Input, Card (CardHeader, CardContent, CardFooter, CardTitle,\n"
        "    CardDescription), Badge, Avatar (AvatarImage, AvatarFallback),\n"
        "    Table (TableHeader, TableBody, TableRow, TableHead, TableCell),\n"
        "    Textarea, Modal, Pagination, EmptyState, Spinner,\n"
        "    Accordion, AlertDialog, Checkbox, DropdownMenu, Label, ScrollArea,\n"
        "    Select, Separator, Sheet, Skeleton, Switch, Tabs, Tooltip.\n\n"
        "HOW TO USE THIS LIST:\n"
        "1. The list above is the SAFE-IMPORT set. Every section component\n"
        "   you build (Hero, FeatureGrid, Testimonials, PricingTable, CTA,\n"
        "   Footer, etc.) should be a NEW custom component file you write\n"
        "   from scratch in src/components/ — NOT one of the names above.\n"
        "2. Inside those custom sections, you may use the primitives above\n"
        "   as building blocks (a CTA might use Button, a TestimonialCard\n"
        "   might use Card + Avatar). That is how primitives are meant to\n"
        "   be consumed.\n"
        "3. ANTI-PATTERN — DO NOT do this: 'I need a feature section, so\n"
        "   I'll just render <Card> × 6 in a grid'. That is templated. A\n"
        "   feature section is a UNIQUE composition: alternating image+text\n"
        "   rows, a numbered process flow, a comparison table, an interactive\n"
        "   tabbed showcase, etc. Pick a layout that fits THIS project's\n"
        "   industry — never the same one twice.\n"
        "4. If you need a UI primitive NOT in the list (Carousel, Command,\n"
        "   Calendar, DatePicker, Form, Toast, etc.): build it inline. DO\n"
        "   NOT import unknown names from '@/components/ui' — the import\n"
        "   resolves to undefined and crashes the build."
    ),
    "react": (
        "PRIMITIVES INVENTORY — these are your Lego bricks, not the design.\n\n"
        "From '@/components/ui':\n"
        "  Button, Input, Card (CardHeader, CardContent, CardFooter, CardTitle,\n"
        "    CardDescription), Badge, Avatar (AvatarImage, AvatarFallback),\n"
        "    Table (TableHeader, TableBody, TableRow, TableHead, TableCell),\n"
        "    Pagination, Spinner, EmptyState, DataTable,\n"
        "    Dialog, AlertDialog, DropdownMenu, Select, Sheet, Tabs, Tooltip,\n"
        "    Popover, Alert, Checkbox, Switch, Textarea, Label, Separator,\n"
        "    Skeleton, ScrollArea, Toaster (from 'sonner').\n\n"
        "HOW TO USE THIS LIST:\n"
        "1. The list above is the SAFE-IMPORT set. Every page-level component\n"
        "   you build (Dashboard widgets, KPI cards, charts, sidebars, modals,\n"
        "   wizards) should be a NEW custom component file in src/components/\n"
        "   — NOT one of the names above.\n"
        "2. Use the primitives as building blocks INSIDE your custom\n"
        "   components (a KpiCard wraps Card + Badge; a UserMenu wraps\n"
        "   DropdownMenu + Avatar). That is how primitives are meant to\n"
        "   be consumed.\n"
        "3. ANTI-PATTERN — DO NOT just render <Card> × 4 in a grid for every\n"
        "   dashboard. A dashboard is a UNIQUE composition: chart strip on\n"
        "   top, kanban below; or stat callouts + activity feed; or pivoting\n"
        "   data table with filter sidebar. Pick what fits THIS admin\n"
        "   panel's domain.\n"
        "4. If you need a primitive NOT in the list (Carousel, Command,\n"
        "   Calendar, DatePicker, Form, etc.): build it inline. DO NOT\n"
        "   import unknown names from '@/components/ui'."
    ),
    "vue": (
        "PRIMITIVES INVENTORY — Vue 3 SFCs under src/components/ui/.\n\n"
        "Custom wrappers (PascalCase, default-export): UiAvatar, UiBadge,\n"
        "  UiButton, UiCard, UiEmptyState, UiInput, UiModal, UiPagination,\n"
        "  UiSpinner, UiTable.\n"
        "shadcn-vue primitives (kebab-case folders, import individual parts):\n"
        "  accordion, alert, avatar, badge, breadcrumb, button, card, checkbox,\n"
        "  collapsible, command, context-menu, dialog, drawer, dropdown-menu,\n"
        "  form, hover-card, input, label, menubar, navigation-menu, popover,\n"
        "  progress, radio-group, resizable, scroll-area, select, separator,\n"
        "  sheet, skeleton.\n\n"
        "HOW TO USE THIS LIST:\n"
        "1. Every page-level section is a NEW Vue SFC you write — the list\n"
        "   above is just the safe-import set of building blocks.\n"
        "2. ANTI-PATTERN: rendering <UiCard> × 6 in a grid for every page.\n"
        "   Compose unique layouts that fit this project's domain.\n"
        "3. If you need a primitive NOT in the list: build it as a new SFC.\n"
        "   Do not import unknown names."
    ),
}


def shadcn_components_block(stack: str | None) -> str:
    """Return the AVAILABLE UI COMPONENTS block for a given stack identifier.

    Stack matching is loose: we look for substrings ("nextjs", "next",
    "vue", "react", "vite") so callers can pass whatever ``project_stack``
    or ``skeleton_stack`` happens to be. Defaults to the nextjs block on
    unknown input — landing pages are the most common path and over-listing
    one extra component is cheaper than missing the whole block.
    """
    if not stack:
        return _SHADCN_COMPONENTS["nextjs"]
    s = str(stack).lower()
    if "nextjs" in s or "next" in s:
        return _SHADCN_COMPONENTS["nextjs"]
    if "vue" in s:
        return _SHADCN_COMPONENTS["vue"]
    if "react" in s or "vite" in s:
        return _SHADCN_COMPONENTS["react"]
    return _SHADCN_COMPONENTS["nextjs"]
