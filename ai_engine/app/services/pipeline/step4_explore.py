"""
pipeline/step4_explore.py — Pipeline Step 4: Gemini-powered explore, research, plan, and blueprint.

Extracted verbatim from task_pipeline.py (lines 1396–2796).
Zero logic changes.
"""

from __future__ import annotations

import os
import json
import asyncio
import logging

import google.generativeai as genai
from fastapi import WebSocket

from .constants import GEMINI_MODEL, GEMINI_BLUEPRINT_MODEL, GEMINI_RESEARCH_MODEL
from .ws_utils import _send_chat_message

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  STEP 4 — Explore codebase with Gemini Flash
# ═══════════════════════════════════════════════════════════════

async def explore_with_gemini(
    task: str,
    workspace_path: str,
    classification: dict,
    gemini_key: str,
    websocket: WebSocket,
) -> str:
    """Read specific codebase files and generate implementation plan.

    NEVER raises — always returns a string.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔍 Analyzing repository structure...",
        })
    except Exception:
        pass

    skip_dirs = {
        "node_modules", ".git", "dist", "build",
        ".next", "__pycache__", ".cache", "coverage",
    }
    skip_extensions = {
        ".png", ".jpg", ".jpeg", ".svg", ".ico", ".gif",
        ".woff", ".ttf", ".map", ".lock", ".zip",
    }
    skip_files = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    }

    # STEP 1: Read file tree only (no content)
    file_paths = []
    try:
        for root, dirs, files in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f in skip_files:
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in skip_extensions:
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, workspace_path)
                file_paths.append(rel_path)
    except Exception as e:
        logger.warning("explore_with_gemini file walk error: %s", e)

    # STEP 2: Ask Gemini which files are relevant
    relevant_files = []
    fallback_count = 10
    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(GEMINI_MODEL)

        file_tree_str = "\\n".join(file_paths)

        # Hard limit just in case repo has massive number of files
        if len(file_tree_str) > 50000:
            file_tree_str = file_tree_str[:50000] + "\\n... (truncated)"

        # Dynamic file count based on task complexity
        complexity = classification.get("complexity", "medium")
        if complexity == "simple":
            file_range = "3-5"
            fallback_count = 5
        elif complexity == "complex":
            file_range = "8-15"
            fallback_count = 15
        else:
            file_range = "5-10"
            fallback_count = 10

        filter_prompt = f"""Task type: {classification.get('task_type', 'feature')}
Task: {task}

Here are the files in the repository:
{file_tree_str}

Which {file_range} files are most relevant to completing this task?

IMPORTANT SELECTION RULES:
1. ALWAYS include entry points (index.js, page.js, layout.js, App.js, main.py, etc.)
2. ALWAYS include shared config files (tailwind.config.js, tsconfig.json, package.json, etc.) if they could be relevant
3. ALWAYS include component/module files directly referenced by the task
4. Include parent layout/wrapper files if the task involves UI changes
5. Include utility/helper files that the target files import from

Return ONLY a valid JSON list of file paths. No markdown formatting, no backticks, just the JSON array.
Example: ["src/app/page.js", "src/components/Header.js"]"""

        filter_response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, filter_prompt),
            timeout=60,
        )

        text = filter_response.text.strip()
        if "```" in text:
            # Extract JSON from markdown fencing
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
            if text.startswith("json"):
                text = text[4:]

        relevant_files = json.loads(text.strip())
        if not isinstance(relevant_files, list):
            relevant_files = file_paths[:fallback_count]
    except Exception as e:
        logger.warning("explore_with_gemini file filtering failed: %s", e)
        relevant_files = file_paths[:fallback_count]

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔍 Reading relevant files...",
        })
    except Exception:
        pass

    # STEP 3: Read ONLY those relevant files
    all_files = {}
    for rel_path in relevant_files:
        if not rel_path or not isinstance(rel_path, str):
            continue

        full_path = os.path.join(workspace_path, rel_path)
        # Prevent directory traversal
        abs_ws = os.path.abspath(workspace_path)
        abs_fp = os.path.abspath(full_path)
        if not abs_fp.startswith(abs_ws):
            continue

        try:
            with open(abs_fp, "r", encoding="utf-8", errors="replace") as fh:
                all_files[rel_path] = fh.read()
        except Exception:
            pass

    files_content = "\\n\\n".join(
        f"=== FILE: {path} ===\\n{content}"
        for path, content in all_files.items()
    )

    # Ensure we still have some fallback limit if single files are huge
    if len(files_content) > 100000:
       files_content = files_content[:100000] + "\\n... (truncated)"

    # STEP 4: Send focused context to Gemini
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                f"""You are a senior software engineer.
Task type: {classification.get('task_type', 'feature')}
Task: {task}

Focused Codebase Context:
{files_content}

Create EXACT implementation instructions.
Be specific about file paths and code.

Return in this format:

BEFORE creating implementation plan,
analyze these constraints:

1. INPUT TYPES:
   Check each form field type:
   - type="email" → must be valid email format
   - type="text" → accepts any string
   - type="number" → must be number
   If user wants to add default value,
   check if it matches the input type.

2. VALIDATION RULES:
   Look for any validation logic like:
   - email validation
   - password requirements
   - required fields
   Note these in your plan.

3. CONFLICTS:
   If user request conflicts with 
   existing constraints, flag it:
   
   Example conflict:
   User wants: default value "admin1234"
   Form field: type="email"
   Conflict: "admin1234" is not valid email
   
   Resolution options:
   Option A: Change input type to text
   Option B: Use valid email like 
             admin1234@example.com
   Option C: Add separate username field

4. INCLUDE IN PLAN:
   Always state which option resolves
   the conflict and implement that.

CONSTRAINT ANALYSIS:
(analyze existing code constraints here)

CONFLICTS FOUND:
(list any conflicts between user request
and existing code)

RESOLUTION:
(how to resolve each conflict)

FILES TO CHANGE:
(list files)

EXACT CHANGES:
(show before/after for each file)
""",
            ),
            timeout=180,
        )
        plan = response.text

    except Exception as e:
        logger.warning("explore_with_gemini Gemini plan generation failed: %s", e)
        plan = f"Task: {task}\\nImplement this directly in the most relevant file."

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📋 Plan ready!",
        })
    except Exception:
        pass

    return plan


# ═══════════════════════════════════════════════════════════════
#  STEP 4a — Gemini Research: generates .lucid/spec.md
# ═══════════════════════════════════════════════════════════════

async def gemini_research(
    task: str,
    workspace_path: str,
    validated: dict,
    gemini_key: str,
    websocket: WebSocket,
) -> str:
    """Use Gemini to research and create a detailed spec for new projects.

    Reads app_patterns.json, selects best match, generates spec.md.
    Returns the spec text. NEVER raises.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔬 Researching project requirements...",
        })
    except Exception:
        pass

    # Load app patterns
    patterns_text = "[]"
    try:
        patterns_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "app_patterns.json"
        )
        with open(patterns_path, "r") as f:
            patterns_text = f.read()
    except Exception as e:
        logger.warning("Could not load app_patterns.json: %s", e)

    # Read existing skeleton file tree AND key file contents
    file_tree = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), workspace_path)
            file_tree.append(rel)
    file_tree_str = "\n".join(file_tree)

    # Read key template files so Gemini knows what ALREADY EXISTS
    _spec_stack = (validated.get("project_stack", "") or validated.get("skeleton_stack", "") or "").lower()
    _spec_key_files_candidates = []
    if "nextjs" in _spec_stack or "next" in _spec_stack:
        _spec_key_files_candidates = [
            "src/app/globals.css", "src/app/layout.js", "src/app/layout.tsx",
            "src/app/page.js", "src/app/page.tsx",
            "src/lib/config.js", "src/lib/config.ts",
            "src/components/Navbar.jsx", "src/components/Footer.jsx",
        ]
    elif "vue" in _spec_stack:
        _spec_key_files_candidates = [
            "src/App.vue", "src/main.ts", "src/assets/main.css",
            "src/router/index.ts", "src/components/layout/AppSidebar.vue",
        ]
    else:
        _spec_key_files_candidates = [
            "src/index.css", "src/App.jsx", "src/App.tsx",
            "src/components/Layout.jsx", "src/components/Sidebar.jsx",
        ]

    _spec_inlined = {}
    for _sf in _spec_key_files_candidates:
        _sf_abs = os.path.join(workspace_path, _sf)
        if os.path.isfile(_sf_abs):
            try:
                with open(_sf_abs, "r", errors="replace") as _fp:
                    _sc = _fp.read()
                if len(_sc) < 6000:
                    _spec_inlined[_sf] = _sc
            except Exception:
                pass

    _template_content_block = ""
    if _spec_inlined:
        _tc_parts = []
        for _tp, _tc in _spec_inlined.items():
            _tc_parts.append(f"### {_tp}\n```\n{_tc}\n```")
        _template_content_block = (
            "\n\nKEY TEMPLATE FILES (already in workspace — do NOT recreate, only modify):\n"
            + "\n\n".join(_tc_parts)
        )

    skeleton_name = validated.get("skeleton_name", "unknown")
    is_admin = validated.get("is_admin", False)

    try:
        genai.configure(api_key=gemini_key)
        _research_system_instruction = (
            "You are a world-class product researcher and UX strategist. "
            "You research real products (Stripe, Linear, Notion, Vercel, Airbnb, Shopify, etc.) "
            "and distill what makes them premium. "
            "Return precise, factual, structured specifications. "
            "Prioritize specificity: name real color palettes (HSL values), actual font pairings, "
            "and proven UI patterns. Never use generic placeholders — every spec must be unique "
            "to the requested domain."
        )
        # Try to enable Google Search grounding (requires Gemini 2.5 model + SDK support)
        try:
            _search_tool = genai.protos.Tool(google_search=genai.protos.GoogleSearch())
            model = genai.GenerativeModel(
                GEMINI_RESEARCH_MODEL,
                system_instruction=_research_system_instruction,
                tools=[_search_tool],
            )
        except (AttributeError, Exception):
            # SDK version does not support google_search grounding — use model without tools
            model = genai.GenerativeModel(
                GEMINI_RESEARCH_MODEL,
                system_instruction=_research_system_instruction,
            )

        spec_prompt = f"""You are a world-class product researcher and UX designer.
A user wants to build a COMPLETE, PRODUCTION-READY project. Your job is to RESEARCH what this type of project actually needs in the real world, then write a detailed specification.

## CRITICAL: EXPAND VAGUE REQUESTS
The user may give a very short or vague request like "I need a CRM" or "movie website" or "build SaaS tool".
YOUR JOB is to turn that into a COMPREHENSIVE, DETAILED specification as if you were a senior product manager.
- "CRM system" → research what Salesforce, HubSpot, Pipedrive do → define pipelines, contacts, deals, activities, reports pages
- "movie website" → research what IMDb, Letterboxd, Netflix do → define catalog, movie details, genres, reviews, watchlist pages
- "restaurant" → research what real restaurant sites do → define menu, reservations, about, gallery, contact pages
DO NOT generate a generic template. RESEARCH this specific niche.

## HOW TO RESEARCH
1. Think about REAL examples of this type of project (e.g., if they want a "Netflix blog", think about Netflix's actual blog, Medium, Substack, Ghost)
2. What pages do REAL projects like this have? (NOT every project needs the same pages)
3. What sections does each page include?
4. What makes a project in this niche feel professional and complete?
5. What features differentiate a "$50 template" from a "$5000 custom build"?

USER REQUEST: {task}

TEMPLATE: {skeleton_name} (this is ONLY the starting codebase — all content, design, and logic must be built from scratch)
IS ADMIN PANEL: {is_admin}

EXISTING TEMPLATE FILES (starting code — you are building ON TOP of this):
{file_tree_str}
{_template_content_block}

REFERENCE PATTERNS (for inspiration only):
{patterns_text[:3000]}

Write a COMPREHENSIVE specification. The template provides code structure — YOUR spec defines what the actual PRODUCT looks like.

---

## Project Overview
- What this project does and WHO uses it
- What problem it solves
- 3-5 key differentiators that make it feel premium

## MVP Feature List
Research what THIS SPECIFIC type of project needs. Do NOT use a generic list.
Think: what would a real user of this product expect to see?
- List EVERY page and feature the project needs to feel COMPLETE and PRODUCTION-READY
- Include only pages and features that are RELEVANT to this specific project type
- Do NOT include "Pricing" unless this project actually sells tiered services
- Do NOT include "Testimonials" unless social proof is relevant to this project type
- For admin panels: make features SPECIFIC to the industry (hospital→patients, school→students, CRM→contacts/deals)
- Navigation with active states
- Mobile responsive design
- Loading states, empty states, error states
- Micro-animations and transitions

## Design System (UNIQUE to this project)
- Primary color: MUST match the industry (red for media/entertainment, green for eco/health, blue for finance/tech, purple for creative, orange for food/energy). NEVER use default #6366f1
- Color palette: primary + accent + neutral scale (use Tailwind HSL variables in :root)
- Background: light/dark mode with appropriate contrast
- Typography: heading + body font that matches the mood
- Card style: flat / raised / glass / bordered — pick ONE that fits
- Spacing: tight and professional (14px base, 13px secondary, 11px labels)
- Animations: subtle translateY, opacity, scale transitions

## Pages (COMPLETE — every page the project needs)
Determine what pages THIS SPECIFIC project needs. Examples:
- Movie site: Home (hero+trending+genres), Movies (catalog), Movie Details, About → NO pricing
- SaaS site: Home (hero+features+pricing+testimonials+FAQ), About, Contact → YES pricing
- Restaurant: Home (hero+highlights+chef), Menu, Reservations, About, Contact → NO blog
- CRM admin: Dashboard (deals+stats), Contacts, Companies, Deals (pipeline), Activities, Reports, Settings

For EACH page, describe in detail:
- Page name, route, and purpose
- EVERY section with specific content descriptions
- Layout: how sections are arranged (full-width hero vs centered content vs grid vs split)
- Interactive elements: buttons, forms, hover effects, scroll animations
- Mobile layout differences
- Content: specific headlines, descriptions, button labels — not "add a hero section" but "Hero with headline 'Discover Stories That Matter', subtitle about curated content, red CTA button 'Start Reading', background gradient from dark to primary"

## Shared Components
Components used across multiple pages:
- Name, props, exact visual description
- Hover/active/disabled states
- Responsive behavior

## Data Model
{"Supabase tables with columns, types, RLS policies, relationships, and 5+ sample rows per table" if is_admin else "Content structure: what data each section displays, mock data for development"}

## Content Strategy
- Exact placeholder headlines and descriptions (realistic, not "Lorem ipsum")
- Number of items in grids/lists (e.g., "6 feature cards in 3x2 grid")
- Image descriptions (what type of images to use)
- Icons (specific lucide-react icon names)

## Quality Standards
- $5000 custom-build quality, NOT a $50 template
- Tight spacing: 14px base, compact cards (16px padding), tight sidebar (240px)
- Skeleton loaders for loading states
- Empty states with muted icons and helpful messages
- Every page has a UNIQUE layout — never repeat the same grid
- Micro-animations: 0.2s ease-out, subtle translateY(2px), opacity transitions

REMEMBER: The template is a STARTING POINT for code. Your spec defines a COMPLETE, DYNAMIC product.
If two different users asking for "blog landing page" get the same spec, you have FAILED.
If the user's request is short/vague, you MUST still produce a COMPREHENSIVE spec by researching the niche.
Research the specific niche. Customize everything.
"""
        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                spec_prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=16384,
                ),
            ),
            timeout=180,
        )
        spec = response.text.strip()
        if not spec:
            spec = f"# Project Specification\n\nBuild: {task}"

    except asyncio.TimeoutError:
        logger.warning("gemini_research timed out after 180s — using fallback spec")
        spec = f"# Project Specification\n\nBuild: {task}"
    except Exception as e:
        logger.warning("gemini_research failed: %s", e)
        spec = f"# Project Specification\n\nBuild: {task}"

    # Save spec to workspace
    try:
        lucid_dir = os.path.join(workspace_path, ".lucid")
        os.makedirs(lucid_dir, exist_ok=True)
        with open(os.path.join(lucid_dir, "spec.md"), "w") as f:
            f.write(spec)
        logger.info("Saved spec.md (%d chars)", len(spec))
    except Exception as e:
        logger.warning("Could not save spec.md: %s", e)

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📋 Specification created",
        })
        # Send a summary of the research to the chat panel
        spec_lines = spec.split('\n')
        summary_lines = []
        for line in spec_lines[:30]:
            if line.strip() and not line.startswith('#'):
                summary_lines.append(line.strip())
            if len(summary_lines) >= 5:
                break
        if summary_lines:
            research_summary = '\n'.join(summary_lines[:5])
            await _send_chat_message(
                websocket,
                f"🔍 **Research Complete**\n\n{research_summary}\n\n_Full spec saved to `.lucid/spec.md`_"
            )
    except Exception:
        pass

    return spec


# ═══════════════════════════════════════════════════════════════
#  Fallback helpers
# ═══════════════════════════════════════════════════════════════

def _get_fallback_sections(task: str, project_name: str) -> list:
    """Generate project-relevant fallback sections based on task keywords."""
    _task_lower = (task or "").lower()

    if any(kw in _task_lower for kw in ["restaurant", "food", "cafe", "bakery", "pizza", "dining"]):
        return [
            {"name": "Hero", "type": "hero", "description": f"Hero section for {project_name} with appetizing food imagery, restaurant tagline, and reservation CTA button."},
            {"name": "MenuHighlights", "type": "catalog", "description": f"Featured dishes for {project_name} with tabbed categories (Appetizers, Mains, Desserts), prices, and descriptions. Interactive category filter."},
            {"name": "ChefStory", "type": "custom", "description": f"Chef spotlight and restaurant story for {project_name}. Split layout with chef photo placeholder and compelling backstory. Warm, inviting tone."},
            {"name": "ReservationCTA", "type": "cta", "description": f"Reservation call-to-action for {project_name}. Includes operating hours, phone number, and a prominent 'Book a Table' button."},
            {"name": "PhotoGallery", "type": "gallery", "description": f"Photo gallery for {project_name} showing restaurant ambiance and dishes. Masonry grid with hover overlay effects."},
        ]
    elif any(kw in _task_lower for kw in ["movie", "film", "cinema", "streaming", "netflix"]):
        return [
            {"name": "Hero", "type": "hero", "description": f"Cinematic hero for {project_name} with featured movie spotlight, dark overlay, play button, and genre tags."},
            {"name": "TrendingNow", "type": "catalog", "description": f"Trending movies carousel for {project_name}. Horizontal scroll with movie poster cards, ratings, and hover details overlay."},
            {"name": "GenreShowcase", "type": "custom", "description": f"Genre categories grid for {project_name}. Visual cards for Action, Comedy, Drama, Horror, SciFi with background imagery."},
            {"name": "TopRated", "type": "catalog", "description": f"Top rated movies for {project_name}. Cards with star ratings, year, duration, and brief synopsis."},
            {"name": "Newsletter", "type": "cta", "description": f"Newsletter signup for {project_name}. Dark background with email input and 'Get Movie Updates' button."},
        ]
    elif any(kw in _task_lower for kw in ["saas", "software", "app", "platform", "tool", "startup"]):
        return [
            {"name": "Hero", "type": "hero", "description": f"Hero section for {project_name} with headline, subtext, CTA button, and product screenshot/mockup area."},
            {"name": "FeatureShowcase", "type": "features", "description": f"Feature showcase for {project_name}. 6 cards with icons, alternating layout. Interactive hover effects."},
            {"name": "HowItWorks", "type": "custom", "description": f"How it works section for {project_name}. 3-step numbered process with icons and descriptions. Visual flow arrows."},
            {"name": "PricingPlans", "type": "pricing", "description": f"Pricing section for {project_name}. Monthly/yearly toggle. 3 tier cards. Highlight popular plan."},
            {"name": "CallToAction", "type": "cta", "description": f"Final CTA for {project_name}. Gradient background, bold headline, email signup or demo button."},
        ]
    elif any(kw in _task_lower for kw in ["portfolio", "agency", "freelance", "designer", "developer"]):
        return [
            {"name": "Hero", "type": "hero", "description": f"Portfolio hero for {project_name} with name/title, brief intro, and scroll-down indicator."},
            {"name": "SelectedWorks", "type": "gallery", "description": f"Project showcase for {project_name}. Masonry grid of project cards with category filter tabs. Hover shows project title and tech stack."},
            {"name": "SkillsExpertise", "type": "custom", "description": f"Skills and expertise section for {project_name}. Visual skill bars or progress indicators grouped by category."},
            {"name": "WorkProcess", "type": "custom", "description": f"Work process timeline for {project_name}. 4-step horizontal process with icons: Discovery, Design, Develop, Deploy."},
            {"name": "ContactCTA", "type": "cta", "description": f"Contact CTA for {project_name}. 'Let's work together' headline with email and social links."},
        ]
    elif any(kw in _task_lower for kw in ["shop", "store", "ecommerce", "e-commerce", "product"]):
        return [
            {"name": "Hero", "type": "hero", "description": f"Hero banner for {project_name} with featured product, sale announcement, and shop-now button."},
            {"name": "ProductShowcase", "type": "catalog", "description": f"Featured products grid for {project_name}. Product cards with image, name, price, rating, and add-to-cart button."},
            {"name": "CategoryGrid", "type": "custom", "description": f"Product categories for {project_name}. Visual cards with category images and names. Hover zoom effect."},
            {"name": "FlashDeals", "type": "custom", "description": f"Flash deals section for {project_name} with countdown timer, discounted prices, and urgency badges."},
            {"name": "TrustBadges", "type": "cta", "description": f"Trust section for {project_name}. Free shipping, secure payment, money-back guarantee badges."},
        ]
    else:
        return [
            {"name": "Hero", "type": "hero", "description": f"Hero section for {project_name}. Full-width with compelling headline, descriptive subtext, and prominent CTA button."},
            {"name": "Showcase", "type": "custom", "description": f"Main showcase section for {project_name}. Present the core offering with visual cards, icons, and descriptions."},
            {"name": "Story", "type": "custom", "description": f"Background story section for {project_name}. Split layout with text and visual placeholder. Warm, engaging tone."},
            {"name": "SocialProof", "type": "custom", "description": f"Social proof section for {project_name}. Reviews, testimonials, or client logos with real-looking content."},
            {"name": "GetStarted", "type": "cta", "description": f"Get started section for {project_name}. Gradient background, action-oriented headline, and button."},
        ]


def _build_dynamic_fallback(task: str) -> dict:
    """Build a complete fallback blueprint with project-relevant sections."""
    _task_clean = (task or "project").strip()
    _name = _task_clean[:50].split(".")[0].split(",")[0].strip()
    if len(_name) < 3:
        _name = "Project"

    return {
        "projectName": _name,
        "projectType": "other",
        "description": _task_clean,
        "theme": {},
        "navigation": {"items": [{"label": "Home", "route": "/"}]},
        "pages": [{"name": "Home", "route": "/", "sections": _get_fallback_sections(_task_clean, _name)}],
        "sharedComponents": [],
    }


# ═══════════════════════════════════════════════════════════════
#  STEP 4b — Gemini Plan: generates .lucid/plan.json
# ═══════════════════════════════════════════════════════════════

async def gemini_create_plan(
    task: str,
    workspace_path: str,
    spec: str,
    validated: dict,
    gemini_key: str,
    websocket: WebSocket,
) -> str:
    """Generate a framework-agnostic project blueprint, then convert to plan.json.

    Gemini produces a blueprint describing WHAT to build (pages, sections, nav, theme).
    blueprint_to_file_plan() converts it to exact file paths based on the detected stack.
    This separation means Gemini never needs to know the file system conventions.

    Returns the plan as a JSON string. NEVER raises.
    """
    try:
        await websocket.send_json({
            "type": "progress",
            "message": "📐 Creating project blueprint...",
        })
    except Exception:
        pass

    # Read current file tree
    file_tree = []
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".lucid"}]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), workspace_path)
            file_tree.append(rel)
    file_tree_str = "\n".join(file_tree)

    skeleton_name = validated.get("skeleton_name", "")
    detected_stack = (
        validated.get("project_stack", "")
        or validated.get("skeleton_stack", "")
        or validated.get("stack", "")
    )
    _stk = detected_stack.lower()

    if "nextjs" in _stk or "next" in _stk:
        stack_description = "Next.js 14 website (App Router, file-based routing)"
    elif "vue" in _stk:
        stack_description = "Vue 3 + Vite single-page application"
    else:
        stack_description = "React + Vite single-page application"

    _spec_key_files_candidates = []
    if "nextjs" in _stk or "next" in _stk:
        _spec_key_files_candidates = [
            "src/app/globals.css", "src/app/layout.js", "src/app/page.js",
            "src/lib/config.js", "src/components/Navbar.jsx", "src/components/Footer.jsx",
        ]
    elif "vue" in _stk:
        _spec_key_files_candidates = [
            "src/App.vue", "src/main.ts", "src/assets/main.css",
            "src/router/index.ts", "src/components/layout/AppSidebar.vue",
        ]
    else:
        _spec_key_files_candidates = [
            "src/index.css", "src/App.jsx", "src/main.jsx",
            "src/components/Layout.jsx", "src/components/Sidebar.jsx",
        ]

    _template_ctx_parts = []
    for _sf in _spec_key_files_candidates:
        _sf_abs = os.path.join(workspace_path, _sf)
        if os.path.isfile(_sf_abs):
            try:
                with open(_sf_abs, "r", errors="replace") as _fp:
                    _sc = _fp.read()
                if len(_sc) < 5000:
                    _template_ctx_parts.append(f"### {_sf}\n```\n{_sc}\n```")
            except Exception:
                pass
    template_content = "\n\n".join(_template_ctx_parts) if _template_ctx_parts else ""

    blueprint = {}
    blueprint_text = ""
    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel(GEMINI_BLUEPRINT_MODEL)

        blueprint_prompt = f"""You are a senior product architect converting a product specification into a complete implementation blueprint.

The project MUST be a UNIQUE, DYNAMIC, production-ready application.
DO NOT just return the existing template with minor text or color changes.
You MUST instruct the AI to COMPLETELY TRANSFORM the existing components (layout, styling, logic) to fit the specific needs of this project's industry.

USER REQUEST: {task}

FRAMEWORK: {stack_description}
TEMPLATE: {skeleton_name or detected_stack or 'standard template'}

PROJECT SPECIFICATION (this is your primary source — implement EVERYTHING described here):
{spec[:6000]}

EXISTING TEMPLATE FILES (starting code to build on top of):
{template_content or file_tree_str}

## YOUR JOB
Convert the spec into a concrete blueprint. Define:
1. EVERY page the project needs (with ALL sections per page)
2. EVERY shared component
3. The complete design theme
4. Navigation structure
5. Required npm packages

You do NOT specify file paths — that is handled automatically based on the framework.

## CONTEXT-AWARE ARCHITECTURE
Think about what THIS SPECIFIC PROJECT actually needs. NOT every project is the same.

Return ONLY valid JSON (no markdown, no backticks):
{{
  "projectName": "BriefName",
  "projectType": "blog|ecommerce|portfolio|saas-landing|admin-dashboard|docs|marketing|movie|restaurant|booking|other",
  "description": "One line description of what this project does",

  "theme": {{
    "--color-primary": "#hex",
    "--color-primary-light": "#hex",
    "--color-primary-dark": "#hex",
    "--color-primary-50": "#hex",
    "--color-accent": "#hex",
    "--color-bg": "#hex",
    "--color-bg-secondary": "#hex",
    "--color-bg-tertiary": "#hex",
    "--color-surface": "#hex",
    "--color-border": "#hex",
    "--color-text": "#hex",
    "--color-text-secondary": "#hex",
    "--color-text-muted": "#hex",
    "--font-family": "'FontName', sans-serif",
    "--font-heading": "'HeadingFont', sans-serif",
    "--radius-sm": "0.25rem",
    "--radius-md": "0.375rem",
    "--radius-lg": "0.5rem",
    "--radius-xl": "0.75rem",
    "darkMode": true
  }},
  "googleFonts": ["FontName"],

  "navigation": {{
    "style": "top-navbar|sidebar|both",
    "brand": "Project name or brand",
    "items": [
      {{"label": "Page Name", "route": "/route", "icon": "LucideIconName"}}
    ],
    "ctaButton": {{"label": "Primary Action", "route": "/action"}}
  }},

  "pages": [
    {{
      "name": "PageName",
      "route": "/route",
      "title": "SEO page title",
      "description": "What this page is about",
      "sections": [
        {{
          "name": "SectionName",
          "type": "hero|features|catalog|details|gallery|stats|testimonials|pricing|faq|cta|form|table|chart|calendar|timeline|team|menu|map|newsletter|custom",
          "description": "DETAILED (80+ words): exact visual implementation, layout, content, interactions, animations.",
          "components": ["SharedComponentName"]
        }}
      ]
    }}
  ],

  "sharedComponents": [
    {{
      "name": "ComponentName",
      "description": "Purpose, props, visual design, where used"
    }}
  ],

  "packages": {{
    "dependencies": {{}}
  }}
}}

## CRITICAL RULES
1. Theme MUST be unique — match the project's INDUSTRY and MOOD
2. EVERY section description MUST be 80+ words with specific content
3. Pages and sections must be UNIQUE to this project
4. Only include Pricing if the project actually sells tiered plans
5. Only include Testimonials if social proof is relevant
6. Routes MUST be simple flat paths: "/", "/about". NEVER route groups
7. Section names: simple PascalCase only. Page names: simple words only
8. MAX TOTAL: No more than 25 sections across ALL pages combined
9. sharedComponents: maximum 2-3 reusable components

Return ONLY the raw JSON object. No markdown. No backticks. No explanation.
"""
        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                blueprint_prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                ),
            ),
            timeout=240,
        )
        blueprint_text = response.text.strip()

        # Clean up markdown fences if present
        if "```" in blueprint_text:
            parts = blueprint_text.split("```")
            for part in parts:
                candidate = part.lstrip("json").strip()
                try:
                    json.loads(candidate)
                    blueprint_text = candidate
                    break
                except Exception:
                    pass

        blueprint = json.loads(blueprint_text)

    except json.JSONDecodeError:
        logger.warning("gemini_create_plan: invalid JSON — attempting repair")
        _repaired = False
        try:
            _raw = blueprint_text
            _open_braces = _raw.count("{") - _raw.count("}")
            _open_brackets = _raw.count("[") - _raw.count("]")
            _raw = _raw.rstrip().rstrip(",").rstrip(":")
            if _raw.count('"') % 2 != 0:
                _last_quote = _raw.rfind('"')
                if _last_quote > 0:
                    _raw = _raw[:_last_quote + 1]
            _raw += "]" * max(0, _open_brackets) + "}" * max(0, _open_braces)
            blueprint = json.loads(_raw)
            _repaired = True
            logger.info("gemini_create_plan: repaired truncated JSON — %d pages recovered",
                        len(blueprint.get("pages", [])))
        except Exception as _repair_err:
            logger.warning("gemini_create_plan: repair failed: %s", _repair_err)

        if not _repaired:
            logger.warning("gemini_create_plan: using minimal blueprint fallback")
            blueprint = _build_dynamic_fallback(task)
    except Exception as e:
        logger.warning("gemini_create_plan failed: %s", e)
        blueprint = _build_dynamic_fallback(task)

    # ── POST-BLUEPRINT VALIDATION ─────────────────────────────
    _project_type = blueprint.get("projectType", "other")
    _is_admin = "admin" in _project_type or "dashboard" in _project_type

    for page in blueprint.get("pages", []):
        sections = page.get("sections", [])
        section_count = len(sections)
        is_home = page.get("route") == "/" or page.get("name", "").lower() in ("home", "landing", "main")

        if is_home and not _is_admin and section_count < 4:
            _pname = page.get("name", "Home")
            _fallback_secs = _get_fallback_sections(task, blueprint.get("projectName", _pname))
            needed = 4 - section_count
            page["sections"] = sections + _fallback_secs[:needed]
            logger.info(
                "gemini_create_plan: padded '%s' page from %d to %d sections",
                _pname, section_count, len(page["sections"]),
            )
        elif not is_home and section_count < 2:
            _pname = page.get("name", "Page")
            _fallback_secs = _get_fallback_sections(task, blueprint.get("projectName", _pname))
            page["sections"] = sections + _fallback_secs[:2 - section_count]
            logger.info("gemini_create_plan: padded non-home page '%s' to 2 sections", _pname)

    # ── Convert blueprint → file plan ─────────────────────────
    plan_data = blueprint_to_file_plan(blueprint, workspace_path)

    # ── Save plan.json ────────────────────────────────────────
    plan_json = json.dumps(plan_data, indent=2, ensure_ascii=False)
    try:
        lucid_dir = os.path.join(workspace_path, ".lucid")
        os.makedirs(lucid_dir, exist_ok=True)
        with open(os.path.join(lucid_dir, "plan.json"), "w") as f:
            f.write(plan_json)
        logger.info(
            "Saved plan.json: %d files, stack=%s",
            len(plan_data.get("files", [])),
            plan_data.get("stack", ""),
        )
    except Exception as e:
        logger.warning("Could not save plan.json: %s", e)

    try:
        await websocket.send_json({
            "type": "progress",
            "message": f"📋 Blueprint ready ({len(plan_data.get('files', []))} files planned)",
        })
    except Exception:
        pass

    return plan_json


# ═══════════════════════════════════════════════════════════════
#  STEP 4c — Blueprint → File Plan converter
# ═══════════════════════════════════════════════════════════════

def blueprint_to_file_plan(blueprint: dict, workspace_path: str) -> dict:
    """Convert a Gemini blueprint into an exact list of files to create/modify.

    This is the bridge between "WHAT to build" (Gemini's domain) and
    "WHERE to write it" (filesystem conventions per framework).

    Returns a plan dict with 'files' list, ready for execute_project_in_batches().
    """
    stack = (
        blueprint.get("stack", "")
        or blueprint.get("projectType", "")
        or ""
    )

    # Detect stack from workspace if not in blueprint
    if not stack or stack in ("other", "marketing", "blog", "ecommerce", "portfolio",
                              "saas-landing", "admin-dashboard", "docs", "movie",
                              "restaurant", "booking"):
        # Detect from actual files in workspace
        if os.path.isfile(os.path.join(workspace_path, "next.config.js")) or \
           os.path.isfile(os.path.join(workspace_path, "next.config.mjs")) or \
           os.path.isdir(os.path.join(workspace_path, "src", "app")):
            stack = "nextjs"
        elif os.path.isfile(os.path.join(workspace_path, "vite.config.ts")) or \
             os.path.isfile(os.path.join(workspace_path, "vite.config.js")):
            if os.path.isfile(os.path.join(workspace_path, "src", "App.vue")):
                stack = "vue"
            else:
                stack = "react"
        else:
            stack = "react"

    is_nextjs = "nextjs" in stack.lower() or "next" in stack.lower()
    is_vue = "vue" in stack.lower()

    pages = blueprint.get("pages", [])
    shared_components = blueprint.get("sharedComponents", [])
    theme = blueprint.get("theme", {})
    navigation = blueprint.get("navigation", {})
    packages = blueprint.get("packages", {})
    project_name = blueprint.get("projectName", "project")
    project_desc = blueprint.get("description", "")

    files = []

    # ── Detect component base path from the actual template structure ──
    if is_nextjs:
        if os.path.isdir(os.path.join(workspace_path, "src", "app", "components")):
            comp_base = "src/app/components"
        else:
            comp_base = "src/components"
    elif is_vue:
        comp_base = "src/components"
    else:
        comp_base = "src/components"

    # ── Detect route group for Next.js (e.g. (marketing), (dashboard)) ──
    nextjs_route_group = ""
    if is_nextjs:
        app_dir = os.path.join(workspace_path, "src", "app")
        if os.path.isdir(app_dir):
            for entry in os.listdir(app_dir):
                if entry.startswith("(") and entry.endswith(")") and entry != "(auth)":
                    group_path = os.path.join(app_dir, entry)
                    if os.path.isdir(group_path):
                        nextjs_route_group = entry
                        logger.info("Detected Next.js route group: %s", nextjs_route_group)
                        break

    # ── 1. CSS/Globals ──────────────────────────────────────────
    if is_nextjs:
        css_path = "src/app/globals.css"
    elif is_vue:
        css_path = "src/assets/main.css"
    else:
        css_path = "src/index.css"

    css_exists = os.path.isfile(os.path.join(workspace_path, css_path))
    files.append({
        "path": css_path,
        "action": "modify" if css_exists else "create",
        "priority": 1,
        "description": (
            f"Apply the project theme: update :root HSL values with the new color palette. "
            f"Project: {project_name}. Theme values: {json.dumps(theme)}. "
            f"Add Google Font import for {blueprint.get('googleFonts', ['Inter'])}. "
            f"Set the overall feel: {'dark mode' if theme.get('darkMode') else 'light mode'}."
        ),
        "sections": ["HSL color overrides", "Google Font import", "Base body/html styles"],
    })

    # ── 1b. Site config (direct-write) ──────────────────────────
    if is_nextjs:
        _site_config_path = "src/config/site.js"
        _site_config_content = (
            f'export const siteConfig = {{\n'
            f'  name: "{project_name}",\n'
            f'  description: "{project_desc[:150]}",\n'
            f'  url: "https://example.com",\n'
            f'  logoText: "{project_name[:2].upper()}",\n'
            f'  ogImage: "/og-image.png",\n'
            f'}};\n'
        )
        files.append({
            "path": _site_config_path,
            "action": "create",
            "priority": 1,
            "_direct_content": _site_config_content,
            "description": f"Site config with brand: {project_name}",
        })

    # ── 1c. Navigation header ────────────────────────────────────
    if is_nextjs:
        _header_path = "src/components/layout/MarketingHeader.jsx"
        if os.path.isfile(os.path.join(workspace_path, _header_path)):
            _nav_items = navigation.get("items", [])
            _nav_links_str = ", ".join(
                f'{{"href": "{item.get("route", "/")}", "label": "{item.get("label", "")}"}}' 
                for item in _nav_items if item.get("route") != "/"
            )
            files.append({
                "path": _header_path,
                "action": "modify",
                "priority": 2,
                "description": (
                    f"Update the navigation header for '{project_name}'. "
                    f"Change the navLinks array to: [{_nav_links_str}]. "
                    f"The CTA button should say '{navigation.get('cta', 'Get Started')}' and link to the most relevant page. "
                    f"Keep the ENTIRE component structure, scroll behavior, mobile menu, and Tailwind classes intact. "
                    f"ONLY change: (1) navLinks array values, (2) CTA button text/link. "
                    f"DO NOT change imports, component name, or layout structure."
                ),
            })

    # ── 1d. Footer ───────────────────────────────────────────────
    if is_nextjs:
        _footer_path = "src/components/layout/MarketingFooter.jsx"
        if os.path.isfile(os.path.join(workspace_path, _footer_path)):
            files.append({
                "path": _footer_path,
                "action": "modify",
                "priority": 2,
                "description": (
                    f"Update the footer for '{project_name}'. "
                    f"Change the brand name, tagline, and footer link columns to match this project. "
                    f"Footer links should include: {json.dumps([item.get('label') for item in navigation.get('items', [])])}. "
                    f"Keep the ENTIRE component structure and Tailwind classes intact. "
                    f"ONLY change: text content, link labels/hrefs, brand name, tagline."
                ),
            })

    # ── 2. Root layout ────────────────────────────────────────────
    if is_nextjs:
        layout_path = "src/app/layout.js"
        layout_exists = os.path.isfile(os.path.join(workspace_path, layout_path))
        files.append({
            "path": layout_path,
            "action": "modify" if layout_exists else "create",
            "priority": 2,
            "description": (
                f"Update ONLY the root layout metadata for {project_name}. "
                f"DO NOT add Navbar, Header, Footer, or any navigation components here. "
                f"ONLY update: page metadata (title='{project_name}', description='{project_desc}'), "
                f"Google Fonts import for {blueprint.get('googleFonts', ['Inter'])}, "
                f"and ensure Providers wrapper is preserved. "
                f"Keep the existing structure — this is a MINIMAL modification."
            ),
            "sections": ["Page metadata update", "Google Font import"],
        })

        if nextjs_route_group:
            group_layout_path = f"src/app/{nextjs_route_group}/layout.js"
            group_layout_exists = os.path.isfile(os.path.join(workspace_path, group_layout_path))
            if group_layout_exists:
                nav_items_str = json.dumps(navigation.get("items", []))
                files.append({
                    "path": group_layout_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update the marketing layout for {project_name}. "
                        f"This layout ALREADY has MarketingHeader and MarketingFooter — keep them. "
                        f"Customize the navigation items: {nav_items_str}. "
                        f"Brand/logo: {navigation.get('brand', project_name)}. "
                        f"DO NOT rename or remove the existing header/footer components."
                    ),
                    "sections": ["Navigation items update", "Brand customization"],
                })
    elif is_vue:
        _vue_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.vue"))
        if _vue_admin:
            routes_path = "src/router/routes.js"
            if os.path.isfile(os.path.join(workspace_path, routes_path)):
                files.append({
                    "path": routes_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update Vue router routes for {project_name}. "
                        f"ADD route entries as children of MainLayout for all pages: {[p['name'] for p in pages]}. "
                        f"Use lazy imports: () => import('@/pages/<name>/<Name>Page.vue'). "
                        f"Keep existing Dashboard, Login, and NotFound routes."
                    ),
                    "sections": ["Route entries for new pages"],
                })
            nav_config_path = "src/config/navigation.js"
            if os.path.isfile(os.path.join(workspace_path, nav_config_path)):
                files.append({
                    "path": nav_config_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update sidebar navigation items for {project_name}. "
                        f"Items: {json.dumps(navigation.get('items', []))}. "
                        f"Use lucide icons. Keep the existing structure."
                    ),
                    "sections": ["Navigation items update"],
                })
        else:
            app_path = "src/App.vue"
            app_exists = os.path.isfile(os.path.join(workspace_path, app_path))
            files.append({
                "path": app_path,
                "action": "modify" if app_exists else "create",
                "priority": 2,
                "description": (
                    f"Update root App.vue for {project_name}. "
                    f"Navigation: {json.dumps(navigation)}. "
                    f"RouterView for page content."
                ),
                "sections": ["Navigation bar", "RouterView", "Footer"],
            })
    else:
        _react_admin = os.path.isfile(os.path.join(workspace_path, "src", "components", "layout", "MainLayout.jsx"))
        if _react_admin:
            routes_path = "src/router/routes.jsx"
            if os.path.isfile(os.path.join(workspace_path, routes_path)):
                files.append({
                    "path": routes_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update React router routes for {project_name}. "
                        f"ADD entries to privateRoutes for all pages: {[p['name'] for p in pages]}. "
                        f"Import each page component from src/pages/ or src/features/. "
                        f"Keep existing Dashboard and Login routes."
                    ),
                    "sections": ["Route entries for new pages"],
                })
            nav_config_path = "src/config/navigation.js"
            if os.path.isfile(os.path.join(workspace_path, nav_config_path)):
                files.append({
                    "path": nav_config_path,
                    "action": "modify",
                    "priority": 2,
                    "description": (
                        f"Update sidebar navigation items for {project_name}. "
                        f"Items: {json.dumps(navigation.get('items', []))}. "
                        f"Use lucide-react icons. Keep the existing structure."
                    ),
                    "sections": ["Navigation items update"],
                })
        else:
            app_path = "src/App.jsx"
            app_exists = os.path.isfile(os.path.join(workspace_path, app_path))
            nav_items = navigation.get("items", [])

            files.append({
                "path": app_path,
                "action": "modify" if app_exists else "create",
                "priority": 2,
                "description": (
                    f"Update App.jsx for {project_name}. "
                    f"Add BrowserRouter with Routes for all pages: {[p['name'] for p in pages]}. "
                    f"Include Navbar with items: {json.dumps(nav_items)}. "
                    f"Include Footer."
                ),
                "sections": ["Navbar", "React Router Routes", "Footer"],
            })

    # ── 3. Shared components ──────────────────────────────────────
    for comp in shared_components:
        comp_name = comp.get("name", "Component")
        comp_desc = comp.get("description", "Shared component")
        if is_vue:
            comp_path = f"{comp_base}/{comp_name}.vue"
        else:
            comp_path = f"{comp_base}/{comp_name}.jsx"
        comp_exists = os.path.isfile(os.path.join(workspace_path, comp_path))
        files.append({
            "path": comp_path,
            "action": "modify" if comp_exists else "create",
            "priority": 3,
            "description": comp_desc,
            "sections": [comp_desc],
        })

    # ── 4. Pages — each section becomes its own component file ────
    import re as _re_sanitize
    for page in pages:
        page_name = page.get("name", "Page")
        page_route = page.get("route", "/")
        page_sections = page.get("sections", [])
        page_title = page.get("title", page_name)
        page_desc_text = page.get("description", page_name)
        safe_page = _re_sanitize.sub(r'[^a-zA-Z0-9]', '', page_name)
        if not safe_page:
            safe_page = "Page"
        page_route = _re_sanitize.sub(r'\([^)]*\)/?', '', page_route).strip('/')
        if page_route:
            page_route = f"/{page_route}"
        else:
            page_route = "/"

        section_component_names = []
        for sec in page_sections:
            if isinstance(sec, dict):
                sec_name = _re_sanitize.sub(r'[^a-zA-Z0-9]', '', sec.get("name", "Section"))
                sec_desc = sec.get("description", "")
                sec_type = sec.get("type", "")
                sec_components = sec.get("components", [])
            else:
                sec_name = _re_sanitize.sub(r'[^a-zA-Z0-9]', '', str(sec))
                sec_desc = str(sec)
                sec_type = ""
                sec_components = []
            if not sec_name:
                sec_name = "Section"

            comp_name = f"{safe_page}{sec_name}Section"
            section_component_names.append(comp_name)

            if is_vue:
                sec_file = f"{comp_base}/sections/{comp_name}.vue"
            else:
                sec_file = f"{comp_base}/sections/{comp_name}.jsx"

            files.append({
                "path": sec_file,
                "action": "create",
                "priority": 4,
                "description": (
                    f"Section component for the '{page_name}' page. "
                    f"Section: '{sec_name}' (type: {sec_type}). "
                    f"{sec_desc}. "
                    f"{'Uses components: ' + ', '.join(sec_components) + '. ' if sec_components else ''}"
                    f"This is a SELF-CONTAINED section component — it should render "
                    f"a complete section of the page with proper padding, responsive layout, "
                    f"and beautiful design using Tailwind utility classes. "
                    f"A stub file exists — OVERWRITE it completely with the real implementation. "
                    f"Export as: export function {comp_name}() {{ ... }} and then export default {comp_name}. "
                    f"Use lucide-react for icons. Make it fully responsive. "
                    f"Write SUBSTANTIAL, production-quality content with real-looking placeholder text."
                ),
                "sections": [sec_desc or sec_name],
            })

        # ── 4b. Page file (thin — just imports sections) ──
        if is_nextjs:
            route_prefix = f"src/app/{nextjs_route_group}" if nextjs_route_group else "src/app"
            if page_route == "/":
                page_file = f"{route_prefix}/page.js"
            else:
                clean_route = page_route.strip("/").replace(" ", "-").lower()
                if not clean_route:
                    clean_route = safe_page.lower()
                page_file = f"{route_prefix}/{clean_route}/page.js"
        elif is_vue:
            page_file = f"src/views/{safe_page}View.vue"
        else:
            if page_route == "/":
                page_file = "src/pages/Home.jsx"
            else:
                page_file = f"src/pages/{safe_page}.jsx"

        page_dir = os.path.dirname(page_file)
        sections_dir = f"{comp_base}/sections"
        rel_path = os.path.relpath(sections_dir, page_dir)
        if not rel_path.startswith('.'):
            rel_prefix = f"./{rel_path}"
        else:
            rel_prefix = rel_path

        import_paths = [
            f"import {{ {name} }} from '{rel_prefix}/{name}'"
            for name in section_component_names
        ]
        import_block = "\n".join(import_paths)
        jsx_elements = "\n      ".join(
            f"<{name} />" for name in section_component_names
        )

        if is_nextjs:
            page_content = (
                f"{import_block}\n\n"
                f"export default function {safe_page}Page() {{\n"
                f"  return (\n"
                f"    <main>\n"
                f"      {jsx_elements}\n"
                f"    </main>\n"
                f"  )\n"
                f"}}\n"
            )
        elif is_vue:
            vue_imports = "\n".join(
                f"import {name} from '{rel_prefix}/{name}.vue'"
                for name in section_component_names
            )
            vue_components = "\n    ".join(
                f"<{name} />" for name in section_component_names
            )
            page_content = (
                f"<template>\n"
                f"  <main>\n"
                f"    {vue_components}\n"
                f"  </main>\n"
                f"</template>\n\n"
                f"<script setup>\n"
                f"{vue_imports}\n"
                f"</script>\n"
            )
        else:
            page_content = (
                f"{import_block}\n\n"
                f"export default function {safe_page}Page() {{\n"
                f"  return (\n"
                f"    <main>\n"
                f"      {jsx_elements}\n"
                f"    </main>\n"
                f"  )\n"
                f"}}\n"
            )

        files.append({
            "path": page_file,
            "action": "create",
            "priority": 5,
            "_direct_content": page_content,
            "description": f"Thin composition page for '{page_name}'",
            "sections": [f"Import and render: {', '.join(section_component_names)}"],
        })

    # ── 5. Assemble final plan.json ────────────────────────────
    return {
        "projectName": project_name,
        "stack": stack,
        "theme": theme,
        "googleFonts": blueprint.get("googleFonts", ["Inter"]),
        "navigation": navigation,
        "packageUpdates": packages,
        "_comp_base": comp_base,
        "files": files,
    }
