"""Session registry, prompt lifecycle, monitor loop and event bus."""
import itertools
import logging
import threading
import time
from collections import deque

from . import rules as rules_mod
from .pty_session import PtySession
from .reviewer import LlmReviewer
from .wt_attach import WtSession, list_windows

log = logging.getLogger("termpilot")


def _slug(title, maxlen=24):
    import re
    t = re.sub(r"^(Administrator|管理员)\s*:\s*", "", title).strip()
    t = re.sub(r"[^\w一-鿿-]+", "-", t).strip("-")
    return t[:maxlen]

TAIL_LINES = 25      # lines checked for an approval prompt
CONTEXT_LINES = 60   # lines given to the policy / reviewer

PROMPT_ACTIVE = ("candidate", "pending", "reviewing", "held", "approved", "denied")


class Prompt:
    _ids = itertools.count(1)

    def __init__(self, session, phash, tail, context):
        self.id = next(self._ids)
        self.session = session.name
        self.hash = phash
        self.snippet = rules_mod.snippet(tail)
        self.context = context
        self.detected_at = time.time()
        self.status = "pending"     # pending|reviewing|approved|held|denied|resolved
        self.reason = ""
        self.decided_by = ""        # rules|llm|human|manual
        self.seen = 1
        self.acted_seen = 0
        self.retries = 0
        self.resolved_at = None

    def info(self):
        return {
            "id": self.id, "session": self.session, "hash": self.hash,
            "snippet": self.snippet, "status": self.status, "reason": self.reason,
            "decided_by": self.decided_by, "detected_at": self.detected_at,
            "resolved_at": self.resolved_at, "retries": self.retries,
        }


class Manager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.rules = rules_mod.Rules(cfg.get("rules"))
        self.reviewer = LlmReviewer(cfg.get("reviewer"))
        self.sessions = {}
        self.prompts = {}
        self.events = deque(maxlen=1000)
        self._ev_id = itertools.count(1)
        self._lock = threading.RLock()
        self._subs = []          # (loop, queue) for events
        self._term_subs = {}     # name -> [(loop, queue)]
        self._stop = threading.Event()
        self._thread = None

    # -- events ------------------------------------------------------------
    def emit(self, kind, session=None, msg="", **extra):
        ev = {"id": next(self._ev_id), "ts": time.time(), "kind": kind,
              "session": session, "msg": msg}
        ev.update(extra)
        self.events.append(ev)
        log.info("%s %s %s", kind, session or "-", msg)
        for loop, q in list(self._subs):
            try:
                loop.call_soon_threadsafe(q.put_nowait, ev)
            except Exception:
                pass
        return ev

    def subscribe_events(self, loop, q):
        self._subs.append((loop, q))

    def unsubscribe_events(self, q):
        self._subs = [(l, x) for l, x in self._subs if x is not q]

    def subscribe_term(self, name, loop, q):
        self._term_subs.setdefault(name, []).append((loop, q))

    def unsubscribe_term(self, name, q):
        self._term_subs[name] = [(l, x) for l, x in self._term_subs.get(name, []) if x is not q]

    def _on_output(self, session, data):
        for loop, q in list(self._term_subs.get(session.name, [])):
            try:
                loop.call_soon_threadsafe(q.put_nowait, data)
            except Exception:
                pass
        if data is None:
            self.emit("exited", session.name, "process exited")

    # -- sessions ----------------------------------------------------------
    def create(self, name, cmd=None, cwd=None, cols=None, rows=None, mode="auto"):
        with self._lock:
            if name in self.sessions:
                raise ValueError("session exists: %s" % name)
            s = PtySession(name, cmd or self.cfg["default_shell"], cwd,
                           cols or self.cfg["default_cols"], rows or self.cfg["default_rows"],
                           on_output=self._on_output)
            s.mode = mode
            self.sessions[name] = s
        self.emit("created", name, "spawned %r (pid %s)" % (s.cmd, s.pid))
        return s

    def attach(self, name, hwnd, tab=None, mode="auto"):
        with self._lock:
            if name in self.sessions:
                raise ValueError("session exists: %s" % name)
            s = WtSession(name, hwnd, tab)
            s.mode = mode
            self.sessions[name] = s
        self.emit("attached", name, "attached to hwnd %s %s" % (hwnd, ("tab %r" % tab) if tab else ""))
        self._save_state()
        return s

    # -- persistence of attachments (pty processes cannot survive a restart) ----
    def _state_path(self):
        import os
        from . import config as config_mod
        return os.path.join(config_mod.ROOT, "sessions.json")

    def _save_state(self):
        import json
        try:
            wts = [{"name": s.name, "hwnd": s.hwnd, "tab": s.tab, "mode": s.mode, "pinned": getattr(s, "pinned", False)}
                   for s in self.sessions.values() if s.kind == "wt"]
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump({"wt": wts}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("save state failed: %s", e)

    def restore_state(self):
        import json
        import os
        import win32gui
        p = self._state_path()
        if not os.path.exists(p):
            return
        try:
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
        except Exception as e:
            log.warning("load state failed: %s", e)
            return
        for w in st.get("wt", []):
            try:
                if win32gui.IsWindow(int(w["hwnd"])) and w["name"] not in self.sessions:
                    s = self.attach(w["name"], int(w["hwnd"]), w.get("tab"), w.get("mode", "auto"))
                    s.pinned = bool(w.get("pinned", False))
            except Exception as e:
                log.warning("re-attach %s failed: %s", w.get("name"), e)

    def remove(self, name):
        with self._lock:
            s = self.sessions.pop(name, None)
        if s is None:
            raise KeyError("no such session: %s" % name)
        s.kill()
        if s.kind == "wt":
            self._on_output(s, None)
        self.emit("removed", name, "session removed")
        self._save_state()

    def get(self, name):
        s = self.sessions.get(name)
        if not s:
            raise KeyError("no such session: %s" % name)
        return s

    def rename(self, old, new):
        new = (new or "").strip()
        if not new or "/" in new:
            raise ValueError("bad name")
        with self._lock:
            if new in self.sessions:
                raise ValueError("session exists: %s" % new)
            s = self.sessions.pop(old, None)
            if s is None:
                raise KeyError("no such session: %s" % old)
            s.name = new
            self.sessions[new] = s
            self._term_subs[new] = self._term_subs.pop(old, [])
        for p in self.prompts.values():
            if p.session == old:
                p.session = new
        self.emit("renamed", new, "was %r" % old)
        self._save_state()
        return s

    def set_pinned(self, name, pinned):
        s = self.get(name)
        s.pinned = bool(pinned)
        self.emit("pinned" if pinned else "unpinned", name, "")
        self._save_state()

    def set_mode(self, name, mode):
        if mode not in ("auto", "manual", "off"):
            raise ValueError("mode must be auto|manual|off")
        s = self.get(name)
        s.mode = mode
        self.emit("mode", name, "mode -> %s" % mode)
        self._save_state()

    # -- the approver ----------------------------------------------------------
    def supervisor_cfg(self, **over):
        from . import config as config_mod
        c = dict(config_mod.DEFAULTS["supervisor"])
        c.update(self.cfg.get("supervisor") or {})
        c.update({k: v for k, v in over.items() if v is not None})
        return c

    def save_supervisor_cfg(self, changes):
        """Persist approver settings (model/effort/every/...) to config.json."""
        from . import config as config_mod
        sc = self.supervisor_cfg()
        for k in config_mod.DEFAULTS["supervisor"]:
            if k in changes:
                v = changes[k]
                if k == "autostart":
                    v = bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "y", "on")
                else:
                    v = (v or "").strip() if isinstance(v, str) else v
                sc[k] = v
        config_mod.supervisor_command(sc)  # validate before saving
        self.cfg["supervisor"] = sc
        disk = config_mod.load()
        disk["supervisor"] = sc
        config_mod.save(disk)
        self.emit("config", None, "approver settings saved: %s" % config_mod.supervisor_command(sc))
        return sc

    def start_supervisor(self, restart=False, **over):
        """Start the configured approver; keep its own prompts in manual mode."""
        from . import config as config_mod
        sc = self.supervisor_cfg(**over)
        cmd = config_mod.supervisor_command(sc)
        name = sc["name"]
        old = self.sessions.get(name)
        if old is not None:
            if old.alive() and not restart:
                raise ValueError("approver %r already running (use restart)" % name)
            self.remove(name)
            time.sleep(0.5)
        if sc.get("provider", "codex") == "claude":
            from .install import ensure_command_file
            ensure_command_file()
        s = self.create(name, cmd, config_mod.ROOT, 140, 40, mode="manual")
        s.pinned = True
        s.role = "approver"
        if sc.get("provider", "codex") in ("codex", "antigravity"):
            info = config_mod.provider_info(sc.get("provider", "codex"))
            self.emit("approver", name, "started %s scheduler (%s)" % (info["label"], sc.get(info["model_key"])))
            return s
        loop_cmd = "/loop %s /termpilot" % sc["every"] if sc.get("every") else "/termpilot"

        def handoff():
            t0 = time.time()
            while time.time() - t0 < 90 and s.alive():
                text = s.screen_text()
                if "❯" in text and "shift+tab" in text:
                    time.sleep(1.0)
                    s.write(loop_cmd)
                    time.sleep(0.8)
                    s.send_keys(["enter"])
                    self.emit("approver", name, "started, sent %r" % loop_cmd)
                    return
                time.sleep(1)
            self.emit("approver", name, "claude prompt not seen within 90s; send the loop command by hand")

        threading.Thread(target=handoff, name="approver-handoff", daemon=True).start()
        return s

    def list_wt_windows(self):
        wts = [s for s in self.sessions.values() if s.kind == "wt"]
        wins = list_windows()
        for w in wins:
            w["attached"] = [s.name for s in wts if s.hwnd == w["hwnd"] and not s.tab]
            for t in w["tabs"]:
                t["attached"] = [s.name for s in wts if s.hwnd == w["hwnd"] and s.tab and
                                 (t["name"] == s.tab or t["name"].startswith(s.tab))]
        return wins

    def attach_all(self, mode="auto"):
        """Attach every Windows Terminal tab that is not attached yet (skipping our own console)."""
        made = []
        for w in self.list_wt_windows():
            tabs = w["tabs"] or [{"name": w["title"], "active": True, "attached": w["attached"]}]
            for t in tabs:
                if t.get("attached") or "termpilot" in t["name"].lower():
                    continue
                base = _slug(t["name"]) or "wt%d" % w["hwnd"]
                name, i = base, 2
                while name in self.sessions:
                    name, i = "%s-%d" % (base, i), i + 1
                tab = t["name"] if len(tabs) > 1 else None
                made.append(self.attach(name, w["hwnd"], tab, mode).info())
        return made

    def snapshot(self):
        infos = []
        for s in self.sessions.values():
            i = s.info()
            i["pinned"] = getattr(s, "pinned", False)
            i["role"] = getattr(s, "role", "")
            infos.append(i)
        infos.sort(key=lambda i: (not i["pinned"], i["created_at"]))
        return {
            "sessions": infos,
            "pending": [p.info() for p in self.prompts.values() if p.status in ("pending", "reviewing", "held")],
            "reviewer": self.reviewer.enabled,
        }

    # -- prompt actions (human or automation) -------------------------------
    def send_keys(self, name, keys):
        s = self.get(name)
        s.send_keys(keys)
        s.last_action_at = time.time()

    def approve(self, pid, by="human", reason=""):
        p = self.prompts[pid]
        s = self.get(p.session)
        self.send_keys(s.name, ["enter"])
        p.status, p.decided_by, p.reason = "approved", by, reason or p.reason
        p.acted_seen = p.seen
        self.emit("approved", s.name, "[%s] %s" % (by, p.reason), prompt=p.info())
        return p

    def deny(self, pid, by="human", reason=""):
        p = self.prompts[pid]
        s = self.get(p.session)
        self.send_keys(s.name, ["esc"])
        p.status, p.decided_by, p.reason = "denied", by, reason or "denied"
        p.acted_seen = p.seen
        self.emit("denied", s.name, "[%s] %s" % (by, p.reason), prompt=p.info())
        return p

    def hold(self, p, by, reason):
        p.status, p.decided_by, p.reason = "held", by, reason
        self.emit("held", p.session, "[%s] %s" % (by, reason), prompt=p.info())

    # -- monitor loop --------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        interval = float(self.cfg.get("poll_interval", 1.0))
        while not self._stop.is_set():
            t0 = time.time()
            for s in list(self.sessions.values()):
                try:
                    if s.kind == "wt":
                        if s.refresh():
                            # push the new screen to WebUI viewers right away
                            self._on_output(s, s.screen_text())
                    self._check(s)
                except Exception as e:
                    log.exception("check %s failed: %s", s.name, e)
            dt = time.time() - t0
            time.sleep(max(0.1, interval - dt))

    def _check(self, s):
        if s.mode == "off":
            if s.current_prompt and s.current_prompt.status in PROMPT_ACTIVE:
                self._resolve(s.current_prompt, "detection off")
                s.current_prompt = None
            return
        text = (s.screen_text() or "").rstrip()   # drop blank rows below the cursor
        lines = text.splitlines()
        tail = "\n".join(lines[-TAIL_LINES:])
        ctx = "\n".join(lines[-CONTEXT_LINES:])
        cur = s.current_prompt

        if not self.rules.find_live_prompt(tail, int(self.cfg.get("stale_lines", 1))):
            if cur and cur.status in PROMPT_ACTIVE:
                self._resolve(cur, "prompt gone")
                s.current_prompt = None
            return

        h = rules_mod.prompt_hash(tail)
        confirm = int(self.cfg.get("confirm_cycles", 2))
        if cur and cur.hash == h and cur.status in PROMPT_ACTIVE:
            cur.seen += 1
            if cur.status == "candidate":
                if cur.seen >= confirm:
                    self._decide(s, cur)
            elif cur.status == "approved" and cur.retries < int(self.cfg.get("max_retries", 3)) \
                    and cur.seen - cur.acted_seen >= int(self.cfg.get("retry_cycles", 5)):
                cur.retries += 1
                cur.acted_seen = cur.seen
                self.send_keys(s.name, ["enter"])
                self.emit("retry", s.name, "prompt still visible, re-sent Enter (%d)" % cur.retries)
            return

        if cur and cur.status in PROMPT_ACTIVE:
            if cur.status == "candidate":
                self.prompts.pop(cur.id, None)   # was still rendering; replace silently
            else:
                if time.time() - getattr(s, "last_action_at", 0) < float(self.cfg.get("action_cooldown", 2.5)):
                    return  # screen still settling after our keypress; re-evaluate next cycle
                self._resolve(cur, "replaced by new prompt")

        p = Prompt(s, h, tail, ctx)
        p.status = "candidate"
        self.prompts[p.id] = p
        s.current_prompt = p
        if confirm <= 1:
            self._decide(s, p)

    def _decide(self, s, p):
        p.status = "pending"
        self.emit("detected", s.name, p.snippet.splitlines()[-1] if p.snippet else "", prompt=p.info())
        ctx = p.context
        if s.mode == "manual":
            self.hold(p, "manual", "manual mode: waiting for human")
            return

        decision, reason = self.rules.classify(ctx)
        if decision == "hold":
            self.hold(p, "rules", reason)
            return
        if self.reviewer.enabled:
            p.status, p.reason = "reviewing", "rules ok; asking reviewer"
            self.emit("reviewing", s.name, reason, prompt=p.info())
            threading.Thread(target=self._llm_review, args=(p, s), daemon=True).start()
            return
        self.approve(p.id, by="rules", reason=reason)

    def _llm_review(self, p, s):
        decision, reason = self.reviewer.review(p.context)
        if p.status != "reviewing" or s.current_prompt is not p:
            return  # resolved or human acted meanwhile
        if decision == "approve":
            self.approve(p.id, by="llm", reason=reason)
        else:
            self.hold(p, "llm", reason)

    def _resolve(self, p, why):
        p.status = "resolved"
        p.resolved_at = time.time()
        self.emit("resolved", p.session, why, prompt=p.info())
