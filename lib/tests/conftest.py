from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_real_secrets(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    """Point the personal secrets folder at an empty location so no test sees real ones."""
    missing = tmp_path_factory.mktemp("secrets-root") / "absent"
    monkeypatch.setenv("KATAPULT_SECRETS_DIR", str(missing))
    return missing
