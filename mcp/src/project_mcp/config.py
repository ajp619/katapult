from __future__ import annotations

import os
from pathlib import Path

# Project root inside the container (bind-mounted from the host in dev).
CONTENT_ROOT = Path(os.environ.get("KATAPULT_CONTENT", "/content"))

# Data-plane handoff directory shared with the host via the bind mount.
HANDOFF_DIR = CONTENT_ROOT / ".handoff"

PROJECT_SLUG = "katapult"

# HTTP transport settings. nginx strips the /<slug> prefix and proxies to this
# host:port; the server serves the MCP endpoint at MCP_PATH.
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "5100"))
MCP_PATH = os.environ.get("MCP_PATH", "/mcp")


def resolve_backend() -> str:
    """Resolve the opt-in delegate backend name.

    Resolution order, kept intentionally tiny so the ``~/.katapult`` overlay can
    activate a backend without cloning ``build/BuildConfig``:

    1. ``AGENT_BACKEND`` environment variable.
    2. ``AGENT_BACKEND`` in ``/content/build/agent.env`` (dropped by the overlay).
    3. ``"none"`` -- the neutral default; the ``delegate`` tool stays unregistered.
    """
    env = os.environ.get("AGENT_BACKEND")
    if env:
        return env.strip().lower()

    agent_env = CONTENT_ROOT / "build" / "agent.env"
    if agent_env.is_file():
        for line in agent_env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "AGENT_BACKEND":
                return val.strip().strip('"').strip("'").lower()

    return "none"
