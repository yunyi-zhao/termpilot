#!/usr/bin/env python
"""termpilot CLI.

  python termpilot.py serve [--port N] [--host IP ...]
  python termpilot.py console                 (interactive control console)
  python termpilot.py new NAME [--cmd "pwsh -NoLogo"] [--cwd DIR] [--mode auto|manual|off]
  python termpilot.py ls
  python termpilot.py screen NAME
  python termpilot.py send NAME TEXT...        (text + Enter)
  python termpilot.py keys NAME KEY...         (enter esc up down tab ctrl-c 1 2 y n ...)
  python termpilot.py mode NAME auto|manual|off
  python termpilot.py kill NAME
  python termpilot.py prompts [--all]
  python termpilot.py approve ID | deny ID
  python termpilot.py windows                  (attachable Windows Terminal windows)
  python termpilot.py attach NAME HWND [--tab TABNAME]
  python termpilot.py log [-n 50]
  python termpilot.py firewall                 (allow the port from the Tailscale range)
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from termpilot import config as config_mod  # noqa: E402

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


from termpilot_cli_client import Client  # noqa: E402


def fmt_ts(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def self_argv():
    """argv prefix that re-invokes this program (exe or python script)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]


def stop_server(port):
    """Kill whatever is listening on the port. Returns the number of processes killed."""
    import psutil
    pids = set()
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                pids.add(c.pid)
    except Exception:
        pass
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            for ch in proc.children(recursive=True):
                try:
                    ch.kill()
                except Exception:
                    pass
            proc.kill()
        except Exception:
            pass
    if pids:
        time.sleep(0.5)
    return len(pids)


def setup_interactive(cfg, quick=False):
    """Terminal wizard for the approver settings. Returns the saved supervisor config."""
    from termpilot.config import DEFAULTS, SUPERVISOR_FIELDS, SUPERVISOR_PRESETS, supervisor_command, save
    sc = dict(DEFAULTS["supervisor"]); sc.update(cfg.get("supervisor") or {})
    print("\n=== termpilot 审批者（approver）设置 ===  回车 = 保留 [当前值]，输入数字选预设，或直接输入自定义值\n")
    if quick:
        print("  (quick mode: keeping current settings)")
        print("  command: " + supervisor_command(sc) + "\n")
        return sc
    for key, label, kind, ck in SUPERVISOR_FIELDS:
        cur = sc.get(key)
        if kind == "bool":
            ans = input("  %s [%s] (y/n): " % (label, "y" if cur else "n")).strip().lower()
            if ans in ("y", "yes", "1", "true"): sc[key] = True
            elif ans in ("n", "no", "0", "false"): sc[key] = False
            continue
        choices = SUPERVISOR_PRESETS.get(ck) if ck else None
        if choices:
            print("  %s" % label)
            for i, ch in enumerate(choices, 1):
                v, desc = (ch if isinstance(ch, tuple) else (ch, ""))
                print("     %d) %-10s %s" % (i, v, desc))
            ans = input("    选择或输入 [%s]: " % cur).strip()
            if ans.isdigit() and 1 <= int(ans) <= len(choices):
                ch = choices[int(ans) - 1]; sc[key] = ch[0] if isinstance(ch, tuple) else ch
            elif ans:
                sc[key] = ans
        else:
            ans = input("  %s [%s]: " % (label, cur if cur else "")).strip()
            if ans:
                sc[key] = ans if ans != "-" else ""
    cfg["supervisor"] = sc
    save(cfg)
    print("\n  已保存到 config.json。审批者命令: " + supervisor_command(sc) + "\n")
    return sc


def start_supervisor(c, name=None, every=None, loop=True):
    """Ask the server to (re)start the approver session (see Manager.start_supervisor)."""
    s = c.call("POST", "/api/supervisor", {"name": name, "every": every if loop else "", "restart": True})
    print("approver %r started: %s  (pid %s). Watch it in the WebUI or: termpilot.py watch %s"
          % (s["name"], s["cmd"], s.get("pid"), s["name"]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="termpilot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("serve"); p.add_argument("--port", type=int); p.add_argument("--host", action="append")
    p.add_argument("--no-approver", action="store_true", help="don't auto-start the approver session")
    p = sub.add_parser("start", help="setup wizard, then run the server detached in the background")
    p.add_argument("--quick", "-q", action="store_true", help="skip the wizard"); p.add_argument("--no-approver", action="store_true")
    p = sub.add_parser("stop", help="stop the background server"); p.add_argument("--port", type=int)
    p = sub.add_parser("install", help="(re)write ~/.claude/commands/termpilot.md for this machine"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("new"); p.add_argument("name"); p.add_argument("--cmd", dest="shell"); p.add_argument("--cwd"); p.add_argument("--mode", default="auto")
    p.add_argument("--cols", type=int); p.add_argument("--rows", type=int)
    sub.add_parser("ls")
    p = sub.add_parser("screen"); p.add_argument("name"); p.add_argument("-n", type=int, default=40)
    p = sub.add_parser("send"); p.add_argument("name"); p.add_argument("text", nargs="+")
    p = sub.add_parser("keys"); p.add_argument("name"); p.add_argument("keys", nargs="+")
    p = sub.add_parser("mode"); p.add_argument("name"); p.add_argument("mode", choices=["auto", "manual", "off"])
    p = sub.add_parser("kill"); p.add_argument("name")
    p = sub.add_parser("prompts"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("approve"); p.add_argument("id", type=int)
    p = sub.add_parser("deny"); p.add_argument("id", type=int)
    sub.add_parser("windows")
    sub.add_parser("attach-all")
    p = sub.add_parser("rename"); p.add_argument("name"); p.add_argument("new")
    p = sub.add_parser("pin"); p.add_argument("name"); p.add_argument("--off", action="store_true")
    p = sub.add_parser("attach"); p.add_argument("name"); p.add_argument("hwnd", type=int); p.add_argument("--tab"); p.add_argument("--mode", default="auto")
    p = sub.add_parser("log"); p.add_argument("-n", type=int, default=50)
    sub.add_parser("firewall")
    sub.add_parser("url")
    sub.add_parser("console")
    p = sub.add_parser("wait", help="block until a prompt needs a human/agent decision; print it with context")
    p.add_argument("--timeout", type=int, default=50); p.add_argument("-n", type=int, default=60)
    p = sub.add_parser("context"); p.add_argument("name"); p.add_argument("-n", type=int, default=60)
    p = sub.add_parser("watch", help="live view of a session's screen (Ctrl-C to stop)"); p.add_argument("name"); p.add_argument("-n", type=int, default=0)
    p = sub.add_parser("supervisor", help="start a Claude Code session that supervises the others (/loop 1m /termpilot)")
    p.add_argument("--name"); p.add_argument("--every"); p.add_argument("--no-loop", action="store_true")
    p = sub.add_parser("setup", help="interactive approver settings (model / effort / interval ...)")
    p.add_argument("--quick", action="store_true", help="don't ask, just print the current settings")
    p.add_argument("--show", action="store_true")
    p = sub.add_parser("gui", help="desktop control panel (default when launched without arguments)")
    p.add_argument("--selftest", action="store_true")
    if argv is None and len(sys.argv) == 1:
        argv = ["gui"]          # double-clicked exe: open the control panel
    a = ap.parse_args(argv)

    cfg = config_mod.load()

    if a.action == "gui":
        from termpilot.gui import main as gui_main
        gui_main(selftest=a.selftest)
        return

    if a.action == "serve":
        from termpilot.server import serve
        serve(cfg, hosts=a.host, port=a.port, approver=not a.no_approver)
        return

    if a.action == "install":
        from termpilot.install import ensure_command_file, TARGET
        print(("installed " if ensure_command_file(force=a.force) else "unchanged ") + TARGET)
        return

    if a.action == "stop":
        port = a.port or int(cfg["port"])
        n = stop_server(port)
        print("stopped %d process(es) on port %d" % (n, port) if n else "nothing listening on port %s" % port)
        return

    if a.action == "start":
        setup_interactive(cfg, quick=a.quick)
        cfg = config_mod.load()
        stop_server(int(cfg["port"]))
        exe = self_argv() + ["serve"] + (["--no-approver"] if a.no_approver else [])
        out = open(os.path.join(config_mod.ROOT, "serve.out"), "ab")
        err = open(os.path.join(config_mod.ROOT, "serve.err"), "ab")
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        p = subprocess.Popen(exe, cwd=config_mod.ROOT, stdout=out, stderr=err, stdin=subprocess.DEVNULL, creationflags=flags)
        for _ in range(40):
            time.sleep(0.25)
            if p.poll() is not None:
                sys.exit("server exited early, see serve.err")
            try:
                urllib.request.urlopen("http://%s:%d/" % (config_mod.resolve_hosts(cfg)[-1], int(cfg["port"])), timeout=2)
                break
            except Exception:
                pass
        print("termpilot server running (pid %d)" % p.pid)
        for h in config_mod.resolve_hosts(cfg):
            print("  http://%s:%d/?token=%s" % (h, int(cfg["port"]), cfg["token"]))
        print("stop: %s stop" % " ".join(self_argv()))
        return

    if a.action == "firewall":
        port = int(cfg["port"])
        rule = ('netsh advfirewall firewall add rule name="termpilot %d" dir=in action=allow '
                'protocol=TCP localport=%d remoteip=100.64.0.0/10' % (port, port))
        print("running (needs admin, via gsudo if present):\n  " + rule)
        gsudo = r"C:\Program Files\gsudo\gsudo.exe"
        cmd = ([gsudo] if os.path.exists(gsudo) else []) + ["cmd", "/c", rule]
        sys.exit(subprocess.call(cmd))

    if a.action == "console":
        from termpilot.console import run_console
        run_console(cfg, ["http://%s:%d/?token=%s" % (h, int(cfg["port"]), cfg["token"]) for h in config_mod.resolve_hosts(cfg)])
        return

    if a.action == "setup":
        if a.show:
            from termpilot.config import supervisor_command
            sc = dict(config_mod.DEFAULTS["supervisor"]); sc.update(cfg.get("supervisor") or {})
            for k, v in sc.items():
                print("  %-16s %s" % (k, v))
            print("  command:         " + supervisor_command(sc))
            return
        setup_interactive(cfg, quick=a.quick)
        return

    if a.action == "url":
        for h in config_mod.resolve_hosts(cfg):
            print("http://%s:%d/?token=%s" % (h, int(cfg["port"]), cfg["token"]))
        return

    c = Client(cfg)

    if a.action == "new":
        s = c.call("POST", "/api/sessions", {"name": a.name, "cmd": a.shell, "cwd": a.cwd,
                                             "mode": a.mode, "cols": a.cols, "rows": a.rows})
        print("created %s pid=%s cmd=%r" % (s["name"], s.get("pid"), s["cmd"]))
    elif a.action == "ls":
        ss = c.call("GET", "/api/sessions")
        if not ss:
            print("(no sessions)")
        for s in ss:
            pr = s.get("prompt")
            pstat = ("PROMPT %s #%s" % (pr["status"], pr["id"])) if pr and pr["status"] in ("pending", "reviewing", "held") else ""
            print("%-16s %-4s %-6s %-5s %s  %s" % (s["name"], s["kind"], s["mode"], "alive" if s["alive"] else "dead",
                                                   s["cmd"][:40], pstat))
    elif a.action == "screen":
        r = c.call("GET", "/api/sessions/%s/screen" % a.name)
        lines = r["text"].rstrip("\n ").splitlines()
        print("\n".join(lines[-a.n:]))
    elif a.action == "send":
        c.call("POST", "/api/sessions/%s/input" % a.name, {"data": " ".join(a.text) + "\r"})
    elif a.action == "keys":
        c.call("POST", "/api/sessions/%s/keys" % a.name, {"keys": a.keys})
    elif a.action == "mode":
        c.call("POST", "/api/sessions/%s/mode" % a.name, {"mode": a.mode})
    elif a.action == "kill":
        c.call("DELETE", "/api/sessions/%s" % a.name)
    elif a.action == "prompts":
        ps = c.call("GET", "/api/prompts")
        for p in ps:
            if not a.all and p["status"] not in ("pending", "reviewing", "held"):
                continue
            print("#%-4d %s %-10s %-9s %s" % (p["id"], fmt_ts(p["detected_at"]), p["session"], p["status"], p["reason"]))
            for line in p["snippet"].splitlines()[-4:]:
                print("       | " + line)
    elif a.action == "approve":
        p = c.call("POST", "/api/prompts/%d/approve" % a.id); print("approved #%d" % p["id"])
    elif a.action == "deny":
        p = c.call("POST", "/api/prompts/%d/deny" % a.id); print("denied #%d" % p["id"])
    elif a.action == "windows":
        for w in c.call("GET", "/api/wt/windows"):
            print("%-9d %s %s" % (w["hwnd"], ("-> " + ",".join(w["attached"])) if w["attached"] else "", w["title"]))
            for t in w.get("tabs", []):
                print("          %s %s %s" % ("*" if t["active"] else " ", t["name"], ("-> " + ",".join(t["attached"])) if t["attached"] else ""))
    elif a.action == "rename":
        c.call("POST", "/api/sessions/%s/rename" % a.name, {"new": a.new}); print("renamed %s -> %s" % (a.name, a.new))
    elif a.action == "pin":
        c.call("POST", "/api/sessions/%s/pin" % a.name, {"pinned": not a.off}); print(("unpinned " if a.off else "pinned ") + a.name)
    elif a.action == "attach-all":
        for s in c.call("POST", "/api/wt/attach_all", {}):
            print("attached %-24s -> hwnd %s %s" % (s["name"], s["hwnd"], ("tab %r" % s["tab"]) if s["tab"] else ""))
    elif a.action == "attach":
        s = c.call("POST", "/api/wt/attach", {"name": a.name, "hwnd": a.hwnd, "tab": a.tab, "mode": a.mode})
        print("attached %s -> %s" % (s["name"], s["cmd"]))
    elif a.action == "supervisor":
        start_supervisor(c, a.name, a.every, not a.no_loop)
    elif a.action == "watch":
        rows = a.n or max(10, os.get_terminal_size().lines - 3)
        last = None
        try:
            while True:
                text = c.call("GET", "/api/sessions/%s/screen" % a.name)["text"].rstrip("\n ")
                if text != last:
                    last = text
                    sys.stdout.write("\x1b[2J\x1b[H" + "\n".join(text.splitlines()[-rows:]) + "\n")
                    sys.stdout.flush()
                time.sleep(0.25)
        except KeyboardInterrupt:
            print()
    elif a.action in ("wait", "context"):
        def held():
            return [p for p in c.call("GET", "/api/prompts") if p["status"] in ("held", "pending", "reviewing")]
        if a.action == "wait":
            t0 = time.time()
            ps = held()
            while not ps and time.time() - t0 < a.timeout:
                time.sleep(1)
                ps = held()
            if not ps:
                ss = c.call("GET", "/api/sessions")
                print("NO_PROMPT after %ds. sessions: %s" % (a.timeout, ", ".join("%s(%s,%s)" % (s["name"], s["mode"], "alive" if s["alive"] else "dead") for s in ss) or "none"))
                return
            targets = ps
        else:
            targets = [{"session": a.name, "id": None, "status": "-", "reason": "-", "decided_by": "-"}]
        for p in targets:
            r = c.call("GET", "/api/sessions/%s/screen" % p["session"])
            lines = r["text"].rstrip("\n ").splitlines()
            print("PROMPT id=%s session=%s status=%s by=%s reason=%s" % (p["id"], p["session"], p["status"], p["decided_by"], p["reason"]))
            print("---CONTEXT_START--- (last %d lines of %s)" % (min(a.n, len(lines)), p["session"]))
            print("\n".join(lines[-a.n:]))
            print("---CONTEXT_END---")
            if p["id"] is not None:
                print("ACTIONS: python termpilot.py approve %s | deny %s | keys %s 2 | send %s <text>" % (p["id"], p["id"], p["session"], p["session"]))
    elif a.action == "log":
        for e in c.call("GET", "/api/events")[-a.n:]:
            print("%s %-9s %-10s %s" % (fmt_ts(e["ts"]), e["kind"], e.get("session") or "-", e["msg"]))


if __name__ == "__main__":
    main()
