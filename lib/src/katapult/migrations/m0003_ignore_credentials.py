"""0003 -- keep credential files and handoff data out of git and out of the image.

The project Dockerfile does ``COPY . /content/``, so anything not docker-ignored is baked
into the image. Older templates git-ignored only ``build/.agent_credentials`` and
docker-ignored neither credentials nor ``.handoff/``; projects patched this by hand,
inconsistently. This migration appends the same patterns the template now ships:

- ``.gitignore``: ``build/.*_credentials`` (with a ``.example`` exception);
- ``.dockerignore``: the same, plus ``.handoff/``.

Each block is marker-guarded, so re-running is a no-op. A project that already covers these
files with a different pattern (e.g. ``**/.*_credentials``) just gets a redundant line. A
project with no ``.dockerignore`` is skipped (reported as missing) rather than given a new one.
"""

from __future__ import annotations

from katapult.migrations.base import EnsureBlock, Migration

_CREDENTIALS_BLOCK = """\
# Credential env files (any build/.<name>_credentials); keep only the .example templates
build/.*_credentials
!build/.*_credentials.example
"""

_HANDOFF_BLOCK = """\
# MCP handoff data-plane (transient inter-agent exchange)
.handoff/
"""

migration = Migration(
    id="0003_ignore_credentials",
    katapult_version="0.11.1",
    description="Git- and docker-ignore build/.*_credentials; docker-ignore .handoff/.",
    actions=[
        EnsureBlock(
            ".gitignore",
            marker="build/.*_credentials",
            block=_CREDENTIALS_BLOCK,
            description="git-ignore credential env files",
        ),
        EnsureBlock(
            ".dockerignore",
            marker="build/.*_credentials",
            block=_CREDENTIALS_BLOCK,
            description="docker-ignore credential env files",
        ),
        EnsureBlock(
            ".dockerignore",
            marker=".handoff",
            block=_HANDOFF_BLOCK,
            description="docker-ignore .handoff/",
        ),
    ],
)
