from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .backends import get_backend
from .config import (
    CONTENT_ROOT,
    MCP_HOST,
    MCP_PATH,
    MCP_PORT,
    PROJECT_SLUG,
    resolve_backend,
)
from .handoff import list_handoff, read_handoff, write_handoff

_MAX_OUTPUT = 20_000


def _run(cmd: list[str], cwd: Path) -> str:
    """Run a subprocess and return a bounded `exit=<rc>` + combined output string."""
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > _MAX_OUTPUT:
        out = out[-_MAX_OUTPUT:]
    return f"exit={proc.returncode}\n{out}"


def build_server() -> FastMCP:
    """Build the FastMCP server, registering neutral tools plus (optionally) delegate."""
    mcp = FastMCP(
        name=f"katapult-{PROJECT_SLUG}",
        host=MCP_HOST,
        port=MCP_PORT,
        streamable_http_path=MCP_PATH,
    )

    # --- Neutral core tools (IDE-independent, no vendor dependency) ---

    @mcp.tool()
    def project_status() -> str:
        """Return supervisor service status for this project (read-only)."""
        return _run(["supervisorctl", "status"], CONTENT_ROOT)

    @mcp.tool()
    def run_tests(target: str = "") -> str:
        """Run the project's library test suite (`uv run pytest` in /content/lib).

        `target` is optional extra pytest args (e.g. "tests/test_x.py -k foo").
        """
        cmd = ["uv", "run", "pytest"]
        if target:
            cmd += shlex.split(target)
        return _run(cmd, CONTENT_ROOT / "lib")

    @mcp.tool()
    def render() -> str:
        """Render the project's Quarto documentation (`quarto render`)."""
        return _run(["quarto", "render"], CONTENT_ROOT)

    @mcp.tool()
    def restart_service(name: str) -> str:
        """Restart a supervisor-managed service (destructive; approval-gated client-side)."""
        return _run(["supervisorctl", "restart", name], CONTENT_ROOT)

    @mcp.tool()
    def handoff_list() -> list[str]:
        """List files in the /content/.handoff data-plane directory."""
        return list_handoff()

    @mcp.tool()
    def handoff_read(path: str) -> str:
        """Read a handoff file (path relative to /content/.handoff)."""
        return read_handoff(path)

    @mcp.tool()
    def handoff_write(path: str, content: str) -> str:
        """Write a handoff file (path relative to /content/.handoff); returns the path."""
        return write_handoff(path, content)

    # --- Pluggable second brain (opt-in): only when a backend is configured ---

    backend = get_backend(resolve_backend())
    if backend is not None:

        @mcp.tool()
        def delegate(task: str, resume_id: str | None = None) -> dict:
            """Delegate a task to the project's in-container second brain.

            Registered only when AGENT_BACKEND selects a configured backend.
            Returns {agent_id, status, summary}; pass `resume_id` to continue a
            prior session.
            """
            return backend.delegate(task, resume_id)

    return mcp


def main() -> None:
    """Entry point: serve the MCP server over streamable HTTP."""
    build_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
