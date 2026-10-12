# mcp

An IDE-neutral [MCP](https://modelcontextprotocol.io/) server that exposes this
project's dev operations as tools over the open protocol, so any MCP-capable
client (Claude Desktop, a bare MCP CLI, the Cursor hub, ...) can drive the
project. It is vendored into the project (like `.katapult/katx`) and runs
standalone from the project's own `uv` environment -- it never calls back into
the `kat` library at runtime.

## Layers

- **Neutral core (always):** `run_tests`, `render`, `project_status`,
  `restart_service`, and `handoff_*` tools, depending only on the open `mcp`
  package. No vendor SDK in the default dependency closure.
- **Pluggable "second brain" (opt-in):** a `delegate` tool, registered only when
  `AGENT_BACKEND` selects a configured backend (see `build/agent.env`). The
  Cursor backend is an optional extra (`uv sync --extra cursor`, needs
  `CURSOR_API_KEY`); other backends can be added under `src/project_mcp/backends/`.

## Run

Supervisor runs it on the dev path via `cd /content/mcp && uv run project-mcp`,
served over Traefik at `http://<host>/<project>/mcp`. Toggle with `MCP_SERVER` in
`build/BuildConfig`. See `katx mcp` for the URL + health.
