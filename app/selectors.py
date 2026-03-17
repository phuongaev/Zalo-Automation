from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@lru_cache(maxsize=1)
def load_selectors() -> dict[str, list[dict[str, Any]]]:
    path = Path(__file__).resolve().parent.parent / "config" / "selectors.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw.get("selectors", {})
