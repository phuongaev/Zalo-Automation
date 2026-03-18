"""Zalo Auto Server – Start / Stop GUI.

Double-click gui.pyw to open.
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
RUN_SCRIPT = str(APP_DIR / "run.py")
PID_FILE = APP_DIR / ".server.pid"
LOG_FILE = APP_DIR / "logs" / "server_stderr.log"

# Use the same python that launched this GUI.
# Prefer venv if it exists, otherwise use system python.
_venv_python = APP_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON = str(_venv_python) if _venv_python.exists() else sys.executable

server_process: subprocess.Popen | None = None


def _read_pid() -> int | None:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            PID_FILE.unlink(missing_ok=True)
    return None


def _is_running(pid: int | None = None) -> bool:
    if pid is None:
        pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_server():
    global server_process
    existing_pid = _read_pid()
    if _is_running(existing_pid):
        update_status("Running", "green")
        return

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stderr_fh = open(LOG_FILE, "a", encoding="utf-8")

    try:
        proc = subprocess.Popen(
            [PYTHON, RUN_SCRIPT],
            cwd=str(APP_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fh,
        )
        server_process = proc
        PID_FILE.write_text(str(proc.pid))
        update_status("Starting...", "orange")
        # Check after 3 seconds if process is still alive
        root.after(3000, _verify_started, proc.pid)
    except Exception as exc:
        messagebox.showerror("Error", f"Cannot start server:\n{exc}")


def _verify_started(pid: int):
    if _is_running(pid):
        update_status("Running", "green")
    else:
        update_status("Stopped", "red")
        # Show last few lines of stderr log
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            tail = "\n".join(lines[-15:])
            messagebox.showerror("Server crashed", f"Python: {PYTHON}\n\nLast log:\n{tail}")


def stop_server():
    global server_process
    pid = server_process.pid if server_process else _read_pid()

    if pid is None or not _is_running(pid):
        _cleanup_stopped()
        return

    update_status("Stopping...", "orange")
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True,
        )
    except Exception:
        pass
    root.after(2000, _cleanup_stopped)


def _cleanup_stopped():
    global server_process
    PID_FILE.unlink(missing_ok=True)
    server_process = None
    update_status("Stopped", "red")


def update_status(text: str, color: str):
    status_label.config(text=f"Status: {text}", fg=color)
    if text == "Running":
        btn_start.config(state=tk.DISABLED)
        btn_stop.config(state=tk.NORMAL)
    elif text == "Stopped":
        btn_start.config(state=tk.NORMAL)
        btn_stop.config(state=tk.DISABLED)
    else:
        btn_start.config(state=tk.DISABLED)
        btn_stop.config(state=tk.DISABLED)


def check_status():
    pid = server_process.pid if server_process else _read_pid()
    if _is_running(pid):
        update_status("Running", "green")
    else:
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
        # Only update to stopped if not in a transitional state
        current = status_label.cget("text")
        if "Starting" not in current and "Stopping" not in current:
            update_status("Stopped", "red")
    root.after(5000, check_status)


# --- GUI ---
root = tk.Tk()
root.title("Zalo Auto Server")
root.geometry("320x160")
root.resizable(False, False)

title_label = tk.Label(root, text="Zalo Auto Post Server", font=("Segoe UI", 12, "bold"))
title_label.pack(pady=(15, 5))

status_label = tk.Label(root, text="Status: Checking...", font=("Segoe UI", 10))
status_label.pack(pady=5)

btn_frame = tk.Frame(root)
btn_frame.pack(pady=10)

btn_start = tk.Button(btn_frame, text="Start", width=10, font=("Segoe UI", 10), bg="#4CAF50", fg="white", command=start_server)
btn_start.pack(side=tk.LEFT, padx=10)

btn_stop = tk.Button(btn_frame, text="Stop", width=10, font=("Segoe UI", 10), bg="#f44336", fg="white", command=stop_server)
btn_stop.pack(side=tk.LEFT, padx=10)

root.after(100, check_status)
root.protocol("WM_DELETE_WINDOW", root.destroy)
root.mainloop()
