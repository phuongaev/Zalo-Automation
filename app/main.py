from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import load_app_config
from app.logger import setup_logging
from app.orchestrator import TriggerRunService
from app.routes.debug import router as debug_router

setup_logging()
app = FastAPI(title="Zalo Post Trigger API")
app.include_router(debug_router)
service = TriggerRunService()


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
    account_ids = body.account_ids if body else []
    return service.run_once(account_ids=account_ids or None)
