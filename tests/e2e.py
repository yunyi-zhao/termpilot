"""End-to-end test against a running termpilot server (127.0.0.1).

  python tests/e2e.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from termpilot import config as config_mod  # noqa: E402
from termpilot_cli_client import Client  # noqa: E402  (shim below)

FAKE = os.path.join(ROOT, "tests", "fake_agent.ps1")


def pending(c):
    return [p for p in c.call("GET", "/api/prompts") if p["status"] in ("pending", "reviewing", "held")]


def wait_for(fn, timeout=8, every=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = fn()
        if r:
            return r
        time.sleep(every)
    return None


def main():
    cfg = config_mod.load()
    c = Client(cfg)
    name = "e2e"
    for s in c.call("GET", "/api/sessions"):
        if s["name"] == name:
            c.call("DELETE", "/api/sessions/" + name)
    c.call("POST", "/api/sessions", {"name": name})
    time.sleep(3)
    ok = True

    def run(kind):
        c.call("POST", "/api/sessions/%s/input" % name, {"data": "& '%s' %s\r" % (FAKE, kind)})

    def screen():
        return c.call("GET", "/api/sessions/%s/screen" % name)["text"].rstrip()

    # 1. safe -> auto approved, exactly one approval
    n0 = len([e for e in c.call("GET", "/api/events") if e["kind"] == "approved" and e["session"] == name])
    run("safe")
    got = wait_for(lambda: "APPROVED -> running: Get-ChildItem" in screen())
    time.sleep(1)  # the screen can update a few ms before the 'approved' event is recorded
    n1 = len([e for e in c.call("GET", "/api/events") if e["kind"] == "approved" and e["session"] == name])
    print("[1] safe auto-approved:", bool(got), "| approvals:", n1 - n0)
    ok &= bool(got) and (n1 - n0) == 1
    time.sleep(3)
    print("    pending after settle:", len(pending(c)))
    ok &= len(pending(c)) == 0

    # 2. unsafe -> held, then human approve
    run("unsafe")
    p = wait_for(lambda: [x for x in pending(c) if x["session"] == name])
    print("[2] unsafe held:", bool(p), "|", p[0]["status"] if p else None, "|", p[0]["reason"] if p else None)
    ok &= bool(p) and p[0]["status"] == "held" and "unsafe" in p[0]["reason"]
    c.call("POST", "/api/prompts/%d/approve" % p[0]["id"])
    got = wait_for(lambda: "APPROVED -> running: Remove-Item" in screen())
    print("    human approve delivered:", bool(got))
    ok &= bool(got)
    time.sleep(3)
    print("    pending after settle:", len(pending(c)))
    ok &= len(pending(c)) == 0

    # 3. direction choice -> held, then deny (Esc)
    run("choice")
    p = wait_for(lambda: [x for x in pending(c) if x["session"] == name])
    print("[3] choice held:", bool(p), "|", p[0]["reason"] if p else None)
    ok &= bool(p) and "direction" in p[0]["reason"]
    c.call("POST", "/api/prompts/%d/deny" % p[0]["id"])
    got = wait_for(lambda: "CANCELLED by Esc" in screen())
    print("    deny delivered:", bool(got))
    ok &= bool(got)
    time.sleep(3)
    print("    pending after settle:", len(pending(c)))
    ok &= len(pending(c)) == 0

    # 4. manual mode -> safe prompt is held
    c.call("POST", "/api/sessions/%s/mode" % name, {"mode": "manual"})
    run("safe")
    p = wait_for(lambda: [x for x in pending(c) if x["session"] == name])
    print("[4] manual mode holds safe prompt:", bool(p), "|", p[0]["reason"] if p else None)
    ok &= bool(p) and p[0]["status"] == "held"
    c.call("POST", "/api/sessions/%s/keys" % name, {"keys": ["enter"]})
    time.sleep(3)
    ok &= len(pending(c)) == 0
    c.call("POST", "/api/sessions/%s/mode" % name, {"mode": "auto"})

    # 5. events log sanity
    evs = [e for e in c.call("GET", "/api/events") if e["session"] == name]
    print("[5] events:", " ".join(e["kind"] for e in evs))

    c.call("DELETE", "/api/sessions/" + name)
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
