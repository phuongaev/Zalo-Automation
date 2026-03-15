from __future__ import annotations

import base64
import logging
import subprocess
from pathlib import Path

from app.config import load_app_config

log = logging.getLogger(__name__)


class AdbClient:
    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.adb_path = load_app_config().global_.adb_path

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.adb_path, "-s", self.serial, *args]
        log.info("ADB: %s", " ".join(cmd))
        return subprocess.run(cmd, text=True, capture_output=True, check=check)

    def connect(self) -> None:
        self._run("connect", self.serial, check=False)

    def wait_for_device(self) -> None:
        self._run("wait-for-device")

    def start_app(self, package: str) -> None:
        self._run("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")

    def push_files(self, local_files: list[Path], remote_dir: str) -> list[str]:
        self._run("shell", "mkdir", "-p", remote_dir)
        remote_paths: list[str] = []
        for path in local_files:
            remote_path = f"{remote_dir}/{path.name}"
            self._run("push", str(path), remote_path)
            remote_paths.append(remote_path)
        return remote_paths

    def delete_remote_dir(self, remote_dir: str) -> None:
        self._run("shell", "rm", "-rf", remote_dir, check=False)

    def clear_all_media(self) -> None:
        log.info("Clearing all emulator media roots before pushing API images")
        # User requested: clear all old images on emulator before pushing the new API set.
        # Remove whole media trees, clear MediaStore, then recreate a clean Camera folder.
        for remote_dir in [
            "/sdcard/DCIM",
            "/sdcard/Pictures",
            "/sdcard/Download",
            "/sdcard/Movies",
            "/storage/emulated/0/DCIM",
            "/storage/emulated/0/Pictures",
            "/storage/emulated/0/Download",
            "/storage/emulated/0/Movies",
        ]:
            self._run("shell", "rm", "-rf", remote_dir, check=False)
        self._run("shell", "content", "delete", "--uri", "content://media/external/images/media", check=False)
        self._run("shell", "content", "delete", "--uri", "content://media/external/file", check=False)
        self._run("shell", "mkdir", "-p", "/sdcard/DCIM/Camera", check=False)
        self._run("shell", "mkdir", "-p", "/storage/emulated/0/DCIM/Camera", check=False)

    def dismiss_ads_and_prepare_home(self) -> None:
        log.info("Dismissing emulator ads/overlays before opening Zalo")
        # Back out of overlays, go home, clear recents, stop common ad/browser packages.
        for _ in range(4):
            self.keyevent("4")
        self.keyevent("3")
        self.keyevent("187")
        self.keyevent("4")
        for pkg in [
            "com.android.browser",
            "com.android.chrome",
            "com.ldmnq.launcher3",
            "com.android.vending",
            "com.google.android.youtube",
        ]:
            self._run("shell", "am", "force-stop", pkg, check=False)
        self.keyevent("3")

    def scan_media_root(self, remote_dir: str) -> None:
        self._run(
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_DIR",
            "-d",
            f"file://{remote_dir}",
            check=False,
        )

    def scan_media(self, remote_path: str) -> None:
        self._run(
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{remote_path}",
            check=False,
        )

    def force_adb_keyboard(self) -> None:
        # Force ADB Keyboard every time before text input.
        log.info("Forcing ADB Keyboard IME")
        self._run("shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME", check=False)
        self._run("shell", "ime", "set", "com.android.adbkeyboard/.AdbIME", check=False)
        self._run("shell", "am", "broadcast", "-a", "ADB_INPUT_METHOD", check=False)

    def input_text_adb_keyboard(self, value: str) -> None:
        self.force_adb_keyboard()
        log.info("Input text via ADB Keyboard broadcast")
        self._run("shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", value, check=False)

    def input_text_adb_keyboard_b64(self, value: str) -> None:
        self.force_adb_keyboard()
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        log.info("Input text via ADB Keyboard base64 broadcast")
        self._run("shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded, check=False)

    def input_text(self, value: str) -> None:
        safe = value.replace(" ", "%s")
        self.force_adb_keyboard()
        self._run("shell", "input", "text", safe, check=False)

    def keyevent(self, key_code: str) -> None:
        self._run("shell", "input", "keyevent", key_code, check=False)

    def keycombo(self, *key_codes: str) -> None:
        self._run("shell", "input", "keycombo", *key_codes, check=False)

    def tap(self, x: int, y: int) -> None:
        self._run("shell", "input", "tap", str(x), str(y), check=False)

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        self._run("shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms), check=False)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 180) -> None:
        self._run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms), check=False)

    def clear_focused_text_field(self, backspaces: int = 24) -> None:
        log.info("Clearing focused text field")
        self.keycombo("113", "29")  # CTRL + A
        self.keyevent("67")          # DEL
        for _ in range(backspaces):
            self.keyevent("67")

    def screenshot(self, local_path: Path) -> None:
        remote = "/sdcard/__zalo_auto_screen.png"
        self._run("shell", "screencap", "-p", remote)
        self._run("pull", remote, str(local_path))
        self._run("shell", "rm", "-f", remote, check=False)
