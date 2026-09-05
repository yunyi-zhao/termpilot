"""Tiny HTTP client used by the CLI and tests."""
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def default_base(cfg):
    """Prefer a non-loopback address. On this machine 127.0.0.1 gets random
    connection resets from local filtering drivers (AdGuard / Huorong), while the
    Tailscale address is clean. `api_base` in config.json overrides."""
    if cfg.get("api_base"):
        return cfg["api_base"].rstrip("/")
    try:
        from termpilot import config as config_mod
        hosts = config_mod.resolve_hosts(cfg)
    except Exception:
        hosts = ["127.0.0.1"]
    host = next((h for h in hosts if not h.startswith("127.") and h != "0.0.0.0"), None)
    if host is None:
        # bound to 0.0.0.0 or loopback only: prefer the Tailscale address if there is one
        try:
            host = config_mod.tailscale_ip() or "127.0.0.1"
        except Exception:
            host = "127.0.0.1"
    return "http://%s:%d" % (host, int(cfg["port"]))


class Client:
    def __init__(self, cfg, base=None):
        self.base = base or default_base(cfg)
        self.token = cfg["token"]

    def call(self, method, path, body=None):
        path = urllib.parse.quote(path, safe="/?=&%")   # non-ASCII session names
        req = urllib.request.Request(self.base + path, method=method,
                                     headers={"X-Token": self.token, "Content-Type": "application/json"})
        data = json.dumps(body).encode() if body is not None else None
        try:
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, data, timeout=30) as r:
                        return json.loads(r.read().decode())
                except (ConnectionResetError, ConnectionAbortedError, http.client.RemoteDisconnected):
                    # transient reset on Windows; safe to retry reads, and our writes are
                    # idempotent enough (a duplicate 'create' just reports 'exists')
                    if method != "GET" and attempt > 0:
                        raise
                    time.sleep(0.3)
            raise SystemExit("connection kept resetting")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode()).get("error")
            except Exception:
                err = str(e)
            sys.exit("error: %s" % err)
        except urllib.error.URLError as e:
            sys.exit("cannot reach termpilot server at %s (%s). Start it with: python termpilot.py serve"
                     % (self.base, e.reason))
