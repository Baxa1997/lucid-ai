"""
pipeline/step4b_images.py — Pipeline Step 4.5: analyze uploaded images with Gemini Vision.

Extracted verbatim from task_pipeline.py (lines 2807–2932).
Zero logic changes.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from .constants import GEMINI_MODEL

logger = logging.getLogger(__name__)


async def analyze_images(
    images: list,
    task: str,
    websocket: WebSocket,
) -> str:
    """Analyze user-uploaded images using Gemini Flash Vision.

    Handles three attachment types:
    - Images (base64 data URL) → analyzed with Gemini Vision
    - Videos (base64 data URL) → noted as context (not analyzed frame-by-frame)
    - Figma links (URL string) → included as design reference

    Args:
        images: List of dicts with 'name', 'data', and optionally 'type', 'url'.
        task: The user's task description.
        websocket: WebSocket for progress updates.

    Returns:
        A text description of all attachments, or empty string if none/failure.
    """
    if not images:
        return ""

    try:
        await websocket.send_json({
            "type": "progress",
            "message": f"🖼️ Analyzing {len(images)} attachment(s)...",
        })
    except Exception:
        pass

    descriptions = []

    # ── Separate attachments by type ──────────────────────
    actual_images = []
    videos = []
    figma_links = []

    for item in images:
        item_type = item.get("type", "image")
        if item_type == "video":
            videos.append(item)
        elif item_type == "figma":
            figma_links.append(item)
        else:
            actual_images.append(item)

    # ── Process Figma links (no analysis needed) ──────────
    for fig in figma_links:
        url = fig.get("url", fig.get("data", ""))
        name = fig.get("name", "Figma Design")
        descriptions.append(f"### Figma Reference: {name}\nDesign link: {url}\nUse this Figma design as a visual reference for the UI implementation.")

    # ── Process videos (note their presence) ──────────────
    for vid in videos:
        name = vid.get("name", "video")
        descriptions.append(f"### Video Attachment: {name}\nA video file was attached. Consider the user may be showing a UI flow, bug reproduction, or desired behavior.")

    # ── Process actual images with Gemini Vision ──────────
    if actual_images:
        try:
            import base64
            from app.services.gemini_http import gemini_post
            from knowledge.loader import safe_gemini_text

            for i, img_data in enumerate(actual_images):
                try:
                    raw = img_data.get("data", "")
                    name = img_data.get("name", f"image_{i+1}")

                    if not raw:
                        descriptions.append(f"### Image: {name}\n(No image data received)")
                        continue

                    # Extract mime type and base64 from data URL.
                    # Format: data:image/png;base64,xxxxx
                    mime_type = "image/png"
                    if raw.startswith("data:"):
                        try:
                            header, b64data = raw.split(",", 1)
                            # header = "data:image/png;base64"
                            mime_type = header.split(":")[1].split(";")[0] or mime_type
                            raw = b64data
                        except Exception:
                            if "," in raw:
                                raw = raw.split(",", 1)[1]
                    elif "," in raw:
                        raw = raw.split(",", 1)[1]

                    analysis_prompt = f"""Analyze this image in the context of this coding task:
Task: {task}

Describe what you see in detail:
1. If it's a UI screenshot — describe the layout, components, colors, text, and navigation.
2. If it's a design mockup — describe the intended design, positioning, and visual hierarchy.
3. If it's an error/log screenshot — extract the error message and stack trace.
4. If it's a diagram — describe the architecture/flow.

Be specific and technical. Your description will be used by another AI to implement code changes."""

                    payload = {
                        "contents": [{
                            "parts": [
                                {"text": analysis_prompt},
                                {"inlineData": {"mimeType": mime_type, "data": raw}},
                            ]
                        }],
                    }

                    status, data, _ = await gemini_post(
                        model=GEMINI_MODEL,
                        payload=payload,
                        timeout_s=60.0,
                        label="step4b_image",
                    )
                    if status != 200 or data is None:
                        descriptions.append(f"### Image: {name}\n(Vision API error {status})")
                        continue

                    desc = safe_gemini_text(data).strip()
                    descriptions.append(f"### Image: {name}\n{desc}")

                    logger.info("Image '%s' analyzed: %d chars", name, len(desc))

                except Exception as e:
                    logger.warning("Failed to analyze image '%s': %s", name, e)
                    descriptions.append(f"### Image: {name}\n(Failed to analyze: {str(e)[:100]})")

        except Exception as e:
            logger.warning("analyze_images failed: %s", e)

    if descriptions:
        try:
            await websocket.send_json({
                "type": "progress",
                "message": f"✅ Processed {len(descriptions)} attachment(s)",
            })
        except Exception:
            pass

    return "\n\n".join(descriptions)
