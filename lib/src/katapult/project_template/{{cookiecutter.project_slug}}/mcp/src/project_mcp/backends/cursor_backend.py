from __future__ import annotations

import os

from ..config import CONTENT_ROOT


class CursorBackend:
    """Cursor SDK adapter: a durable, resumable agent rooted at /content.

    Optional dependency: requires ``cursor-sdk`` (``uv sync --extra cursor``) and
    ``CURSOR_API_KEY`` in the environment. ``setting_sources=["all"]`` is what
    makes the agent inherit this project's own ``.cursor/rules`` and
    ``cursor_context/`` instead of inline-only config.
    """

    name = "cursor"

    def delegate(self, task: str, resume_id: str | None = None) -> dict:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

        opts = AgentOptions(
            api_key=os.environ["CURSOR_API_KEY"],
            model=os.environ.get("AGENT_MODEL", "composer-2.5"),
            local=LocalAgentOptions(
                cwd=str(CONTENT_ROOT),
                setting_sources=["all"],
            ),
        )
        agent = Agent.resume(resume_id, opts) if resume_id else Agent.create(opts)
        try:
            run = agent.send(task)
            result = run.wait()
            return {
                "agent_id": getattr(agent, "agent_id", None),
                "status": getattr(result, "status", "unknown"),
                "summary": getattr(result, "result", None),
            }
        finally:
            close = getattr(agent, "close", None)
            if callable(close):
                close()
