"""Knowledge Loader — Layer 2 of the 3-Layer AI Architecture.

Reads pattern files, quality standards, and framework rules from the
local `knowledge/` directory and builds context blocks that are injected
into Claude's prompts.

Architecture:
  Layer 1 (MCP):   TEMPLATE_MANIFEST.md — what exists in the workspace
  Layer 2 (Skills): Knowledge files — HOW to build things correctly
  Layer 3 (Plugin): Layer 1 + Layer 2 bundled per-template

This module implements Layer 2.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger("lucid.knowledge")

# Path to the knowledge directory (relative to this file)
_KNOWLEDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "knowledge"
)
_KNOWLEDGE_DIR = os.path.normpath(_KNOWLEDGE_DIR)


def _read_file(filepath: str, max_chars: int = 8000) -> str:
    """Read a file, returning empty string on failure."""
    try:
        if os.path.isfile(filepath):
            with open(filepath, "r", errors="replace") as f:
                content = f.read()
            return content[:max_chars]
    except Exception as e:
        logger.warning("Failed to read knowledge file %s: %s", filepath, e)
    return ""


def classify_project_type(task: str) -> str:
    """Classify the project type from the user's task description.

    Returns one of:
      'admin_panel', 'ecommerce', 'blog', 'saas_app', 'social',
      'booking', 'analytics', 'documentation', 'portfolio',
      'entertainment', 'food_restaurant', 'medical', 'education',
      'fitness', 'travel', 'real_estate', 'landing_page'
    """
    task_lower = (task or "").lower()

    # Admin/Dashboard keywords
    admin_kw = ["admin", "dashboard", "panel", "management system", "cms", "backoffice",
                "crm", "erp", "inventory", "manage users", "manage products",
                "data table", "crud", "admin panel"]
    if any(kw in task_lower for kw in admin_kw):
        return "admin_panel"

    # Entertainment / Movies / Streaming
    entertainment_kw = ["movie", "film", "cinema", "streaming", "tv show", "series",
                        "netflix", "video", "entertainment", "theater", "anime",
                        "music", "podcast", "radio"]
    if any(kw in task_lower for kw in entertainment_kw):
        return "entertainment"

    # Food / Restaurant
    food_kw = ["restaurant", "food", "recipe", "menu", "cooking", "chef",
               "cafe", "bakery", "catering", "dinner", "kitchen", "meal"]
    if any(kw in task_lower for kw in food_kw):
        return "food_restaurant"

    # Medical / Health
    medical_kw = ["medical", "health", "hospital", "clinic", "doctor", "patient",
                  "pharmacy", "dental", "wellness", "therapy",
                  "healthcare", "telemedicine"]
    if any(kw in task_lower for kw in medical_kw):
        return "medical"

    # Education
    education_kw = ["school", "education", "university", "course", "learning",
                    "student", "teacher", "academy", "training", "lms",
                    "tutorial", "e-learning", "classroom"]
    if any(kw in task_lower for kw in education_kw):
        return "education"

    # E-commerce keywords
    ecom_kw = ["ecommerce", "e-commerce", "shop", "store", "product", "cart",
               "marketplace", "catalog", "checkout", "shopping"]
    if any(kw in task_lower for kw in ecom_kw):
        return "ecommerce"

    # Blog/Content keywords
    blog_kw = ["blog", "articles", "posts", "news", "magazine", "content",
               "editorial", "publication"]
    if any(kw in task_lower for kw in blog_kw):
        return "blog"

    # SaaS application
    saas_kw = ["saas", "application", "tool", "platform", "software", "service",
               "app builder", "automation", "workflow"]
    if any(kw in task_lower for kw in saas_kw):
        return "saas_app"

    # Travel / Tourism
    travel_kw = ["travel", "tourism", "hotel", "flight", "trip", "vacation",
                 "destination", "tour", "airbnb", "hostel"]
    if any(kw in task_lower for kw in travel_kw):
        return "travel"

    # Real Estate
    real_estate_kw = ["real estate", "property", "apartment", "house", "rental",
                      "listing", "agent", "broker", "mortgage"]
    if any(kw in task_lower for kw in real_estate_kw):
        return "real_estate"

    # Social/Community
    social_kw = ["social", "community", "forum", "feed", "chat", "messaging",
                 "network", "social media"]
    if any(kw in task_lower for kw in social_kw):
        return "social"

    # Booking/Reservation
    booking_kw = ["booking", "reservation", "appointment", "scheduling",
                  "calendar", "event"]
    if any(kw in task_lower for kw in booking_kw):
        return "booking"

    # Fitness / Gym
    fitness_kw = ["fitness", "gym", "workout", "exercise", "yoga", "crossfit",
                  "personal trainer", "body building"]
    if any(kw in task_lower for kw in fitness_kw):
        return "fitness"

    # Analytics dashboard
    analytics_kw = ["analytics", "metrics", "reports", "monitoring", "insights",
                    "charts", "visualization", "data viz"]
    if any(kw in task_lower for kw in analytics_kw):
        return "analytics"

    # Documentation
    docs_kw = ["docs", "documentation", "wiki", "knowledge base", "help center",
               "guide", "manual"]
    if any(kw in task_lower for kw in docs_kw):
        return "documentation"

    # Portfolio/Creative
    portfolio_kw = ["portfolio", "showcase", "gallery", "photographer",
                    "designer", "freelance", "creative"]
    if any(kw in task_lower for kw in portfolio_kw):
        return "portfolio"

    # Default: landing page / marketing website
    return "landing_page"


def get_pattern_knowledge(project_type: str) -> str:
    """Load the architecture pattern knowledge for the given project type.

    Returns a markdown string with code patterns that Claude should follow.
    """
    # Map project types to pattern files
    pattern_map = {
        "admin_panel": "admin_panel.md",
        "ecommerce": "admin_panel.md",  # uses same CRUD patterns
        "analytics": "admin_panel.md",  # sidebar + charts
        "saas_app": "website_landing.md",  # landing + admin hybrid
        "landing_page": "website_landing.md",
        "blog": "website_landing.md",
        "portfolio": "website_landing.md",
        "social": "admin_panel.md",
        "booking": "website_landing.md",
        "documentation": "website_landing.md",
    }

    filename = pattern_map.get(project_type, "website_landing.md")
    filepath = os.path.join(_KNOWLEDGE_DIR, "patterns", filename)
    content = _read_file(filepath)

    if content:
        return (
            f"\n## ARCHITECTURE PATTERN: {project_type.upper().replace('_', ' ')}\n"
            f"Follow these exact code patterns when implementing components.\n"
            f"Copy the structure — adapt the content to the specific project.\n\n"
            f"{content}\n"
        )
    return ""


def get_quality_standards() -> str:
    """Load the quality standards knowledge.

    Returns a markdown string with coding standards that Claude must follow.
    """
    filepath = os.path.join(_KNOWLEDGE_DIR, "quality_standards.md")
    content = _read_file(filepath, max_chars=6000)

    if content:
        return f"\n## CODING STANDARDS — FOLLOW EXACTLY\n{content}\n"
    return ""


def get_framework_knowledge(stack: str) -> str:
    """Load framework-specific knowledge.

    Returns additional rules for the specific framework (Next.js, Vue, React).
    """
    _stk = (stack or "").lower()

    if "nextjs" in _stk or "next" in _stk:
        filename = "nextjs.md"
    elif "vue" in _stk:
        filename = "vue.md"
    else:
        filename = "react.md"

    filepath = os.path.join(_KNOWLEDGE_DIR, "frameworks", filename)
    content = _read_file(filepath, max_chars=4000)

    if content:
        return f"\n## FRAMEWORK KNOWLEDGE\n{content}\n"
    return ""


def build_knowledge_context(task: str, stack: str = "") -> dict:
    """Build the complete knowledge context for a project.

    Returns a dict with:
      - project_type: str — classified project type
      - pattern_knowledge: str — architecture pattern markdown
      - quality_standards: str — coding standards markdown
      - framework_knowledge: str — framework-specific rules markdown
      - full_context: str — all three combined into one injection block

    This is the main entry point. Call this once per project and inject
    `full_context` into every Claude prompt.
    """
    project_type = classify_project_type(task)
    logger.info("Knowledge loader: project_type=%s, stack=%s", project_type, stack)

    patterns = get_pattern_knowledge(project_type)
    quality = get_quality_standards()
    framework = get_framework_knowledge(stack)

    full = ""
    if patterns:
        full += patterns
    if quality:
        full += quality
    if framework:
        full += framework

    return {
        "project_type": project_type,
        "pattern_knowledge": patterns,
        "quality_standards": quality,
        "framework_knowledge": framework,
        "full_context": full,
    }


def generate_claude_md(task: str, stack: str, workspace_path: str) -> Optional[str]:
    """Generate a CLAUDE.md file in the workspace.

    Claude Code automatically reads CLAUDE.md from the working directory
    BEFORE any prompts. This is the most powerful injection point — it
    acts as Layer 3 (Plugin = MCP + Skills bundled together).

    Returns the path to the created file, or None on failure.
    """
    ctx = build_knowledge_context(task, stack)
    project_type = ctx["project_type"]

    # Read template manifest if available
    manifest_content = ""
    manifest_path = os.path.join(workspace_path, "TEMPLATE_MANIFEST.md")
    if os.path.isfile(manifest_path):
        manifest_content = _read_file(manifest_path, max_chars=5000)

    # Build CLAUDE.md content
    claude_md = f"""# Project: {task[:100]}
# Type: {project_type}
# Stack: {stack}

## YOU ARE
A SENIOR FRONTEND ENGINEER at a top design agency (like Vercel, Linear, or Stripe).
You build production-grade web applications that look like they belong on Dribbble or Awwwards.
The UI must be STUNNING — users pay for this product. Quality is everything.

## DESIGN PHILOSOPHY (non-negotiable)

### Visual Excellence
- Every component must feel PREMIUM — not a tutorial project
- Use subtle gradients, glassmorphism, and depth (box-shadows at multiple levels)
- Micro-animations on EVERY interactive element (transition: all 0.2s ease)
- Cards hover: translateY(-4px) + enhanced shadow
- Buttons: gradient backgrounds + scale(1.02) on hover
- Generous whitespace — sections breathe, content is scannable
- Typography hierarchy: clamp-based fluid sizing for h1-h6

### Color Discipline
- ONLY CSS variables: var(--color-primary), var(--color-bg), var(--color-text), etc.
- NEVER hardcoded hex colors — not even in inline styles
- Gradient buttons: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark))
- Subtle tinted backgrounds: var(--color-primary-50) for feature callouts

### Content Quality
- Write REAL, compelling content — headlines that sell, descriptions that inform
- Feature cards: 6+ items with specific, descriptive text (3+ sentences each)
- Testimonials: realistic quotes with names, roles, and companies
- Data tables: 5-10 rows of realistic mock data with proper formatting
- NO "Lorem ipsum", NO "Sample text", NO placeholder content
- Numbers and stats should be realistic: "$2.4M revenue", "10,000+ users", "99.9% uptime"

### Layout & Spacing
- Container: max-width 1200px, auto margins, responsive padding clamp(1rem, 3vw, 2rem)
- Section padding: clamp(3rem, 8vw, 6rem) vertically
- Card grids: CSS Grid with repeat(auto-fit, minmax(300px, 1fr)), gap: 1.5rem
- Mobile: single column. Tablet: 2 columns. Desktop: 3-4 columns

### Responsive (mobile-first, all breakpoints)
- 320px: single column, compact spacing
- 768px: two columns, medium spacing
- 1024px: full layout with sidebar (admin) or 3-column grids
- 1280px: comfortable max-width with generous whitespace

## WORKSPACE RULES

### Import Safety (CRITICAL — build MUST pass)
1. Before writing ANY import, verify the target file exists in the workspace
2. If a file doesn't exist, create it before importing
3. Use RELATIVE imports only (../components/X, ./sections/Y)
4. Never use @/ alias — it may not be configured
5. Icons: import {{ IconName }} from 'lucide-react'

### Component Architecture
1. Named export AND default export: `export function Name() {{ }} export default Name`
2. Loading state (skeleton shimmer or spinner)
3. Empty state (illustration + message + CTA)
4. CSS variables only — zero hardcoded colors
5. Responsive — test mentally at 320px, 768px, 1024px, 1280px

### After Every File
Ask yourself: "Would npm run build pass right now?"
- Are all imports pointing to existing files?
- Did I use 'use client' for components with hooks? (Next.js only)
- Are all CSS variables defined in the globals file?

"""

    if manifest_content:
        claude_md += f"""## TEMPLATE MANIFEST — Available Components
{manifest_content}

"""

    claude_md += ctx.get("pattern_knowledge", "")
    claude_md += "\n" + ctx.get("quality_standards", "")

    # Write to workspace
    claude_md_path = os.path.join(workspace_path, "CLAUDE.md")
    try:
        with open(claude_md_path, "w") as f:
            f.write(claude_md)
        logger.info("Generated CLAUDE.md (%d bytes) at %s", len(claude_md), claude_md_path)
        return claude_md_path
    except Exception as e:
        logger.warning("Failed to write CLAUDE.md: %s", e)
        return None
