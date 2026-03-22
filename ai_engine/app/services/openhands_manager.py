"""
OpenHands V1 SDK lifecycle manager.

Manages creation and destruction of OpenHands Conversations.
Ensures mutual exclusion with Claude Code SDK — only one
can be active at a time.

Singleton instance: ``openhands_manager``
"""

import asyncio
import gc
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OpenHandsManager:
    """Thread-safe manager for OpenHands V1 Conversation lifecycle.

    Usage::

        from app.services.openhands_manager import openhands_manager

        conv = await openhands_manager.create_conversation(
            task_id="abc123",
            workspace="/tmp/lucid_abc123",
            gemini_key="...",
            tools=["terminal"],
        )
        # ... use conversation ...
        await openhands_manager.destroy_conversation("abc123")
    """

    def __init__(self) -> None:
        self._conversations: dict[str, Any] = {}
        self._agents: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def build_repo_url(self, user: dict) -> str:
        """Build authenticated git clone URL from user settings.

        Handles both raw slugs ("owner/repo") and full URLs
        ("https://github.com/owner/repo") stored in Supabase.
        """
        provider = user.get("git_provider", "github")

        if provider == "gitlab":
            token = str(user.get("gitlab_token") or "").strip()
            repo = str(user.get("gitlab_repo") or "").strip()
            repo = (
                repo.replace("https://gitlab.com/", "")
                .strip("/")
                .replace(".git", "")
            )
            return f"https://oauth2:{token}@gitlab.com/{repo}.git"

        else:
            token = str(user.get("github_token") or "").strip()
            repo = str(user.get("github_repo") or "").strip()
            repo = (
                repo.replace("https://github.com/", "")
                .strip("/")
                .replace(".git", "")
            )
            return f"https://{token}@github.com/{repo}.git"

    async def create_conversation(
        self,
        task_id: str,
        workspace: str,
        gemini_key: str,
        tools: list[str],
    ) -> Any:
        """Create a new OpenHands V1 Conversation.

        Parameters
        ----------
        task_id : str
            Unique identifier for this conversation.
        workspace : str
            Local filesystem path for the workspace.
        gemini_key : str
            Gemini API key for the LLM (used for clone/push agent).
        tools : list[str]
            Tool names to enable (e.g. ["terminal"]).

        Returns
        -------
        Conversation or None if SDK unavailable.
        """
        async with self._lock:
            # Destroy any existing conversation for this task_id
            if task_id in self._conversations:
                await self._destroy_unlocked(task_id)

            try:
                from openhands.sdk import LLM, Agent, Conversation, Tool
                from openhands.tools.terminal import TerminalTool
                from openhands.tools.file_editor import FileEditorTool

                # Build tool list
                tool_instances = []
                for t in tools:
                    if t == "terminal":
                        tool_instances.append(Tool(name=TerminalTool.name))
                    elif t == "file_editor":
                        tool_instances.append(Tool(name=FileEditorTool.name))

                # Create LLM — use Gemini Flash for git operations (cheap)
                llm = LLM(
                    model="gemini/gemini-2.5-flash",
                    api_key=str(gemini_key).strip(),
                )

                # Create Agent with minimal tools
                agent = Agent(
                    llm=llm,
                    tools=tool_instances,
                )

                # Create Conversation
                conversation = Conversation(
                    agent=agent,
                    workspace=workspace,
                )

                self._conversations[task_id] = conversation
                self._agents[task_id] = agent

                logger.info(
                    "OpenHands conversation created: task_id=%s, workspace=%s",
                    task_id, workspace,
                )
                return conversation

            except ImportError as e:
                logger.warning(
                    "OpenHands SDK not available — cannot create conversation: %s", e
                )
                return None

            except Exception as e:
                logger.error(
                    "Failed to create OpenHands conversation: %s", e, exc_info=True
                )
                return None

    async def destroy_conversation(self, task_id: str) -> None:
        """Completely destroy an OpenHands conversation.

        - Closes the conversation
        - Removes all references
        - Forces garbage collection
        """
        async with self._lock:
            await self._destroy_unlocked(task_id)

    async def _destroy_unlocked(self, task_id: str) -> None:
        """Internal destroy — must be called with lock held."""
        conversation = self._conversations.pop(task_id, None)
        agent = self._agents.pop(task_id, None)

        if conversation is not None:
            try:
                if hasattr(conversation, "close"):
                    close_result = conversation.close()
                    if asyncio.iscoroutine(close_result):
                        await close_result
                logger.info("OpenHands conversation destroyed: task_id=%s", task_id)
            except Exception as e:
                logger.warning(
                    "Error closing OpenHands conversation %s: %s", task_id, e
                )

        # Dereference agent explicitly
        del agent
        del conversation

        # Force garbage collection to free resources
        gc.collect()

    async def is_active(self, task_id: str | None = None) -> bool:
        """Check if any OpenHands conversation is currently active.

        If task_id is provided, checks only that specific conversation.
        If task_id is None, checks if ANY conversation is active.
        """
        async with self._lock:
            if task_id is not None:
                return task_id in self._conversations
            return len(self._conversations) > 0

    async def destroy_all(self) -> None:
        """Destroy ALL active conversations. Emergency cleanup."""
        async with self._lock:
            task_ids = list(self._conversations.keys())
            for tid in task_ids:
                await self._destroy_unlocked(tid)
            logger.info("All OpenHands conversations destroyed (%d)", len(task_ids))


# ── Singleton instance ──────────────────────────────────────
openhands_manager = OpenHandsManager()
