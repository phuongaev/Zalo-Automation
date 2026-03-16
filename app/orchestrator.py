from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests

from app.adb_client import AdbClient
from app.api_client import ContentApiClient
from app.config import AccountConfig, GlobalConfig, load_app_config
from app.debug_store import DebugStore
from app.ldplayer import LDPlayerController
from app.storage import TempStorage
from app.ui_debug import UiDebug
from app.zalo_automation import ZaloAutomation

COMPLETION_WEBHOOK_URL = "https://go.dungmoda.com/webhook/zalo-mkt-dang-bai-len-tuong-done"
MAX_RETRIES = 2  # total attempts = 1 + MAX_RETRIES

log = logging.getLogger(__name__)


@dataclass
class AccountRunResult:
    account_id: str
    adb_serial: str
    emulator_index: int | None
    ok: bool
    status: str
    message: str = ""
    screenshot_path: str | None = None


# ---------------------------------------------------------------------------
# Top-level worker function (must be picklable for ProcessPoolExecutor)
# ---------------------------------------------------------------------------

def _run_single_attempt(
    account_dict: dict,
    global_cfg_dict: dict,
    text: str,
    image_urls: list[str],
    post_id: str,
) -> dict:
    """Execute one posting attempt for a single account. Runs in a child process."""
    # Reconstruct objects from dicts (each process gets fresh instances)
    account = AccountConfig.model_validate(account_dict)
    cfg = GlobalConfig.model_validate(global_cfg_dict)

    adb = AdbClient(account.adb_serial)
    auto = ZaloAutomation(account.adb_serial, dry_run=cfg.dry_run)
    ui_debug = UiDebug(account.adb_serial)
    ldplayer = LDPlayerController()
    storage = TempStorage()

    screenshot_path = Path(cfg.screenshots_root) / account.account_id / f"trigger_{post_id}.png"
    xml_path = Path(cfg.screenshots_root) / account.account_id / f"trigger_{post_id}.u2.xml"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    job_dir = storage.prepare_job_dir(account.account_id, post_id)
    remote_dir = cfg.emulator_media_dir

    try:
        if not cfg.dry_run:
            ldplayer.ensure_running(account)
            adb.connect()
            adb.wait_for_device()
            adb.clear_all_media()
            adb.delete_remote_dir(f"{cfg.emulator_media_dir}/{account.account_id}")
            local_images = storage.download_images(image_urls, job_dir) if image_urls else []
            remote_paths = adb.push_files(local_images, remote_dir) if local_images else []
            for rp in remote_paths:
                adb.scan_media(rp)
            adb.scan_media_root(remote_dir)
            adb.dismiss_ads_and_prepare_home()
            adb.start_app(cfg.zalo_package)

        login_state = auto.check_login_state()
        if login_state != "logged_in":
            login_result = auto.login_if_needed(account.login.phone, account.login.password, adb=adb)
            if not login_result.ok:
                _capture_debug(adb, ui_debug, screenshot_path, xml_path, cfg.dry_run)
                return _make_result(account, False, login_result.status, login_result.message, screenshot_path)

        result = auto.create_post(text, len(image_urls), adb=adb)
        if not result.ok:
            _capture_debug(adb, ui_debug, screenshot_path, xml_path, cfg.dry_run)
            return _make_result(account, False, result.status, result.message, screenshot_path)

        return _make_result(account, True, result.status, result.message, screenshot_path)

    except Exception as exc:
        logging.getLogger(__name__).exception("Attempt failed for %s", account.account_id)
        try:
            _capture_debug(adb, ui_debug, screenshot_path, xml_path, cfg.dry_run)
        except Exception:
            pass
        return _make_result(account, False, "failed", str(exc), screenshot_path)
    finally:
        storage.cleanup_dir(job_dir)


def _run_account_worker(
    account_dict: dict,
    global_cfg_dict: dict,
    text: str,
    image_urls: list[str],
    post_id: str,
    max_retries: int,
    batch_number: int,
) -> dict:
    """Run account with retry. This is the top-level entry point for each child process."""
    logger = logging.getLogger(__name__)
    account = AccountConfig.model_validate(account_dict)
    cfg = GlobalConfig.model_validate(global_cfg_dict)
    ldplayer = LDPlayerController()

    result: dict = {}
    total_attempts = 1 + max_retries

    for attempt in range(1, total_attempts + 1):
        logger.info(
            "[%s] Attempt %d/%d (batch %d)",
            account.account_id, attempt, total_attempts, batch_number,
        )

        result = _run_single_attempt(account_dict, global_cfg_dict, text, image_urls, post_id)

        if result["ok"]:
            logger.info("[%s] Success on attempt %d", account.account_id, attempt)
            break

        if attempt < total_attempts:
            logger.warning(
                "[%s] Failed (status=%s). Restarting emulator for retry...",
                account.account_id, result["status"],
            )
            # Stop emulator, wait, then retry (ensure_running will relaunch)
            if not cfg.dry_run:
                try:
                    ldplayer.quit(account)
                except Exception:
                    pass
                time.sleep(10)
        else:
            logger.error("[%s] All %d attempts exhausted", account.account_id, total_attempts)

    # Stop emulator after all attempts (with random delay)
    result["batch"] = batch_number
    _stop_emulator_if_needed(account, cfg, ldplayer, result["ok"])

    return result


def _stop_emulator_if_needed(account: AccountConfig, cfg: GlobalConfig, ldplayer: LDPlayerController, ok: bool) -> None:
    logger = logging.getLogger(__name__)
    should_stop = cfg.stop_emulator_after_run and not cfg.dry_run
    if not should_stop:
        return
    if cfg.keep_emulator_open_on_failure and not ok:
        logger.info("Keeping emulator open for %s because it failed", account.account_id)
        return
    delay = random.uniform(10, 20)
    logger.info("Waiting %.1f seconds before stopping emulator for %s", delay, account.account_id)
    time.sleep(delay)
    try:
        ldplayer.quit(account)
    except Exception:
        logger.exception("Failed to stop emulator for %s", account.account_id)


def _capture_debug(adb: AdbClient, ui_debug: UiDebug, screenshot_path: Path, xml_path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    try:
        adb.screenshot(screenshot_path)
    except Exception:
        pass
    try:
        ui_debug.page_source_via_u2(xml_path)
    except Exception:
        pass


def _make_result(account: AccountConfig, ok: bool, status: str, message: str, screenshot_path: Path) -> dict:
    return {
        "account_id": account.account_id,
        "adb_serial": account.adb_serial,
        "emulator_index": account.emulator_index,
        "ok": ok,
        "status": status,
        "message": message,
        "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
    }


# ---------------------------------------------------------------------------
# Orchestrator service
# ---------------------------------------------------------------------------

class TriggerRunService:
    def __init__(self) -> None:
        self.cfg = load_app_config().global_
        self.api = ContentApiClient()
        self.debug_store = DebugStore()

    def _enabled_accounts(self) -> list[AccountConfig]:
        return [a for a in load_app_config().accounts if a.enabled]

    def _chunked(self, items: list[AccountConfig], size: int) -> list[list[AccountConfig]]:
        return [items[i : i + size] for i in range(0, len(items), size)]

    def run_once(self, account_ids: list[str] | None = None) -> dict:
        payload = self.api.fetch_post()
        if payload is None:
            return {"ok": True, "status": "no_content", "message": "Content API returned no content"}

        accounts = self._enabled_accounts()
        if account_ids:
            wanted = set(account_ids)
            accounts = [a for a in accounts if a.account_id in wanted]

        if not accounts:
            return {"ok": False, "status": "no_accounts", "message": "No enabled accounts matched request"}

        batch_size = max(1, int(self.cfg.batch_size or 1))
        batches = self._chunked(accounts, batch_size)
        all_results: list[dict] = []

        # Serialize config to dicts for cross-process transfer
        global_cfg_dict = self.cfg.model_dump()

        for batch_number, batch in enumerate(batches, start=1):
            log.info("=== Batch %d: %d accounts ===", batch_number, len(batch))
            futures = []

            with ProcessPoolExecutor(max_workers=batch_size) as executor:
                for i, account in enumerate(batch):
                    # Stagger: wait 10 seconds between each emulator launch
                    if i > 0 and not self.cfg.dry_run:
                        log.info("Waiting 10s before launching next emulator...")
                        time.sleep(10)

                    log.info("Launching account %s (emulator_index=%s)", account.account_id, account.emulator_index)
                    future = executor.submit(
                        _run_account_worker,
                        account.model_dump(),
                        global_cfg_dict,
                        payload.text,
                        payload.images,
                        payload.post_id,
                        MAX_RETRIES,
                        batch_number,
                    )
                    futures.append(future)

                # Wait for all processes in this batch to complete
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        all_results.append(result)
                        # Store debug info
                        self.debug_store.append({
                            "account_id": result["account_id"],
                            "post_id": payload.post_id,
                            "ok": result["ok"],
                            "status": result["status"],
                            "message": result["message"],
                            "screenshot": result.get("screenshot_path"),
                            "xml": None,
                        })
                    except Exception:
                        log.exception("Unexpected error in account process")

            log.info("=== Batch %d completed ===", batch_number)

        success_count = sum(1 for r in all_results if r["ok"])
        report = {
            "ok": success_count == len(all_results),
            "status": "completed",
            "post": {"post_id": payload.post_id, "text": payload.text, "image_count": len(payload.images)},
            "batch_size": batch_size,
            "total_accounts": len(all_results),
            "success_count": success_count,
            "failure_count": len(all_results) - success_count,
            "results": all_results,
        }

        # Send completion report to webhook
        self._send_completion_report(report)

        return report

    def _send_completion_report(self, report: dict) -> None:
        """Send completion report to the done webhook after all accounts finish."""
        try:
            resp = requests.post(
                COMPLETION_WEBHOOK_URL,
                json=report,
                timeout=self.cfg.request_timeout_seconds,
            )
            log.info(
                "Completion report sent to %s — status=%s",
                COMPLETION_WEBHOOK_URL,
                resp.status_code,
            )
        except Exception:
            log.exception("Failed to send completion report to %s", COMPLETION_WEBHOOK_URL)
