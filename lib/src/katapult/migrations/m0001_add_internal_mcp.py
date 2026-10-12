"""0001 -- add the per-project internal MCP server (and opt-in delegate backend).

Brings pre-MCP projects (generated before katapult grew the dev-only ``mcp/`` service)
up to the current template: the ``mcp/`` env + neutral server, the ``build/agent.env``
activation knob, the supervisor + nginx wiring, the ``MCP_SERVER`` build gate, the
compose volume/env_file additions, and a wholesale refresh of ``.katapult/katx`` (which
bakes in no project identity, so it is safe to overwrite).

Every action is idempotent and scaffolding-only; see :mod:`katapult.migrations.base`.
"""

from __future__ import annotations

from katapult.migrations.base import AddFile, EnsureBlock, Migration

# --- EnsureBlock payloads (kept close to the template content) ----------------

_BUILDCONFIG_MCP = """
# MCP server (per-project, IDE-neutral). Set to `off` to omit the dev-only MCP
# service from the image entirely (keeps test/prod agent-free). The opt-in
# delegate backend is configured separately in build/agent.env (default none),
# so the ~/.katapult overlay can activate it without cloning this file.
MCP_SERVER=on
"""

_DOCKERFILE_MCP_GATE = """
# Dev-only MCP service: when MCP_SERVER != on, disable it at build time so it
# neither bakes a venv (the bake loop below only touches enabled *.conf dirs)
# nor runs under supervisor -- keeping test/prod images agent-free.
ARG MCP_SERVER=on
RUN if [ "${MCP_SERVER}" != "on" ]; then \\
        [ -f /etc/supervisor/conf.d/mcp.conf ] && mv /etc/supervisor/conf.d/mcp.conf /etc/supervisor/conf.d/mcp.conf_; \\
        rm -f /etc/nginx/conf.d/location_blocks/mcp.conf; \\
        true; \\
    fi
"""

_DOCKERFILE_MCP_VENV_MOUNTPOINT = """
# Pre-create the dev-only mcp service's .venv mountpoint owned by the app user.
# Older pre-MCP Dockerfiles seed only api/app/lib/nbk in the bake loop above;
# compose mounts an anonymous volume at /content/mcp/.venv, which Docker seeds
# root-owned from the image path when the dir is absent, so the non-root app
# user can't create its venv at runtime (the mcp service would FATAL).
RUN mkdir -p /content/mcp/.venv && chown ${USERNAME}:${USERGROUP} /content/mcp/.venv
"""

_COMPOSE_MCP_VENV = "      - /content/mcp/.venv\n"

_COMPOSE_AGENT_STORE_MOUNT = """      # Persist the opt-in delegate backend's agent/session store across
      # `katx rebuild` (used only when AGENT_BACKEND selects a backend).
      - mcp_agent_store:/home/katapult/.cursor
"""

_COMPOSE_VOLUMES_DECL = "volumes:\n  mcp_agent_store:\n"

_COMPOSE_AGENT_ENVFILE = """      # Optional backend credentials (e.g. CURSOR_API_KEY). Absent by default;
      # the ~/.katapult overlay drops this in when a backend is activated.
      - path: ./.agent_credentials
        required: false
"""


# --- mcp/ tree (single-sourced from the template) -----------------------------

_MCP_TREE = [
    AddFile("mcp/pyproject.toml", "mcp/pyproject.toml", templated=True),
    AddFile("mcp/.python-version", "mcp/.python-version", templated=True),
    AddFile("mcp/README.md", "mcp/README.md"),
    AddFile("mcp/src/project_mcp/__init__.py", "mcp/src/project_mcp/__init__.py"),
    AddFile(
        "mcp/src/project_mcp/config.py",
        "mcp/src/project_mcp/config.py",
        templated=True,
    ),
    AddFile("mcp/src/project_mcp/handoff.py", "mcp/src/project_mcp/handoff.py"),
    AddFile("mcp/src/project_mcp/server.py", "mcp/src/project_mcp/server.py"),
    AddFile(
        "mcp/src/project_mcp/backends/__init__.py",
        "mcp/src/project_mcp/backends/__init__.py",
    ),
    AddFile(
        "mcp/src/project_mcp/backends/cursor_backend.py",
        "mcp/src/project_mcp/backends/cursor_backend.py",
    ),
]


migration = Migration(
    id="0001_add_internal_mcp",
    katapult_version="0.11.1",
    description="Add the dev-only internal MCP server + opt-in delegate backend.",
    actions=[
        *_MCP_TREE,
        # Activation knob + service wiring (agnostic, create-only).
        AddFile("build/agent.env", "build/agent.env"),
        AddFile(
            "build/conf/supervisor/conf.d/mcp.conf",
            "build/conf/supervisor/conf.d/mcp.conf",
        ),
        AddFile(
            "build/conf/nginx/location_blocks/mcp.conf",
            "build/conf/nginx/location_blocks/mcp.conf",
            templated=True,
        ),
        # katx carries no project identity -> safe wholesale refresh (brings the
        # `mcp` verb and all newer logic).
        AddFile(".katapult/katx", ".katapult/katx", overwrite_ok=True),
        # BuildConfig: expose the MCP_SERVER build gate (append; each line is a build-arg).
        EnsureBlock(
            "build/BuildConfig",
            marker="MCP_SERVER",
            block=_BUILDCONFIG_MCP,
            description="MCP_SERVER build gate",
        ),
        # Dockerfile: strip the service from test/prod images when MCP_SERVER != on.
        EnsureBlock(
            "build/Dockerfile",
            marker="ARG MCP_SERVER",
            block=_DOCKERFILE_MCP_GATE,
            after="COPY ./build/conf/supervisor/conf.d/* /etc/supervisor/conf.d/",
            description="MCP_SERVER gating block",
        ),
        # Pre-create + chown the mcp service's .venv mountpoint. The template
        # Dockerfile's bake loop includes `mcp`, but older pre-MCP Dockerfiles
        # being upgraded list only api/app/lib/nbk, so without this the anon
        # volume at /content/mcp/.venv comes up root-owned and the mcp service
        # FATALs on a uv permission error.
        EnsureBlock(
            "build/Dockerfile",
            marker="mkdir -p /content/mcp/.venv && chown",
            block=_DOCKERFILE_MCP_VENV_MOUNTPOINT,
            after="COPY ./build/conf/supervisor/conf.d/* /etc/supervisor/conf.d/",
            description="mcp /.venv mountpoint pre-create",
        ),
        # docker-compose: mcp venv mount, agent-store named volume + mount, agent creds.
        EnsureBlock(
            "build/docker-compose.yml",
            marker="/content/mcp/.venv",
            block=_COMPOSE_MCP_VENV,
            after="- /content/nbk/.venv",
            description="mcp /.venv mount",
        ),
        EnsureBlock(
            "build/docker-compose.yml",
            marker="mcp_agent_store:/home/katapult/.cursor",
            block=_COMPOSE_AGENT_STORE_MOUNT,
            # Anchor on the stable nbk line (not the mcp line added just above) so the
            # dry-run preview matches apply: each EnsureBlock re-reads the file, and on a
            # dry run nothing is written, so a dependency on a prior insertion would
            # falsely report a missing anchor.
            after="- /content/nbk/.venv",
            description="agent-store volume mount",
        ),
        EnsureBlock(
            "build/docker-compose.yml",
            marker="\nvolumes:\n",
            block=_COMPOSE_VOLUMES_DECL,
            before_regex=r"^networks:",
            description="top-level volumes declaration",
        ),
        EnsureBlock(
            "build/docker-compose.yml",
            marker=".agent_credentials",
            block=_COMPOSE_AGENT_ENVFILE,
            after="env_file:",
            description="optional agent credentials env_file",
        ),
    ],
)
