"""
pipeline/step4b_images.py — Pipeline Step 4.5: analyze uploaded images with Gemini Vision.

Extracted verbatim from task_pipeline.py (lines 2807–2932).
Zero logic changes.
"""

from __future__ import annotations

import asyncio
import logging

import google.generativeai as genai
from fastapi import WebSocket

from .constants import GEMINI_MODEL

logger = logging.getLogger(__name__)


async def analyze_images(
    images: list,
    task: str,
    gemini_key: str,
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
        gemini_key: Gemini API key.
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
            from PIL import Image
            from io import BytesIO

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel(GEMINI_MODEL)

            for i, img_data in enumerate(actual_images):
                try:
                    raw = img_data.get("data", "")
                    name = img_data.get("name", f"image_{i+1}")

                    if not raw:
                        descriptions.append(f"### Image: {name}\n(No image data received)")
                        continue

                    # Extract base64 from data URL (data:image/png;base64,xxxxx)
                    if "," in raw:
                        raw = raw.split(",", 1)[1]

                    img_bytes = base64.b64decode(raw)
                    pil_image = Image.open(BytesIO(img_bytes))

                    analysis_prompt = f"""Analyze this image in the context of this coding task:
Task: {task}

Describe what you see in detail:
1. If it's a UI screenshot — describe the layout, components, colors, text, and navigation.
2. If it's a design mockup — describe the intended design, positioning, and visual hierarchy.
3. If it's an error/log screenshot — extract the error message and stack trace.
4. If it's a diagram — describe the architecture/flow.

Be specific and technical. Your description will be used by another AI to implement code changes."""

                    response = await asyncio.to_thread(
                        model.generate_content,
                        [analysis_prompt, pil_image],
                    )

                    desc = response.text.strip()
                    descriptions.append(f"### Image: {name}\n{desc}")

                    logger.info("Image '%s' analyzed: %d chars", name, len(desc))

                except Exception as e:
                    logger.warning("Failed to analyze image '%s': %s", name, e)
                    descriptions.append(f"### Image: {name}\n(Failed to analyze: {str(e)[:100]})")

        except ImportError:
            logger.warning("PIL not available for image analysis, skipping")
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
