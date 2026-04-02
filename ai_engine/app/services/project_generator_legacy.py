"""Project generator — AI-powered app research & requirements discovery.

Uses Gemini Flash for classification and Google Search grounding to
discover best UI patterns, features and industry practices for any
app type the user describes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Optional

import google.generativeai as genai
from google.generativeai.types import GenerateContentResponse

from app.config import settings

logger = logging.getLogger("lucid.project_generator")


# ╔══════════════════════════════════════════════════════════════╗
# ║  FALLBACK TEMPLATES — used ONLY when dynamic search fails   ║
# ╚══════════════════════════════════════════════════════════════╝

_FALLBACK_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "admin_panel": {
        "must_have_features": [
            "Stats overview with KPI cards",
            "Data tables with search, filter, sort",
            "CRUD operations with modals",
            "User management",
            "Role based permissions",
            "Activity logs",
            "Export to CSV/PDF",
            "Bulk actions on tables",
            "Notification center",
            "Settings and configuration",
        ],
        "reference_products": [
            "Linear - clean table design",
            "Vercel - dashboard metrics",
            "Notion - database views",
            "Retool - admin interface",
        ],
    },
    "logistics": {
        "must_have_features": [
            "Order tracking with status",
            "Driver/courier management",
            "Route optimization view",
            "Delivery status timeline",
            "Customer management",
            "Invoice generation",
            "Real-time map view",
            "Performance analytics",
            "Warehouse management",
            "Notification system",
        ],
        "reference_products": [
            "Project44 - shipment visibility",
            "Samsara - fleet management",
            "Flexport - logistics dashboard",
            "Uber Freight - load management",
        ],
    },
    "crm": {
        "must_have_features": [
            "Contact management",
            "Deal pipeline (Kanban)",
            "Activity timeline",
            "Email integration",
            "Task management",
            "Reports and analytics",
            "Lead scoring",
            "Document management",
        ],
        "reference_products": [
            "HubSpot - deal pipeline",
            "Salesforce - contact management",
            "Pipedrive - sales CRM",
            "Close - activity tracking",
        ],
    },
    "finance": {
        "must_have_features": [
            "Transaction history",
            "Account overview",
            "Budget tracking",
            "Invoice management",
            "Expense categories",
            "Reports with charts",
            "Export functionality",
            "Multi-currency support",
        ],
        "reference_products": [
            "Stripe Dashboard - transaction views",
            "Mercury - banking interface",
            "Brex - expense management",
            "QuickBooks - financial reports",
        ],
    },
    "e_commerce": {
        "must_have_features": [
            "Product catalog with filters",
            "Shopping cart and checkout",
            "Order management",
            "Payment integration",
            "Inventory tracking",
            "Customer reviews and ratings",
            "Search with autocomplete",
            "Wishlist and favorites",
            "Shipping and delivery tracking",
            "Discount and coupon management",
        ],
        "reference_products": [
            "Shopify - store management",
            "Amazon - product listing UX",
            "Stripe - checkout flow",
            "Gumroad - simple storefront",
        ],
    },
    "dashboard": {
        "must_have_features": [
            "KPI cards with real-time data",
            "Interactive charts and graphs",
            "Date range filtering",
            "Data export options",
            "Custom widget layout",
            "Alert and threshold indicators",
            "Drill-down capabilities",
            "Responsive grid layout",
        ],
        "reference_products": [
            "Datadog - monitoring dashboard",
            "Grafana - data visualization",
            "Mixpanel - analytics dashboard",
            "Vercel Analytics - web metrics",
        ],
    },
    "saas_app": {
        "must_have_features": [
            "User onboarding wizard",
            "Subscription and billing",
            "Team/workspace management",
            "Settings and preferences",
            "API key management",
            "Usage analytics",
            "Role based access control",
            "Integration marketplace",
        ],
        "reference_products": [
            "Linear - project management SaaS",
            "Notion - workspace SaaS",
            "Figma - design SaaS",
            "Vercel - deployment SaaS",
        ],
    },
    "healthcare": {
        "must_have_features": [
            "Patient records management",
            "Appointment scheduling",
            "Medical history timeline",
            "Prescription management",
            "Lab results viewer",
            "Insurance and billing",
            "Telemedicine integration",
            "HIPAA compliant data handling",
        ],
        "reference_products": [
            "Epic MyChart - patient portal",
            "Zocdoc - appointment booking",
            "Athenahealth - practice management",
            "Doximity - physician network",
        ],
    },
    "education": {
        "must_have_features": [
            "Course content management",
            "Student progress tracking",
            "Assignment submission",
            "Grade book and analytics",
            "Discussion forums",
            "Video lesson player",
            "Quiz and assessment builder",
            "Certificate generation",
        ],
        "reference_products": [
            "Coursera - learning platform",
            "Canvas LMS - course management",
            "Duolingo - gamified learning",
            "Notion - knowledge base",
        ],
    },
    "erp": {
        "must_have_features": [
            "Multi-module navigation",
            "Inventory management",
            "HR and payroll",
            "Procurement and purchasing",
            "Financial accounting",
            "Production planning",
            "Supply chain management",
            "Customizable reports",
        ],
        "reference_products": [
            "SAP S/4HANA - enterprise ERP",
            "Odoo - modular ERP",
            "NetSuite - cloud ERP",
            "ERPNext - open source ERP",
        ],
    },
}

# Valid app type slugs the classifier can choose from
VALID_APP_TYPES = [
    "admin_panel",
    "dashboard",
    "e_commerce",
    "saas_app",
    "landing_page",
    "blog",
    "portfolio",
    "crm",
    "erp",
    "logistics",
    "healthcare",
    "finance",
    "education",
    "other",
]


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — safe JSON extraction from LLM responses          ║
# ╚══════════════════════════════════════════════════════════════╝

def _extract_json(text: str) -> dict:
    """Try to extract a JSON object from text that may contain markdown fences."""
    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = cleaned.index("\n")
        cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[: cleaned.rfind("```")]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: find the first { … } block
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {}


def _configure_genai() -> None:
    """Ensure the google-generativeai SDK is configured with our API key."""
    api_key = os.getenv("GOOGLE_API_KEY") or settings.LLM_API_KEY
    if not api_key:
        raise RuntimeError(
            "No Google API key found. Set GOOGLE_API_KEY in your environment."
        )
    genai.configure(api_key=api_key)


# ╔══════════════════════════════════════════════════════════════╗
# ║  DYNAMIC TEMPLATE DISCOVERY — searches the internet         ║
# ╚══════════════════════════════════════════════════════════════╝

async def _fetch_dynamic_templates(
    app_type: str,
    app_description: str,
) -> dict[str, Any]:
    """Search the internet for real templates, projects, and features.

    Uses Gemini 2.0 Flash with Google Search grounding to find:
    - Open-source templates and starter kits on GitHub
    - Production apps and their feature sets
    - Industry-standard UI patterns and page structures
    - Best-practice reference products

    Returns a dict with ``must_have_features``, ``reference_products``,
    ``standard_pages``, ``ui_patterns``, or an empty dict on failure.
    """

    _configure_genai()

    app_label = app_type.replace("_", " ")

    template_prompt = f"""Search the internet and find REAL templates, projects, and best practices for building a {app_label} application.

User's specific description: {app_description}

Search for these specific things:
1. Open-source {app_label} templates on GitHub (find actual repo names and stars)
2. Top 10 production {app_label} applications and what features make them great
3. Standard pages and sections every {app_label} app must have
4. UI patterns and component patterns used by market leaders
5. Common technology stacks used for {app_label} apps in 2025

Search queries to use:
- "{app_label} template github 2025 stars"
- "best {app_label} open source project"
- "{app_label} UI design patterns 2025"
- "{app_label} app must have features"
- "{app_label} SaaS examples"

Return ONLY valid JSON (no markdown fences):
{{
    "must_have_features": ["feature1", "feature2", ..., "feature10"],
    "standard_pages": ["page1", "page2", ..., "page8"],
    "reference_products": [
        "ProductName - what they do well (URL if found)",
        "ProductName2 - their standout feature"
    ],
    "ui_patterns": [
        "Pattern - used by Company (specific description)",
        "Pattern2 - used by Company2"
    ],
    "open_source_templates": [
        "repo/name - description (stars count if found)",
        "repo/name2 - description"
    ],
    "tech_stack_recommendations": [
        "Frontend: specific frameworks",
        "Backend: specific choices",
        "Database: recommendation"
    ]
}}
"""

    try:
        search_model = genai.GenerativeModel("gemini-2.0-flash")

        response = search_model.generate_content(
            template_prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.3,
                max_output_tokens=4096,
            ),
            tools=[{"google_search": {}}],
        )

        result = _extract_json(response.text.strip())

        # Validate we got meaningful data
        if result and (
            result.get("must_have_features")
            or result.get("reference_products")
        ):
            logger.info(
                "Dynamic template search for '%s' returned %d features, %d refs",
                app_type,
                len(result.get("must_have_features", [])),
                len(result.get("reference_products", [])),
            )
            return result

        logger.warning(
            "Dynamic template search for '%s' returned empty/invalid data",
            app_type,
        )
        return {}

    except Exception as exc:
        logger.error("Dynamic template search failed for '%s': %s", app_type, exc)
        return {}


# ╔══════════════════════════════════════════════════════════════╗
# ║  MAIN FUNCTION                                              ║
# ╚══════════════════════════════════════════════════════════════╝

async def research_app_requirements(
    app_description: str,
    send_progress: Optional[Callable] = None,
) -> dict[str, Any]:
    """Research the internet and return structured requirements for an app.

    Parameters
    ----------
    app_description:
        Free-text description of the app the user wants to build.
    send_progress:
        Optional async callback ``async def(data: dict) -> None`` that
        sends real-time progress events to the frontend (e.g. over WebSocket).

    Returns
    -------
    dict  –  Merged research results with keys:
        app_type, must_have_features, standard_pages, data_entities,
        user_workflows, ui_patterns, reference_products,
        common_mistakes_to_avoid
    """

    _configure_genai()

    # ── helper to emit progress ──────────────────────────────
    async def _progress(step: str, found: str = "") -> None:
        if send_progress:
            try:
                await send_progress({
                    "type": "research_progress",
                    "step": step,
                    "found": found,
                })
            except Exception as exc:
                logger.warning("send_progress failed: %s", exc)

    # ══════════════════════════════════════════════════════════
    #  STEP 1 — Classify app type with Gemini Flash
    # ══════════════════════════════════════════════════════════
    await _progress("Classifying app type...")

    classify_prompt = f"""You are an app-type classifier.

Given the user's app description, classify it into EXACTLY ONE of these types:
{', '.join(VALID_APP_TYPES)}

User description: \"\"\"{app_description}\"\"\"

Reply with ONLY valid JSON (no markdown, no extra text):
{{"app_type": "<one_of_the_types_above>", "confidence": "<high|medium|low>"}}
"""

    flash_model = genai.GenerativeModel("gemini-2.0-flash")

    classify_response: GenerateContentResponse = flash_model.generate_content(
        classify_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.1,
            max_output_tokens=128,
        ),
    )

    classify_text = classify_response.text.strip()
    classify_data = _extract_json(classify_text)
    app_type = classify_data.get("app_type", "other")

    # Validate — fall back to "other" if hallucinated
    if app_type not in VALID_APP_TYPES:
        app_type = "other"

    logger.info(
        "Classified '%s' → app_type=%s (confidence=%s)",
        app_description[:60],
        app_type,
        classify_data.get("confidence", "?"),
    )
    await _progress(
        "App type classified",
        f"Detected: {app_type.replace('_', ' ').title()}",
    )

    # ══════════════════════════════════════════════════════════
    #  STEP 2 — Search for best practices (Google Search grounding)
    # ══════════════════════════════════════════════════════════
    await _progress("Searching internet for best practices...")

    search_prompt = f"""Search the internet and find:

App type: {app_type}
User description: {app_description}

Find and return:
1. TOP 5 most important features this type of app must have
2. TOP 5 pages that are standard for this app type
3. Industry-specific data that needs to be managed
4. Common user workflows and actions
5. Best UI patterns used by top companies for this app type (mention specific companies)
6. What makes users love or hate this type of app

Search for: "{app_type.replace('_', ' ')} best UI features 2025"
Search for: "{app_description} software features"
Search for: "best {app_type.replace('_', ' ')} design patterns"

Return as structured JSON (no markdown fences, pure JSON only):
{{
    "app_type": "{app_type}",
    "must_have_features": ["feature1", "feature2", "feature3", "feature4", "feature5"],
    "standard_pages": ["page1", "page2", "page3", "page4", "page5"],
    "data_entities": ["entity1", "entity2", "entity3"],
    "user_workflows": ["workflow1", "workflow2", "workflow3"],
    "ui_patterns": ["pattern1 - used by CompanyX", "pattern2 - used by CompanyY"],
    "reference_products": ["Product1 - what they do well", "Product2 - what they do well"],
    "common_mistakes_to_avoid": ["mistake1", "mistake2", "mistake3"]
}}
"""

    # Use Gemini 2.0 Flash with Google Search grounding
    search_model = genai.GenerativeModel("gemini-2.0-flash")

    search_response: GenerateContentResponse = search_model.generate_content(
        search_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.3,
            max_output_tokens=2048,
        ),
        tools=[{"google_search": {}}],
    )

    search_text = search_response.text.strip()
    search_results = _extract_json(search_text)

    # Count how many features were found for the progress message
    features_found = len(search_results.get("must_have_features", []))
    pages_found = len(search_results.get("standard_pages", []))
    total_items = features_found + pages_found

    logger.info(
        "Search grounding returned %d features, %d pages for %s",
        features_found,
        pages_found,
        app_type,
    )
    await _progress(
        "Searching internet...",
        f"Found {total_items} features for {app_type.replace('_', ' ')} app",
    )

    # ══════════════════════════════════════════════════════════
    #  STEP 3 — DYNAMIC template discovery via Gemini Search
    #  Searches the internet for real templates, projects, and
    #  best-practice features for this specific app type.
    #  Falls back to _FALLBACK_TEMPLATES only if search fails.
    # ══════════════════════════════════════════════════════════
    await _progress(
        "Searching for real-world templates and projects...",
        f"Finding production examples for {app_type.replace('_', ' ')} apps",
    )

    dynamic_template = await _fetch_dynamic_templates(
        app_type=app_type,
        app_description=app_description,
    )

    # If dynamic search returned nothing, fall back to hardcoded
    if not dynamic_template or not dynamic_template.get("must_have_features"):
        logger.warning(
            "Dynamic template search returned nothing for %s — using fallback",
            app_type,
        )
        dynamic_template = _FALLBACK_TEMPLATES.get(app_type, {})
        await _progress(
            "Using built-in templates (search unavailable)",
            f"Loaded fallback template for {app_type.replace('_', ' ')}",
        )
    else:
        dyn_features = len(dynamic_template.get("must_have_features", []))
        dyn_refs = len(dynamic_template.get("reference_products", []))
        logger.info(
            "Dynamic template search found %d features, %d reference products",
            dyn_features, dyn_refs,
        )
        await _progress(
            "Found real-world templates",
            f"{dyn_features} features, {dyn_refs} reference products from live search",
        )

    # Start with search results as the base
    final: dict[str, Any] = {
        "app_type": app_type,
        "must_have_features": search_results.get("must_have_features", []),
        "standard_pages": search_results.get("standard_pages", []),
        "data_entities": search_results.get("data_entities", []),
        "user_workflows": search_results.get("user_workflows", []),
        "ui_patterns": search_results.get("ui_patterns", []),
        "reference_products": search_results.get("reference_products", []),
        "common_mistakes_to_avoid": search_results.get(
            "common_mistakes_to_avoid", []
        ),
    }

    # Merge dynamic template features — add any not already present
    if dynamic_template:
        template_features = dynamic_template.get("must_have_features", [])
        existing_lower = {f.lower() for f in final["must_have_features"]}
        for feat in template_features:
            if feat.lower() not in existing_lower:
                final["must_have_features"].append(feat)
                existing_lower.add(feat.lower())

        template_refs = dynamic_template.get("reference_products", [])
        existing_refs_lower = {r.lower() for r in final["reference_products"]}
        for ref in template_refs:
            if ref.lower() not in existing_refs_lower:
                final["reference_products"].append(ref)
                existing_refs_lower.add(ref.lower())

        # Also merge template-specific pages and patterns
        template_pages = dynamic_template.get("standard_pages", [])
        existing_pages_lower = {p.lower() for p in final["standard_pages"]}
        for page in template_pages:
            if page.lower() not in existing_pages_lower:
                final["standard_pages"].append(page)
                existing_pages_lower.add(page.lower())

        template_patterns = dynamic_template.get("ui_patterns", [])
        existing_patterns_lower = {p.lower() for p in final["ui_patterns"]}
        for pattern in template_patterns:
            if pattern.lower() not in existing_patterns_lower:
                final["ui_patterns"].append(pattern)
                existing_patterns_lower.add(pattern.lower())

    # Also merge hardcoded fallback (belt + suspenders — catch anything missed)
    fallback = _FALLBACK_TEMPLATES.get(app_type, {})
    if fallback:
        existing_lower = {f.lower() for f in final["must_have_features"]}
        for feat in fallback.get("must_have_features", []):
            if feat.lower() not in existing_lower:
                final["must_have_features"].append(feat)
                existing_lower.add(feat.lower())

    total_features = len(final["must_have_features"])
    total_refs = len(final["reference_products"])

    logger.info(
        "After template merge: %d features, %d reference products",
        total_features,
        total_refs,
    )

    # ══════════════════════════════════════════════════════════
    #  STEP 4 — Final progress event
    # ══════════════════════════════════════════════════════════
    await _progress(
        "Research complete",
        (
            f"Found {total_features} features, "
            f"{len(final['standard_pages'])} pages, "
            f"{total_refs} reference products for "
            f"{app_type.replace('_', ' ')} app"
        ),
    )

    return final


# ╔══════════════════════════════════════════════════════════════╗
# ║  HELPER — count files in nested file_structure dict         ║
# ╚══════════════════════════════════════════════════════════════╝

def _count_files(structure: Any, count: int = 0) -> int:
    """Recursively count file entries in the nested file_structure dict."""
    if isinstance(structure, list):
        return count + len(structure)
    if isinstance(structure, dict):
        for value in structure.values():
            count = _count_files(value, count)
    return count


# ╔══════════════════════════════════════════════════════════════╗
# ║  GENERATE COMPLETE APP STRUCTURE                            ║
# ╚══════════════════════════════════════════════════════════════╝

async def generate_app_structure(
    app_description: str,
    research: dict[str, Any],
    stack: str = "nextjs",
    send_progress: Optional[Callable] = None,
) -> dict[str, Any]:
    """Generate a complete project blueprint from research findings.

    Parameters
    ----------
    app_description:
        Free-text description of the app the user wants to build.
    research:
        Dict returned by ``research_app_requirements()``.
    stack:
        Frontend stack identifier — ``"nextjs"`` | ``"react"``
        (default ``"nextjs"``).
    send_progress:
        Optional async callback ``async def(data: dict) -> None`` that
        sends real-time progress events to the frontend.

    Returns
    -------
    dict  –  Complete project structure including pages, components,
             data models, navigation, design system, and generation order.
    """

    _configure_genai()

    app_type = research.get("app_type", "other")

    # Map stack id → human-readable label for the prompt
    stack_label = {
        "nextjs": "Next.js (App Router, TypeScript)",
        "react": "React (Vite, TypeScript)",
        "vue": "Vue.js 3 (TypeScript)",
        "angular": "Angular (TypeScript)",
    }.get(stack, "Next.js (App Router, TypeScript)")

    # ── helper to emit progress ──────────────────────────────
    async def _progress(step: str, found: str = "") -> None:
        if send_progress:
            try:
                await send_progress({
                    "type": "research_progress",
                    "step": step,
                    "found": found,
                })
            except Exception as exc:
                logger.warning("send_progress failed: %s", exc)

    await _progress("Generating project structure...", f"Stack: {stack_label}")

    # ── Serialize research for prompt context ─────────────────
    research_json = json.dumps(research, indent=2, ensure_ascii=False)

    architect_prompt = f"""You are a senior software architect.
Based on this research, create a COMPLETE project blueprint.

App description: {app_description}
App type: {app_type}
Research findings: {research_json}
Stack: {stack_label}

Create this EXACT JSON structure.
Be very specific. No vague descriptions.
Every page must have real components.

{{
    "app_name": "descriptive name",
    "app_description": "2 sentence description",
    "total_pages": <number>,
    "total_components": <number>,

    "file_structure": {{
        "src/": {{
            "components/": {{
                "ui/": ["Button.tsx", "Input.tsx", "Modal.tsx", "Table.tsx", "Badge.tsx", "Card.tsx", "Sidebar.tsx", "Navbar.tsx"],
                "charts/": ["BarChart.tsx", "LineChart.tsx"],
                "forms/": ["CreateOrderForm.tsx"]
            }},
            "pages/": ["Dashboard.tsx", "Orders.tsx"],
            "hooks/": ["useOrders.ts", "useAuth.ts"],
            "types/": ["order.types.ts", "user.types.ts"],
            "utils/": ["formatDate.ts", "formatCurrency.ts"],
            "constants/": ["routes.ts", "config.ts"]
        }}
    }},

    "pages": [
        {{
            "name": "Dashboard",
            "file": "pages/Dashboard.tsx",
            "route": "/dashboard",
            "priority": 1,
            "description": "Main overview",
            "layout": "sidebar + topnav",
            "sections": [
                {{
                    "name": "Stats Overview",
                    "component": "StatsCards",
                    "data": "total_orders, active_drivers, pending_deliveries, revenue"
                }},
                {{
                    "name": "Recent Orders",
                    "component": "RecentOrdersTable",
                    "data": "last 10 orders with status"
                }}
            ],
            "components_used": ["StatsCard", "DataTable", "StatusBadge"],
            "actions": ["View order detail", "Quick status update"],
            "mock_data_needed": true
        }}
    ],

    "shared_components": [
        {{
            "name": "DataTable",
            "file": "components/ui/DataTable.tsx",
            "props": ["data", "columns", "onRowClick", "loading"],
            "features": ["pagination", "search", "sort", "bulk select", "export button"]
        }}
    ],

    "data_models": [
        {{
            "name": "Order",
            "fields": [
                {{"name": "id", "type": "string"}},
                {{"name": "status", "type": "enum", "values": ["pending", "processing", "delivered", "cancelled"]}},
                {{"name": "customer", "type": "Customer"}},
                {{"name": "created_at", "type": "Date"}}
            ]
        }}
    ],

    "navigation": {{
        "type": "sidebar",
        "items": [
            {{
                "label": "Dashboard",
                "icon": "LayoutDashboard",
                "route": "/dashboard",
                "badge": null
            }},
            {{
                "label": "Orders",
                "icon": "Package",
                "route": "/orders",
                "badge": "orders_count"
            }}
        ]
    }},

    "design_system": {{
        "primary_color": "#3B82F6",
        "style": "clean modern minimal",
        "reference": "Linear + Vercel style",
        "dark_mode": true
    }},

    "generation_order": [
        "1. types/ folder first",
        "2. utils/ folder",
        "3. constants/ folder",
        "4. shared UI components",
        "5. Layout (Sidebar + Navbar)",
        "6. Pages in priority order"
    ]
}}

Be extremely specific.
Real component names.
Real field names.
Real route names.
Think about what a real developer would build for this exact app.
Return JSON only. No markdown fences. No explanations."""

    # ── Call Gemini Flash ─────────────────────────────────────
    flash_model = genai.GenerativeModel("gemini-2.0-flash")

    await _progress("AI architect is designing your app...")

    response: GenerateContentResponse = flash_model.generate_content(
        architect_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.4,
            max_output_tokens=8192,
        ),
    )

    response_text = response.text.strip()
    structure = _extract_json(response_text)

    if not structure:
        logger.error(
            "Failed to parse structure from Gemini response: %s",
            response_text[:500],
        )
        raise RuntimeError(
            "Failed to generate app structure. Gemini returned unparseable output."
        )

    # ── Compute summary stats ─────────────────────────────────
    pages = structure.get("pages", [])
    shared_components = structure.get("shared_components", [])
    data_models = structure.get("data_models", [])
    file_structure = structure.get("file_structure", {})
    total_files = _count_files(file_structure)

    # Estimate generation time based on complexity
    page_count = len(pages)
    component_count = len(shared_components)
    if page_count <= 3:
        estimated_time = "4-6 minutes"
    elif page_count <= 6:
        estimated_time = "8-12 minutes"
    elif page_count <= 10:
        estimated_time = "12-18 minutes"
    else:
        estimated_time = "18-25 minutes"

    summary = {
        "pages": page_count,
        "components": component_count,
        "data_models": len(data_models),
        "estimated_files": total_files,
        "estimated_time": estimated_time,
    }

    logger.info(
        "App structure generated: %d pages, %d components, "
        "%d models, %d total files – est. %s",
        page_count,
        component_count,
        len(data_models),
        total_files,
        estimated_time,
    )

    # ── Send structure_ready event to frontend ────────────────
    if send_progress:
        try:
            await send_progress({
                "type": "structure_ready",
                "structure": structure,
                "summary": summary,
            })
        except Exception as exc:
            logger.warning("send_progress (structure_ready) failed: %s", exc)

    await _progress(
        "Structure ready for review",
        (
            f"{page_count} pages, {component_count} components, "
            f"{total_files} files – {estimated_time}"
        ),
    )

    return {
        "structure": structure,
        "summary": summary,
    }


# ╔══════════════════════════════════════════════════════════════╗
# ║  DESIGN SYSTEM DOCS — injected into every generation prompt ║
# ╚══════════════════════════════════════════════════════════════╝

DESIGN_SYSTEM_DOCS = """
DESIGN SYSTEM — Follow exactly:

COLORS (Tailwind classes only):
Primary: blue-600 (hover: blue-700)
Secondary: gray-600 (hover: gray-700)
Success: green-500
Warning: yellow-500
Error: red-500
Background: gray-50
Surface: white
Border: gray-200
Text primary: gray-900
Text secondary: gray-500

SPACING SYSTEM:
Use 4px grid always:
xs: p-1 (4px)
sm: p-2 (8px)
md: p-4 (16px)
lg: p-6 (24px)
xl: p-8 (32px)
2xl: p-12 (48px)

TYPOGRAPHY:
Page title: text-2xl font-bold text-gray-900
Section title: text-lg font-semibold text-gray-900
Card title: text-base font-medium text-gray-900
Body: text-sm text-gray-700
Caption: text-xs text-gray-500
Label: text-xs font-medium text-gray-700 uppercase tracking-wider

COMPONENT PATTERNS:

Button primary:
<button className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg font-medium text-sm transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2">

Button secondary:
<button className="bg-white hover:bg-gray-50 text-gray-700 px-4 py-2 rounded-lg font-medium text-sm border border-gray-300 transition-colors flex items-center gap-2">

Button danger:
<button className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-lg font-medium text-sm transition-colors">

Card:
<div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">

Stats card:
<div className="bg-white rounded-xl border border-gray-200 p-6">
  <div className="flex items-center justify-between">
    <div>
      <p className="text-sm text-gray-500">Label</p>
      <p className="text-2xl font-bold text-gray-900 mt-1">Value</p>
      <p className="text-xs text-green-600 mt-1">+12% from last month</p>
    </div>
    <div className="bg-blue-50 p-3 rounded-lg">
      <Icon className="w-6 h-6 text-blue-600" />
    </div>
  </div>
</div>

Table:
<div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
  <table className="w-full">
    <thead>
      <tr className="bg-gray-50 border-b border-gray-200">
        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
    <tbody>
      <tr className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
        <td className="px-6 py-4 text-sm text-gray-900">

Input:
<input className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all placeholder:text-gray-400" />

Badge status:
active: <span className="bg-green-100 text-green-800 px-2.5 py-0.5 rounded-full text-xs font-medium">
pending: <span className="bg-yellow-100 text-yellow-800 px-2.5 py-0.5 rounded-full text-xs font-medium">
error: <span className="bg-red-100 text-red-800 px-2.5 py-0.5 rounded-full text-xs font-medium">
inactive: <span className="bg-gray-100 text-gray-800 px-2.5 py-0.5 rounded-full text-xs font-medium">

Sidebar:
<aside className="w-64 bg-gray-900 h-screen fixed left-0 top-0 flex flex-col">
  <div className="p-6 border-b border-gray-800">Logo area</div>
  <nav className="flex-1 p-4 space-y-1">
    Active item: "flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-800 text-white text-sm font-medium"
    Inactive: "flex items-center gap-3 px-3 py-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 text-sm transition-all"

Modal:
<div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
  <div className="bg-white rounded-xl shadow-xl w-full max-w-md">
    <div className="p-6 border-b border-gray-200 flex items-center justify-between">
    <div className="p-6">
    <div className="p-6 border-t border-gray-200 flex justify-end gap-3">

QUALITY RULES:
1. Every list/table MUST have:
   - Loading skeleton state
   - Empty state with icon + message
   - Error state with retry button

2. Every form MUST have:
   - Field validation messages
   - Submit loading state
   - Success feedback

3. Every page MUST have:
   - Page title + breadcrumb
   - Action buttons top right
   - Responsive layout

4. Icons: Use lucide-react always
   import { Icon } from 'lucide-react'

5. Reference designs:
   Tables → Linear issue list
   Dashboard → Vercel analytics
   Forms → Linear create modal
   Navigation → Linear sidebar
"""


# ╔══════════════════════════════════════════════════════════════╗
# ║  GENERATE PROJECT CODE — cost-optimized batch generation    ║
# ╚══════════════════════════════════════════════════════════════╝

async def generate_project_code(
    app_description: str,
    structure: dict[str, Any],
    stack: str = "nextjs",
    send_progress: Optional[Callable] = None,
) -> dict[str, Any]:
    """Generate complete project source code from a structure blueprint.

    Uses cost-optimized batch generation:
      Batch 1 — Foundation: types, utils, constants, mock data   (~$0.05)
      Batch 2 — Shared UI components                             (~$0.10)
      Batch 3 — Layout: sidebar + navbar + wrapper               (~$0.05)
      Batch 4 — Pages: one call per page                         (~$0.05-0.10 each)

    Parameters
    ----------
    app_description:
        Free-text description of the app.
    structure:
        The ``"structure"`` dict returned by ``generate_app_structure()``.
    stack:
        ``"nextjs"`` | ``"react"`` | ``"vue"`` | ``"angular"``
    send_progress:
        Async callback for real-time frontend events.

    Returns
    -------
    dict with keys:
        files          – dict[filepath, code_string]
        cost_breakdown – per-batch cost estimates
        total_cost     – estimated total USD
        generation_log – list of log entries
    """

    _configure_genai()

    app_name = structure.get("app_name", "My App")
    app_type = structure.get("app_type", "other")
    data_models = structure.get("data_models", [])
    shared_components = structure.get("shared_components", [])
    pages = structure.get("pages", [])
    navigation = structure.get("navigation", {})
    design_system = structure.get("design_system", {})
    generation_order = structure.get("generation_order", [])

    # Sort pages by priority
    pages_sorted = sorted(pages, key=lambda p: p.get("priority", 99))

    flash_model = genai.GenerativeModel("gemini-2.0-flash")

    # ── Accumulators ──────────────────────────────────────────
    generated_files: dict[str, str] = {}
    cost_breakdown: list[dict[str, Any]] = []
    generation_log: list[str] = []
    total_cost = 0.0

    # ── helper to emit progress ──────────────────────────────
    async def _progress(step: str, found: str = "") -> None:
        if send_progress:
            try:
                await send_progress({
                    "type": "research_progress",
                    "step": step,
                    "found": found,
                })
            except Exception as exc:
                logger.warning("send_progress failed: %s", exc)

    # ── helper: call Gemini and extract file blocks ───────────
    def _parse_file_blocks(text: str) -> dict[str, str]:
        """Parse LLM output that contains multiple file blocks.

        Expected format from the LLM:
            // FILE: src/types/order.types.ts
            ```tsx
            ... code ...
            ```

            // FILE: src/types/user.types.ts
            ```tsx
            ... code ...
            ```
        """
        files: dict[str, str] = {}
        lines = text.split("\n")
        current_file: str | None = None
        current_code: list[str] = []
        in_code_block = False

        for line in lines:
            stripped = line.strip()

            # Detect file markers
            if stripped.startswith("// FILE:") or stripped.startswith("// file:"):
                # Save previous file if exists
                if current_file and current_code:
                    files[current_file] = "\n".join(current_code).strip()
                current_file = stripped.split(":", 1)[1].strip()
                current_code = []
                in_code_block = False
                continue

            # Handle code fences
            if stripped.startswith("```"):
                if in_code_block:
                    # Closing fence
                    in_code_block = False
                else:
                    # Opening fence — skip the language tag line
                    in_code_block = True
                continue

            if in_code_block and current_file:
                current_code.append(line)

        # Save last file
        if current_file and current_code:
            files[current_file] = "\n".join(current_code).strip()

        return files

    def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
        """Rough cost estimate for Gemini 2.0 Flash."""
        # Gemini 2.0 Flash pricing (approx): $0.10/1M input, $0.40/1M output
        return (input_tokens * 0.10 / 1_000_000) + (output_tokens * 0.40 / 1_000_000)

    # ══════════════════════════════════════════════════════════
    #  BATCH 1 — Foundation: types, utils, constants, mock data
    # ══════════════════════════════════════════════════════════
    await _progress(
        "Batch 1/4 — Generating foundation files...",
        "Types, utils, constants, mock data",
    )

    data_models_json = json.dumps(data_models, indent=2, ensure_ascii=False)

    foundation_prompt = f"""Create all foundation files for {app_name} ({app_type}).
Return each file in this exact format:
// FILE: src/path/to/file.ts
```tsx
...code...
```

Files to create:

1. TypeScript type files — one per data model:
{data_models_json}
Create a file src/types/{{modelName}}.types.ts for EACH model above.
Export interfaces with all fields typed correctly.
Use enums for status/type fields.

2. Utility files:
// FILE: src/utils/formatDate.ts
- formatDate(date) → "Jan 15, 2025"
- formatDateTime(date) → "Jan 15, 2025 2:30 PM"
- formatRelative(date) → "2 hours ago"

// FILE: src/utils/formatCurrency.ts
- formatCurrency(amount, currency?) → "$1,234.56"
- formatCompact(amount) → "$1.2K"

// FILE: src/utils/helpers.ts
- cn() classnames helper
- truncate(str, len)
- generateId()
- capitalize(str)
- sleep(ms)

3. Constants:
// FILE: src/constants/routes.ts
Export all route paths as constants.
Routes: {json.dumps([p.get("route", "/") for p in pages_sorted], ensure_ascii=False)}

// FILE: src/constants/config.ts
App name, API base URL, pagination defaults, etc.

// FILE: src/constants/mockData.ts
Create REALISTIC mock data for each data model.
10-15 items per entity.
Use real-sounding names, dates, statuses.
Data models: {data_models_json}

Use TypeScript throughout.
Export everything as named exports.
No default exports."""

    foundation_response = flash_model.generate_content(
        foundation_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.2,
            max_output_tokens=8192,
        ),
    )

    foundation_text = foundation_response.text.strip()
    foundation_files = _parse_file_blocks(foundation_text)
    generated_files.update(foundation_files)

    batch1_cost = _estimate_cost(len(foundation_prompt) * 4, len(foundation_text) * 4)
    total_cost += batch1_cost
    cost_breakdown.append({
        "batch": "Foundation",
        "files": len(foundation_files),
        "cost": round(batch1_cost, 4),
    })
    generation_log.append(
        f"Batch 1 (Foundation): {len(foundation_files)} files — ${batch1_cost:.4f}"
    )
    logger.info("Batch 1 done: %d files, ~$%.4f", len(foundation_files), batch1_cost)

    await _progress(
        "Batch 1/4 complete",
        f"Generated {len(foundation_files)} foundation files (${batch1_cost:.3f})",
    )

    # ══════════════════════════════════════════════════════════
    #  BATCH 2 — Shared UI components
    # ══════════════════════════════════════════════════════════
    await _progress(
        "Batch 2/4 — Generating shared components...",
        f"{len(shared_components)} components",
    )

    components_json = json.dumps(shared_components, indent=2, ensure_ascii=False)

    components_prompt = f"""Create all shared UI components for {app_name}.
Return each file in this exact format:
// FILE: src/path/to/Component.tsx
```tsx
...code...
```

{DESIGN_SYSTEM_DOCS}

Components to create:
{components_json}

For EACH component:
- Accept TypeScript props with proper interface
- Follow the design system EXACTLY (colors, spacing, typography)
- Make fully reusable with sensible defaults
- Include "loading" prop that shows skeleton state
- Make responsive (mobile-friendly)
- Use lucide-react for all icons
- Include JSDoc comments on the props interface

Additional base components to always include:
// FILE: src/components/ui/Button.tsx — primary, secondary, danger, ghost variants with size prop
// FILE: src/components/ui/Input.tsx — with label, error, helper text props
// FILE: src/components/ui/Modal.tsx — overlay, header, body, footer sections with onClose
// FILE: src/components/ui/Badge.tsx — status variants (active, pending, error, inactive)
// FILE: src/components/ui/Card.tsx — with optional header, footer slots
// FILE: src/components/ui/Skeleton.tsx — line, circle, rect skeleton primitives
// FILE: src/components/ui/EmptyState.tsx — icon + title + description + action button
// FILE: src/components/ui/ErrorState.tsx — error icon + message + retry button

Reference quality: Linear and Vercel UI.
No default exports — use named exports only.
Every component must be production-quality."""

    components_response = flash_model.generate_content(
        components_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.3,
            max_output_tokens=16384,
        ),
    )

    components_text = components_response.text.strip()
    component_files = _parse_file_blocks(components_text)
    generated_files.update(component_files)

    batch2_cost = _estimate_cost(len(components_prompt) * 4, len(components_text) * 4)
    total_cost += batch2_cost
    cost_breakdown.append({
        "batch": "Shared Components",
        "files": len(component_files),
        "cost": round(batch2_cost, 4),
    })
    generation_log.append(
        f"Batch 2 (Components): {len(component_files)} files — ${batch2_cost:.4f}"
    )
    logger.info("Batch 2 done: %d files, ~$%.4f", len(component_files), batch2_cost)

    await _progress(
        "Batch 2/4 complete",
        f"Generated {len(component_files)} components (${batch2_cost:.3f})",
    )

    # ══════════════════════════════════════════════════════════
    #  BATCH 3 — Layout: sidebar + navbar + wrapper
    # ══════════════════════════════════════════════════════════
    await _progress(
        "Batch 3/4 — Generating layout system...",
        "Sidebar, Navbar, Layout wrapper",
    )

    nav_json = json.dumps(navigation, indent=2, ensure_ascii=False)
    design_json = json.dumps(design_system, indent=2, ensure_ascii=False)

    layout_prompt = f"""Create the layout system for {app_name}.
Return each file in this exact format:
// FILE: src/path/to/Component.tsx
```tsx
...code...
```

{DESIGN_SYSTEM_DOCS}

Navigation config:
{nav_json}

Design system config:
{design_json}

Files to create:

// FILE: src/components/layout/Sidebar.tsx
- Dark sidebar (bg-gray-900) fixed left
- Logo/app name at top
- Navigation items from config above
- Active state highlighting using current route
- Collapse/expand toggle for mobile
- User profile section at bottom
- Use lucide-react icons: {json.dumps([item.get("icon", "Circle") for item in navigation.get("items", [])], ensure_ascii=False)}

// FILE: src/components/layout/Navbar.tsx
- Top navbar with:
  - Breadcrumb on left
  - Search bar (optional)
  - Notification bell with badge
  - User avatar dropdown
- Sticky top-0 with backdrop blur

// FILE: src/components/layout/Layout.tsx
- Main layout wrapper
- Sidebar + Navbar + content area
- Content area: ml-64 (sidebar width) pt-16 (navbar height)
- Responsive: sidebar hidden on mobile, hamburger menu
- Accepts children prop

// FILE: src/components/layout/MobileMenu.tsx
- Slide-out menu for mobile
- Same nav items as sidebar
- Overlay backdrop with close on click

All components must:
- Use 'use client' directive (Next.js)
- Handle active route via usePathname()
- Be fully responsive
- Follow design system exactly
- Use named exports only"""

    layout_response = flash_model.generate_content(
        layout_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.3,
            max_output_tokens=8192,
        ),
    )

    layout_text = layout_response.text.strip()
    layout_files = _parse_file_blocks(layout_text)
    generated_files.update(layout_files)

    batch3_cost = _estimate_cost(len(layout_prompt) * 4, len(layout_text) * 4)
    total_cost += batch3_cost
    cost_breakdown.append({
        "batch": "Layout",
        "files": len(layout_files),
        "cost": round(batch3_cost, 4),
    })
    generation_log.append(
        f"Batch 3 (Layout): {len(layout_files)} files — ${batch3_cost:.4f}"
    )
    logger.info("Batch 3 done: %d files, ~$%.4f", len(layout_files), batch3_cost)

    await _progress(
        "Batch 3/4 complete",
        f"Generated {len(layout_files)} layout files (${batch3_cost:.3f})",
    )

    # ══════════════════════════════════════════════════════════
    #  BATCH 4 — Pages: one Gemini call per page
    # ══════════════════════════════════════════════════════════
    total_pages = len(pages_sorted)

    for idx, page in enumerate(pages_sorted, 1):
        page_name = page.get("name", f"Page{idx}")
        page_file = page.get("file", f"pages/{page_name}.tsx")

        await _progress(
            f"Batch 4 — Page {idx}/{total_pages}: {page_name}",
            f"Creating {page_name} with {len(page.get('sections', []))} sections",
        )

        page_json = json.dumps(page, indent=2, ensure_ascii=False)

        # Build list of available imports from previous batches
        available_components = list(component_files.keys())
        available_types = [
            f for f in foundation_files.keys() if "types/" in f
        ]
        available_utils = [
            f for f in foundation_files.keys() if "utils/" in f or "constants/" in f
        ]

        page_prompt = f"""Create the {page_name} page for {app_name}.
Return the file in this exact format:
// FILE: src/{page_file}
```tsx
...complete page code...
```

{DESIGN_SYSTEM_DOCS}

Page details:
{page_json}

Available imports (use these, do NOT recreate):
Types: {json.dumps(available_types, ensure_ascii=False)}
Components: {json.dumps(available_components, ensure_ascii=False)}
Utils: {json.dumps(available_utils, ensure_ascii=False)}
Mock data: src/constants/mockData.ts

This page MUST include:
1. 'use client' directive at top
2. Proper TypeScript types for all data
3. import mock data from constants/mockData
4. Import shared components (don't recreate them)
5. Loading state with skeleton placeholders
6. Empty state when no data
7. Error state with retry button
8. Page title + breadcrumbs at top
9. Action buttons (top right aligned)
10. Responsive layout (works on mobile)
11. All sections listed in page details above
12. Realistic interactions (search, filter, sort where applicable)
13. Smooth transitions and hover effects

Use lucide-react for ALL icons.
Named export only.
Production-quality code — this ships to users."""

        page_response = flash_model.generate_content(
            page_prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.3,
                max_output_tokens=8192,
            ),
        )

        page_text = page_response.text.strip()
        page_files = _parse_file_blocks(page_text)

        # If parser found no blocks, treat entire response as single file
        if not page_files:
            # Try to extract raw code from fenced block
            code = page_text
            if code.startswith("```"):
                first_nl = code.index("\n")
                code = code[first_nl + 1:]
            if code.endswith("```"):
                code = code[: code.rfind("```")]
            page_files = {f"src/{page_file}": code.strip()}

        generated_files.update(page_files)

        page_cost = _estimate_cost(len(page_prompt) * 4, len(page_text) * 4)
        total_cost += page_cost
        cost_breakdown.append({
            "batch": f"Page: {page_name}",
            "files": len(page_files),
            "cost": round(page_cost, 4),
        })
        generation_log.append(
            f"Batch 4 ({page_name}): {len(page_files)} files — ${page_cost:.4f}"
        )
        logger.info(
            "Page %d/%d (%s): %d files, ~$%.4f",
            idx, total_pages, page_name, len(page_files), page_cost,
        )

        await _progress(
            f"Page {idx}/{total_pages} complete",
            f"{page_name} generated (${page_cost:.3f})",
        )

    # ══════════════════════════════════════════════════════════
    #  DONE — Send final summary
    # ══════════════════════════════════════════════════════════
    total_generated = len(generated_files)

    logger.info(
        "Code generation complete: %d files, total cost ~$%.4f",
        total_generated, total_cost,
    )

    await _progress(
        "Code generation complete!",
        f"{total_generated} files generated — estimated cost ${total_cost:.2f}",
    )

    # Send completion event to frontend
    if send_progress:
        try:
            await send_progress({
                "type": "generation_complete",
                "total_files": total_generated,
                "cost_breakdown": cost_breakdown,
                "total_cost": round(total_cost, 4),
                "generation_log": generation_log,
            })
        except Exception as exc:
            logger.warning("send_progress (generation_complete) failed: %s", exc)

    return {
        "files": generated_files,
        "cost_breakdown": cost_breakdown,
        "total_cost": round(total_cost, 4),
        "generation_log": generation_log,
    }


# ╔══════════════════════════════════════════════════════════════╗
# ║  IMPROVEMENT 1 — Validate structure before generation       ║
# ╚══════════════════════════════════════════════════════════════╝

async def validate_structure(
    structure: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate a project structure blueprint before code generation.

    Returns
    -------
    (is_valid, errors) — True if structure passes all checks,
    otherwise False with a list of human-readable error strings.
    """

    errors: list[str] = []

    # ── Required top-level fields ─────────────────────────────
    if not structure.get("app_name"):
        errors.append("No app name defined")

    if not structure.get("pages"):
        errors.append("No pages defined")

    if not structure.get("data_models"):
        errors.append("No data models defined")

    if not structure.get("navigation"):
        errors.append("No navigation structure defined")

    if not structure.get("file_structure"):
        errors.append("No file structure defined")

    # ── Pages validation ──────────────────────────────────────
    seen_routes: set[str] = set()
    for idx, page in enumerate(structure.get("pages", [])):
        page_label = page.get("name", f"Page #{idx + 1}")

        if not page.get("name"):
            errors.append(f"Page #{idx + 1} is missing a name")

        if not page.get("route"):
            errors.append(f"Page '{page_label}' is missing a route")
        else:
            route = page["route"]
            if route in seen_routes:
                errors.append(f"Duplicate route '{route}' on page '{page_label}'")
            seen_routes.add(route)

        if not page.get("file"):
            errors.append(f"Page '{page_label}' is missing a file path")

        if not page.get("components_used"):
            errors.append(f"Page '{page_label}' has no components listed")

        if not page.get("sections"):
            errors.append(f"Page '{page_label}' has no sections defined")

    # ── Data models validation ────────────────────────────────
    for idx, model in enumerate(structure.get("data_models", [])):
        model_label = model.get("name", f"Model #{idx + 1}")

        if not model.get("name"):
            errors.append(f"Data model #{idx + 1} is missing a name")

        if not model.get("fields"):
            errors.append(f"Data model '{model_label}' has no fields defined")
        else:
            for field in model["fields"]:
                if not field.get("name") or not field.get("type"):
                    errors.append(
                        f"Data model '{model_label}' has an incomplete field: {field}"
                    )

    # ── Navigation validation ─────────────────────────────────
    nav_items = structure.get("navigation", {}).get("items", [])
    if not nav_items:
        errors.append("Navigation has no items")
    for nav in nav_items:
        if not nav.get("label") or not nav.get("route"):
            errors.append(f"Navigation item missing label or route: {nav}")

    logger.info(
        "Structure validation: %s (%d errors)",
        "PASS" if not errors else "FAIL",
        len(errors),
    )

    return len(errors) == 0, errors


# ╔══════════════════════════════════════════════════════════════╗
# ║  IMPROVEMENT 2 — Retry failed Gemini generations            ║
# ╚══════════════════════════════════════════════════════════════╝

async def generate_with_retry(
    model: Any,
    prompt: str,
    generation_config: Any,
    max_retries: int = 2,
    send_progress: Optional[Callable] = None,
    label: str = "generation",
) -> str:
    """Call Gemini with automatic retry + prompt simplification.

    On first failure the prompt is truncated to the first 2000 chars
    and a shorter, more explicit instruction wrapper is added.

    Parameters
    ----------
    model:        genai.GenerativeModel instance.
    prompt:       full prompt text.
    generation_config:  genai.GenerationConfig.
    max_retries:  total attempts (default 2).
    send_progress: optional websocket callback.
    label:        human-readable batch name for logs.

    Returns
    -------
    str — raw response text from Gemini (stripped).

    Raises
    ------
    RuntimeError — if all retries are exhausted.
    """

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            current_prompt = prompt

            # On retry: simplify the prompt
            if attempt > 1:
                logger.warning(
                    "[%s] Retry %d/%d — simplifying prompt",
                    label, attempt, max_retries,
                )
                if send_progress:
                    try:
                        await send_progress({
                            "type": "research_progress",
                            "step": f"Retrying {label}...",
                            "found": f"Attempt {attempt}/{max_retries}",
                        })
                    except Exception:
                        pass

                # Simplify: keep core instruction, trim context
                current_prompt = (
                    f"RETRY — Previous attempt failed. "
                    f"Simplify your output and try again.\n\n"
                    f"{prompt[:2000]}\n\n"
                    f"Return valid output only. No markdown explanations."
                )

                await asyncio.sleep(2)  # brief backoff

            response = model.generate_content(
                current_prompt,
                generation_config=generation_config,
            )

            text = response.text.strip()
            if not text:
                raise RuntimeError("Gemini returned empty response")

            logger.info(
                "[%s] Attempt %d/%d succeeded (%d chars)",
                label, attempt, max_retries, len(text),
            )
            return text

        except Exception as exc:
            last_error = exc
            logger.error(
                "[%s] Attempt %d/%d failed: %s",
                label, attempt, max_retries, exc,
            )

    raise RuntimeError(
        f"[{label}] All {max_retries} attempts failed. "
        f"Last error: {last_error}"
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  IMPROVEMENT 3 — Verify generated files on disk             ║
# ╚══════════════════════════════════════════════════════════════╝

async def verify_generation(
    workspace_path: str,
    expected_files: list[str],
    send_progress: Optional[Callable] = None,
) -> tuple[bool, list[str], list[str]]:
    """Check that expected files actually exist on disk.

    Returns
    -------
    (all_present, present, missing)
    """

    present: list[str] = []
    missing: list[str] = []

    for filepath in expected_files:
        full_path = os.path.join(workspace_path, filepath)
        if os.path.exists(full_path):
            # Also check the file is not empty
            if os.path.getsize(full_path) > 0:
                present.append(filepath)
            else:
                missing.append(filepath)
                logger.warning("File exists but is empty: %s", full_path)
        else:
            missing.append(filepath)

    all_ok = len(missing) == 0

    if missing:
        logger.warning(
            "Verification: %d/%d files missing — %s",
            len(missing), len(expected_files), missing,
        )
        if send_progress:
            try:
                await send_progress({
                    "type": "warning",
                    "message": f"{len(missing)} files missing after generation",
                    "missing_files": missing,
                })
            except Exception:
                pass
    else:
        logger.info(
            "Verification: all %d files present ✓", len(expected_files)
        )

    return all_ok, present, missing


# ╔══════════════════════════════════════════════════════════════╗
# ║  IMPROVEMENT 4 — Save structure to workspace for resume     ║
# ╚══════════════════════════════════════════════════════════════╝

async def save_structure_to_workspace(
    workspace_path: str,
    structure: dict[str, Any],
    research: Optional[dict[str, Any]] = None,
) -> str:
    """Persist the project structure (and optionally research) to disk.

    Saves to ``<workspace>/.lucid/structure.json`` so that a failed
    generation can be resumed without re-running research + architecture.

    Returns the path to the saved file.
    """

    lucid_dir = os.path.join(workspace_path, ".lucid")
    os.makedirs(lucid_dir, exist_ok=True)

    # Save structure
    structure_path = os.path.join(lucid_dir, "structure.json")
    payload = {
        "structure": structure,
        "saved_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    if research:
        payload["research"] = research

    with open(structure_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    logger.info("Structure saved to %s", structure_path)
    return structure_path


async def load_structure_from_workspace(
    workspace_path: str,
) -> Optional[dict[str, Any]]:
    """Load a previously saved structure from disk.

    Returns None if no saved structure exists.
    """

    structure_path = os.path.join(workspace_path, ".lucid", "structure.json")
    if not os.path.exists(structure_path):
        logger.info("No saved structure found at %s", structure_path)
        return None

    try:
        with open(structure_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Loaded saved structure from %s", structure_path)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load structure from %s: %s", structure_path, exc)
        return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  IMPROVEMENT 5 — Resume failed generation (skip existing)   ║
# ╚══════════════════════════════════════════════════════════════╝

async def get_generation_progress(
    workspace_path: str,
    structure: dict[str, Any],
) -> dict[str, Any]:
    """Scan a workspace and determine which parts are already generated.

    Compares the blueprint against files on disk so a failed or
    interrupted generation can resume from where it left off.

    Returns
    -------
    dict with keys:
        completed_pages   – list of page names already generated
        remaining_pages   – list of page dicts still to generate
        completed_files   – list of all existing file paths
        missing_files     – list of all missing file paths
        percentage        – overall completion (0-100)
    """

    pages = structure.get("pages", [])
    file_structure = structure.get("file_structure", {})

    # ── Check pages ───────────────────────────────────────────
    completed_pages: list[str] = []
    remaining_pages: list[dict] = []

    for page in pages:
        page_file = page.get("file", "")
        full_path = os.path.join(workspace_path, "src", page_file)
        if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
            completed_pages.append(page.get("name", page_file))
        else:
            remaining_pages.append(page)

    # ── Check all files from file_structure ────────────────────
    def _collect_files(struct: Any, prefix: str = "") -> list[str]:
        """Flatten the nested file_structure into a list of paths."""
        paths: list[str] = []
        if isinstance(struct, list):
            for item in struct:
                paths.append(os.path.join(prefix, item) if prefix else item)
        elif isinstance(struct, dict):
            for key, value in struct.items():
                new_prefix = os.path.join(prefix, key) if prefix else key
                paths.extend(_collect_files(value, new_prefix))
        return paths

    all_expected = _collect_files(file_structure)
    completed_files: list[str] = []
    missing_files: list[str] = []

    for filepath in all_expected:
        full_path = os.path.join(workspace_path, filepath)
        if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
            completed_files.append(filepath)
        else:
            missing_files.append(filepath)

    # ── Compute percentage ────────────────────────────────────
    total = len(all_expected) if all_expected else 1
    percentage = round(len(completed_files) / total * 100, 1)

    progress = {
        "completed_pages": completed_pages,
        "remaining_pages": remaining_pages,
        "completed_files": completed_files,
        "missing_files": missing_files,
        "total_expected": len(all_expected),
        "percentage": percentage,
    }

    logger.info(
        "Generation progress: %.1f%% — %d/%d files, %d/%d pages remaining",
        percentage,
        len(completed_files),
        len(all_expected),
        len(remaining_pages),
        len(pages),
    )

    return progress
