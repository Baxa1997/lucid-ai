"""
pipeline/step5_fixers.py — Pre-build import auto-correction utilities.

Extracted verbatim from task_pipeline.py (lines 158–269).
Zero logic changes.
"""

import os
import logging

logger = logging.getLogger(__name__)


async def _fix_broken_layout_imports(workspace_path: str, websocket=None):
    """Pre-build safety net: fix JS/JSX imports that reference non-existent component files.

    Common scenario: Claude rewrites layout.js to import 'Navbar'/'Footer'
    instead of the template's actual names (MarketingHeader/MarketingFooter).
    This function scans layout and page files for broken imports and auto-corrects them.
    """
    import re as _re_fix
    import glob

    layout_dir = os.path.join(workspace_path, "src", "components", "layout")
    if not os.path.isdir(layout_dir):
        return  # No layout components → nothing to fix

    # Map of actual component files that exist
    actual_files = {}
    for f in os.listdir(layout_dir):
        name_no_ext = os.path.splitext(f)[0]
        actual_files[name_no_ext.lower()] = name_no_ext  # lowered key → original name

    # Scan layout.js / layout.jsx / layout.tsx files for broken imports
    target_files = glob.glob(os.path.join(workspace_path, "src", "**", "layout.js"), recursive=True)
    target_files += glob.glob(os.path.join(workspace_path, "src", "**", "layout.jsx"), recursive=True)
    target_files += glob.glob(os.path.join(workspace_path, "src", "**", "layout.tsx"), recursive=True)

    # Also check App.vue / App.jsx
    for app_name in ["App.vue", "App.jsx", "App.tsx"]:
        app_path = os.path.join(workspace_path, "src", app_name)
        if os.path.isfile(app_path):
            target_files.append(app_path)

    # Known renames: Claude's generic names → likely template names
    _rename_map = {
        "navbar": ["marketingheader", "appheader", "header"],
        "footer": ["marketingfooter", "appfooter"],
        "sidebar": ["appsidebar"],
        "header": ["marketingheader", "appheader"],
    }

    fixes_made = 0
    for target_file in target_files:
        try:
            content = open(target_file, "r").read()
            original = content

            # Find all component imports from layout directory
            import_pattern = _re_fix.compile(
                r"""(import\s+\{?\s*)(\w+)(\s*\}?\s*from\s*['"][^'"]*?/layout/)(\w+)(['"])""",
            )
            for match in import_pattern.finditer(content):
                imported_name = match.group(4)  # The component file name
                if imported_name.lower() not in actual_files:
                    # This import references a file that doesn't exist!
                    # Try to find the correct name from rename map
                    wanted = _rename_map.get(imported_name.lower(), [])
                    replacement = None
                    for candidate in wanted:
                        if candidate in actual_files:
                            replacement = actual_files[candidate]
                            break

                    if not replacement:
                        # Fallback: find ANY file with similar purpose
                        for key, actual_name in actual_files.items():
                            if "header" in key and "header" in imported_name.lower():
                                replacement = actual_name
                                break
                            if "footer" in key and "footer" in imported_name.lower():
                                replacement = actual_name
                                break

                    if replacement:
                        old_import = match.group(0)
                        # Also fix the imported binding name
                        old_binding = match.group(2)
                        new_import = old_import.replace(imported_name, replacement)
                        if old_binding.lower() != replacement.lower():
                            new_import = new_import.replace(old_binding, replacement, 1)
                        content = content.replace(old_import, new_import)

                        # Also fix JSX usage: <Navbar /> → <MarketingHeader />
                        content = _re_fix.sub(
                            rf'<{old_binding}(\s|/|>)',
                            f'<{replacement}\\1',
                            content,
                        )
                        content = _re_fix.sub(
                            rf'</{old_binding}>',
                            f'</{replacement}>',
                            content,
                        )
                        fixes_made += 1

            if content != original:
                with open(target_file, "w") as f:
                    f.write(content)
                rel = os.path.relpath(target_file, workspace_path)
                logger.info("Pre-build import fixer: fixed imports in %s", rel)
                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "progress",
                            "message": f"🔧 Auto-fixed broken imports in {rel}",
                        })
                    except Exception:
                        pass
        except Exception as _e:
            logger.warning("Pre-build import fixer: error scanning %s: %s", target_file, _e)

    if fixes_made:
        logger.info("Pre-build import fixer: %d imports auto-corrected", fixes_made)
