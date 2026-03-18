from __future__ import annotations

import logging
import threading
import time

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import load_app_config
from app.logger import setup_logging
from app.orchestrator import TriggerRunService
from app.routes.debug import router as debug_router

setup_logging()
log = logging.getLogger(__name__)

app = FastAPI(title="Zalo Post Trigger API")
app.include_router(debug_router)
service = TriggerRunService()

# ---------------------------------------------------------------------------
# Background job state
# ---------------------------------------------------------------------------
_job_lock = threading.Lock()
_current_job: dict | None = None


class TriggerRequest(BaseModel):
    account_ids: list[str] = Field(default_factory=list)


@app.get("/health")
def health() -> dict:
    cfg = load_app_config()
    enabled_accounts = [
        {
            "account_id": a.account_id,
            "adb_serial": a.adb_serial,
            "emulator_index": a.emulator_index,
            "enabled": a.enabled,
        }
        for a in cfg.accounts
        if a.enabled
    ]
    return {
        "ok": True,
        "dry_run": cfg.global_.dry_run,
        "batch_size": cfg.global_.batch_size,
        "content_api": cfg.global_.content_api.url,
        "enabled_accounts": enabled_accounts,
    }


@app.post("/trigger")
def trigger_run(body: TriggerRequest | None = None) -> dict:
    """Start automation in background. Returns immediately."""
    global _current_job
    account_ids = body.account_ids if body else []

    with _job_lock:
        # Reject if a job is already running
        if _current_job is not None and _current_job["thread"].is_alive():
            return {"ok": False, "status": "busy", "message": "A job is already running. Check /status for progress."}

        job: dict = {
            "started_at": time.time(),
            "result": None,
            "finished_at": None,
            "error": None,
            "thread": None,
        }

        def worker() -> None:
            try:
                log.info("Background job started")
                result = service.run_once(account_ids=account_ids or None)
                job["result"] = result
                log.info("Background job completed: %s", result.get("status"))
            except Exception as exc:
                log.exception("Background job failed")
                job["error"] = str(exc)
            finally:
                job["finished_at"] = time.time()

        t = threading.Thread(target=worker, daemon=True)
        job["thread"] = t
        _current_job = job
        t.start()

    return {"ok": True, "status": "accepted", "message": "Job started in background. Check /status for progress."}


@app.get("/status")
def job_status() -> dict:
    """Check current/last job status."""
    if _current_job is None:
        return {"ok": True, "status": "idle", "message": "No job has been run yet"}

    running = _current_job["thread"].is_alive()
    elapsed = time.time() - _current_job["started_at"]

    resp: dict = {
        "ok": True,
        "status": "running" if running else "completed",
        "started_at": _current_job["started_at"],
        "elapsed_seconds": round(elapsed, 1),
    }

    if not running:
        resp["finished_at"] = _current_job.get("finished_at")
        resp["result"] = _current_job.get("result")
        if _current_job.get("error"):
            resp["status"] = "error"
            resp["error"] = _current_job["error"]

    return resp


@app.post("/reset")
def reset_job() -> dict:
    """Force reset busy state. Use when a job is stuck."""
    global _current_job
    with _job_lock:
        if _current_job is None:
            return {"ok": True, "message": "No job to reset"}
        was_running = _current_job["thread"].is_alive()
        _current_job = None
    return {"ok": True, "message": f"Job reset (was_running={was_running}). You can now trigger again."}
