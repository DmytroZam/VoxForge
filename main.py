from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from app.server import app, init_runtime, shutdown_runtime, ws_state


APP_DIR = Path(__file__).resolve().parent
UI_PROFILE_DIR = APP_DIR / ".ui_profile"
LOG_PATH = APP_DIR / "voxforge.log"

if sys.stdout is None or sys.stderr is None:
    _log_file = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_server(url: str, timeout_sec: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.08)


def _find_chromium_app_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    for cmd in ("msedge", "chrome"):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def main() -> None:
    init_runtime()
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)

    srv_thread = threading.Thread(target=server.run, daemon=True)
    srv_thread.start()
    _wait_for_server(f"{url}/api/bootstrap")

    browser_exe = _find_chromium_app_browser()
    proc: subprocess.Popen[bytes] | None = None
    if browser_exe:
        UI_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1  # SW_SHOWNORMAL even if parent pythonw started with SW_HIDE
        proc = subprocess.Popen(
            [
                browser_exe,
                f"--app={url}",
                f"--user-data-dir={str(UI_PROFILE_DIR)}",
                "--window-size=1180,780",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=msEdgeSidebarV2",
            ],
            startupinfo=si,
        )
    else:
        webbrowser.open(url)

    try:
        while srv_thread.is_alive():
            time.sleep(0.25)
            # Clean shutdown once the app window closes and WebSocket disconnects
            if ws_state["ever_connected"] and ws_state["active_count"] == 0:
                if time.monotonic() - ws_state["last_disconnect"] > 1.8:
                    break
            if proc is not None and proc.poll() is not None and ws_state["ever_connected"] and ws_state["active_count"] == 0:
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        shutdown_runtime()


if __name__ == "__main__":
    main()
