import asyncio
import json
import os
import logging
import traceback
from fastapi import WebSocket
from claude_code_sdk import query, ClaudeCodeOptions, ResultMessage, AssistantMessage

logger = logging.getLogger(__name__)

active_tasks = {}  # task_id -> asyncio.Task


async def _send_phase(websocket: WebSocket, phase: int, title: str, description: str, status: str = "active"):
    """Send a structured task_phase event to the frontend."""
    try:
        await websocket.send_json({
            "type": "task_phase",
            "phase": phase,
            "title": title,
            "description": description,
            "status": status  # active | done | error
        })
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
#  STEP 1 — Classify task with Gemini Flash (~$0.001)
# ═══════════════════════════════════════════════════════════
async def classify_task(
    task: str,
    gemini_api_key: str
) -> dict:
    """Use Gemini Flash to classify task complexity and pick model."""

    print(f"DEBUG classify_task starting")
    print(f"DEBUG gemini_api_key present: {bool(gemini_api_key)}")

    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel("gemini-3-flash-preview")

        response = model.generate_content(f"""
Classify this development task.

Task: {task}

Return ONLY this JSON, nothing else:
{{
    "model": "sonnet" or "opus",
    "task_type": "ui|bug|feature|logic|auth|database",
    "reason": "one line explanation",
    "complexity": "simple|medium|complex"
}}

Classification rules:
Use "sonnet" for:
- Any UI changes (color, layout, design, styling, CSS, components)
- Simple bug fixes
- Text/copy changes
- Simple feature additions
- Page redesigns
- Responsive design fixes
- Animation changes
- Simple API endpoints

Use "opus" for:
- Complex business logic
- Authentication systems
- Payment integration
- Database schema design
- Algorithm implementation
- Security related code
- Complex state management
- Performance optimization
- Multi step workflows
- API integrations with complex data transformations

Be cost conscious.
Default to sonnet unless task clearly needs opus level reasoning.
""")

        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]

        result = json.loads(text)
        print(f"DEBUG classification result: {result}")
        return result
    except json.JSONDecodeError:
        print(f"DEBUG classify_task JSON parse failed, using fallback")
        return {"model": "sonnet", "complexity": "medium",
                "reason": "fallback", "task_type": "feature"}
    except Exception as e:
        print(f"ERROR classify_task failed: {e}")
        traceback.print_exc()
        return {"model": "sonnet", "complexity": "medium",
                "reason": "classification error fallback", "task_type": "feature"}


# ═══════════════════════════════════════════════════════════
#  STEP 2 — Gemini explores codebase + creates plan
# ═══════════════════════════════════════════════════════════
async def gemini_explore(
    task: str,
    repo_path: str,
    gemini_api_key: str,
    websocket: WebSocket
) -> str:
    """Read codebase with Gemini and create a detailed implementation plan."""

    print(f"DEBUG gemini_explore starting")
    print(f"DEBUG repo_path: {repo_path}")
    print(f"DEBUG repo_path exists: {os.path.exists(repo_path)}")

    try:
        await websocket.send_json({
            "type": "progress",
            "message": "🔍 Analyzing codebase..."
        })
    except Exception as e:
        print(f"ERROR sending progress to websocket: {e}")

    skip_dirs = [
        'node_modules', '.git', 'dist',
        'build', '.next', '__pycache__',
        '.cache', 'coverage', '.turbo'
    ]
    skip_extensions = [
        '.png', '.jpg', '.jpeg', '.svg',
        '.ico', '.gif', '.woff', '.ttf',
        '.map', '.lock'
    ]
    skip_files = [
        'package-lock.json',
        'yarn.lock',
        'pnpm-lock.yaml'
    ]

    all_files = {}
    try:
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f in skip_files:
                    continue
                if any(f.endswith(ext) for ext in skip_extensions):
                    continue
                rel_path = os.path.relpath(
                    os.path.join(root, f),
                    repo_path
                )
                full_path = os.path.join(root, f)
                try:
                    with open(full_path, 'r', encoding='utf-8') as file:
                        content = file.read()
                        if len(content.splitlines()) < 500:
                            all_files[rel_path] = content
                except Exception:
                    pass
    except Exception as e:
        print(f"ERROR walking repo_path: {e}")
        traceback.print_exc()

    files_content = "\n\n".join([
        f"=== FILE: {path} ===\n{content}"
        for path, content in all_files.items()
    ])

    # Limit to 30000 chars max
    MAX_CHARS = 30000
    if len(files_content) > MAX_CHARS:
        files_content = files_content[:MAX_CHARS] + "\n... (truncated for brevity)"

    print(f"DEBUG files_content length: {len(files_content)}")

    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel("gemini-3-flash-preview")

        response = model.generate_content(f"""
You are a senior software engineer.
Analyze this codebase and create a detailed implementation plan.

Task: {task}

Codebase:
{files_content}

Return this exact format:

FILES TO MODIFY:
- exact/path/to/file.tsx
- exact/path/to/styles.css

CURRENT CODE CONTEXT:
(paste the specific parts of current code that need to change)

EXACT IMPLEMENTATION:
(step by step what to write, be extremely specific)

COMPLETE FILE TEMPLATES:
(for each file provide complete new file content as template)

TESTING CHECKLIST:
- what to verify after changes
""")

        print(f"DEBUG gemini_explore complete")

        try:
            await websocket.send_json({
                "type": "progress",
                "message": "📋 Plan ready!"
            })
        except Exception:
            pass

        return response.text

    except Exception as e:
        print(f"ERROR gemini_explore API call failed: {e}")
        traceback.print_exc()
        logger.error(f"Gemini API error during explore: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Gemini explore failed: {str(e)[:200]}"
            })
        except Exception:
            pass
        return f"Failed to analyze codebase: {str(e)[:200]}. Implement the task based on your understanding."


# ═══════════════════════════════════════════════════════════
#  STEP 3 — Smart handle_task() (replaces fix_bug + write_code)
# ═══════════════════════════════════════════════════════════
async def handle_task(
    task: str,
    repo_path: str,
    api_key: str,
    gemini_api_key: str,
    websocket: WebSocket,
    task_id: str = None
):
    """
    Smart task handler:
    1. Gemini classifies → picks sonnet or opus
    2. Gemini explores → creates implementation plan
    3. Claude writes code from plan (focused, no exploration)

    NEVER lets any exception propagate to caller.
    """

    print(f"DEBUG handle_task called")
    print(f"DEBUG task[:100]: {task[:100]}")
    print(f"DEBUG repo_path: {repo_path}")
    print(f"DEBUG api_key present: {bool(api_key)}")
    print(f"DEBUG gemini_api_key present: {bool(gemini_api_key)}")
    print(f"DEBUG task_id: {task_id}")

    if task_id:
        task_obj = asyncio.current_task()
        active_tasks[task_id] = task_obj

    try:
        try:
            await websocket.send_json({
                "type": "debug",
                "message": f"Starting task, repo: {repo_path}"
            })
        except Exception:
            pass

        # ── Stage 1 — Classify task with Gemini ───────────────
        print(f"DEBUG Stage 1: classify_task starting")
        await _send_phase(websocket, 1, "Classifying task", "Analyzing task complexity...", "active")

        try:
            classification = await classify_task(task, gemini_api_key)
            selected_model = (
                "claude-opus-4-6"
                if classification.get("model") == "opus"
                else "claude-sonnet-4-6"
            )
            print(f"DEBUG classification done: model={classification.get('model')}, selected={selected_model}")
            classify_desc = (
                f"Using {classification.get('model', 'sonnet').upper()} — "
                f"{classification.get('reason', 'auto-selected')} "
                f"({classification.get('complexity', 'medium')} {classification.get('task_type', 'feature')})"
            )
            await _send_phase(websocket, 1, "Classifying task", classify_desc, "done")
        except Exception as e:
            print(f"ERROR classification failed: {e}")
            traceback.print_exc()
            logger.error(f"Classification failed: {e}")
            classification = {"model": "sonnet"}
            selected_model = "claude-sonnet-4-6"
            await _send_phase(websocket, 1, "Classifying task", "Using Sonnet (default fallback)", "done")

        # ── Stage 2 — Gemini explores + creates plan ─────────
        print(f"DEBUG Stage 2: gemini_explore starting")
        await _send_phase(websocket, 2, "Analyzing codebase", "Reading project files and creating implementation plan...", "active")

        try:
            gemini_plan = await gemini_explore(
                task, repo_path, gemini_api_key, websocket
            )
            print(f"DEBUG gemini_plan length: {len(gemini_plan)}")
            explore_desc = f"Analyzed codebase and created implementation plan ({len(gemini_plan)} chars)"
            await _send_phase(websocket, 2, "Analyzing codebase", explore_desc, "done")
        except Exception as e:
            print(f"ERROR gemini_explore failed: {e}")
            traceback.print_exc()
            gemini_plan = f"No codebase analysis available. Implement this task: {task}"
            await _send_phase(websocket, 2, "Analyzing codebase", f"Skipped analysis: {str(e)[:100]}", "error")

        # ── Stage 3 — Claude writes code from plan ───────────
        print(f"DEBUG Stage 3: claude streaming starting")
        model_label = classification.get('model', 'sonnet').upper()
        await _send_phase(websocket, 3, "Writing code", f"{model_label} is implementing changes...", "active")

        # ── Dynamic max_turns based on complexity ─────────
        complexity = classification.get("complexity", "medium")
        if complexity == "simple":
            max_turns = 10
        elif complexity == "complex":
            max_turns = 30
        else:  # medium
            max_turns = 20

        focused_prompt = f"""
IMPLEMENTATION PLAN:
{gemini_plan}

Execute this plan completely and thoroughly.
Do not hold back — implement everything described in the plan above.
"""

        options = ClaudeCodeOptions(
            cwd=repo_path,
            env={"ANTHROPIC_API_KEY": api_key},
            model=selected_model,
            max_turns=max_turns,
            permission_mode="acceptEdits",
            disallowed_tools=[
                "GitCommit",
                "GitPush",
                "GitPull",
                "GitClone"
            ],
            append_system_prompt=(
                "You are Claude Code, an expert software engineer.\n"
                "You have a detailed implementation plan.\n"
                "Execute it completely and thoroughly.\n"
                "For UI tasks: make comprehensive, professional changes to all related files.\n"
                "For bug fixes: find and fix the root cause.\n"
                "For features: implement completely end to end.\n"
                "Do not stop until the task is fully complete."
            )
        )

        try:
            await _stream_claude(
                focused_prompt, options,
                classification.get("model", "sonnet"), websocket
            )
            print(f"DEBUG claude streaming complete")
            await _send_phase(websocket, 3, "Writing code", f"{model_label} finished implementing changes", "done")
        except Exception as e:
            print(f"ERROR claude streaming failed: {e}")
            traceback.print_exc()
            await _send_phase(websocket, 3, "Writing code", f"Failed: {str(e)[:150]}", "error")
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Claude execution failed: {str(e)[:200]}"
                })
            except Exception:
                pass
            return

        # ── Stage 4 — Complete ────────────────────────────────
        await _send_phase(websocket, 4, "Task complete", "All changes written successfully", "done")

        try:
            await websocket.send_json({
                "type": "code_complete",
                "message": "✅ Code written successfully!"
            })
        except Exception:
            pass

    except Exception as e:
        print(f"ERROR handle_task top-level exception: {e}")
        traceback.print_exc()
        logger.error(f"handle_task failed: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Task failed: {str(e)[:300]}"
            })
        except Exception:
            pass
    finally:
        if task_id:
            active_tasks.pop(task_id, None)


async def stop_task(task_id: str, websocket: WebSocket):
    task = active_tasks.get(task_id)
    if task:
        task.cancel()
        active_tasks.pop(task_id, None)
        await websocket.send_json({
            "type": "stopped",
            "message": "⛔ Task stopped by user."
        })
    else:
        await websocket.send_json({
            "type": "error",
            "message": "No active task found."
        })


# ═══════════════════════════════════════════════════════════
#  Helper — Stream Claude SDK messages to WebSocket
# ═══════════════════════════════════════════════════════════
async def _stream_claude(
    prompt: str,
    options: ClaudeCodeOptions,
    model_label: str,
    websocket: WebSocket
):
    """Run Claude query and stream each message to the websocket."""
    print(f"DEBUG _stream_claude starting with model: {model_label}")
    result_text = ""
    try:
        async for message in query(prompt=prompt, options=options):
            try:
                # Capture the result message for summary
                if isinstance(message, ResultMessage):
                    result_text = getattr(message, 'result', '') or ''
                    cost = getattr(message, 'total_cost_usd', 0) or 0
                    num_turns = getattr(message, 'num_turns', 0) or 0
                    print(f"DEBUG ResultMessage: result={result_text[:200]}, cost=${cost:.4f}, turns={num_turns}")
                    await websocket.send_json({
                        "type": "claude_result",
                        "model": model_label,
                        "result": result_text,
                        "cost": round(cost, 4),
                        "turns": num_turns
                    })
                elif isinstance(message, AssistantMessage):
                    # Extract text content from assistant messages
                    content = getattr(message, 'content', [])
                    text_parts = []
                    for block in (content if isinstance(content, list) else [content]):
                        if hasattr(block, 'text'):
                            text_parts.append(block.text)
                        elif isinstance(block, str):
                            text_parts.append(block)
                    if text_parts:
                        combined = "\n".join(text_parts)
                        print(f"DEBUG AssistantMessage: {combined[:150]}")
                        await websocket.send_json({
                            "type": "claude_message",
                            "model": model_label,
                            "content": combined
                        })
                else:
                    # Other message types (SystemMessage, UserMessage, etc.)
                    await websocket.send_json({
                        "type": "claude_message",
                        "model": model_label,
                        "content": str(message)
                    })
            except Exception as send_err:
                print(f"WARN failed to send message to ws: {send_err}")
                pass
        print(f"DEBUG _stream_claude finished successfully")
    except Exception as e:
        print(f"ERROR _stream_claude failed: {e}")
        traceback.print_exc()
        raise  # Re-raise so caller can handle it
