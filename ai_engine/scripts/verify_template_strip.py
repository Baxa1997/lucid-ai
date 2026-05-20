"""End-to-end smoke for the template-strip + V2 foundation flow.

Clones the react-admin template fresh, runs build_admin_foundation
against it with a synthetic 2-entity data_model (so we exercise the
per-entity file emission too), then runs `pnpm install && pnpm build`
to verify the result actually compiles.

No Anthropic calls. No Supabase calls. Just deterministic foundation
emission + a build check.

Run from ai_engine/:
    venv/bin/python scripts/verify_template_strip.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")
os.environ.setdefault("SUPABASE_JWT_SECRET", "x")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
os.environ.setdefault("ENCRYPTION_KEY", "x" * 32)
os.environ.setdefault("INTERNAL_API_KEY", "x")


from app.services.admin_foundation_builder import build_admin_foundation
from app.services.data_model import DataModel, TableDefinition, FieldDefinition


def _make_model() -> DataModel:
    # Two entities: one with a status (would get kanban detection) and
    # one with a scheduled date (calendar detection). Verifies the
    # foundation builder handles extras + standard CRUD.
    members = TableDefinition(
        name="members",
        description="Studio members and their status",
        singular_label="Member",
        plural_label="Members",
        public_read=False,
        fields=[
            FieldDefinition(name="full_name", type="text", required=True),
            FieldDefinition(name="email", type="email", required=True),
            FieldDefinition(
                name="status", type="text",
                enum_values=["active", "paused", "cancelled"],
            ),
            FieldDefinition(name="joined_at", type="datetime"),
        ],
    )
    classes = TableDefinition(
        name="classes",
        description="Scheduled fitness classes",
        singular_label="Class",
        plural_label="Classes",
        public_read=False,
        fields=[
            FieldDefinition(name="title", type="text", required=True),
            FieldDefinition(name="instructor", type="text"),
            FieldDefinition(name="scheduled_at", type="datetime", required=True),
        ],
    )
    return DataModel(tables=[members, classes])


def main():
    tmp = tempfile.mkdtemp(prefix="verify_template_strip_")
    print(f"Workspace: {tmp}")

    # 1. Clone template
    print("Cloning react-admin template…")
    r = subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet",
         "https://github.com/LucidSoftware-tech/lucid-template-react-admin.git", "."],
        cwd=tmp, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print(f"❌ clone failed: {r.stderr[:300]}")
        return 1

    pre_count = sum(1 for _, _, fs in os.walk(os.path.join(tmp, "src")) for _ in fs)
    print(f"Template src files: {pre_count}")

    # 2. Build foundation (which now strips first)
    data_model = _make_model()
    admin_plan = {
        "branding": {
            "brand_name": "FitStudio",
            "primary_color": "#0ea5e9",
            "typography_voice": "soft",
            "cultural_intensity": "calm",
            "layout_density": "comfortable",
            "accent_motif": "minimal",
        },
        "navigation": [
            {"label": "Dashboard", "route": "/", "icon": "home"},
            {"label": "Members", "route": "/members", "icon": "users"},
            {"label": "Classes", "route": "/classes", "icon": "calendar"},
        ],
        "pages": [],
        "product": "admin",
        "brand": {"name": "FitStudio", "tagline": ""},
    }

    print("Running build_admin_foundation…")
    result = build_admin_foundation(
        workspace_path=tmp,
        data_model=data_model,
        admin_plan=admin_plan,
        tenant_schema="tenant_test",
        project_id="00000000-0000-0000-0000-000000000000",
        supabase_url="https://test.supabase.co",
        supabase_anon_key="x",
    )
    print(f"  wrote {len(result['files_written'])} files")
    post_count = sum(1 for _, _, fs in os.walk(os.path.join(tmp, "src")) for _ in fs)
    print(f"  src/ files: {pre_count} → {post_count}")

    # 3. Verify no template leftovers
    leftovers = []
    for path in [
        "src/features", "src/router", "src/pages/auth", "src/pages/dashboard",
        "src/components/layout", "src/components/ui", "src/services",
        "src/store", "src/api", "src/i18n", "src/providers", "src/constants",
        "src/styles", "src/config/navigation.js", "src/config/icons.js",
    ]:
        if os.path.exists(os.path.join(tmp, path)):
            leftovers.append(path)
    if leftovers:
        print(f"⚠️  Leftover template paths: {leftovers}")
    else:
        print("✓ All template conflict paths stripped")

    # 4. Verify V2 wrote expected files
    expected = [
        "src/App.jsx", "src/main.jsx", "src/index.css",
        "src/lib/supabase.js", "src/lib/db_admin.js", "src/lib/auth.js",
        "src/lib/utils.js",
        "src/components/AuthGuard.jsx", "src/components/Layout.jsx",
        "src/components/EmptyState.jsx", "src/components/EntityListSkeleton.jsx",
        "src/components/Toaster.jsx",
        "src/pages/Login.jsx", "src/pages/Dashboard.jsx",
        "src/pages/Settings.jsx", "src/pages/Profile.jsx",
        "src/pages/MembersList.jsx", "src/pages/MembersCreate.jsx",
        "src/pages/MembersEdit.jsx",
        "src/pages/MembersKanban.jsx",        # auto-detected
        "src/pages/ClassesList.jsx", "src/pages/ClassesCreate.jsx",
        "src/pages/ClassesEdit.jsx",
        "src/pages/ClassesCalendar.jsx",      # auto-detected
        "package.json", "vite.config.js", "tailwind.config.js",
        "postcss.config.mjs", "jsconfig.json", "index.html",
    ]
    missing = [p for p in expected if not os.path.isfile(os.path.join(tmp, p))]
    if missing:
        print(f"❌ Missing expected V2 files: {missing}")
        return 1
    print(f"✓ All {len(expected)} expected V2 files present")

    # 5. pnpm install + build
    print("\nRunning pnpm install…")
    r = subprocess.run(
        ["pnpm", "install", "--reporter=silent"],
        cwd=tmp, capture_output=True, text=True, timeout=180,
        env={**os.environ, "PATH": "/opt/homebrew/opt/node@22/bin:" + os.environ.get("PATH", "")},
    )
    if r.returncode != 0:
        print(f"❌ pnpm install failed:\n{r.stderr[-500:]}")
        return 1
    print("  ✓ install ok")

    print("Running pnpm build…")
    r = subprocess.run(
        ["pnpm", "run", "build"],
        cwd=tmp, capture_output=True, text=True, timeout=300,
        env={**os.environ, "PATH": "/opt/homebrew/opt/node@22/bin:" + os.environ.get("PATH", "")},
    )
    if r.returncode != 0:
        print(f"❌ build failed:\n{r.stdout[-700:]}\n---STDERR---\n{r.stderr[-700:]}")
        return 1
    # Show last few lines of build output
    print(r.stdout[-400:])
    print(f"\n✓ ALL CHECKS PASSED — workspace at {tmp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
