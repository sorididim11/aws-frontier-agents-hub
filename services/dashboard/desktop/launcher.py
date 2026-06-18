#!/usr/bin/env python3
"""Desktop launcher: Flask + pywebview + expert sidecar."""
import os
import sys
import socket
import subprocess
import threading
import time
import atexit

from desktop.path_resolver import (
    is_bundled, setup_paths, ensure_config_exists,
    get_bundle_dir, get_config_dir, get_sidecar_dir, get_node_binary,
)

setup_paths()
ensure_config_exists()

os.environ.setdefault("DASHBOARD_MODE", "local")
os.environ["DASHBOARD_CONFIG_DIR"] = get_config_dir()


def _find_free_port(start: int, end: int) -> int:
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port in {start}-{end}")


class SidecarManager:
    def __init__(self, port: int):
        self.port = port
        self.process = None

    def start(self):
        sidecar_dir = get_sidecar_dir()
        entry = os.path.join(sidecar_dir, "dist", "index.js")
        if not os.path.exists(entry):
            print(f"[sidecar] {entry} not found, skipping")
            return

        node_bin = get_node_binary()
        env = os.environ.copy()
        env["SIDECAR_PORT"] = str(self.port)
        env["NODE_PATH"] = os.path.join(sidecar_dir, "node_modules")

        self.process = subprocess.Popen(
            [node_bin, entry],
            cwd=sidecar_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print(f"[sidecar] pid={self.process.pid} port={self.port}")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            print("[sidecar] stopped")


def _start_flask(port: int):
    from overview_app import app
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


def _wait_for_port(port: int, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


_LOADING_HTML = """
<html>
<head><style>
body { background: #0f172a; color: #e2e8f0; font-family: -apple-system, sans-serif;
       display: flex; align-items: center; justify-content: center; height: 100vh; }
.loader { text-align: center; }
.spinner { width: 40px; height: 40px; border: 3px solid #1e293b; border-top: 3px solid #38bdf8;
           border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 16px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style></head>
<body><div class="loader"><div class="spinner"></div><p>Starting...</p></div></body>
</html>
"""


def main():
    import webview

    flask_port = _find_free_port(5003, 5099)
    sidecar_port = _find_free_port(3100, 3199)

    print(f"[launcher] Flask port: {flask_port}, Sidecar port: {sidecar_port}")

    os.environ["EXPERT_SIDECAR_URL"] = f"http://localhost:{sidecar_port}"

    sidecar = SidecarManager(sidecar_port)
    sidecar.start()
    atexit.register(sidecar.stop)

    flask_thread = threading.Thread(target=_start_flask, args=(flask_port,), daemon=True)
    flask_thread.start()

    window = webview.create_window(
        title="DevOps Agent Dashboard",
        html=_LOADING_HTML,
        width=1400,
        height=900,
        resizable=True,
        min_size=(1024, 680),
        text_select=True,
    )

    def _navigate_when_ready(w):
        url = f"http://127.0.0.1:{flask_port}"
        ready = _wait_for_port(flask_port)
        print(f"[launcher] Flask ready: {ready}, navigating to: {url}")
        if ready:
            w.load_url(url)

    window.events.closing += lambda: sidecar.stop()
    webview.start(func=_navigate_when_ready, args=[window])
    sidecar.stop()


if __name__ == "__main__":
    main()
