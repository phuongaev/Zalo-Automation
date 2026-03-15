from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from app.config import load_app_config


class UiDebug:
    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.adb_path = load_app_config().global_.adb_path

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.adb_path, "-s", self.serial, *args]
        return subprocess.run(cmd, text=True, capture_output=True, check=check)

    def capture_screenshot(self, out_path: Path) -> str:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        remote = "/sdcard/__zalo_debug_screen.png"
        self._run("shell", "screencap", "-p", remote)
        self._run("pull", remote, str(out_path))
        self._run("shell", "rm", "-f", remote, check=False)
        return str(out_path)

    def dump_ui_xml(self, out_path: Path) -> dict[str, Any]:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        remote = "/sdcard/window_dump.xml"
        result = self._run("shell", "uiautomator", "dump", remote, check=False)
        pulled = False
        pull_error = ""
        if result.returncode == 0:
            pull_result = self._run("pull", remote, str(out_path), check=False)
            pulled = pull_result.returncode == 0 and out_path.exists()
            if not pulled:
                pull_error = (pull_result.stderr or pull_result.stdout or "").strip()
        self._run("shell", "rm", "-f", remote, check=False)
        return {
            "ok": pulled,
            "path": str(out_path),
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
            "pull_error": pull_error,
            "returncode": result.returncode,
        }

    def page_source_via_u2(self, out_path: Path) -> dict[str, Any]:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import uiautomator2 as u2
        except Exception as exc:
            return {"ok": False, "error": f"uiautomator2 import failed: {exc}"}

        try:
            device = u2.connect(self.serial)
            xml = device.dump_hierarchy(compressed=False)
            out_path.write_text(xml, encoding="utf-8")
            return {"ok": True, "path": str(out_path), "length": len(xml)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
