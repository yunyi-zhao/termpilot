"""Offline executable/ConPTY smoke test. Uses a temporary config and a fake service."""
import base64
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from termpilot import config
from termpilot.pty_session import PtySession


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/prompts":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = None
    try:
        with tempfile.TemporaryDirectory(prefix="packaged smoke ") as tmp:
            root = Path(tmp)
            exe = root / "termpilot.exe"
            shutil.copy2(Path(config.ROOT) / "dist" / "termpilot.exe", exe)
            cfg = copy.deepcopy(config.DEFAULTS)
            cfg.update(token="isolated-test-token", api_base="http://127.0.0.1:%s" % server.server_port)
            cfg["supervisor"].update(autostart=False, every="")
            (root / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            encoded = base64.urlsafe_b64encode(json.dumps(cfg["supervisor"]).encode()).decode()
            for provider in ("codex", "antigravity"):
                settings = dict(cfg["supervisor"], provider=provider)
                encoded = base64.urlsafe_b64encode(json.dumps(settings).encode()).decode()
                argv = [str(exe), "supervisor-run", "--settings", encoded]
                p = subprocess.run(argv, input="", capture_output=True, text=True, encoding="utf-8", timeout=40)
                assert p.returncode == 0, p.stderr
                assert "NO_PROMPT" in p.stdout, p.stdout
                print("Packaged %s scheduler: PASS (fake service; zero model calls)." % provider, flush=True)
            session = PtySession("codex-smoke", subprocess.list2cmdline(argv), str(root))
            deadline = time.monotonic() + 30
            while session.alive() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not session.alive(), "ConPTY smoke timed out"
            # Let the reader drain the final output after the process exits.
            time.sleep(0.2)
            screen = session.screen_text()
            assert "NO_PROMPT" in screen, screen
            print("Packaged scheduler via ConPTY with spaces in paths: PASS.", flush=True)
            session = None
    finally:
        if session is not None:
            session.kill()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
