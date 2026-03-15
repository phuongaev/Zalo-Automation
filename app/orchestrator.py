from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from app.adb_client import AdbClient
from app.api_client import ContentApiClient
from app.config import AccountConfig, load_app_config
from app.debug_store import DebugStore
from app.ldplayer import LDPlayerController
from app.storage import TempStorage
from app.ui_debug import UiDebug
from app.zalo_automation import ZaloAutomation

COMPLETION_WEBHOOK_URL = "https://go.dungmoda.com/webhook/zalo-mkt-dang-bai-len-tuong-done"

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


class TriggerRunService:
    def __init__(self) -> None:
        self.cfg = load_app_config().global_
        self.api = ContentApiClient()
        self.storage = TempStorage()
        self.ldplayer = LDPlayerController()
        self.debug_store = DebugStore()

    def _enabled_accounts(self) -> list[AccountConfig]:
        return [a for a in load_app_config().accounts if a.enabled]

    def _chunked(self, items: list[AccountConfig], size: int) -> list[list[AccountConfig]]:
        return [items[i : i + size] for i in range(0, len(items), size)]

    def _run_account(self, account: AccountConfig, text: str, image_urls: list[str], post_id: str) -> AccountRunResult:
        adb = AdbClient(account.adb_serial)
        auto = ZaloAutomation(account.adb_serial, dry_run=self.cfg.dry_run)
        ui_debug = UiDebug(account.adb_serial)
        screenshot_path = Path(self.cfg.screenshots_root) / account.account_id / f"trigger_{post_id}.png"
        xml_path = Path(self.cfg.screenshots_root) / account.account_id / f"trigger_{post_id}.u2.xml"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        job_dir = self.storage.prepare_job_dir(account.account_id, post_id)
        # Push images into a top-level media folder so Zalo quick picker can see them reliably.
        remote_dir = self.cfg.emulator_media_dir
        remote_paths: list[str] = []

        try:
            if not self.cfg.dry_run:
                self.ldplayer.ensure_running(account)
                adb.connect()
                adb.wait_for_device()
                # Clear all old media on emulator first, then push only the current API images.
                adb.clear_all_media()
                adb.delete_remote_dir(f"{self.cfg.emulator_media_dir}/{account.account_id}")
                local_images = self.storage.download_images(image_urls, job_dir) if image_urls else []
                remote_paths = adb.push_files(local_images, remote_dir) if local_images else []
                for remote_path in remote_paths:
                    adb.scan_media(remote_path)
                adb.scan_media_root(remote_dir)
                adb.dismiss_ads_and_prepare_home()
                adb.start_app(self.cfg.zalo_package)

            login_state = auto.check_login_state()
            if login_state != "logged_in":
                login_result = auto.login_if_needed(account.login.phone, account.login.password, adb=adb)
                if not login_result.ok:
                    if not self.cfg.dry_run:
                        adb.screenshot(screenshot_path)
                        try:
                            ui_debug.page_source_via_u2(xml_path)
                        except Exception:
                            pass
                    self.debug_store.append({
                        "account_id": account.account_id,
                        "post_id": post_id,
                        "ok": False,
                        "status": login_result.status,
                        "message": login_result.message,
                        "screenshot": str(screenshot_path) if screenshot_path.exists() else None,
                        "xml": str(xml_path) if xml_path.exists() else None,
                    })
                    return AccountRunResult(
                        account_id=account.account_id,
                        adb_serial=account.adb_serial,
                        emulator_index=account.emulator_index,
                        ok=False,
                        status=login_result.status,
                        message=login_result.message,
                        screenshot_path=str(screenshot_path) if screenshot_path.exists() else None,
                    )

            result = auto.create_post(text, len(image_urls), adb=adb)
            if not result.ok:
                if not self.cfg.dry_run:
                    adb.screenshot(screenshot_path)
                    try:
                        ui_debug.page_source_via_u2(xml_path)
                    except Exception:
                        pass
                self.debug_store.append({
                    "account_id": account.account_id,
                    "post_id": post_id,
                    "ok": False,
                    "status": result.status,
                    "message": result.message,
                    "screenshot": str(screenshot_path) if screenshot_path.exists() else None,
                    "xml": str(xml_path) if xml_path.exists() else None,
                })
                return AccountRunResult(
                    account_id=account.account_id,
                    adb_serial=account.adb_serial,
                    emulator_index=account.emulator_index,
                    ok=False,
                    status=result.status,
                    message=result.message,
                    screenshot_path=str(screenshot_path) if screenshot_path.exists() else None,
                )

            self.debug_store.append({
                "account_id": account.account_id,
                "post_id": post_id,
                "ok": True,
                "status": result.status,
                "message": result.message,
                "screenshot": None,
                "xml": None,
            })
            return AccountRunResult(
                account_id=account.account_id,
                adb_serial=account.adb_serial,
                emulator_index=account.emulator_index,
                ok=True,
                status=result.status,
                message=result.message,
            )
        except Exception as exc:
            log.exception("Trigger run failed for %s", account.account_id)
            try:
                if not self.cfg.dry_run:
                    adb.screenshot(screenshot_path)
                    try:
                        ui_debug.page_source_via_u2(xml_path)
                    except Exception:
                        pass
            except Exception:
                pass
            self.debug_store.append({
                "account_id": account.account_id,
                "post_id": post_id,
                "ok": False,
                "status": "failed",
                "message": str(exc),
                "screenshot": str(screenshot_path) if screenshot_path.exists() else None,
                "xml": str(xml_path) if xml_path.exists() else None,
            })
            return AccountRunResult(
                account_id=account.account_id,
                adb_serial=account.adb_serial,
                emulator_index=account.emulator_index,
                ok=False,
                status="failed",
                message=str(exc),
                screenshot_path=str(screenshot_path) if screenshot_path.exists() else None,
            )
        finally:
            # Keep pushed media on emulator during the full post flow.
            # We clear the whole account media root at the start of the next run instead.
            self.storage.cleanup_dir(job_dir)

    def _run_account_thread(
        self,
        account: AccountConfig,
        text: str,
        image_urls: list[str],
        post_id: str,
        batch_number: int,
        results_lock: threading.Lock,
        all_results: list[dict],
    ) -> None:
        """Run a single account in its own thread. Appends result to all_results (thread-safe)."""
        result = self._run_account(account, text, image_urls, post_id)
        row = asdict(result)
        row["batch"] = batch_number
        with results_lock:
            all_results.append(row)

        # Each emulator stops itself after finishing (with random delay)
        should_stop = self.cfg.stop_emulator_after_run and not self.cfg.dry_run
        if should_stop:
            if self.cfg.keep_emulator_open_on_failure and not result.ok:
                log.info("Keeping emulator open for %s because it failed", account.account_id)
            else:
                delay = random.uniform(10, 20)
                log.info("Waiting %.1f seconds before stopping emulator for %s", delay, account.account_id)
                time.sleep(delay)
                try:
                    self.ldplayer.quit(account)
                except Exception:
                    log.exception("Failed to stop emulator for %s", account.account_id)

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
        results_lock = threading.Lock()

        for batch_number, batch in enumerate(batches, start=1):
            log.info("=== Batch %d: %d accounts ===", batch_number, len(batch))
            futures = []

            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                for i, account in enumerate(batch):
                    # Stagger: wait 10 seconds between each emulator launch
                    if i > 0 and not self.cfg.dry_run:
                        log.info("Waiting 10s before launching next emulator...")
                        time.sleep(10)

                    log.info("Launching account %s (emulator_index=%s)", account.account_id, account.emulator_index)
                    future = executor.submit(
                        self._run_account_thread,
                        account,
                        payload.text,
                        payload.images,
                        payload.post_id,
                        batch_number,
                        results_lock,
                        all_results,
                    )
                    futures.append(future)

                # Wait for all threads in this batch to complete
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        log.exception("Unexpected error in account thread")

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
