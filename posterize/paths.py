"""Shared path-like type and conversion helper."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypeAlias

Pathish: TypeAlias = str | os.PathLike[str]


def as_path(path: Pathish) -> Path:
    """Convert string/path-like input to ``Path`` at package boundaries."""

    return Path(path)
