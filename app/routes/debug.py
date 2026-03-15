from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app.adb_client import AdbClient
from app.config import load_app_config
from app.debug_store import DebugStore
from app.ldplayer import LDPlayerController
from app.ui_debug import UiDebug

router = APIRouter()
debug_store = DebugStore()


def _find_account(account_id: str):
    cfg = load_app_config()
    account = next((a for a in cfg.accounts if a.account_id == account_id), None)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Unknown account_id: {account_id}")
    return cfg, account


def _debug_root(account_id: str) -> Path:
    cfg = load_app_config()
    return Path(cfg.global_.screenshots_root) / account_id / "debug"


@router.post("/api/debug/capture/{account_id}")
def debug_capture(account_id: str) -> dict:
    cfg, account = _find_account(account_id)

    root = _debug_root(account_id)
    root.mkdir(parents=True, exist_ok=True)

    ldplayer = LDPlayerController()
    adb = AdbClient(account.adb_serial)
    debug = UiDebug(account.adb_serial)
    screenshot = root / "screen.png"
    adb_xml = root / "window_dump.adb.xml"
    u2_xml = root / "window_dump.u2.xml"

    try:
        ldplayer.ensure_running(account)
        adb.connect()
        adb.wait_for_device()
        adb.start_app(cfg.global_.zalo_package)
        time.sleep(5)
    except Exception as exc:
        return {
            "ok": False,
            "account_id": account_id,
            "adb_serial": account.adb_serial,
            "emulator_index": account.emulator_index,
            "error": f"Failed to start/connect emulator or open Zalo: {exc}",
        }

    screenshot_ok = False
    screenshot_error = ""
    try:
        screenshot_path = debug.capture_screenshot(screenshot)
        screenshot_ok = True
    except Exception as exc:
        screenshot_path = str(screenshot)
        screenshot_error = str(exc)

    adb_dump = debug.dump_ui_xml(adb_xml)
    u2_dump = debug.page_source_via_u2(u2_xml)

    return {
        "ok": screenshot_ok or bool(adb_dump.get("ok")) or bool(u2_dump.get("ok")),
        "account_id": account_id,
        "adb_serial": account.adb_serial,
        "emulator_index": account.emulator_index,
        "screenshot": {
            "ok": screenshot_ok,
            "path": screenshot_path,
            "url": f"/api/debug/file/{account_id}/screen.png",
            "error": screenshot_error,
        },
        "adb_dump": {
            **adb_dump,
            "url": f"/api/debug/file/{account_id}/window_dump.adb.xml",
        },
        "u2_dump": {
            **u2_dump,
            "url": f"/api/debug/file/{account_id}/window_dump.u2.xml",
        },
    }


@router.get("/api/debug/files/{account_id}")
def debug_files(account_id: str) -> dict:
    _cfg, account = _find_account(account_id)
    root = _debug_root(account_id)
    root.mkdir(parents=True, exist_ok=True)

    debug_items = []
    for path in sorted(root.glob("*")):
        if path.is_file():
            debug_items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "url": f"/api/debug/file/{account_id}/{path.name}",
                    "modified": path.stat().st_mtime,
                }
            )

    trigger_root = Path(load_app_config().global_.screenshots_root) / account_id
    trigger_items = []
    for path in sorted(trigger_root.glob("trigger_*")):
        if path.is_file():
            trigger_items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "url": f"/api/debug/trigger-file/{account_id}/{path.name}",
                    "modified": path.stat().st_mtime,
                }
            )

    return {
        "ok": True,
        "account_id": account.account_id,
        "adb_serial": account.adb_serial,
        "emulator_index": account.emulator_index,
        "debug_files": debug_items,
        "trigger_files": trigger_items,
    }


@router.get("/api/debug/file/{account_id}/{filename}")
def debug_file(account_id: str, filename: str):
    _cfg, _account = _find_account(account_id)
    path = (_debug_root(account_id) / filename).resolve()
    root = _debug_root(account_id).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if path.suffix.lower() in {".xml", ".log", ".txt"}:
        return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))
    return FileResponse(path)


@router.get("/api/debug/trigger-file/{account_id}/{filename}")
def trigger_file(account_id: str, filename: str):
    _cfg, _account = _find_account(account_id)
    root = (Path(load_app_config().global_.screenshots_root) / account_id).resolve()
    path = (root / filename).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if path.suffix.lower() in {".xml", ".log", ".txt"}:
        return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))
    return FileResponse(path)


@router.get("/api/logs")
def read_logs(lines: int = 200) -> PlainTextResponse:
    cfg = load_app_config()
    log_path = Path(cfg.global_.log_dir) / "app.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = content[-max(1, min(lines, 2000)) :]
    return PlainTextResponse("\n".join(tail))


@router.get("/api/debug/recent-triggers")
def recent_triggers() -> dict:
    return debug_store.read()


@router.post("/api/debug/reset")
def reset_debug() -> dict:
    return debug_store.reset()
