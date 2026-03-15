from __future__ import annotations

import logging
import subprocess
import time

from app.config import AccountConfig, load_app_config

log = logging.getLogger(__name__)


class LDPlayerController:
    def __init__(self) -> None:
        cfg = load_app_config().global_
        self.ldconsole_path = cfg.ldconsole_path
        self.launch_wait_seconds = cfg.launch_wait_seconds
        self.after_launch_connect_seconds = cfg.after_launch_connect_seconds

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.ldconsole_path, *args]
        log.info("LDConsole: %s", " ".join(cmd))
        return subprocess.run(cmd, text=True, capture_output=True, check=check)

    def launch(self, account: AccountConfig) -> None:
        if account.emulator_index is not None:
            self._run("launch", "--index", str(account.emulator_index), check=False)
        elif account.emulator_name:
            self._run("launch", "--name", account.emulator_name, check=False)
        time.sleep(self.launch_wait_seconds)

    def quit(self, account: AccountConfig) -> None:
        if account.emulator_index is not None:
            self._run("quit", "--index", str(account.emulator_index), check=False)
        elif account.emulator_name:
            self._run("quit", "--name", account.emulator_name, check=False)

    def is_running(self, account: AccountConfig) -> bool:
        result = self._run("list2", check=False)
        if result.returncode != 0:
            return False
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if account.emulator_index is not None and len(parts) > 4 and parts[0] == str(account.emulator_index):
                return parts[4] == "1"
            if account.emulator_name and len(parts) > 4 and parts[1] == account.emulator_name:
                return parts[4] == "1"
        return False

    def ensure_running(self, account: AccountConfig) -> None:
        if not self.is_running(account):
            self.launch(account)
            time.sleep(self.after_launch_connect_seconds)
