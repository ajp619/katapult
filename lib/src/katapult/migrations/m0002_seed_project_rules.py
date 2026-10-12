"""0002 -- seed the generic project `.cursor/rules`.

Older projects were generated before katapult shipped project-level Cursor rules, so the
in-container "second brain" had no engineering standards to follow. This migration drops
the generic, vendor-neutral rules into `.cursor/rules/` so the delegate subagent
(`setting_sources=["all"]`, `cwd=/content`) picks them up:

- `engineering-standards.mdc` -- dependencies via uv, docs/README upkeep, security, tests;
- `katapult-project-conventions.mdc` -- project anatomy + documentation layout;
- `notebook-standards.mdc` -- narrative structure + jupytext pairing + cell labels.

Org-specific rules (e.g. Athena/Salesforce query context) stay in the ``~/.katapult``
overlay, not here. Files are copied create-only, so a project that already authored its
own rule of the same name is never clobbered (a ``.katapult-new`` sidecar is written
instead). The migration intentionally does **not** touch `nbk/` user code: for a project
that predates jupytext pairing, the seeded `notebook-standards.mdc` documents the one-time
`nbk/` setup to apply in context.
"""

from __future__ import annotations

from katapult.migrations.base import AddFile, Migration

_RULES = [
    "engineering-standards.mdc",
    "katapult-project-conventions.mdc",
    "notebook-standards.mdc",
]

migration = Migration(
    id="0002_seed_project_rules",
    katapult_version="0.11.1",
    description="Seed generic project .cursor/rules (engineering standards, conventions, notebooks).",
    actions=[
        AddFile(f".cursor/rules/{name}", f".cursor/rules/{name}") for name in _RULES
    ],
)
