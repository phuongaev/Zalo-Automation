"""Zalo Auto Server – Start / Stop GUI.

Double-click gui.pyw to open.  Uses pythonw so no console window appears.
"""

import os
import signal
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PYTHON = str(APP_DIR / ".venv" / "Scripts" / "python.exe")
RUN_SCRIPT = str(APP_DIR / "run.py")
PID_FILE = APP_DIR / ".server.pid"

server_process: subprocess.Popen | None = None


def _read_pid() -> int | None:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            return pid
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

    try:
        proc = subprocess.Popen(
            [PYTHON, RUN_SCRIPT],
            cwd=str(APP_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        server_process = proc
        PID_FILE.write_text(str(proc.pid))
        update_status("Running", "green")
    except Exception as exc:
        messagebox.showerror("Error", f"Cannot start server:\n{exc}")


def stop_server():
    global server_process
    pid = None

    if server_process is not None:
        pid = server_process.pid
    else:
        pid = _read_pid()

    if pid is None or not _is_running(pid):
        update_status("Stopped", "red")
        PID_FILE.unlink(missing_ok=True)
        server_process = None
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    # Force kill after 3 seconds if still alive
    root.after(3000, _force_kill, pid)


def _force_kill(pid: int):
    global server_process
    if _is_running(pid):
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
            )
        except Exception:
            pass
    PID_FILE.unlink(missing_ok=True)
    server_process = None
    update_status("Stopped", "red")


def update_status(text: str, color: str):
    status_label.config(text=f"Status: {text}", fg=color)
    if text == "Running":
        btn_start.config(state=tk.DISABLED)
        btn_stop.config(state=tk.NORMAL)
    else:
        btn_start.config(state=tk.NORMAL)
        btn_stop.config(state=tk.DISABLED)


def check_status():
    pid = _read_pid() if server_process is None else (server_process.pid if server_process else None)
    if _is_running(pid):
        update_status("Running", "green")
    else:
        update_status("Stopped", "red")
        PID_FILE.unlink(missing_ok=True)
    root.after(5000, check_status)


def on_close():
    root.destroy()


# --- GUI ---
root = tk.Tk()
root.title("Zalo Auto Server")
root.geometry("300x150")
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

# Initial status check
root.after(100, check_status)
root.protocol("WM_DELETE_CLOSE", on_close)
root.mainloop()
