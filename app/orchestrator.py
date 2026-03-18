from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
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
MAX_RETRIES = 2  # total attempts = 1 + MAX_RETRIES
ACCOUNT_TIMEOUT = 300  # 5 minutes max per account attempt

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

    def _run_account(self, account: AccountConfig, text: str, image_urls: list[str], post_id: str) -> AccountRunResult:
        adb = AdbClient(account.adb_serial)
        auto = ZaloAutomation(account.adb_serial, dry_run=self.cfg.dry_run)
        ui_debug = UiDebug(account.adb_serial)
        screenshot_path = Path(self.cfg.screenshots_root) / account.account_id / f"trigger_{post_id}.png"
        xml_path = Path(self.cfg.screenshots_root) / account.account_id / f"trigger_{post_id}.u2.xml"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        job_dir = self.storage.prepare_job_dir(account.account_id, post_id)
        remote_dir = self.cfg.emulator_media_dir

        try:
            if not self.cfg.dry_run:
                self.ldplayer.ensure_running(account)
                adb.ensure_connected()
                adb.clear_all_media()
                adb.delete_remote_dir(f"{self.cfg.emulator_media_dir}/{account.account_id}")
                local_images = self.storage.download_images(image_urls, job_dir) if image_urls else []
                remote_paths = adb.push_files(local_images, remote_dir) if local_images else []
                for remote_path in remote_paths:
                    adb.scan_media(remote_path)
                adb.scan_media_root(remote_dir)
                adb.dismiss_ads_and_prepare_home()
                adb.start_app(self.cfg.zalo_package)
                # Wait for Zalo to fully load before checking login state
                log.info("[%s] Waiting 8s for Zalo UI to load...", account.account_id)
                time.sleep(8)

            # Check login state with retry (poll every 3s for up to 15s)
            login_state = auto.check_login_state(wait_seconds=15)
            log.info("[%s] Login state: %s", account.account_id, login_state)
            if login_state == "unknown":
                log.info("[%s] State unknown — assuming logged in, skipping login", account.account_id)
            elif login_state == "logged_out":
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
            self.storage.cleanup_dir(job_dir)

    def _run_account_with_timeout(self, account: AccountConfig, text: str, image_urls: list[str], post_id: str) -> AccountRunResult:
        """Run _run_account with a timeout. Returns failure result if timeout exceeded."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._run_account, account, text, image_urls, post_id)
            try:
                return future.result(timeout=ACCOUNT_TIMEOUT)
            except FuturesTimeoutError:
                log.error("[%s] Account run timed out after %ds", account.account_id, ACCOUNT_TIMEOUT)
                # Force kill emulator to unblock
                if not self.cfg.dry_run:
                    try:
                        self.ldplayer.quit(account)
                    except Exception:
                        pass
                return AccountRunResult(
                    account_id=account.account_id,
                    adb_serial=account.adb_serial,
                    emulator_index=account.emulator_index,
                    ok=False,
                    status="timeout",
                    message=f"Timed out after {ACCOUNT_TIMEOUT}s",
                )
            except Exception as exc:
                log.exception("[%s] Unexpected error in timeout wrapper", account.account_id)
                return AccountRunResult(
                    account_id=account.account_id,
                    adb_serial=account.adb_serial,
                    emulator_index=account.emulator_index,
                    ok=False,
                    status="failed",
                    message=str(exc),
                )

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

        all_results: list[dict] = []

        # Run accounts sequentially — one emulator at a time
        for i, account in enumerate(accounts):
            log.info(
                "=== Account %d/%d: %s (emulator_index=%s) ===",
                i + 1, len(accounts), account.account_id, account.emulator_index,
            )

            # Retry loop with per-account timeout
            final_result: AccountRunResult | None = None
            total_attempts = 1 + MAX_RETRIES

            for attempt in range(1, total_attempts + 1):
                log.info("[%s] Attempt %d/%d (timeout=%ds)", account.account_id, attempt, total_attempts, ACCOUNT_TIMEOUT)

                # Run with timeout to prevent hanging forever
                result = self._run_account_with_timeout(account, payload.text, payload.images, payload.post_id)
                final_result = result

                if result.ok:
                    log.info("[%s] Success on attempt %d", account.account_id, attempt)
                    break

                if attempt < total_attempts:
                    log.warning(
                        "[%s] Failed (status=%s, message=%s). Restarting emulator for retry...",
                        account.account_id, result.status, result.message,
                    )
                    if not self.cfg.dry_run:
                        try:
                            self.ldplayer.quit(account)
                        except Exception:
                            log.exception("Failed to stop emulator for retry")
                        time.sleep(10)
                else:
                    log.error("[%s] All %d attempts exhausted", account.account_id, total_attempts)

            # Append final result
            row = asdict(final_result)
            all_results.append(row)

            # Stop emulator after this account finishes
            self._stop_emulator(account, final_result.ok)

            # Delay 10 seconds before starting next account
            if i < len(accounts) - 1:
                log.info("Waiting 10s before starting next account...")
                time.sleep(10)

        success_accounts = [r for r in all_results if r["ok"]]
        failure_accounts = [r for r in all_results if not r["ok"]]
        report = {
            "ok": len(failure_accounts) == 0,
            "status": "completed",
            "row_index": payload.row_index,
            "post": {
                "post_id": payload.post_id,
                "text": payload.text,
                "image_count": len(payload.images),
                "row_index": payload.row_index,
            },
            "total_accounts": len(all_results),
            "success_count": len(success_accounts),
            "failure_count": len(failure_accounts),
            "success_accounts": [
                {"account_id": r["account_id"], "emulator_index": r["emulator_index"], "status": r["status"], "message": r["message"]}
                for r in success_accounts
            ],
            "failure_accounts": [
                {"account_id": r["account_id"], "emulator_index": r["emulator_index"], "status": r["status"], "message": r["message"]}
                for r in failure_accounts
            ],
            "results": all_results,
        }

        self._send_completion_report(report)
        return report

    def _stop_emulator(self, account: AccountConfig, ok: bool) -> None:
        """Stop emulator with random delay 10-20s."""
        should_stop = self.cfg.stop_emulator_after_run and not self.cfg.dry_run
        if not should_stop:
            return
        if self.cfg.keep_emulator_open_on_failure and not ok:
            log.info("Keeping emulator open for %s because it failed", account.account_id)
            return
        delay = random.uniform(10, 20)
        log.info("Waiting %.1f seconds before stopping emulator for %s", delay, account.account_id)
        time.sleep(delay)
        try:
            self.ldplayer.quit(account)
        except Exception:
            log.exception("Failed to stop emulator for %s", account.account_id)

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
