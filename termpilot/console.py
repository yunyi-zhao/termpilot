"""Interactive control console: live event feed + commands, in one terminal.

    python termpilot.py console
"""
import os
import shlex
import sys
import threading
import time

from termpilot_cli_client import Client

HELP = """commands:
  ls                         list sessions (with pending prompts)
  new NAME [--cwd DIR] [--cmd "..." | --provider codex|claude|antigravity] [--model ID] [--mode auto|manual|off]
  send NAME TEXT...          type a line + Enter into NAME
  keys NAME KEY...           enter esc up down tab 1 2 3 y n ctrl-c
  screen NAME [N]            last N lines of NAME's screen (default 30)
  watch NAME [N]             live view of NAME's screen, refreshes ~4x/s; any key stops
  supervisor [NAME] [EVERY]  (re)start the configured Codex / Claude / Antigravity approver
  setup [restart]            approver settings wizard (model / effort / interval ...); 'setup restart' applies now
  prompts                    pending prompts
  approve ID | deny ID       act on a prompt (approve = Enter, deny = Esc)
  a | d                      approve / deny the newest pending prompt
  mode NAME auto|manual|off
  rename NAME NEW            rename a session
  pin NAME [off]             pin a session to the top of every list (★)
  windows                    attachable Windows Terminal windows
  attach NAME HWND [TAB]     attach to a Windows Terminal window
  kill NAME                  remove a session
  log [N]                    last N events
  url                        WebUI addresses
  quiet | loud               hide / show the live event feed
  help | quit
"""

C_RESET, C_DIM, C_Y, C_G, C_R, C_B = "\x1b[0m", "\x1b[2m", "\x1b[33m", "\x1b[32m", "\x1b[31m", "\x1b[36m"
KIND_COLOR = {"approved": C_G, "held": C_Y, "detected": C_B, "denied": C_Y, "exited": C_R, "removed": C_R, "retry": C_Y}


def fmt_ts(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


class Console:
    def __init__(self, cfg, urls):
        self.c = Client(cfg)
        self.urls = urls
        self.last_ev = 0
        self.loud = True
        self.stop = False
        self.lock = threading.Lock()

    # -- output helpers ---------------------------------------------------
    def out(self, s=""):
        with self.lock:
            sys.stdout.write("\r\x1b[K" + s + "\n")
            sys.stdout.write("termpilot> ")
            sys.stdout.flush()

    def event_line(self, e):
        col = KIND_COLOR.get(e["kind"], C_DIM)
        s = "%s%s %-9s %-10s %s%s" % (col, fmt_ts(e["ts"]), e["kind"], e.get("session") or "-", e["msg"], C_RESET)
        p = e.get("prompt")
        if e["kind"] in ("held", "detected") and p:
            for line in p["snippet"].splitlines()[-4:]:
                s += "\n" + C_DIM + "    | " + line + C_RESET
            if e["kind"] == "held":
                s += "\n" + C_Y + "    -> approve %d / deny %d" % (p["id"], p["id"]) + C_RESET
        return s

    # -- live feed thread ----------------------------------------------------
    def feed(self):
        evs = self.c.call("GET", "/api/events")
        if evs:
            self.last_ev = evs[-1]["id"]
        while not self.stop:
            time.sleep(1)
            try:
                evs = self.c.call("GET", "/api/events?since=%d" % self.last_ev)
            except SystemExit:
                self.out(C_R + "server unreachable" + C_RESET)
                continue
            for e in evs:
                self.last_ev = e["id"]
                if self.loud or e["kind"] in ("held", "exited"):
                    self.out(self.event_line(e))

    # -- commands ------------------------------------------------------------
    def cmd_ls(self, a):
        ss = self.c.call("GET", "/api/sessions")
        if not ss:
            print("(no sessions)  -- try: new agent1")
        for s in ss:
            pr = s.get("prompt")
            live = pr and pr["status"] in ("pending", "reviewing", "held")
            flag = (C_Y + "PROMPT %s #%s" % (pr["status"], pr["id"]) + C_RESET) if live else ""
            print("  %-14s %-4s %-6s %-5s %-40s %s" % (s["name"], s["kind"], s["mode"],
                                                    "alive" if s["alive"] else C_R + "dead" + C_RESET, s["cmd"][:40], flag))

    def cmd_new(self, a):
        name, cwd, cmd, mode = a[0], None, None, "auto"
        provider, model = None, None
        i = 1
        while i < len(a):
            if a[i] == "--cwd": cwd = a[i + 1]; i += 2
            elif a[i] == "--cmd": cmd = a[i + 1]; i += 2
            elif a[i] == "--mode": mode = a[i + 1]; i += 2
            elif a[i] == "--provider": provider = a[i + 1]; i += 2
            elif a[i] == "--model": model = a[i + 1]; i += 2
            else: i += 1
        s = self.c.call("POST", "/api/sessions", {"name": name, "cwd": cwd, "cmd": cmd, "mode": mode,
                                                "provider": provider, "model": model})
        print("created %s (pid %s)" % (s["name"], s.get("pid")))

    def cmd_send(self, a):
        self.c.call("POST", "/api/sessions/%s/input" % a[0], {"data": " ".join(a[1:]) + "\r"})

    def cmd_keys(self, a):
        self.c.call("POST", "/api/sessions/%s/keys" % a[0], {"keys": a[1:]})

    def cmd_screen(self, a):
        n = int(a[1]) if len(a) > 1 else 30
        r = self.c.call("GET", "/api/sessions/%s/screen" % a[0])
        lines = r["text"].rstrip("\n ").splitlines()
        print(C_DIM + "-" * 60 + C_RESET)
        print("\n".join(lines[-n:]))
        print(C_DIM + "-" * 60 + C_RESET)

    def cmd_watch(self, a):
        """Live view of a session's screen; any key returns to the prompt."""
        import msvcrt
        name = a[0]
        rows = int(a[1]) if len(a) > 1 else max(10, os.get_terminal_size().lines - 3)
        self.loud_saved, self.loud = self.loud, False   # silence the feed while watching
        last = None
        try:
            while True:
                if msvcrt.kbhit():
                    msvcrt.getwch()
                    break
                try:
                    r = self.c.call("GET", "/api/sessions/%s/screen" % name)
                except SystemExit as e:
                    print(C_R + str(e) + C_RESET); break
                text = r["text"].rstrip("\n ")
                if text != last:
                    last = text
                    lines = text.splitlines()[-rows:]
                    sys.stdout.write("\x1b[2J\x1b[H")
                    sys.stdout.write(C_B + "── watch %s ──  (press any key to stop)%s\n" % (name, C_RESET))
                    sys.stdout.write("\n".join(lines) + "\n")
                    sys.stdout.flush()
                time.sleep(0.25)
        finally:
            self.loud = self.loud_saved
            sys.stdout.write("\x1b[2J\x1b[H")
            print(C_DIM + "stopped watching %s" % name + C_RESET)

    def pending(self):
        return [p for p in self.c.call("GET", "/api/prompts") if p["status"] in ("pending", "reviewing", "held")]

    def cmd_prompts(self, a):
        ps = self.pending()
        if not ps:
            print("(no pending prompts)")
        for p in ps:
            print("  #%-4d %s %-10s %-9s %s" % (p["id"], fmt_ts(p["detected_at"]), p["session"], p["status"], p["reason"]))
            for line in p["snippet"].splitlines()[-4:]:
                print(C_DIM + "       | " + line + C_RESET)

    def _act(self, what, pid):
        p = self.c.call("POST", "/api/prompts/%s/%s" % (pid, what))
        print("%s #%d (%s)" % (what, p["id"], p["session"]))

    def cmd_approve(self, a): self._act("approve", int(a[0]))
    def cmd_deny(self, a): self._act("deny", int(a[0]))

    def cmd_a(self, a):
        ps = self.pending(); ps and self._act("approve", ps[-1]["id"]) or (not ps and print("nothing pending"))

    def cmd_d(self, a):
        ps = self.pending(); ps and self._act("deny", ps[-1]["id"]) or (not ps and print("nothing pending"))

    def cmd_mode(self, a):
        self.c.call("POST", "/api/sessions/%s/mode" % a[0], {"mode": a[1]})

    def cmd_windows(self, a):
        for w in self.c.call("GET", "/api/wt/windows"):
            print("  %-8d %s %s" % (w["hwnd"], "[attached]" if w["attached"] else "          ", w["title"]))

    def cmd_attach(self, a):
        s = self.c.call("POST", "/api/wt/attach", {"name": a[0], "hwnd": int(a[1]), "tab": a[2] if len(a) > 2 else None})
        print("attached %s -> %s" % (s["name"], s["cmd"]))

    def cmd_kill(self, a):
        self.c.call("DELETE", "/api/sessions/%s" % a[0]); print("removed %s" % a[0])

    def cmd_log(self, a):
        n = int(a[0]) if a else 30
        for e in self.c.call("GET", "/api/events")[-n:]:
            print(self.event_line(e))

    def cmd_url(self, a):
        for u in self.urls:
            print("  " + u)

    def cmd_supervisor(self, a):
        from termpilot_cli import start_supervisor  # shim around termpilot.py
        start_supervisor(self.c, a[0] if a else None, a[1] if len(a) > 1 else None, True)

    def cmd_setup(self, a):
        """Edit approver settings; `setup restart` also restarts the approver."""
        from termpilot import config as config_mod
        from termpilot_cli import setup_interactive
        cfg = config_mod.load()
        self.loud_saved, self.loud = self.loud, False
        try:
            setup_interactive(cfg, quick=False)
        finally:
            self.loud = self.loud_saved
        if a and a[0] == "restart":
            s = self.c.call("POST", "/api/supervisor", {"restart": True})
            print("approver restarted: %s" % s["cmd"])
        else:
            print("(server picks the new settings up on next `supervisor` restart; or run: setup restart)")

    def cmd_rename(self, a):
        self.c.call("POST", "/api/sessions/%s/rename" % a[0], {"new": a[1]}); print("renamed %s -> %s" % (a[0], a[1]))

    def cmd_pin(self, a):
        off = len(a) > 1 and a[1] == "off"
        self.c.call("POST", "/api/sessions/%s/pin" % a[0], {"pinned": not off}); print(("unpinned " if off else "pinned ") + a[0])

    def cmd_quiet(self, a): self.loud = False; print("event feed hidden (held prompts still shown)")
    def cmd_loud(self, a): self.loud = True; print("event feed shown")
    def cmd_help(self, a): print(HELP)

    # -- main loop -----------------------------------------------------------
    def run(self):
        print(C_B + "termpilot console" + C_RESET + "  (help for commands, quit to exit; the server keeps running)")
        self.cmd_url([])
        self.cmd_ls([])
        ps = self.pending()
        if ps:
            print(C_Y + "%d prompt(s) waiting -- 'prompts' to see, 'a' to approve newest" % len(ps) + C_RESET)
        threading.Thread(target=self.feed, daemon=True).start()
        while True:
            try:
                line = input("termpilot> ")
            except (EOFError, KeyboardInterrupt):
                print(); break
            line = line.strip()
            if not line:
                continue
            if line in ("quit", "exit", "q"):
                break
            try:
                a = shlex.split(line)
            except ValueError as e:
                print("parse error:", e); continue
            fn = getattr(self, "cmd_" + a[0], None)
            if not fn:
                print("unknown command: %s  (help)" % a[0]); continue
            try:
                fn(a[1:])
            except SystemExit as e:
                print(C_R + str(e) + C_RESET)
            except (IndexError, ValueError) as e:
                print("bad arguments (%s) -- help" % e)
        self.stop = True


def run_console(cfg, urls):
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)  # enable ANSI colours
        except Exception:
            pass
    Console(cfg, urls).run()
