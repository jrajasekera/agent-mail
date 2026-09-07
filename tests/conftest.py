import os
from pathlib import Path

import pytest

from amail import db


def clean_env() -> dict[str, str]:
    """os.environ minus harness/amail vars — subprocess tests must not
    inherit the developer's own session identity."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("AMAIL_", "CLAUDE", "CODEX"))}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return db.amail_home({"AMAIL_HOME": str(tmp_path / "amail-home")})


@pytest.fixture
def conn(home: Path):
    c = db.connect(home)
    yield c
    c.close()
