"""Zalo Auto Server – Modern GUI Launcher.

Double-click gui.pyw to open.
Uses customtkinter for modern UI. Falls back to tkinter if unavailable.
All blocking operations run on background threads — UI never freezes.
"""

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Try customtkinter, fallback to tkinter
# ---------------------------------------------------------------------------
try:
    import customtkinter as ctk
    HAS_CTK = True
except ImportError:
    HAS_CTK = False

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
RUN_SCRIPT = str(APP_DIR / "run.py")
PID_FILE = APP_DIR / ".server.pid"
LOG_FILE = APP_DIR / "logs" / "server_stderr.log"
ICON_FILE = APP_DIR / "assets" / "zalo_icon.ico"

_venv_python = APP_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON = str(_venv_python) if _venv_python.exists() else sys.executable

SERVER_PORT = 8787
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
CLR_BLUE = "#0068FF"       # Zalo blue
CLR_GREEN = "#22C55E"      # Running
CLR_RED = "#EF4444"        # Stopped
CLR_ORANGE = "#F59E0B"     # Loading
CLR_GRAY = "#6B7280"       # Muted text
CLR_BG = "#1E1E2E"         # Dark background
CLR_CARD = "#2A2A3C"       # Card background
CLR_TEXT = "#E2E8F0"       # Main text
CLR_MUTED = "#94A3B8"      # Muted text


# ===================================================================
# Backend helpers (no UI references — safe to call from any thread)
# ===================================================================

def _install_deps() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [PYTHON, "-m", "pip", "install", "-e", str(APP_DIR)],
            cwd=str(APP_DIR), capture_output=True, text=True, timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as exc:
        return False, str(exc)


def _read_pid() -> int | None:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            PID_FILE.unlink(missing_ok=True)
    return None


def _server_responding() -> bool:
    try:
        urlopen(f"{BASE_URL}/status", timeout=2)
        return True
    except Exception:
        return False


def _fetch_status() -> dict | None:
    try:
        resp = urlopen(f"{BASE_URL}/status", timeout=2)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def _is_running(pid: int | None = None) -> bool:
    if pid is None:
        pid = _read_pid()
    if pid is None:
        return _server_responding()
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return _server_responding()


def _find_pids_on_port() -> list[int]:
    pids = set()
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in r.stdout.splitlines():
            if f":{SERVER_PORT}" in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    try:
                        pids.add(int(parts[-1]))
                    except ValueError:
                        pass
    except Exception:
        pass
    return list(pids)


# ===================================================================
# App class
# ===================================================================

class ZaloServerApp:
    def __init__(self):
        self.server_process: subprocess.Popen | None = None
        self._build_ui()
        self._schedule_status_check()

    # ---------------------------------------------------------------
    # UI Construction
    # ---------------------------------------------------------------
    def _build_ui(self):
        if HAS_CTK:
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("blue")
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()
            self.root.configure(bg=CLR_BG)

        self.root.title("Zalo Auto Post Server")
        self.root.geometry("420x310")
        self.root.resizable(False, False)

        # Icon
        if ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(ICON_FILE))
            except Exception:
                pass

        # --- Header ---
        header = self._frame(self.root)
        header.pack(fill="x", padx=20, pady=(18, 0))

        self._label(header, text="Zalo Auto Post Server",
                    font=("Segoe UI", 16, "bold"), text_color=CLR_TEXT).pack(anchor="w")

        # --- Status card ---
        card = self._frame(self.root, fg_color=CLR_CARD, corner_radius=12)
        card.pack(fill="x", padx=20, pady=(12, 0))

        status_row = self._frame(card, fg_color="transparent")
        status_row.pack(fill="x", padx=16, pady=(12, 4))

        self.status_dot = self._label(status_row, text="\u25CF", font=("Segoe UI", 14),
                                       text_color=CLR_GRAY)
        self.status_dot.pack(side="left")

        self.status_text = self._label(status_row, text="  Checking...",
                                        font=("Segoe UI", 12, "bold"), text_color=CLR_TEXT)
        self.status_text.pack(side="left")

        self.port_label = self._label(status_row, text=f"Port {SERVER_PORT}",
                                       font=("Segoe UI", 10), text_color=CLR_MUTED)
        self.port_label.pack(side="right")

        info_row = self._frame(card, fg_color="transparent")
        info_row.pack(fill="x", padx=16, pady=(0, 4))

        python_short = PYTHON.replace(str(APP_DIR), ".").replace("\\", "/")
        self.python_label = self._label(info_row, text=f"Python: {python_short}",
                                         font=("Segoe UI", 9), text_color=CLR_MUTED)
        self.python_label.pack(anchor="w")

        self.job_label = self._label(card, text="",
                                      font=("Segoe UI", 9), text_color=CLR_MUTED)
        self.job_label.pack(fill="x", padx=16, pady=(0, 12))

        # --- Main buttons ---
        btn_row = self._frame(self.root)
        btn_row.pack(fill="x", padx=20, pady=(14, 0))

        self.btn_start = self._button(btn_row, text="\u25B6  Start", fg_color=CLR_GREEN,
                                       hover_color="#16A34A", command=self._on_start)
        self.btn_start.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_stop = self._button(btn_row, text="\u25A0  Stop", fg_color=CLR_RED,
                                      hover_color="#DC2626", command=self._on_stop)
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # --- Secondary buttons ---
        btn_row2 = self._frame(self.root)
        btn_row2.pack(fill="x", padx=20, pady=(8, 0))

        self.btn_trigger = self._button(btn_row2, text="\U0001F680  Trigger",
                                         fg_color=CLR_BLUE, hover_color="#0055CC",
                                         command=self._on_trigger)
        self.btn_trigger.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_logs = self._button(btn_row2, text="\U0001F4C2  Logs",
                                      fg_color="#475569", hover_color="#334155",
                                      command=self._on_open_logs)
        self.btn_logs.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # --- Footer ---
        self.footer = self._label(self.root, text="", font=("Segoe UI", 9),
                                   text_color=CLR_MUTED)
        self.footer.pack(pady=(10, 8))

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    # ---------------------------------------------------------------
    # Widget helpers (CTK / TK compatible)
    # ---------------------------------------------------------------
    def _frame(self, parent, fg_color=None, corner_radius=0):
        if HAS_CTK:
            return ctk.CTkFrame(parent, fg_color=fg_color or "transparent",
                                corner_radius=corner_radius)
        f = tk.Frame(parent, bg=fg_color or CLR_BG)
        return f

    def _label(self, parent, text="", font=None, text_color=None):
        if HAS_CTK:
            return ctk.CTkLabel(parent, text=text, font=font, text_color=text_color)
        lbl = tk.Label(parent, text=text, font=font, fg=text_color or CLR_TEXT,
                       bg=parent.cget("bg") if hasattr(parent, "cget") else CLR_BG)
        return lbl

    def _button(self, parent, text="", fg_color=None, hover_color=None, command=None):
        if HAS_CTK:
            return ctk.CTkButton(parent, text=text, fg_color=fg_color,
                                 hover_color=hover_color, command=command,
                                 font=("Segoe UI", 11, "bold"), height=36,
                                 corner_radius=8, text_color="white")
        btn = tk.Button(parent, text=text, bg=fg_color, fg="white",
                        activebackground=hover_color, font=("Segoe UI", 10, "bold"),
                        relief="flat", command=command, height=1)
        return btn

    # ---------------------------------------------------------------
    # Status management
    # ---------------------------------------------------------------
    def _update_status(self, state: str, job_text: str = ""):
        colors = {"running": CLR_GREEN, "stopped": CLR_RED, "loading": CLR_ORANGE}
        labels = {"running": "Running", "stopped": "Stopped", "loading": "Loading..."}
        color = colors.get(state, CLR_GRAY)
        label = labels.get(state, state.title())

        if HAS_CTK:
            self.status_dot.configure(text_color=color)
            self.status_text.configure(text=f"  {label}")
            self.job_label.configure(text=job_text)
        else:
            self.status_dot.config(fg=color)
            self.status_text.config(text=f"  {label}")
            self.job_label.config(text=job_text)

        # Enable/disable buttons
        is_running = state == "running"
        is_loading = state == "loading"
        self._set_btn_state(self.btn_start, not is_running and not is_loading)
        self._set_btn_state(self.btn_stop, is_running)
        self._set_btn_state(self.btn_trigger, is_running)

    def _set_btn_state(self, btn, enabled: bool):
        if HAS_CTK:
            btn.configure(state="normal" if enabled else "disabled")
        else:
            btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def _set_footer(self, text: str):
        if HAS_CTK:
            self.footer.configure(text=text)
        else:
            self.footer.config(text=text)

    # ---------------------------------------------------------------
    # Background status check
    # ---------------------------------------------------------------
    def _schedule_status_check(self):
        threading.Thread(target=self._check_status_worker, daemon=True).start()

    def _check_status_worker(self):
        data = _fetch_status()
        if data is not None:
            job_status = data.get("status", "idle")
            job_text = ""
            if job_status == "running":
                elapsed = data.get("elapsed_seconds", 0)
                job_text = f"Job running... {int(elapsed)}s elapsed"
            elif job_status == "completed":
                result = data.get("result") or {}
                ok = result.get("success_count", 0)
                fail = result.get("failure_count", 0)
                job_text = f"Last job: {ok} success, {fail} failed"
            elif job_status == "error":
                job_text = f"Last job error: {data.get('error', 'unknown')[:60]}"
            self.root.after(0, self._update_status, "running", job_text)
        else:
            pid = self.server_process.pid if self.server_process else _read_pid()
            if _is_running(pid):
                self.root.after(0, self._update_status, "running", "")
            else:
                if PID_FILE.exists():
                    PID_FILE.unlink(missing_ok=True)
                self.root.after(0, self._update_status, "stopped", "")

        now = time.strftime("%H:%M:%S")
        self.root.after(0, self._set_footer, f"Last check: {now}")
        self.root.after(5000, self._schedule_status_check)

    # ---------------------------------------------------------------
    # Start server
    # ---------------------------------------------------------------
    def _on_start(self):
        self._update_status("loading", "Starting server...")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self):
        if _server_responding():
            self.root.after(0, self._update_status, "running", "")
            return

        existing_pid = _read_pid()
        if _is_running(existing_pid):
            self.root.after(0, self._update_status, "running", "")
            return

        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text("")

        self._do_start()

    def _do_start(self):
        stderr_fh = open(LOG_FILE, "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [PYTHON, RUN_SCRIPT],
                cwd=str(APP_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=stderr_fh,
            )
            self.server_process = proc
            PID_FILE.write_text(str(proc.pid))
        except Exception as exc:
            self.root.after(0, self._show_error, "Cannot start server", str(exc))
            self.root.after(0, self._update_status, "stopped", "")
            return

        # Wait up to 10s for server to respond
        for _ in range(20):
            time.sleep(0.5)
            if _server_responding():
                self.root.after(0, self._update_status, "running", "Server started")
                return

        # Check if crashed due to missing deps
        log_text = ""
        if LOG_FILE.exists():
            log_text = LOG_FILE.read_text(encoding="utf-8", errors="replace").strip()

        if "ModuleNotFoundError" in log_text or "No module named" in log_text:
            self.root.after(0, self._update_status, "loading", "Installing dependencies...")
            ok, output = _install_deps()
            if ok:
                LOG_FILE.write_text("")
                self._do_start()  # retry
                return
            else:
                self.root.after(0, self._show_error, "Install failed", output[-500:])
                self.root.after(0, self._update_status, "stopped", "")
                return

        self.root.after(0, self._update_status, "stopped", "")
        if log_text:
            tail = "\n".join(log_text.splitlines()[-15:])
            self.root.after(0, self._show_error, "Server crashed",
                           f"Python: {PYTHON}\n\n{tail}")

    # ---------------------------------------------------------------
    # Stop server
    # ---------------------------------------------------------------
    def _on_stop(self):
        self._update_status("loading", "Stopping server...")
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self):
        pids_to_kill = set()
        if self.server_process is not None:
            pids_to_kill.add(self.server_process.pid)
        saved_pid = _read_pid()
        if saved_pid:
            pids_to_kill.add(saved_pid)
        pids_to_kill.update(_find_pids_on_port())

        if not pids_to_kill and not _server_responding():
            self.root.after(0, self._cleanup_stopped)
            return

        for pid in pids_to_kill:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/T"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True,
                )
            except Exception:
                pass

        time.sleep(2)

        if _server_responding():
            for pid in _find_pids_on_port():
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid), "/T"],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        capture_output=True,
                    )
                except Exception:
                    pass
            time.sleep(2)

        self.root.after(0, self._cleanup_stopped)

    def _cleanup_stopped(self):
        PID_FILE.unlink(missing_ok=True)
        self.server_process = None
        self._update_status("stopped", "Server stopped")

    # ---------------------------------------------------------------
    # Trigger
    # ---------------------------------------------------------------
    def _on_trigger(self):
        self._set_btn_state(self.btn_trigger, False)
        self._set_footer("Sending trigger...")
        threading.Thread(target=self._trigger_worker, daemon=True).start()

    def _trigger_worker(self):
        try:
            req = Request(f"{BASE_URL}/trigger", method="POST",
                         data=b"{}",
                         headers={"Content-Type": "application/json"})
            resp = urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            msg = data.get("message", data.get("status", "OK"))
            self.root.after(0, self._set_footer, f"Trigger: {msg}")
        except Exception as exc:
            self.root.after(0, self._set_footer, f"Trigger failed: {exc}")
        finally:
            self.root.after(0, self._set_btn_state, self.btn_trigger, True)

    # ---------------------------------------------------------------
    # Open logs
    # ---------------------------------------------------------------
    def _on_open_logs(self):
        log_dir = APP_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists():
            os.startfile(str(LOG_FILE))
        else:
            os.startfile(str(log_dir))

    # ---------------------------------------------------------------
    # Error dialog
    # ---------------------------------------------------------------
    def _show_error(self, title: str, msg: str):
        if HAS_CTK:
            # CTkMessagebox not built-in, use tk messagebox
            from tkinter import messagebox
            messagebox.showerror(title, msg)
        else:
            from tkinter import messagebox
            messagebox.showerror(title, msg)

    # ---------------------------------------------------------------
    # Run
    # ---------------------------------------------------------------
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = ZaloServerApp()
    app.run()
