"""Ordered registry of katapult project migrations.

To add a migration for a future release: create ``mNNNN_<slug>.py`` next to this file
exposing a module-level ``migration = Migration(...)``, then append it to ``REGISTRY``
below (order matters -- ids are applied in list order). Keep file *bodies* single-sourced
from the project template via ``AddFile`` rather than duplicating content here.
"""

from __future__ import annotations

from katapult.migrations.base import Migration
from katapult.migrations.m0001_add_internal_mcp import migration as m0001_add_internal_mcp
from katapult.migrations.m0002_seed_project_rules import (
    migration as m0002_seed_project_rules,
)

# Applied in order; a project's manifest records the ids it has already received.
REGISTRY: list[Migration] = [
    m0001_add_internal_mcp,
    m0002_seed_project_rules,
]


def all_ids() -> list[str]:
    """Ids of every registered migration, in apply order."""
    return [m.id for m in REGISTRY]
