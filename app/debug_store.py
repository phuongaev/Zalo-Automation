from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.config import load_app_config


class DebugStore:
    def __init__(self) -> None:
        cfg = load_app_config().global_
        self.root = Path(cfg.log_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "recent_triggers.json"
        self.screenshots_root = Path(cfg.screenshots_root)

    def append(self, item: dict[str, Any]) -> None:
        data = self.read()
        entries = list(data.get("triggers", []))
        item = {"ts": time.time(), **item}
        entries.append(item)
        entries = entries[-3:]
        payload = {"triggers": entries}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"triggers": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"triggers": []}

    def reset(self) -> dict[str, Any]:
        removed = {
            "recent_triggers": False,
            "logs": [],
            "screenshots": [],
            "errors": [],
        }
        if self.path.exists():
            self.path.write_text(json.dumps({"triggers": []}, ensure_ascii=False, indent=2), encoding="utf-8")
            removed["recent_triggers"] = True

        for log_file in self.root.glob("*.log"):
            try:
                log_file.write_text("", encoding="utf-8")
                removed["logs"].append(str(log_file))
            except Exception as exc:
                removed["errors"].append({"path": str(log_file), "error": str(exc)})

        if self.screenshots_root.exists():
            for path in self.screenshots_root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".png", ".xml"}:
                    try:
                        removed["screenshots"].append(str(path))
                        path.unlink(missing_ok=True)
                    except Exception as exc:
                        removed["errors"].append({"path": str(path), "error": str(exc)})

        return {"ok": True, "removed": removed}
