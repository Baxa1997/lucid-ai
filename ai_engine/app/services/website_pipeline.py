"""Unified multi-page website pipeline (v2).

End-to-end orchestrator replacing the legacy Phase 1/2/3 monolithic flow
for consumer_website and related multi-page archetypes:

  Stage 1   — analyze_intent           (Gemini Flash)
  Stage 2   — domain + design research (Gemini, parallel)
  Stage 3   — visual_dna extraction    (Gemini Pro, single source of design truth)
  Stage 4   — website plan             (Gemini Flash → pages + sections per page)
  Stage 4.5 — data-model planning      (Gemini 3.1 Pro Preview → DataModel:
                                        tables for growing collections,
                                        singletons for fixed copy)
  Stage 4.6 — tenant provisioning      (Supabase: create per-project schema,
                                        apply generated DDL — non-fatal)
  Stage 4.7 — seed data                (Gemini 3.1 Pro Preview → realistic
                                        rows for every collection; inserted
                                        into the tenant schema — non-fatal)
  Stage 5   — DETERMINISTIC FOUNDATION (no LLM)
              globals.css from palette + fonts
              design-system.js tokens
              site.js (brand + tagline)
              navigation.js (routes from plan)
              Route shells for inner pages
  Stage 6 — PARALLEL CREATIVE        (Claude × N pages + header + footer)
              Each call receives the same visual_dna → cross-page cohesion
  Stage 7 — VERIFICATION (build check + import audit)  [next step]

Returns True on success (the dev server should be runnable), False otherwise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


def _content_separation_enabled() -> bool:
    """Shared feature flag (default ON). Mirrored from page_generator so
    Stage 5 foundation builders can branch on the same flag."""
    raw = os.environ.get("CONTENT_SEPARATION_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _data_model_planner_enabled() -> bool:
    """Stage 4.5 planner toggle (default ON). Set DATA_MODEL_PLANNER_ENABLED=0
    to skip the planner call entirely — useful for cheap re-runs during
    Stage 6 prompt iteration where the data_model isn't being consumed yet."""
    raw = os.environ.get("DATA_MODEL_PLANNER_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _plan_confirm_enabled() -> bool:
    """Stage 4-gate — show the plan card and wait for the user to confirm
    BEFORE launching parallel Claude page generation (which costs $1–2).
    Default ON. Flip to 0 for demos that want speed over a redirect option.
    """
    raw = os.environ.get("WEBSITE_PLAN_CONFIRM_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# Tenant helpers + their feature flags + the project_id UUID regex
# live in pipeline_tenant.py so the admin pipeline can reuse them
# without importing this website-specific module. Re-import the regex
# so existing in-module call sites (foundation builder, Stage 6
# data_model gate) keep working with their original local name.
from app.services.pipeline_tenant import (  # noqa: E402
    UUID_RE as _UUID_RE,
    provision_tenant_for_project,
    seed_tenant_for_project,
)


_EDITABLE_COMPONENT_JSX = '''"use client";
/* AUTO-GENERATED — Phase 4 editable wrapper. Do not edit by hand. */
import { Children, cloneElement, isValidElement } from "react";

/**
 * Editable — wraps an inline element with data-* attributes the dashboard
 * editor scans for. Renders the child element directly (no wrapper) when
 * possible so the resulting HTML stays valid (no <span> around <h1>).
 *
 *   <Editable path="hero.title" type="text">
 *     <h1>{content.hero.title}</h1>
 *   </Editable>
 */
export function Editable({ path, type = "text", children }) {
  const attrs = {
    "data-editable": "true",
    "data-editable-path": path,
    "data-editable-type": type,
  };
  // When there is exactly one element child we inject the data-attrs
  // directly onto it — keeps HTML valid (no span wrapping a block element).
  if (Children.count(children) === 1 && isValidElement(children)) {
    return cloneElement(children, attrs);
  }
  // Text nodes or multiple children → wrap in a span. Span is invalid
  // around block elements; the editor authoring rule is to pass a single
  // element child, which lands on the fast path above.
  return <span {...attrs}>{children}</span>;
}

export default Editable;
'''


def _build_editable_component() -> str:
    return _EDITABLE_COMPONENT_JSX


# ── Click-to-edit listener (Base44 flow) ────────────────────────────
#
# The website is rendered inside an iframe in the Lucid workspace. The
# workspace's "Edit" button posts ``lucid_set_edit_select_mode`` to the
# iframe; this listener intercepts the next click and posts the
# clicked element's editable-path back to the parent so the chat input
# can attach an ``editable_target`` to the user's next message.
#
# The component is mounted once in the root layout and is a no-op when
# select mode is off — no event listeners attached, no UI rendered.

# Bump this any time the listener body changes — the backfill in
# post_generation_fixer compares the marker in the on-disk file
# against this constant and rewrites the listener when older. Keeps
# already-generated projects in sync with new features (fuzzy
# fallback, hover affordances, etc.).
_EDIT_MODE_LISTENER_VERSION = 3

_EDIT_MODE_LISTENER_JSX = r'''"use client";
/* AUTO-GENERATED — Lucid click-to-edit listener. Do not edit by hand.
 *
 * This component is a SELECTION SENSOR. All UI (selection box, tag
 * badge, action toolbar, inline-input chat) lives in the parent
 * workspace — the iframe just streams events to it via postMessage.
 *
 * Events sent to parent (window.parent.postMessage):
 *
 *   lucid_element_hover        — cursor moved over a pickable element
 *     { type, rect, tag, hasEditablePath }
 *
 *   lucid_element_hover_clear  — cursor left all pickables
 *     { type }
 *
 *   lucid_element_selected     — user clicked. Selection STAYS active
 *                                in the iframe (border stays) until
 *                                the parent sends lucid_clear_selection
 *                                or the user picks a different element.
 *     { type, rect, tag, text, path, editableType, fuzzy, className, src }
 *
 *   lucid_selection_rect_update — selection bounds changed because the
 *                                 user scrolled or the viewport resized
 *     { type, rect }
 *
 *   lucid_element_deselected   — user pressed Esc inside iframe
 *     { type }
 *
 * Events received from parent:
 *
 *   lucid_set_edit_select_mode — toggle the listener on/off
 *     { type, enabled }
 *
 *   lucid_clear_selection      — clear the persistent selection
 *     { type }
 *
 * Selection works in two tiers:
 *   1. Element (or ancestor) has data-editable-path → high-confidence
 *      wrapper flow.
 *   2. Otherwise → fuzzy fallback by tag + text + className + src.
 */
import { useEffect, useRef, useState } from "react";

const _LUCID_PICKABLE_TAGS = new Set([
  "H1", "H2", "H3", "H4", "H5", "H6",
  "P", "BLOCKQUOTE", "FIGCAPTION",
  "A", "BUTTON",
  "IMG",
  "LI",
  "SECTION", "ARTICLE", "HEADER", "FOOTER", "NAV", "ASIDE",
  "FIGURE",
]);

function _lucidFindEditableAncestor(start) {
  let node = start;
  while (node && node !== document.body) {
    if (
      node.nodeType === 1
      && node.getAttribute
      && node.getAttribute("data-editable-path")
    ) {
      return node;
    }
    node = node.parentNode;
  }
  return null;
}

function _lucidFindPickableAncestor(start) {
  let node = start;
  while (node && node !== document.body) {
    if (node.nodeType === 1 && _LUCID_PICKABLE_TAGS.has(node.tagName)) {
      return node;
    }
    node = node.parentNode;
  }
  return null;
}

function _lucidRectOf(el) {
  // getBoundingClientRect gives viewport-relative coordinates, which
  // is what the parent needs to position its overlay over the iframe.
  // We send a plain object because DOMRect doesn't serialize cleanly
  // across postMessage on some browsers.
  if (!el || !el.getBoundingClientRect) return null;
  const r = el.getBoundingClientRect();
  return {
    x: r.x,
    y: r.y,
    width: r.width,
    height: r.height,
  };
}

function _lucidBuildSelectedPayload(el) {
  const rect = _lucidRectOf(el);
  const editablePath = el.getAttribute && el.getAttribute("data-editable-path");
  const tagLower = (el.tagName || "").toLowerCase();
  const text = (el.textContent || "").trim().slice(0, 200);

  // Preferred — Editable-wrapped element with a known content path.
  if (editablePath) {
    return {
      type: "lucid_element_selected",
      rect,
      tag: tagLower,
      text,
      path: editablePath,
      editableType: el.getAttribute("data-editable-type") || "text",
      fuzzy: false,
    };
  }

  // Fuzzy fallback — identify by tag + visible text + className + src.
  const className = (el.getAttribute && el.getAttribute("class")) || "";
  const src = (el.getAttribute && el.getAttribute("src")) || "";
  let displayPath;
  if (tagLower === "img") {
    displayPath = src ? `img:${src.split("/").pop()}` : "img";
  } else if (text) {
    displayPath = `${tagLower}:${text.slice(0, 40)}${text.length > 40 ? "…" : ""}`;
  } else {
    displayPath = tagLower;
  }
  return {
    type: "lucid_element_selected",
    rect,
    tag: tagLower,
    text,
    path: displayPath,
    editableType: tagLower === "img" ? "image_url" : "text",
    fuzzy: true,
    className: className.slice(0, 240),
    src,
  };
}

export default function EditModeListener() {
  const [active, setActive] = useState(false);
  const hoveredRef = useRef(null);
  // Persistent selection — when set, click-to-pick stays active so the
  // user can re-pick, but the iframe also keeps a selected outline.
  const selectedRef = useRef(null);

  // Send a typed message to the parent, swallowing any (rare) errors so
  // the page never breaks because of a closed parent context.
  const post = (msg) => {
    try {
      window.parent && window.parent.postMessage(msg, "*");
    } catch {
      /* no-op */
    }
  };

  // ── Parent → iframe message bus ───────────────────────────
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onMessage = (e) => {
      const data = e && e.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "lucid_set_edit_select_mode") {
        setActive(Boolean(data.enabled));
      } else if (data.type === "lucid_clear_selection") {
        if (selectedRef.current) {
          selectedRef.current.classList.remove("lucid-edit-selected");
          selectedRef.current = null;
        }
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // ── Click + hover capture + Esc + scroll/resize rebroadcast ──
  useEffect(() => {
    if (!active || typeof document === "undefined") return undefined;

    const HOVER = "lucid-edit-hover";
    const SELECTED = "lucid-edit-selected";

    const clearSelected = () => {
      if (selectedRef.current) {
        selectedRef.current.classList.remove(SELECTED);
        selectedRef.current = null;
      }
    };

    const onClick = (e) => {
      const editable = _lucidFindEditableAncestor(e.target);
      const target = editable || _lucidFindPickableAncestor(e.target);
      if (!target) return;
      e.preventDefault();
      e.stopPropagation();

      // Re-selecting the same node? Just refresh the rect — keeps the
      // outline anchored if the user clicked again after a layout shift.
      const same = target === selectedRef.current;
      clearSelected();
      target.classList.add(SELECTED);
      selectedRef.current = target;
      post(_lucidBuildSelectedPayload(target));
      // Clear hover tracking — the selected outline now stands in for it
      // and we don't want both highlights on the same element.
      if (hoveredRef.current) {
        hoveredRef.current.classList.remove(HOVER);
        hoveredRef.current = null;
      }
      // `same` exists only to silence the linter — we want the variable
      // available for future debounce logic.
      void same;
    };

    const onMouseOver = (e) => {
      const editable = _lucidFindEditableAncestor(e.target);
      const target = editable || _lucidFindPickableAncestor(e.target);
      if (!target || target === hoveredRef.current) return;
      // Don't double-highlight the currently selected element.
      if (target === selectedRef.current) return;
      if (hoveredRef.current) hoveredRef.current.classList.remove(HOVER);
      target.classList.add(HOVER);
      hoveredRef.current = target;
      post({
        type: "lucid_element_hover",
        rect: _lucidRectOf(target),
        tag: (target.tagName || "").toLowerCase(),
        hasEditablePath: Boolean(editable),
      });
    };

    const onMouseOut = (e) => {
      if (!hoveredRef.current) return;
      if (e.relatedTarget && document.body.contains(e.relatedTarget)) return;
      hoveredRef.current.classList.remove(HOVER);
      hoveredRef.current = null;
      post({ type: "lucid_element_hover_clear" });
    };

    const onKey = (e) => {
      if (e.key === "Escape") {
        clearSelected();
        post({ type: "lucid_element_deselected" });
      }
    };

    // Throttle scroll/resize rebroadcasts to the next animation frame so
    // we don't flood the parent during fast scrolls.
    let raf = 0;
    const onScrollOrResize = () => {
      if (!selectedRef.current) return;
      if (raf) return;
      raf = window.requestAnimationFrame(() => {
        raf = 0;
        if (!selectedRef.current) return;
        post({
          type: "lucid_selection_rect_update",
          rect: _lucidRectOf(selectedRef.current),
        });
      });
    };

    document.addEventListener("click", onClick, true);
    document.addEventListener("mouseover", onMouseOver, true);
    document.addEventListener("mouseout", onMouseOut, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);

    return () => {
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("mouseover", onMouseOver, true);
      document.removeEventListener("mouseout", onMouseOut, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
      if (raf) window.cancelAnimationFrame(raf);
      if (hoveredRef.current) {
        hoveredRef.current.classList.remove(HOVER);
        hoveredRef.current = null;
      }
      clearSelected();
    };
  }, [active]);

  if (!active) return null;
  // The iframe-side outline is a thin assist for the user; the rich
  // toolbar / tag-badge UI is rendered by the parent workspace.
  return (
    <style>{`
      .lucid-edit-hover {
        outline: 2px solid rgba(37, 99, 235, 0.75) !important;
        outline-offset: 2px;
        cursor: crosshair !important;
        transition: outline-color 120ms ease;
      }
      .lucid-edit-selected {
        outline: 2px solid rgba(37, 99, 235, 1) !important;
        outline-offset: 2px;
        background-color: rgba(37, 99, 235, 0.06) !important;
      }
      body * { cursor: crosshair !important; }
    `}</style>
  );
}
'''


def _build_edit_mode_listener_component() -> str:
    """Return the listener source with the version marker injected so
    the backfill can detect stale on-disk copies and rewrite them."""
    marker = f"/* LUCID_LISTENER_VERSION={_EDIT_MODE_LISTENER_VERSION} */\n"
    # Insert the marker on the line right after the leading "use client"
    # directive so it shows up in the first 3 lines and the backfill
    # only needs to read a tiny prefix to detect the version.
    head, _, rest = _EDIT_MODE_LISTENER_JSX.partition("\n")
    return head + "\n" + marker + rest


def _current_edit_mode_listener_version() -> int:
    """Expose the listener version so the backfill in
    ``post_generation_fixer`` can compare against on-disk copies."""
    return _EDIT_MODE_LISTENER_VERSION


async def _send(websocket, kind: str, message: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": kind, "message": message})
    except Exception:
        pass




async def _phase(websocket, phase: int, title: str, desc: str, status: str) -> None:
    if websocket is None:
        return
    try:
        await websocket.send_json({
            "type": "task_phase",
            "phase": phase, "title": title,
            "description": desc, "status": status,
        })
    except Exception:
        pass


async def _emit_website_plan_and_wait(
    *,
    plan: dict[str, Any],
    description: str,
    visual_dna: dict[str, Any] | None,
    websocket: Any,
    chat_session_id: str,
) -> bool:
    """Stage 4 gate — show the plan card derived from the Gemini plan, then
    wait for the user to click "Looks Good" before launching the expensive
    Stage 5 parallel Claude codegen.

    Returns:
      True   — user confirmed (or no UI to confirm with). Caller continues.
      False  — user rejected, timed out, or send failed. Caller MUST abort
               the pipeline without spending Anthropic credits. If the
               rejection carried a correction, `websocket._plan_correction`
               is set so the orchestrator can re-run with the new prompt.
    """
    if websocket is None:
        return True

    import asyncio
    from app.services.project_generator import (
        register_plan_confirmation,
        pending_plan_confirmations,
        save_persisted_plan,
        clear_persisted_plan,
        _confirmation_key,
        PLAN_CONFIRM_TIMEOUT_SECONDS,
    )

    brand = dict(plan.get("brand") or {})
    pages = list(plan.get("pages") or [])
    brand_name = brand.get("name") or "your website"
    tagline = brand.get("tagline") or ""

    # Frontend's PlanBubble renders this list as the page outline.
    # Include `route` so the frontend label rule classifies this as a
    # multi-page plan ("Pages" header) instead of a single-page landing
    # ("Sections" header). See MessageBubble.js:185 — the deciding signal
    # is whether any page object carries a route/path field.
    page_items: list[dict[str, str]] = []
    for p in pages:
        route = (p.get("route") or "").strip()
        title = (p.get("title") or route.lstrip("/").title() or "Page").strip()
        purpose = (p.get("purpose") or "").strip()
        page_items.append({
            "name": title[:80],
            "desc": purpose[:140],
            "route": route or "/",
        })

    vdna = visual_dna or {}
    design_bits: list[str] = []
    if vdna.get("primary_color"):
        design_bits.append(f"primary {vdna['primary_color']}")
    head_font = (vdna.get("typography") or {}).get("heading") or vdna.get("heading_font")
    body_font = (vdna.get("typography") or {}).get("body") or vdna.get("body_font")
    if head_font or body_font:
        if head_font and body_font and head_font != body_font:
            design_bits.append(f"{head_font} + {body_font}")
        else:
            design_bits.append(head_font or body_font)
    intensity = vdna.get("cultural_intensity") or ""
    if intensity:
        design_bits.append(f"{intensity} vibe")
    design_line = " · ".join(b for b in design_bits if b)

    plan_data = {
        "intro": (
            f"I'll build **{brand_name}** — {tagline}. Here's my plan:"
            if tagline else
            f"I'll build **{brand_name}**. Here's my plan:"
        ),
        "description": description[:280],
        "pages": page_items,
        "entities": [],
        "design": design_line,
        "requiresConfirmation": True,
    }

    try:
        await websocket.send_json({
            "type": "chat_message",
            "role": "agent",
            "messageType": "plan",
            "planData": plan_data,
        })
        await save_persisted_plan(chat_session_id, plan_data, task=description)
    except Exception as exc:
        logger.warning("website_pipeline: plan emit failed — %s", exc)
        # If we can't show the plan, skip the gate rather than block forever.
        return True

    try:
        await websocket.send_json({
            "type": "plan_awaiting_confirmation",
            "message": "Review your plan above and click 'Looks Good' to start building.",
        })
    except Exception:
        pass

    gate_key = _confirmation_key(websocket, chat_session_id)
    plan_future = register_plan_confirmation(gate_key)
    try:
        confirmation = await asyncio.wait_for(
            plan_future, timeout=PLAN_CONFIRM_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.info("website_pipeline: plan confirmation timed out")
        pending_plan_confirmations.pop(gate_key, None)
        await clear_persisted_plan(chat_session_id)
        await _send(
            websocket, "warning",
            "⏱️ Plan expired — send your message again to rebuild it.",
        )
        return False
    except Exception as exc:
        logger.warning("website_pipeline: plan wait error — %s", exc)
        pending_plan_confirmations.pop(gate_key, None)
        await clear_persisted_plan(chat_session_id)
        return False

    if not confirmation.get("confirmed", True):
        correction = confirmation.get("correction", "") or ""
        await clear_persisted_plan(chat_session_id)
        if correction:
            websocket._plan_correction = correction
            await _send(websocket, "progress", "Re-researching with your changes…")
            logger.info("website_pipeline: plan rejected with correction")
        else:
            await _send(websocket, "warning", "Generation canceled.")
            logger.info("website_pipeline: plan rejected without correction")
        return False

    await clear_persisted_plan(chat_session_id)
    await _send(websocket, "progress", "Plan confirmed — building your site now…")
    return True


async def run_website_pipeline(
    *,
    description: str,
    classification: dict[str, Any],
    workspace_path: str,
    validated: dict[str, Any],
    websocket: Any = None,
    chat_session_id: str = "",
) -> bool:
    """Run the full website pipeline. Returns True on success.

    Mirrors the API surface of `run_landing_pipeline` so the orchestrator
    can dispatch to either based on layout_archetype.
    """
    anthropic_key = validated.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        await _send(websocket, "error", "Service is missing its API key — please contact support.")
        return False

    # ── Stage 0.5: Purpose classification ───────────────────────────
    # Runs BEFORE intent so downstream stages know what the site is FOR
    # (recruitment vs ecommerce vs lead-gen), not just what industry it's
    # in. Pure additive — does not modify any existing stage's inputs.
    await _phase(websocket, 1, "Preparing workspace", "Workspace ready", "done")

    from app.services.purpose_classifier import classify_purpose
    from knowledge.loader import extract_clarify_context

    clarity_answers, clean_description = extract_clarify_context(description)
    gemini_key = validated.get("gemini_api_key") or os.environ.get("GOOGLE_API_KEY", "")

    purpose_data = await classify_purpose(
        user_prompt=clean_description,
        clarity_answers=clarity_answers or {},
        gemini_key=gemini_key,
    )
    logger.info(
        "Purpose classified: %s (%d%%) — industry=%r audience=%s named_roles=%s",
        purpose_data["primary_purpose"], purpose_data["confidence"],
        purpose_data["industry"], purpose_data["target_audience"],
        purpose_data["named_roles"],
    )
    await _send(
        websocket, "progress",
        "Understanding what you want to build…",
    )

    # ── Stage 1: Intent — CACHED ────────────────────────────────────
    # Cached not just to save the ~$0.01 Flash call but because the
    # downstream Stage 3 cache key includes `intent`. Without caching
    # intent, Gemini non-determinism produces a slightly different
    # `intent` dict every run, which would force a Stage 3 miss even
    # when Stage 2 is a hit. Caching here keeps the whole tail stable.
    from app.services.pipeline_cache import pipeline_cache
    project_id = chat_session_id or "_session_none_"

    await _phase(websocket, 3, "Researching project", "Analyzing intent + culture…", "active")
    await _send(websocket, "progress", "Studying your brand…")

    from app.services.landing_intent import analyze_intent

    cached_intent = pipeline_cache.get(
        project_id, "intent", clean_description, classification,
    )
    if cached_intent is not None:
        intent = cached_intent
        pass  # cache-hit; no user-facing message needed
    else:
        try:
            intent = await analyze_intent(
                clean_description, classification,
                websocket=websocket, timeout_s=60.0,
            )
        except Exception as exc:
            logger.error("website_pipeline: intent failed — %s", exc, exc_info=True)
            await _send(websocket, "error", "Couldn't understand your request — please try again.")
            return False
        pipeline_cache.set(
            project_id, "intent", intent, clean_description, classification,
        )

    logger.info(
        "website_pipeline: intent ok — category=%s geo=%s personality=%s",
        intent.get("business_category"), intent.get("geographic_specifics"),
        intent.get("brand_personality"),
    )

    # ── Stage 2: Research (parallel) — CACHED per project ───────────
    # Cache key is (prompt + clarity + purpose). Anything that changes
    # the research question changes the hash → automatic invalidation.
    await _send(websocket, "progress", "Researching your industry…")

    from app.services.landing_domain_research import run_domain_research
    from app.services.landing_design_research import run_design_research

    cached_research = pipeline_cache.get(
        project_id, "research",
        clean_description, clarity_answers, purpose_data,
    )
    if cached_research is not None:
        # cache-hit; no user-facing message
        domain_res, design_res = cached_research
    else:
        try:
            domain_res, design_res = await asyncio.gather(
                run_domain_research(intent, timeout_s=120.0, purpose_data=purpose_data),
                run_design_research(intent, timeout_s=120.0, purpose_data=purpose_data),
            )
        except Exception as exc:
            logger.error("website_pipeline: research failed — %s", exc, exc_info=True)
            await _send(websocket, "error", "Couldn't research your industry — please try again.")
            return False
        pipeline_cache.set(
            project_id, "research", (domain_res, design_res),
            clean_description, clarity_answers, purpose_data,
        )

    # ── Stage 3: Visual_DNA + Voice — CACHED per project ────────────
    # Cache key is (research + intent + purpose). When research is a
    # cache hit, the signals cache will be a hit too — saving the full
    # Pro extract call (the most expensive single step in the pipeline).
    await _send(websocket, "progress", "Designing the look and feel…")

    from app.services.landing_research_extract import extract_research_signals

    cached_signals = pipeline_cache.get(
        project_id, "signals",
        domain_res, intent, purpose_data,
    )
    if cached_signals is not None:
        # cache-hit; no user-facing message
        signals = cached_signals
    else:
        try:
            signals = await extract_research_signals(
                intent, domain_res, design_res, timeout_s=180.0,
                purpose_data=purpose_data,
            )
        except Exception as exc:
            logger.error("website_pipeline: visual_dna extract failed — %s", exc, exc_info=True)
            await _send(websocket, "error", "Couldn't design the look and feel — please try again.")
            return False
        pipeline_cache.set(
            project_id, "signals", signals,
            domain_res, intent, purpose_data,
        )

    visual_dna = signals.get("visual_dna") or {}
    anatomies = visual_dna.get("section_anatomies") or {}
    logger.info(
        "website_pipeline: visual_dna ok — intensity=%s anatomies=%d motifs=%d",
        visual_dna.get("cultural_intensity"),
        len(anatomies), len(visual_dna.get("decorative_motifs") or []),
    )
    if not anatomies:
        # Don't hard-fail — orchestrator will produce generic anatomies, still works
        logger.warning("website_pipeline: visual_dna has no anatomies — pages will be more generic")

    # ── Derive per-section codegen context that landing gets for free ──
    # Landing's `enrich_brief_with_signals` + `build_landing_brief` populate
    # voice/purpose/design_system/design_tokens on the brief, which then
    # flow into every section call. Website never built a brief, so until
    # now per-section codegen was running with `voice_context=None` and
    # `design_system={}` — the model loses ~60% of project-specific signal
    # and falls back to generic SaaS defaults. Derive those four channels
    # here from data we already have (signals + intent + visual_dna).
    section_voice_context = _derive_voice_context(signals, intent)
    section_design_system = _derive_design_system_from_dna(visual_dna)
    section_design_tokens = _build_design_tokens_for_website(section_design_system)
    section_personality = dict(visual_dna.get("personality") or {})
    logger.info(
        "website_pipeline: per-section context — voice_phrases=%d industry_terms=%d "
        "regional_refs=%d white_space=%d purpose=%s design_system=%s tokens=%d",
        len(section_voice_context.get("voice_phrases") or []),
        len(section_voice_context.get("industry_terms") or []),
        len(section_voice_context.get("regional_refs") or []),
        len(section_voice_context.get("white_space") or []),
        "yes" if section_voice_context.get("purpose_directive") else "no",
        ",".join(f"{k}={v}" for k, v in section_design_system.items()),
        len(section_design_tokens),
    )

    # Persist visual_dna to chat_sessions so linked admin projects can
    # inherit the parent's brand identity verbatim. Non-fatal — a failed
    # write only affects future linked-admin generations, never this run.
    if visual_dna and _UUID_RE.match(project_id or ""):
        try:
            from app.supabase_client import managed_admin_client
            async with managed_admin_client() as _admin:
                await (
                    _admin.table("chat_sessions")
                    .update({"visual_dna": visual_dna})
                    .eq("id", project_id)
                    .execute()
                )
            logger.info(
                "website_pipeline: persisted visual_dna to chat_sessions (%d keys)",
                len(visual_dna),
            )
        except Exception as exc:
            logger.warning(
                "website_pipeline: visual_dna persist failed (non-fatal) — %s", exc,
            )

    # ── Stage 4: Build plan ─────────────────────────────────────────
    await _send(websocket, "progress", "Planning your pages…")

    from app.services.website_plan import build_website_plan
    try:
        plan = await build_website_plan(
            clean_description, intent, visual_dna, timeout_s=60.0,
            purpose_data=purpose_data, websocket=websocket,
        )
    except Exception as exc:
        logger.error("website_pipeline: plan failed — %s", exc, exc_info=True)
        await _send(websocket, "error", "Couldn't plan your pages — please try again.")
        return False

    pages = plan.get("pages") or []
    if not pages:
        await _send(websocket, "error", "Couldn't plan any pages — try a more specific description.")
        return False

    page_routes = [p.get("route") for p in pages]
    logger.info(
        "website_pipeline: plan ok — brand=%r pages=%d routes=%s",
        plan["brand"]["name"], len(pages), page_routes,
    )
    _page_names = ", ".join(
        (p.get("title") or p.get("route") or "").lstrip("/") or "Home"
        for p in pages
    )
    await _send(websocket, "progress", f"Pages: {_page_names}")

    # ── Stage 4 gate — show plan, wait for user confirmation ────────
    # Inserted to match landing + admin V2 behavior. Blocks Stage 5
    # (parallel Claude codegen, ~$1–2) until the user clicks "Looks Good"
    # in the PlanBubble. Disable with WEBSITE_PLAN_CONFIRM_ENABLED=0 for
    # demos that want speed over a redirect option.
    if _plan_confirm_enabled():
        _confirmed = await _emit_website_plan_and_wait(
            plan=plan,
            description=description,
            visual_dna=visual_dna,
            websocket=websocket,
            chat_session_id=chat_session_id,
        )
        if not _confirmed:
            logger.info(
                "website_pipeline: aborted at plan gate (no Anthropic credits spent)",
            )
            return False

    # ── Stage 4.5: Data-model planning ──────────────────────────────
    # Decides which sections need Supabase-backed collections (tables
    # that grow + are edited over time) vs which stay as JSON singletons.
    # Output is a validated `DataModel`; the SQL generator (Step 1.3)
    # turns it into per-tenant CREATE TABLE statements when the project
    # gets provisioned. Stage 6 codegen will read this in a follow-up
    # step to know which sections to wire to Supabase instead of static
    # JSON. Non-fatal: planner returns empty DataModel on Gemini failure,
    # site still ships with all-JSON content.
    # Entry log — confirms in production whether Stage 4.5 was reached
    # AND with what kind of project_id (real UUID vs dev placeholder).
    logger.info(
        "[%s] Stage 4.5 ENTRY: project_id type=%s, value=%s",
        project_id, type(project_id).__name__, project_id,
    )
    data_model = None
    if not _data_model_planner_enabled():
        logger.warning(
            "[%s] Stage 4.5 SKIPPED: reason=DATA_MODEL_PLANNER_ENABLED=0 "
            "(downstream Stages 4.6 + 4.7 will also skip — site will use JSON only)",
            project_id,
        )
    if _data_model_planner_enabled():
        await _send(websocket, "progress",
                    "Designing your data structure…")
        from app.services.data_model_planner import plan_data_model

        cached_dm = pipeline_cache.get(
            project_id, "data_model", plan, intent, purpose_data,
        )
        if cached_dm is not None:
            data_model = cached_dm
            # cache-hit; no user-facing message
        else:
            try:
                data_model = await plan_data_model(
                    website_plan=plan,
                    intent=intent,
                    purpose_data=purpose_data,
                    visual_dna=visual_dna,
                    gemini_key=gemini_key,
                    project_id=project_id,
                )
                pipeline_cache.set(
                    project_id, "data_model", data_model,
                    plan, intent, purpose_data,
                )
            except Exception as exc:
                # Planner already swallows Gemini errors and returns an
                # empty DataModel — anything that escapes here is a real
                # bug (import error, etc). Log + continue with None so
                # downstream stages keep working.
                logger.error(
                    "website_pipeline: data_model_planner threw — %s", exc,
                    exc_info=True,
                )
                data_model = None

        if data_model is not None:
            logger.info(
                "website_pipeline: data_model ok — tables=%d singletons=%d",
                len(data_model.tables), len(data_model.singletons),
            )
            if data_model.tables:
                _table_names = ", ".join(t.name for t in data_model.tables)
                await _send(
                    websocket, "progress",
                    f"Will manage: {_table_names}",
                )

    # ── Stage 4.6: Tenant provisioning + SQL apply ──────────────────
    tenant_schema = await provision_tenant_for_project(
        data_model=data_model,
        project_id=project_id,
        websocket=websocket,
    )

    # ── Stage 4.7: Seed data generation + INSERT ────────────────────
    # Strict downstream of 4.6 — only fires when a real tenant_schema
    # came back. Failures don't break the pipeline; empty tables fall
    # back to JSON content in the codegen layer (Phase 2.3.C).
    await seed_tenant_for_project(
        data_model=data_model,
        tenant_schema=tenant_schema,
        website_plan=plan,
        intent=intent,
        purpose_data=purpose_data,
        gemini_key=gemini_key,
        project_id=project_id,
        websocket=websocket,
    )

    # ── Stage 4.8: Per-page content brief (closes the landing-parity gap) ──
    # Active website_plan only emits {type, layout_hint, archetype} per
    # section. Without per-section CONTENT, downstream Claude calls have
    # to invent headlines + items + photo queries from voice_context +
    # brand_name alone — the documented root cause of "every website
    # feels generic." This stage runs one Gemini Flash call PER page
    # in parallel that fills the section_schemas.py content shape:
    # headline, subheadline, items[], image_queries[], cta, etc.
    #
    # Merged result lands directly in plan.pages[].sections so:
    #   • Stage 5.5 image_binding sees section.image_queries → photo diversity
    #   • Stage 5.6 website_content writes the rich landing.json
    #   • Stage 6 page_generator's per-section Claude calls receive the
    #     brief content in their SECTION SPEC (json.dumps(section) in the
    #     user prompt surfaces every field automatically)
    #
    # Best-effort: any per-page failure leaves that page's plan untouched,
    # so the pipeline never blocks on a brief miss. Flip off entirely via
    # WEBSITE_CONTENT_BRIEF_ENABLED=0 if Gemini Flash is unreachable.
    if _content_brief_expansion_enabled():
        try:
            from app.services.expand_page_brief import expand_pages_many
            _project_brief_input, _page_inputs = _build_brief_input_for_expand(
                plan=plan, intent=intent, visual_dna=visual_dna or {},
                voice_context=section_voice_context,
                design_tokens=section_design_tokens,
                purpose_data=purpose_data,
            )
            logger.info(
                "Stage 4.8: expanding %d pages via Gemini Flash "
                "(brief=brand=%r purpose=%r industry=%r)",
                len(_page_inputs),
                _project_brief_input["brand"]["name"],
                _project_brief_input["primary_purpose"],
                _project_brief_input["industry"],
            )
            await _send(websocket, "progress",
                        "Writing copy for each page…")
            _briefs = await expand_pages_many(
                pages=_page_inputs,
                project_brief=_project_brief_input,
                websocket=websocket,
                concurrency=4,
                timeout_s=60.0,
            )
            _ok = sum(1 for b in _briefs if isinstance(b, dict) and b.get("sections"))
            logger.info(
                "Stage 4.8: %d/%d pages got brief content from Gemini",
                _ok, len(_page_inputs),
            )
            pages_filled, sections_filled, sections_kept = _merge_briefs_into_plan(plan, _briefs)
            logger.info(
                "Stage 4.8: merged briefs into plan — "
                "pages_filled=%d sections_filled=%d sections_no_brief=%d",
                pages_filled, sections_filled, sections_kept,
            )
            # Refresh `pages` since we mutated `plan` in place.
            pages = plan.get("pages") or []
        except Exception as exc:
            # Brief expansion is the whole point of this stage but we
            # never want to block the pipeline on it. Log + continue with
            # the bare plan — image_binding will use industry defaults and
            # Claude will invent content (legacy behavior).
            logger.warning(
                "Stage 4.8: content brief expansion failed (non-fatal) — %s",
                exc, exc_info=True,
            )
    else:
        logger.info(
            "Stage 4.8 SKIPPED: WEBSITE_CONTENT_BRIEF_ENABLED=0 — sections will "
            "ship without brief content; expect generic copy + repeated photos.",
        )

    # ── Stage 5: Deterministic foundation ───────────────────────────
    await _send(websocket, "progress", "Setting up the project…")
    design_signal = signals.get("design") or {}
    foundation_files = _build_foundation_files(
        plan, visual_dna,
        design_signal=design_signal,
        data_model=data_model,
        tenant_schema=tenant_schema,
        project_id=project_id,
    )
    # globals.css gets its own builder because it has Tailwind directives
    # and template-shaped HSL var blocks that aren't a plain key=value dict
    try:
        globals_css = _build_globals_css(design_signal)
        if globals_css:
            foundation_files["src/app/globals.css"] = globals_css
    except Exception as exc:
        logger.warning("website_pipeline: globals.css build failed (non-fatal) — %s", exc)
    foundation_written = 0
    for rel_path, content in foundation_files.items():
        try:
            abs_path = os.path.join(workspace_path, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            foundation_written += 1
        except Exception as exc:
            logger.warning("website_pipeline: failed to write %s — %s", rel_path, exc)
    logger.info("website_pipeline: foundation written — %d files", foundation_written)

    # ── Route-group cleanup ────────────────────────────────────────────
    # The Next.js skeleton ships `src/app/(marketing)/page.js` as a stub.
    # Once page_generator writes `src/app/page.js` directly, both files
    # resolve to "/" → Next.js build fails with a route conflict and the
    # local-preview hangs at "Preparing Preview" (BuildValidator hits the
    # 180s ceiling). Wipe any route-group dirs in app/ — we don't use them.
    import shutil as _shutil
    app_dir = os.path.join(workspace_path, "src", "app")
    if os.path.isdir(app_dir):
        for entry in os.listdir(app_dir):
            full = os.path.join(app_dir, entry)
            if os.path.isdir(full) and entry.startswith("(") and entry.endswith(")"):
                try:
                    _shutil.rmtree(full, ignore_errors=True)
                    logger.info("website_pipeline: removed conflicting route group %s", entry)
                except OSError as exc:
                    logger.warning("website_pipeline: route-group cleanup failed for %s — %s", entry, exc)

    # ── Stage 5.5: Image binding ────────────────────────────────────
    # Resolve every section that needs imagery to a real Unsplash URL
    # BEFORE Claude sees the page — prevents hallucinated /images/ paths.
    await _send(websocket, "progress", "Finding photos for your pages…")
    from app.services.image_binding import (
        bind_page_images, clear_image_cache,
    )
    clear_image_cache()  # don't leak across pipeline runs
    industry = (purpose_data.get("industry") or intent.get("business_category") or "general").strip()
    try:
        image_bindings = await asyncio.gather(*[
            bind_page_images(page, industry, purpose_data, visual_dna)
            for page in pages
        ])
    except Exception as exc:
        logger.warning("website_pipeline: image binding threw (non-fatal) — %s", exc)
        image_bindings = [{} for _ in pages]
    page_images = {
        (p.get("route") or "/").strip(): img
        for p, img in zip(pages, image_bindings)
    }
    total_imgs = sum(
        sum(len(v) for v in page_imgs.values())
        for page_imgs in page_images.values()
    )
    logger.info(
        "Stage 5.5: Binding images — %d pages bound, %d images total",
        len(page_images), total_imgs,
    )
    await _send(
        websocket, "progress",
        "Photos ready.",
    )

    # ── Stage 5.6: Write runtime content (src/content/landing.json) ──
    # The per-section codegen path emits components that
    # `import landing from "@/content/landing.json"`. Without this file
    # `landing` is undefined at runtime, sections render empty, and no
    # images bind. Written BEFORE codegen so Claude's SECTION SPEC
    # (in the prompt) and the runtime data agree on shape.
    try:
        from app.services.website_content import write_website_landing_content
        _, _content = write_website_landing_content(
            workspace_path, plan, visual_dna or {}, page_images,
            intent=intent,
        )
        logger.info(
            "Stage 5.6: runtime content written — %d sections across %d pages",
            len(_content.get("sections") or []), len(pages),
        )
    except Exception as exc:
        logger.warning(
            "website_pipeline: runtime content write failed (non-fatal) — %s", exc,
        )

    # ── Stage 5.7: Per-page anatomy refinement (Option C) ────────────
    # Specializes each page's section anatomies for its specific purpose
    # so Hero on /home ≠ Hero on /about ≠ Hero on /pricing. All pages
    # refined in parallel via Gemini Flash (~5-10s wall, ~$0.02/page).
    # Falls back to global anatomies for any page that fails — never
    # blocks the pipeline. Disable with WEBSITE_PAGE_ANATOMY_REFINER_ENABLED=0.
    from app.services.page_anatomy_refiner import refine_anatomies_for_all_pages
    await _send(websocket, "progress", "Tailoring each page's design…")
    try:
        await refine_anatomies_for_all_pages(
            pages=pages,
            visual_dna=visual_dna or {},
            brand=plan.get("brand") or {},
        )
    except Exception as exc:
        # Refinement is best-effort. Log and continue with global anatomies.
        logger.warning("website_pipeline: page anatomy refinement failed — %s", exc)

    # ── Stage 6: Parallel creative (the big one) ────────────────────
    # Mark earlier phases done so the BuildingScreen UI transitions
    # cleanly from "Researching" → "Planning" → "Writing code". Without
    # these the UI gets stuck displaying the previous phase forever.
    await _phase(websocket, 3, "Researching project", "Research complete", "done")
    await _phase(websocket, 4, "Planning code", "Plan ready", "done")
    await _phase(websocket, 5, "Writing code",
                 f"Generating {len(pages)} pages + header + footer in parallel…", "active")
    await _send(websocket, "progress",
                "Designing your pages…")

    from app.services.website_orchestrator import generate_website
    try:
        result = await generate_website(
            plan=plan, visual_dna=visual_dna,
            api_key=anthropic_key, websocket=websocket,
            concurrency=4,
            purpose_data=purpose_data,
            page_images=page_images,
            # Pass the planner output only when there's a live tenant
            # behind it. Without 4.6 success there's no src/lib/db.js
            # in the project for Claude to import — fetching code
            # would be a dead reference.
            data_model=data_model if tenant_schema else None,
            # Context-parity with the landing pipeline. Without these,
            # per-section codegen sees `voice_context=None` and
            # `design_system={}` and the page reads like generic SaaS.
            section_voice_context=section_voice_context,
            section_design_system=section_design_system,
            section_design_tokens=section_design_tokens,
            section_personality=section_personality,
        )
    except Exception as exc:
        logger.error("website_pipeline: orchestrator failed — %s", exc, exc_info=True)
        await _send(websocket, "error", "Couldn't generate your pages — please try again.")
        return False

    # Write all generated files to workspace
    files = result.get("files") or []
    written = 0
    for f in files:
        rel = f.get("path", "").strip()
        content = f.get("content", "")
        if not rel or not content:
            continue
        try:
            # Strip leading slash for safety
            rel = rel.lstrip("/")
            abs_path = os.path.join(workspace_path, rel)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written += 1
        except Exception as exc:
            logger.warning("website_pipeline: failed to write %s — %s", rel, exc)

    # ── Fallback Marketing chrome ──────────────────────────────────
    # When Claude's MarketingHeader / MarketingFooter generation returns
    # None, the root layout still imports those modules and `next build`
    # crashes with "Module not found". Write deterministic stubs that
    # consume site.js + navigation.js so the build at least renders a
    # functional (if generic) header/footer instead of failing the page.
    if not result.get("header_ok"):
        try:
            header_path = os.path.join(
                workspace_path, "src", "components", "layout", "MarketingHeader.jsx",
            )
            if not os.path.exists(header_path):
                os.makedirs(os.path.dirname(header_path), exist_ok=True)
                with open(header_path, "w", encoding="utf-8") as fh:
                    fh.write(_FALLBACK_MARKETING_HEADER)
                written += 1
                logger.warning(
                    "website_pipeline: MarketingHeader generation failed — wrote deterministic stub",
                )
        except Exception as exc:
            logger.error(
                "website_pipeline: failed to write fallback MarketingHeader — %s", exc,
            )

    if not result.get("footer_ok"):
        try:
            footer_path = os.path.join(
                workspace_path, "src", "components", "layout", "MarketingFooter.jsx",
            )
            if not os.path.exists(footer_path):
                os.makedirs(os.path.dirname(footer_path), exist_ok=True)
                with open(footer_path, "w", encoding="utf-8") as fh:
                    fh.write(_FALLBACK_MARKETING_FOOTER)
                written += 1
                logger.warning(
                    "website_pipeline: MarketingFooter generation failed — wrote deterministic stub",
                )
        except Exception as exc:
            logger.error(
                "website_pipeline: failed to write fallback MarketingFooter — %s", exc,
            )

    failed_routes = result.get("failed_routes") or []
    page_results = result.get("page_results") or {}
    successful_pages = sum(1 for v in page_results.values() if v)
    total_pages = len(page_results) or len(pages)

    logger.info(
        "website_pipeline: generation done — %d files written, %d/%d pages ok, "
        "header_ok=%s footer_ok=%s failed=%s",
        written, successful_pages, total_pages,
        result.get("header_ok"), result.get("footer_ok"), failed_routes,
    )

    if failed_routes:
        _failed_names = ", ".join(r.lstrip("/") or "Home" for r in failed_routes)
        await _send(
            websocket, "warning",
            f"Some pages didn't generate cleanly: {_failed_names}",
        )

    # ── Stage 6.5: Derive content schema (editor metadata) ─────────
    # Walks src/content/pages/*.json that Claude just wrote and
    # produces .lucid/content-schema.json — the manifest the
    # dashboard editor uses to know what's editable. Pure derivation,
    # no LLM. Skip cleanly when content separation is disabled.
    if _content_separation_enabled():
        try:
            from app.services.content_schema import write_content_schema
            schema_path, field_count = write_content_schema(workspace_path)
            logger.info(
                "website_pipeline: content-schema written — %s (%d fields)",
                schema_path, field_count,
            )
            await _send(
                websocket, "progress",
                "Editor ready.",
            )
        except Exception as exc:
            logger.warning(
                "website_pipeline: content schema derivation threw (non-fatal) — %s", exc,
            )

    # ── Stage 7: Post-generation verification ──────────────────────
    # Static audit of file structure + imports. Catches Claude
    # contract violations before the dev server starts.
    await _send(websocket, "progress", "Final checks…")

    from app.services.website_verification import audit_generated_website
    try:
        audit = audit_generated_website(
            workspace_path, plan,
            expect_header=result.get("header_ok") is True,
            expect_footer=result.get("footer_ok") is True,
        )
        logger.info("website_pipeline: %s", audit["summary"])
        if audit["ok"]:
            pass  # silent success — final "Site is ready!" covers it
        else:
            await _send(
                websocket, "warning",
                "Some parts of the site may have gaps — preview it and let me know what to fix.",
            )
    except Exception as exc:
        logger.warning("website_pipeline: verification threw (non-fatal) — %s", exc)

    # ── Stage 7b: Content / code separation audit ──────────────────
    # Validates Phase-4 editing contract — no-op when feature flag off.
    if _content_separation_enabled():
        try:
            from app.services.website_verification import audit_content_separation
            sep_audit = audit_content_separation(workspace_path)
            logger.info("website_pipeline: %s", sep_audit["summary"])
            if not sep_audit["ok"]:
                detail = ", ".join(
                    f"{k}={len(v)}" for k, v in sep_audit["issues"].items() if v
                )
                await _send(
                    websocket, "warning",
                    "Some content may not be editable from the dashboard — preview it and let me know.",
                )
        except Exception as exc:
            logger.warning(
                "website_pipeline: content separation audit threw (non-fatal) — %s", exc,
            )

    # ── Stage 7.5: Content quality audit ───────────────────────────
    # Catches bad CONTENT (placeholders, fake addresses, broken /routes,
    # missing required sections, voice violations) — NEVER blocks the
    # pipeline. The score lands on the websocket so the frontend can
    # display it; the issues are logged for diagnostics.
    # Content quality audit runs silently — score is sent via a
    # separate content_audit event, no chat message needed.
    from app.services.website_verification import audit_content
    voice_signature = (signals.get("voice") or {}) if isinstance(signals, dict) else {}
    try:
        content_audit = audit_content(
            project_dir=workspace_path,
            purpose_data=purpose_data,
            voice_signature=voice_signature,
        )
        logger.info("website_pipeline: %s", content_audit["summary"])
        if not content_audit["ok"] and content_audit["score"] < 70:
            logger.warning(
                "Content quality below threshold: %d", content_audit["score"],
            )
            logger.warning("Issues: %s", {
                k: len(v) for k, v in content_audit["issues"].items() if v
            })
        # Surface score + categorized issue counts so the frontend can render a badge
        try:
            await websocket.send_json({
                "type": "content_audit",
                "score": content_audit["score"],
                "ok": content_audit["ok"],
                "summary": content_audit["summary"],
                "issue_counts": {
                    k: len(v) for k, v in content_audit["issues"].items()
                },
                "warnings": content_audit.get("warnings", []),
            }) if websocket is not None else None
        except Exception:
            pass
    except Exception as exc:
        logger.warning("website_pipeline: content audit threw (non-fatal) — %s", exc)

    # ── Stage 8: Lean fixer chain ───────────────────────────────────
    # Deterministic lint/import cleanup. No LLM. Mirrors landing's Step 7
    # so we get the same baseline polish (banned-icon polyfills, missing
    # `use client` directives, eslint --fix passes) before the build check.
    try:
        from app.services.landing_fixers import run_landing_fixers
        await run_landing_fixers(workspace_path, websocket=websocket)
    except Exception as exc:
        logger.warning("website_pipeline: fixers failed (non-fatal) — %s", exc)

    # ── Stage 9: Build verification + Claude-driven auto-fix ────────
    # Runs `npm run build` and feeds any errors back to Claude for
    # repair (up to 2 attempts). Without this, build-time failures
    # (missing imports like @/components/ui/Reveal, JSX syntax, bad
    # exports) only get caught by the dev server, leaving the user
    # staring at a red overlay with no recovery.
    await _send(websocket, "progress", "Final build check…")
    await _phase(websocket, 6, "Verifying build",
                 "Running production build to catch errors…", "active")
    try:
        from app.services.build_validator import BuildValidator
        validator = BuildValidator(
            api_key=anthropic_key,
            classification=classification or {},
            websocket=websocket,
            max_retries=2,
        )
        build_result = await validator.validate_and_fix(workspace_path)
        if build_result.get("success"):
            attempts = build_result.get("attempts", 0)
            fixed_n = len(build_result.get("fixed_files") or [])
            if fixed_n:
                await _send(
                    websocket, "progress",
                    "Cleaned up a few small issues.",
                )
            await _phase(websocket, 6, "Verifying build",
                         "Build passed — preview ready", "done")
        else:
            err_count = build_result.get("error_count", 0)
            await _send(
                websocket, "warning",
                "Some issues remain — preview it and let me know what to fix.",
            )
            await _phase(websocket, 6, "Verifying build",
                         f"{err_count} error(s) remain", "done")
    except Exception as exc:
        logger.warning(
            "website_pipeline: build_validator failed (non-fatal) — %s", exc,
        )
        await _phase(websocket, 6, "Verifying build", "Build check skipped", "done")

    # ── Stage 10: Quality gate (purpose contract) ───────────────────
    # Verifies the built site actually fulfils its purpose contract
    # (hiring → form + roles + pay numbers; saas → pricing + signup;
    # restaurant → menu + locations + hours). Never blocks — surfaces
    # via a `quality_report` event so the UI can render a checklist.
    try:
        from app.services.landing_quality_gate import run_quality_gate
        await run_quality_gate(workspace_path, intent, websocket=websocket)
    except Exception as exc:
        logger.warning("website_pipeline: quality_gate failed (non-fatal) — %s", exc)

    await _send(
        websocket, "progress",
        "Your site is ready!",
    )

    # Success criteria: at least 1 page generated AND we wrote >0 files.
    # Partial failure is still a usable site (failed pages get 404 / can be retried).
    return successful_pages >= 1 and written > 0


# ── Stage 5 helpers — deterministic foundation builders ─────────────────

def _build_foundation_files(
    plan: dict[str, Any],
    visual_dna: dict[str, Any],
    design_signal: dict[str, Any],
    *,
    data_model=None,            # DataModel | None — Stage 4.5 output
    tenant_schema: str | None = None,
    project_id: str = "",
) -> dict[str, str]:
    """Build the foundation file contents. Returns {rel_path: content}.

    Foundation files are PURE DATA — no creative decisions, all derivable
    from the plan + visual_dna + research output. They're written
    deterministically so the per-page Claude calls have a stable contract
    to import from.

    When the project has a provisioned tenant_schema and at least one
    collection in `data_model.tables`, this also emits the Supabase
    plumbing (`.env.local`, `src/lib/supabase.js`, `src/lib/db.js`) so
    page components can fetch live rows via the public
    `get_tenant_collection` RPC (migration 025).
    """
    brand = plan.get("brand") or {}
    brand_name = brand.get("name") or "Brand"
    tagline = brand.get("tagline") or ""
    pages = plan.get("pages") or []

    files: dict[str, str] = {}

    # site.js
    files["src/config/site.js"] = _build_site_config(brand_name, tagline)

    # navigation.js — derive from plan routes
    files["src/config/navigation.js"] = _build_navigation(pages)

    # ── Root layout — overwrites template's stub so every page actually
    # renders with MarketingHeader + MarketingFooter. Without this, the
    # header/footer files we generate are orphans (rendered site has no
    # nav, looks like a single-page landing). See _build_marketing_root_layout.
    files["src/app/layout.js"] = _build_marketing_root_layout(brand_name)

    # ── Reveal.jsx — scroll animation wrapper that per-section codegen
    # imports as `@/components/ui/Reveal`. Lives in landing template by
    # default; the website template doesn't ship it. Without this file
    # every generated section fails to build with
    # `Module not found: Can't resolve '@/components/ui/Reveal'`,
    # taking the preview down even when all Claude calls succeeded.
    # Same source we use for landing — keeps animation behavior identical.
    from app.services.landing_phase0 import _REVEAL_SRC
    files["src/components/ui/Reveal.jsx"] = _REVEAL_SRC

    # design-system.js — token presets from visual_dna intensity
    intensity = (visual_dna.get("cultural_intensity") or "bold").lower()
    files["src/lib/design-system.js"] = _build_design_system(intensity)

    # editable.jsx — runtime wrapper for in-place editing (Phase 4)
    # See _build_editable_component() for the React implementation.
    if _content_separation_enabled():
        files["src/lib/editable.jsx"] = _build_editable_component()
        # Click-to-edit (Base44) listener — picks up the parent
        # workspace's edit-select postMessage and reports back the
        # clicked element's editable path. Only useful alongside
        # the <Editable> wrapper above, so we gate on the same flag.
        files["src/components/lucid/EditModeListener.jsx"] = (
            _build_edit_mode_listener_component()
        )

    # ── Supabase plumbing (only when there's a live tenant + tables) ─
    # Conditions:
    #   • A tenant_schema was provisioned (Stage 4.6 succeeded).
    #   • The data_model has at least one collection (something to fetch).
    #   • project_id is a real UUID — the RPC keys off chat_sessions.id.
    # Without all three, the page components have no reason to import
    # the Supabase client, so we skip the files entirely.
    if (
        tenant_schema
        and data_model is not None
        and getattr(data_model, "tables", None)
        and _UUID_RE.match(project_id)
    ):
        env_local, supabase_js, db_js = _build_supabase_plumbing(
            project_id=project_id,
            data_model=data_model,
        )
        files[".env.local"] = env_local
        files["src/lib/supabase.js"] = supabase_js
        files["src/lib/db.js"] = db_js

    # Route shells for non-home pages
    for page in pages:
        route = (page.get("route") or "/").strip()
        if route in ("", "/"):
            continue
        rel = route.lstrip("/")
        if "[" in rel or "]" in rel:
            continue  # skip dynamic routes
        title = (page.get("title") or "Page").strip()
        component_name = "".join(
            part[:1].upper() + part[1:]
            for part in rel.replace("/", "-").split("-") if part
        ) + "Page"
        files[f"src/app/{rel}/page.js"] = (
            f'import {component_name} from "@/components/pages/{rel}/{component_name}";\n'
            f'\nexport const metadata = {{\n'
            f'  title: {json.dumps(title + " | " + brand_name, ensure_ascii=False)},\n'
            f'}};\n'
            f'\nexport default function Page() {{\n'
            f'  return <{component_name} />;\n'
            f'}}\n'
        )

    # Detail-route templates for pages with detail_template=True.
    # Emits BOTH the Next.js dynamic route file and a deterministic
    # detail component + sample data JSON so the route actually renders.
    # Counts toward the workspace file budget but NOT the plan page max
    # (detail templates are paired with list pages, not separate plan entries).
    for page in pages:
        if not page.get("detail_template"):
            continue
        detail_files = _build_detail_route_files(page, brand_name, data_model)
        files.update(detail_files)

    return files


# ── Detail route builder (deterministic, no Claude) ─────────────────────
# For each page with detail_template=True, emits three files:
#   1. src/app/<route>/[slug]/page.js     — Next.js dynamic route
#   2. src/components/pages/<route>-detail/<Pascal>Detail.jsx
#   3. src/data/<source>.json             — sample rows (shared by list+detail)
#
# The list page (e.g. /listings) is already Claude-generated by the
# parallel page orchestrator. The detail template is a paired surface
# that lets users drill into one row — without it the list page has
# nowhere to click into.

def _pascal_case(value: str) -> str:
    """Convert hyphen/underscore-separated text to PascalCase."""
    parts = re.split(r"[^A-Za-z0-9]+", (value or "").strip())
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p) or "Item"


def _find_data_model_table(data_model, source: str):
    """Look up a TableDefinition by name. Returns None when no match.

    Tolerates singular/plural mismatch (`product` vs `products`) by
    trying both forms.
    """
    if data_model is None or not getattr(data_model, "tables", None):
        return None
    src = (source or "").strip().lower()
    if not src:
        return None
    for t in data_model.tables:
        tname = (t.name or "").lower()
        if tname == src or tname == src + "s" or tname + "s" == src:
            return t
    return None


def _placeholder_fields_for(source: str) -> list[dict[str, str]]:
    """Generic field shape when no DataModel table matches.

    Keeps the detail page renderable — generic title/image/description
    fields cover 90% of marketing detail-page UX.
    """
    return [
        {"name": "title", "type": "text"},
        {"name": "image_url", "type": "image_url"},
        {"name": "description", "type": "text"},
        {"name": "category", "type": "text"},
        {"name": "price", "type": "number"},
    ]


def _sample_rows_for(source: str, table) -> list[dict[str, Any]]:
    """Build 3 placeholder rows the detail page can render from.

    These are static fallbacks; real production data flows through the
    Supabase RPC path when tenant provisioning succeeded. The placeholders
    let the route at least render in dev / when the tenant tables haven't
    been seeded yet.
    """
    if table is not None and getattr(table, "fields", None):
        field_defs = [
            {"name": f.name, "type": f.type}
            for f in table.fields
            if (f.name or "").lower() not in ("id", "created_at", "updated_at")
        ]
        if not field_defs:
            field_defs = _placeholder_fields_for(source)
    else:
        field_defs = _placeholder_fields_for(source)

    rows: list[dict[str, Any]] = []
    for idx in range(1, 4):
        row: dict[str, Any] = {
            "id": f"{source}-{idx}",
            "slug": f"{source}-{idx}",
        }
        for fd in field_defs:
            fname = fd["name"]
            ftype = fd["type"]
            if ftype == "image_url" or "image" in fname or "photo" in fname:
                row[fname] = f"https://picsum.photos/seed/{source}{idx}/1200/800"
            elif ftype == "number" or ftype == "integer":
                row[fname] = idx * 100
            elif ftype == "boolean":
                row[fname] = idx % 2 == 0
            elif ftype in ("date", "datetime"):
                row[fname] = "2025-01-01"
            elif fname in ("title", "name"):
                row[fname] = f"Sample {source.capitalize()} {idx}"
                row["slug"] = f"sample-{source}-{idx}"
            elif fname in ("description", "summary", "body"):
                row[fname] = (
                    f"This is a placeholder description for {source} item {idx}. "
                    "Replace via the editor once the data source is wired."
                )
            else:
                row[fname] = f"Sample {fname.replace('_', ' ')} {idx}"
        rows.append(row)
    return rows


def _build_detail_route_files(
    page: dict[str, Any],
    brand_name: str,
    data_model,
) -> dict[str, str]:
    """Build the {path: content} dict for one detail-template page.

    Always returns three files (route, component, data JSON) — the
    caller merges them into the foundation file map.
    """
    route = (page.get("route") or "").strip().lstrip("/")
    if not route or "[" in route:
        return {}
    source = (page.get("detail_source") or route).strip().lower()
    pascal = _pascal_case(route)
    component_name = f"{pascal}Detail"
    title = (page.get("title") or pascal).strip()

    table = _find_data_model_table(data_model, source)
    rows = _sample_rows_for(source, table)

    # 1. Data JSON — shared by list + detail. Lives under src/data/ so
    # both routes can import it deterministically.
    data_path = f"src/data/{source}.json"
    data_content = json.dumps(rows, indent=2, ensure_ascii=False) + "\n"

    # 2. Detail component — renders one row.
    component_path = f"src/components/pages/{route}-detail/{component_name}.jsx"
    component_content = _build_detail_component_jsx(
        component_name=component_name,
        list_route="/" + route,
        list_label=title,
        sample_row=rows[0] if rows else {},
    )

    # 3. Next.js dynamic route.
    route_path = f"src/app/{route}/[slug]/page.js"
    route_content = _build_detail_route_js(
        route_slug=route,
        component_name=component_name,
        source=source,
        brand_name=brand_name,
        list_label=title,
    )

    return {
        data_path: data_content,
        component_path: component_content,
        route_path: route_content,
    }


def _build_detail_route_js(
    *,
    route_slug: str,
    component_name: str,
    source: str,
    brand_name: str,
    list_label: str,
) -> str:
    """Generate src/app/<route>/[slug]/page.js content.

    Static-generation friendly: `generateStaticParams` enumerates known
    slugs from the JSON data file so the route prerenders cleanly.
    `notFound()` triggers Next.js's built-in 404 when a slug doesn't
    match. No client-side data fetching, no useEffect — keeps the
    detail surface fast and SEO-friendly.
    """
    metadata_title = f'{list_label} | {brand_name}'
    return (
        f'import {{ notFound }} from "next/navigation";\n'
        f'import {component_name} from "@/components/pages/{route_slug}-detail/{component_name}";\n'
        f'import data from "@/data/{source}.json";\n'
        f'\n'
        f'export async function generateStaticParams() {{\n'
        f'  return data.map((row) => ({{ slug: String(row.slug || row.id) }}));\n'
        f'}}\n'
        f'\n'
        f'export async function generateMetadata({{ params }}) {{\n'
        f'  const {{ slug }} = await params;\n'
        f'  const row = data.find((r) => String(r.slug || r.id) === slug);\n'
        f'  const title = row ? (row.title || row.name || {json.dumps(list_label)}) : {json.dumps(list_label)};\n'
        f'  return {{ title: `${{title}} | {brand_name}` }};\n'
        f'}}\n'
        f'\n'
        f'export default async function Page({{ params }}) {{\n'
        f'  const {{ slug }} = await params;\n'
        f'  const row = data.find((r) => String(r.slug || r.id) === slug);\n'
        f'  if (!row) notFound();\n'
        f'  return <{component_name} row={{row}} />;\n'
        f'}}\n'
    )


def _build_detail_component_jsx(
    *,
    component_name: str,
    list_route: str,
    list_label: str,
    sample_row: dict[str, Any],
) -> str:
    """Generate the detail component JSX.

    Pure JSX — no hooks, no client directive needed. Renders any row
    shape using `row.title`, `row.image_url`, `row.description` as
    primary fields and falls back to listing every remaining string
    field as a metadata grid entry. Includes a back-link to the list
    route so users can navigate up the hierarchy.
    """
    return (
        'import Link from "next/link";\n'
        '\n'
        f'export default function {component_name}({{ row }}) {{\n'
        '  if (!row) return null;\n'
        '\n'
        '  const title = row.title || row.name || row.heading || "Untitled";\n'
        '  const image = row.image_url || row.image || row.photo || row.cover_image || null;\n'
        '  const description = row.description || row.summary || row.body || "";\n'
        '\n'
        '  const reserved = new Set(["id", "slug", "title", "name", "heading",'
        ' "image_url", "image", "photo", "cover_image", "description", "summary", "body"]);\n'
        '  const meta = Object.entries(row).filter(\n'
        '    ([key, value]) =>\n'
        '      !reserved.has(key) &&\n'
        '      (typeof value === "string" || typeof value === "number" || typeof value === "boolean")\n'
        '  );\n'
        '\n'
        '  return (\n'
        '    <article className="container mx-auto max-w-4xl px-4 py-12 md:py-20">\n'
        f'      <Link href="{list_route}" className="text-sm text-muted-foreground hover:text-foreground mb-6 inline-block">\n'
        f'        ← Back to {list_label}\n'
        '      </Link>\n'
        '\n'
        '      {image && (\n'
        '        <img\n'
        '          src={image}\n'
        '          alt={title}\n'
        '          className="aspect-[16/9] w-full rounded-lg object-cover mb-8 bg-muted"\n'
        '        />\n'
        '      )}\n'
        '\n'
        '      <h1 className="text-3xl md:text-5xl font-bold tracking-tight mb-4">{title}</h1>\n'
        '\n'
        '      {description && (\n'
        '        <p className="text-lg text-muted-foreground leading-relaxed mb-8">{description}</p>\n'
        '      )}\n'
        '\n'
        '      {meta.length > 0 && (\n'
        '        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 border-t border-border pt-6">\n'
        '          {meta.map(([key, value]) => (\n'
        '            <div key={key} className="flex flex-col">\n'
        '              <dt className="text-xs uppercase tracking-wide text-muted-foreground">\n'
        '                {key.replace(/_/g, " ")}\n'
        '              </dt>\n'
        '              <dd className="text-base text-foreground mt-1">{String(value)}</dd>\n'
        '            </div>\n'
        '          ))}\n'
        '        </dl>\n'
        '      )}\n'
        '    </article>\n'
        '  );\n'
        '}\n'
    )


def _build_supabase_plumbing(
    *,
    project_id: str,
    data_model,  # DataModel
) -> tuple[str, str, str]:
    """Build (`.env.local`, `src/lib/supabase.js`, `src/lib/db.js`) contents.

    The generated client uses ONLY public env vars (`NEXT_PUBLIC_*`)
    and the anon Supabase key — no service_role anywhere near the
    browser. All reads go through the public `get_tenant_collection`
    RPC (migration 025) which enforces `public_read` per-table from
    the project's stored data_model.

    `data_model` is used to emit a list of valid collection names as
    a comment in db.js — handy when debugging which collections the
    generated UI is allowed to read.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    anon_key     = os.environ.get("SUPABASE_ANON_KEY", "").strip()

    table_names = [t.name for t in data_model.tables]
    table_list_comment = (
        "/*\n * Collections available via getCollection(name):\n"
        + "".join(f" *   - {n}\n" for n in table_names)
        + " */"
    )

    env_local = (
        "# AUTO-GENERATED — Lucid AI website pipeline.\n"
        "# Public client — these are safe to ship to the browser; the anon\n"
        "# key only grants what RLS + the get_tenant_collection RPC allow.\n"
        f"NEXT_PUBLIC_SUPABASE_URL={supabase_url}\n"
        f"NEXT_PUBLIC_SUPABASE_ANON_KEY={anon_key}\n"
        f"NEXT_PUBLIC_LUCID_PROJECT_ID={project_id}\n"
    )

    supabase_js = (
        '/* AUTO-GENERATED — Lucid AI website pipeline. */\n'
        'import { createClient } from "@supabase/supabase-js";\n'
        '\n'
        'const url     = process.env.NEXT_PUBLIC_SUPABASE_URL;\n'
        'const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;\n'
        '\n'
        '// Single shared client. Throwing on missing env vars is\n'
        '// deliberate — a misconfigured build should fail loudly\n'
        "// at first import, not paper over with `null`.\n"
        'if (!url || !anonKey) {\n'
        '  throw new Error(\n'
        '    "Lucid: NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY is missing.",\n'
        '  );\n'
        '}\n'
        '\n'
        'export const supabase = createClient(url, anonKey);\n'
    )

    # Explicit `.js` extension on the import — Next.js bundler-mode
    # resolves either form, but standalone Node ESM (used by our
    # smoke tests) requires the extension.
    db_js = (
        '/* AUTO-GENERATED — Lucid AI website pipeline. */\n'
        f'{table_list_comment}\n'
        'import { supabase } from "./supabase.js";\n'
        '\n'
        'const PROJECT_ID = process.env.NEXT_PUBLIC_LUCID_PROJECT_ID;\n'
        '\n'
        '/**\n'
        ' * Fetch rows from the project\'s per-tenant collection. Returns\n'
        ' * an array (possibly empty). Errors are logged + swallowed —\n'
        ' * the caller renders an empty section instead of crashing the\n'
        ' * whole page.\n'
        ' *\n'
        ' * @param {string} tableName - snake_case table identifier.\n'
        ' * @param {{ limit?: number }} [opts]\n'
        ' * @returns {Promise<Array<Object>>}\n'
        ' */\n'
        'export async function getCollection(tableName, opts = {}) {\n'
        '  const { data, error } = await supabase.rpc("get_tenant_collection", {\n'
        '    p_project_id: PROJECT_ID,\n'
        '    p_table_name: tableName,\n'
        '    p_limit:      opts.limit ?? 200,\n'
        '  });\n'
        '  if (error) {\n'
        '    console.error("[lucid/db] getCollection(" + tableName + ") failed:", error);\n'
        '    return [];\n'
        '  }\n'
        '  return Array.isArray(data) ? data : [];\n'
        '}\n'
    )

    return env_local, supabase_js, db_js


def _build_marketing_root_layout(brand_name: str) -> str:
    """Build the root `src/app/layout.js` that wraps EVERY page with
    MarketingHeader + MarketingFooter.

    The template ships a layout that just renders {children} — header
    and footer files get generated by header_footer_generator but are
    never imported anywhere, so the rendered site has no nav and no
    footer (looks like a generic single-page landing).

    We overwrite the template layout with one that imports and renders
    both. Idempotent: re-running the pipeline produces the same file.

    Brand name is interpolated into the metadata title.
    """
    safe_name = json.dumps(brand_name, ensure_ascii=False)
    return (
        'import "./globals.css";\n'
        'import { Inter } from "next/font/google";\n'
        'import { siteConfig } from "@/config/site";\n'
        'import { Providers } from "@/components/Providers";\n'
        'import MarketingHeader from "@/components/layout/MarketingHeader";\n'
        'import MarketingFooter from "@/components/layout/MarketingFooter";\n'
        'import EditModeListener from "@/components/lucid/EditModeListener";\n'
        '\n'
        'const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });\n'
        '\n'
        'export const metadata = {\n'
        '  title: {\n'
        f'    default: siteConfig.name,\n'
        f'    template: `%s | ${{siteConfig.name}}`,\n'
        '  },\n'
        '  description: siteConfig.description || siteConfig.tagline,\n'
        '};\n'
        '\n'
        'export default function RootLayout({ children }) {\n'
        '  return (\n'
        '    <html lang="en" className={inter.variable}>\n'
        '      <body className="font-sans antialiased min-h-screen flex flex-col">\n'
        '        <Providers>\n'
        '          <MarketingHeader />\n'
        '          <main className="flex-1">{children}</main>\n'
        '          <MarketingFooter />\n'
        '          <EditModeListener />\n'
        '        </Providers>\n'
        '      </body>\n'
        '    </html>\n'
        '  );\n'
        '}\n'
    )


def _build_site_config(brand_name: str, tagline: str) -> str:
    return (
        '/* AUTO-GENERATED — edit the schema, not this file. */\n'
        'export const siteConfig = {\n'
        f'  name: {json.dumps(brand_name, ensure_ascii=False)},\n'
        f'  tagline: {json.dumps(tagline, ensure_ascii=False)},\n'
        '  url: "https://example.com",\n'
        '  social: {\n'
        '    twitter: "",\n'
        '    instagram: "",\n'
        '  },\n'
        '};\n'
    )


def _derive_voice_context(signals: dict, intent: dict) -> dict:
    """Build the voice_context dict per-section codegen expects.

    Mirrors what `enrich_brief_with_signals` does for the landing path,
    but extracts the values without mutating a brief dict (websites have
    no brief). Pulls `audience_phrases / regional_touchpoints /
    industry_terms / white_space` from `signals["domain"]` and the
    purpose directive from `intent`. Returns an empty dict if signals
    are missing — codegen will skip the VOICE & CONTEXT block.
    """
    domain = (signals or {}).get("domain") or {}
    vc: dict[str, Any] = {
        "voice_phrases":  list(domain.get("audience_phrases") or [])[:10],
        "regional_refs":  list(domain.get("regional_touchpoints") or [])[:5],
        "industry_terms": list(domain.get("industry_terms") or [])[:10],
        "white_space":    list(domain.get("white_space") or [])[:5],
        "purpose_directive": "",
    }
    if intent:
        try:
            from app.services.purpose_research import format_purpose_directive_block
            vc["purpose_directive"] = format_purpose_directive_block(intent) or ""
        except Exception as exc:
            logger.warning(
                "website_pipeline._derive_voice_context: purpose directive failed (non-fatal) — %s", exc,
            )
    return vc


def _derive_design_system_from_dna(visual_dna: dict) -> dict:
    """Map visual_dna cultural cues to the 5-enum design_system landing
    section codegen consumes.

    Without this, the website path passes `design_system={}` and every
    section across every project renders with the same hard-coded defaults
    (subtle / rounded / elevated / natural / balanced) — that's the
    "generic feel" symptom. We derive each enum from visible visual_dna
    signals (cultural_intensity, typography_voice, layout_signature,
    photography_style, decorative_motifs) and pass it through, then
    normalize to ensure every value is a valid enum.
    """
    vd = visual_dna or {}
    intensity = (vd.get("cultural_intensity") or "bold").strip().lower()
    type_voice = (vd.get("typography_voice") or "").lower()
    layout_sig = (vd.get("layout_signature") or "").lower()
    photo = (vd.get("photography_style") or "").lower()
    motifs_str = " ".join(vd.get("decorative_motifs") or []).lower()
    textures_str = " ".join(vd.get("signature_textures") or []).lower()

    # motion — cultural_intensity is the strongest signal
    if intensity == "subtle":
        motion = "subtle"
    elif any(w in layout_sig + " " + motifs_str for w in ("organic", "hand", "brush", "ornate")):
        motion = "organic"
    elif any(w in type_voice + " " + layout_sig for w in ("dramatic", "expressive", "maximalist")):
        motion = "dramatic"
    else:
        motion = "energetic"

    # accent_shape — typography voice + motifs decide
    if any(w in type_voice for w in ("script", "calligraphy", "italic")):
        accent_shape = "pill"
    elif any(w in motifs_str for w in ("organic", "blob", "fluid", "curve")):
        accent_shape = "blob"
    elif any(w in type_voice for w in ("geometric", "modernist", "grotesk", "brutalist")):
        accent_shape = "squared"
    elif "hairline" in type_voice or "thin" in type_voice or "minimalist" in layout_sig:
        accent_shape = "hairline"
    else:
        accent_shape = "rounded"

    # surface — layout signature drives
    if any(w in layout_sig for w in ("asymmetric", "editorial", "magazine")):
        surface = "layered"
    elif "bento" in layout_sig or "card-stack" in layout_sig:
        surface = "bordered"
    elif any(w in layout_sig for w in ("minimal", "spare", "negative-space")):
        surface = "flat"
    elif "alternating" in layout_sig or "duotone" in textures_str:
        surface = "duotone"
    else:
        surface = "elevated"

    # image_treatment — photography_style drives
    if any(w in photo for w in ("dramatic", "moody", "high-contrast", "shadow")):
        image_treatment = "overlay"
    elif any(w in photo for w in ("duotone", "monochrome", "tinted")):
        image_treatment = "duotone"
    elif any(w in photo for w in ("framed", "bordered", "polaroid")):
        image_treatment = "bordered"
    elif "masked" in photo or "gradient-mask" in textures_str:
        image_treatment = "masked"
    else:
        image_treatment = "natural"

    # section_rhythm — intensity + layout drives
    if intensity == "subtle":
        rhythm = "balanced"
    elif any(w in layout_sig for w in ("airy", "spacious", "generous")):
        rhythm = "airy"
    elif any(w in layout_sig for w in ("dense", "tight", "packed")):
        rhythm = "tight"
    else:
        rhythm = "airy" if intensity == "bold" else "balanced"

    raw = {
        "motion": motion,
        "accent_shape": accent_shape,
        "surface": surface,
        "image_treatment": image_treatment,
        "section_rhythm": rhythm,
    }
    # Normalize against the canonical enums so any synonym we picked up
    # collapses to a valid value before reaching the codegen prompt.
    try:
        from app.services.landing_brief import _normalize_design_system
        return _normalize_design_system(raw)
    except Exception:
        return raw


def _build_design_tokens_for_website(design_system: dict) -> dict:
    """Compute the pre-resolved Tailwind class string dict that the
    section codegen prompt injects verbatim under PROJECT_DESIGN_TOKENS.
    """
    try:
        from app.services.landing_brief import _build_design_tokens
        return _build_design_tokens(design_system or {})
    except Exception as exc:
        logger.warning(
            "website_pipeline._build_design_tokens_for_website: failed — %s", exc,
        )
        return {}


# ── Stage 4.8 — content brief expansion (per-page Gemini Flash) ──────
# The active website_plan only emits {type, layout_hint, archetype} per
# section. Without per-section CONTENT (headline, items, image_queries),
# Claude has to invent everything from voice_context + brand name alone —
# the documented root cause of "every website feels generic and same-y".
# expand_pages_many fills the gap by running one Gemini Flash call per
# page that returns schema-shaped content matching section_schemas.py.
# Merge logic below preserves the plan's structural choices (type,
# layout_hint, archetype) while ADDING the brief's content fields.


def _build_brief_input_for_expand(
    plan: dict,
    intent: dict,
    visual_dna: dict,
    voice_context: dict,
    design_tokens: dict,
    purpose_data: dict | None,
) -> tuple[dict, list[dict]]:
    """Build the (project_brief, page_inputs) tuple expand_pages_many expects.

    expand_pages_many reads:
      - project_brief.brand.name           — copywriter prompt
      - project_brief.industry             — domain framing
      - project_brief.audience.primary     — audience framing
      - project_brief.primary_purpose      — page-goal context
      - project_brief.personality          — tone / vibe_keywords / energy
      - project_brief.voice                — sample / audience_voice
      - pages[i].slug / title / nav_label / page_goal / primary_cta / section_types

    Everything else (palette, typography, design_system) it ignores —
    those land via the design_system / design_tokens channels we already
    pass to the section codegen.
    """
    brand = dict(plan.get("brand") or {})
    personality = dict(visual_dna.get("personality") or {})
    if not personality.get("vibe_keywords"):
        # Fall back to intent.brand_personality so the voice block isn't empty.
        bp = intent.get("brand_personality") if isinstance(intent, dict) else None
        if isinstance(bp, list) and bp:
            personality["vibe_keywords"] = [str(x) for x in bp[:5]]

    voice_phrases = list((voice_context or {}).get("voice_phrases") or [])
    industry_terms = list((voice_context or {}).get("industry_terms") or [])
    # Concatenate a few audience-voice samples so the per-page Gemini call
    # has concrete cadence to mirror in headlines/items.
    voice_sample = " ".join(voice_phrases[:4]).strip()
    audience_voice = ", ".join(industry_terms[:6]).strip()

    project_brief: dict = {
        "brand": {"name": (brand.get("name") or "").strip()},
        "brand_name": (brand.get("name") or "").strip(),
        "industry": (intent.get("business_category") or (purpose_data or {}).get("industry") or "general").strip(),
        "business_category": (intent.get("business_category") or "general").strip(),
        "audience": {
            "primary": ((intent.get("target_audience") or {}).get("primary") or "general consumers").strip()
                       if isinstance(intent.get("target_audience"), dict)
                       else (intent.get("target_audience") or "general consumers"),
        },
        "primary_purpose": (intent.get("primary_purpose") or "lead_generation").strip(),
        "personality": personality,
        "voice": {
            "sample": voice_sample,
            "audience_voice": audience_voice,
        },
    }

    # Per-page inputs. Use the home page's primary_cta as the default for
    # pages that didn't get one in the plan (rare — plan typically omits
    # primary_cta entirely, so home is also "Get started" → "#contact").
    default_cta = {
        "label": ((plan.get("ctas") or {}).get("primary") or {}).get("label") or "Get started",
        "href":  ((plan.get("ctas") or {}).get("primary") or {}).get("href") or "#contact",
    }

    page_inputs: list[dict] = []
    for p in (plan.get("pages") or []):
        if not isinstance(p, dict):
            continue
        route = (p.get("route") or p.get("path") or "/").strip()
        slug = "" if route in ("", "/") else route.lstrip("/")
        title = (p.get("title") or "").strip() or ("Home" if slug == "" else slug.replace("-", " ").title())
        nav_label = (p.get("nav_label") or title).strip()
        section_types = []
        for s in (p.get("sections") or []):
            if isinstance(s, dict) and s.get("type"):
                section_types.append(s["type"])
        page_inputs.append({
            "slug": slug,
            "title": title,
            "nav_label": nav_label,
            "page_goal": (p.get("purpose") or "").strip(),
            "primary_cta": dict(p.get("primary_cta") or default_cta),
            "section_types": section_types,
        })
    return project_brief, page_inputs


def _merge_briefs_into_plan(plan: dict, briefs: list[dict | None]) -> tuple[int, int, int]:
    """Merge expand_pages_many output back into plan.pages[].sections.

    For each plan section we keep the structural fields the plan already
    set (type, layout_hint, archetype, role, nav_label) and ADD the brief's
    content fields (headline, subheadline, body, items, image_queries,
    primary_cta, cta, etc.). Plan section is matched to brief section by
    CANONICAL TYPE (via section_schemas.canonical_type) preserving plan
    order — so a plan emitting [hero, features, cta] gets brief content
    for hero+features+cta even if Gemini reorders or aliases.

    Returns (pages_filled, sections_filled, sections_kept_as_plan) for logging.
    """
    from app.services.section_schemas import canonical_type, schema_for

    pages_filled = 0
    sections_filled = 0
    sections_kept = 0
    pages = list(plan.get("pages") or [])
    for i, page in enumerate(pages):
        brief = briefs[i] if i < len(briefs) else None
        if not isinstance(brief, dict):
            continue
        filled_sections = brief.get("sections") or []
        if not filled_sections:
            continue
        # Index brief sections by canonical type so we can look up by alias.
        by_canon: dict[str, list[dict]] = {}
        for fs in filled_sections:
            if not isinstance(fs, dict):
                continue
            canon = canonical_type(fs.get("type") or fs.get("id") or "")
            if canon:
                by_canon.setdefault(canon, []).append(fs)

        page_filled = False
        for s in (page.get("sections") or []):
            if not isinstance(s, dict):
                continue
            canon = canonical_type(s.get("type") or "")
            queue = by_canon.get(canon) or []
            if not queue:
                # No matching brief section — Claude will fill from voice
                # context alone (legacy path).
                sections_kept += 1
                continue
            # Pop the first match so duplicate types within one page
            # consume brief sections in order.
            filled = queue.pop(0)
            # Merge: filled fields land in `s` unless plan already set them.
            for k, v in filled.items():
                if k in ("id", "type"):
                    continue  # plan owns these
                if k in ("layout_hint", "archetype", "role", "nav_label"):
                    # Plan owns structural cohesion fields. Only adopt
                    # brief's value if plan didn't set one.
                    if not s.get(k):
                        s[k] = v
                    continue
                # Content fields — brief wins (it's the whole point).
                if v not in (None, "", [], {}):
                    s[k] = v
            sections_filled += 1
            page_filled = True
        if page_filled:
            pages_filled += 1
    return pages_filled, sections_filled, sections_kept


def _content_brief_expansion_enabled() -> bool:
    """Off-switch in case Gemini Flash is throttled or the schema set
    misses too many of the plan's section types in production. Default ON.
    """
    return (os.environ.get("WEBSITE_CONTENT_BRIEF_ENABLED", "1").strip() != "0")


_NAV_ANCHOR_SECTION_TYPES = (
    # Section types worth surfacing as anchor nav when the plan has only one
    # page. Order roughly matches typical IA priority.
    ("services",            "Services",      "services"),
    ("offerings",           "Services",      "offerings"),
    ("features",            "Features",      "features"),
    ("about",               "About",         "about"),
    ("story",               "About",         "story"),
    ("story_long",          "About",         "story"),
    ("portfolio",           "Work",          "portfolio"),
    ("case_studies",        "Work",          "case-studies"),
    ("gallery",             "Gallery",       "gallery"),
    ("menu",                "Menu",          "menu"),
    ("pricing",             "Pricing",       "pricing"),
    ("testimonials",        "Reviews",       "testimonials"),
    ("team",                "Team",          "team"),
    ("contact",             "Contact",       "contact"),
    ("contact_form",        "Contact",       "contact"),
    ("locations",           "Visit",         "locations"),
    ("faq",                 "FAQ",           "faq"),
)


def _derive_anchor_nav_from_home(pages: list[dict]) -> list[dict[str, str]]:
    """Fallback when the plan only produced a home page: scan its sections
    and emit anchor links (#about, #services, #contact, ...). Mirrors the
    landing pipeline's nav so single-page websites still have a real header.
    """
    home = next((p for p in pages if (p.get("route") or "/").strip() in ("", "/")), None)
    if not home:
        return []
    home_sections = home.get("sections") or []
    seen: set[str] = set()
    nav: list[dict[str, str]] = []
    for sec in home_sections:
        if not isinstance(sec, dict):
            continue
        s_type = (sec.get("type") or sec.get("name") or "").strip().lower()
        if not s_type or s_type in seen:
            continue
        for key, label, anchor in _NAV_ANCHOR_SECTION_TYPES:
            if key == s_type:
                nav.append({"label": label, "href": f"#{anchor}"})
                seen.add(s_type)
                break
        if len(nav) >= 5:
            break
    return nav


def _build_navigation(pages: list[dict]) -> str:
    """Build mainNav + footerNav from the plan's pages list."""
    nav_items: list[dict[str, str]] = []
    for p in pages:
        route = (p.get("route") or "/").strip()
        title = (p.get("title") or "").strip()
        if not route or route == "/":
            continue
        nav_items.append({"label": title or route, "href": route})

    if not nav_items:
        # Single-page result: derive anchor nav from home sections so the
        # header isn't blank. Without this the MarketingHeader prompt renders
        # `mainNav.map(...)` over an empty array and the user sees a header
        # with just a wordmark + CTA, no navigation at all (Reliant Logistics
        # symptom).
        fallback = _derive_anchor_nav_from_home(pages)
        if fallback:
            nav_items = fallback
            logger.warning(
                "website_pipeline._build_navigation: plan has 0 non-home routes "
                "(pages=%d) — falling back to %d anchor links from home sections (%s)",
                len(pages), len(nav_items),
                ", ".join(n["href"] for n in nav_items),
            )
        else:
            logger.error(
                "website_pipeline._build_navigation: plan has no usable nav items "
                "(pages=%d, home has no anchorable sections). Header will render "
                "wordmark + CTA only.",
                len(pages),
            )
    else:
        logger.info(
            "website_pipeline._build_navigation: %d nav items from %d pages (%s)",
            len(nav_items), len(pages),
            ", ".join(n["href"] for n in nav_items),
        )

    items_js = "[\n" + ",\n".join(
        f'  {{ label: {json.dumps(i["label"], ensure_ascii=False)}, '
        f'href: {json.dumps(i["href"], ensure_ascii=False)} }}'
        for i in nav_items
    ) + "\n]"
    return (
        '/* AUTO-GENERATED — edit the schema, not this file. */\n'
        f'export const mainNav = {items_js};\n'
        '\n'
        f'export const footerNav = {items_js};\n'
    )


# Deterministic Marketing chrome — written when Claude generation returns
# None. Generic, but functional (reads site.js + navigation.js so it picks
# up the project's brand/nav rather than placeholders). Tailwind classes
# only; no inline styles.
_FALLBACK_MARKETING_HEADER = '''"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { siteConfig } from "@/config/site";
import { mainNav } from "@/config/navigation";

export default function MarketingHeader() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 16);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => { setOpen(false); }, [pathname]);

  return (
    <header
      className={`sticky top-0 z-40 w-full transition-all duration-200 ${
        scrolled
          ? "bg-background/95 backdrop-blur-md border-b border-border shadow-sm"
          : "bg-background/80 backdrop-blur-sm border-b border-transparent"
      }`}
    >
      <div className="container mx-auto flex h-16 max-w-7xl items-center justify-between gap-6 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="font-[family-name:var(--font-heading)] text-xl font-bold tracking-tight text-foreground hover:text-primary transition">
          {siteConfig.name}
        </Link>
        <nav className="hidden items-center gap-6 md:flex">
          {mainNav.map((item) => {
            const active = item.href === pathname;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`text-sm font-medium transition hover:text-primary ${
                  active ? "text-primary" : "text-foreground/80"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="flex items-center gap-2">
          <Link
            href="#contact"
            className="hidden rounded-full bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 md:inline-flex"
          >
            Get in touch
          </Link>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-foreground md:hidden"
            aria-label="Toggle menu"
            aria-expanded={open}
          >
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>
      {open && (
        <div className="border-t border-border bg-background md:hidden">
          <div className="container mx-auto flex max-w-7xl flex-col gap-1 px-4 py-4">
            {mainNav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="rounded-md px-3 py-2 text-sm font-medium text-foreground hover:bg-muted"
              >
                {item.label}
              </Link>
            ))}
            <Link
              href="#contact"
              className="mt-2 rounded-full bg-primary px-5 py-2 text-center text-sm font-semibold text-primary-foreground"
            >
              Get in touch
            </Link>
          </div>
        </div>
      )}
    </header>
  );
}
'''

_FALLBACK_MARKETING_FOOTER = '''import Link from "next/link";
import { siteConfig } from "@/config/site";
import { footerNav } from "@/config/navigation";

export default function MarketingFooter() {
  return (
    <footer className="border-t border-border bg-card">
      <div className="container mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
        <div className="grid gap-10 md:grid-cols-[1.2fr_1fr_1fr]">
          <div>
            <p className="font-[family-name:var(--font-heading)] text-xl font-bold tracking-tight text-foreground">
              {siteConfig.name}
            </p>
            {siteConfig.tagline && (
              <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
                {siteConfig.tagline}
              </p>
            )}
          </div>
          <div>
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
              Navigate
            </p>
            <ul className="space-y-2 text-sm">
              {footerNav.map((item) => (
                <li key={item.href}>
                  <Link href={item.href} className="text-foreground/80 transition hover:text-primary">
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
              Contact
            </p>
            <p className="text-sm text-foreground/80">
              <Link href="#contact" className="hover:text-primary">Get in touch</Link>
            </p>
          </div>
        </div>
        <div className="mt-10 flex flex-col items-start justify-between gap-3 border-t border-border pt-6 text-xs text-muted-foreground sm:flex-row sm:items-center">
          <p>&copy; {new Date().getFullYear()} {siteConfig.name}. All rights reserved.</p>
          <p>Built with care.</p>
        </div>
      </div>
    </footer>
  );
}
'''


def _build_globals_css(design_signal: dict[str, Any]) -> str:
    """Build src/app/globals.css from design signals.

    Reuses landing_phase0's palette renderer for HSL var blocks. Adds Tailwind
    directives + Google Fonts imports + heading/body font-family overrides.
    """
    palette = (design_signal or {}).get("chosen_palette") or {}
    typo = (design_signal or {}).get("chosen_typography") or {}
    if not palette and not typo:
        return ""  # no design data → skip writing (page generators will still work but use default colors)

    # Build the palette {token: HSL} dict in landing_phase0's expected shape
    palette_for_css = {
        "background": palette.get("background", ""),
        "foreground": palette.get("foreground", ""),
        "primary":    palette.get("primary", ""),
        "secondary":  palette.get("secondary", ""),
        "accent":     palette.get("accent", ""),
        "muted":      palette.get("muted", ""),
        "border":     palette.get("border", ""),
        "card":       palette.get("card", ""),
    }
    # Drop empties
    palette_for_css = {k: v.strip() for k, v in palette_for_css.items() if v and v.strip()}

    heading_font = (typo.get("heading_font") or "Inter").strip()
    body_font = (typo.get("body_font") or "Inter").strip()

    from app.services.landing_phase0 import _palette_vars, _GLOBALS_TEMPLATE
    return _GLOBALS_TEMPLATE.format(
        palette_vars=_palette_vars(palette_for_css),
        heading_font=heading_font,
        body_font=body_font,
    )


def _build_design_system(intensity: str) -> str:
    """Tailwind class tokens. Intensity-driven for now; can be expanded."""
    is_bold = intensity == "bold"
    spacing = '"py-24 md:py-32"' if is_bold else '"py-16 md:py-24"'
    container = '"max-w-7xl mx-auto px-4 md:px-8"'
    card = '"rounded-2xl border bg-card shadow-sm transition-all hover:shadow-md"' if is_bold else '"rounded-lg border bg-card transition-shadow hover:shadow-sm"'
    heading = '"font-heading tracking-tight"'
    body = '"font-body text-base leading-relaxed"'
    button_primary = '"inline-flex items-center justify-center rounded-full bg-primary text-primary-foreground px-6 py-3 text-sm font-medium hover:opacity-90 transition-opacity"'
    button_secondary = '"inline-flex items-center justify-center rounded-full border border-foreground/20 bg-transparent px-6 py-3 text-sm font-medium hover:bg-foreground/5 transition-colors"'
    button_ghost = '"inline-flex items-center justify-center rounded-full bg-transparent px-4 py-2 text-sm font-medium hover:bg-foreground/5 transition-colors"'
    motion = (
        'initial: { opacity: 0, y: 20 },\n'
        '    whileInView: { opacity: 1, y: 0 },\n'
        '    viewport: { once: true, margin: "-100px" },\n'
        '    transition: { duration: 0.6, ease: "easeOut" },'
    )

    return (
        '/* AUTO-GENERATED — edit the schema, not this file. */\n'
        'export const ds = {\n'
        f'  section: {spacing},\n'
        f'  container: {container},\n'
        f'  card: {card},\n'
        f'  cardInteractive: {card},\n'
        f'  heading: {heading},\n'
        f'  body: {body},\n'
        f'  buttonPrimary: {button_primary},\n'
        f'  buttonSecondary: {button_secondary},\n'
        f'  buttonGhost: {button_ghost},\n'
        '  motion: {\n'
        f'    {motion}\n'
        '  },\n'
        '};\n'
    )
