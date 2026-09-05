"""A PowerShell (or any) process hosted in a ConPTY.

We drive `winpty.PTY` directly instead of `winpty.PtyProcess`: PtyProcess relays
output through a 127.0.0.1 socket, and on this machine loopback traffic is
throttled by local filtering drivers (~500 ms per read). Reading the ConPTY pipe
directly takes ~15 ms.
"""
import os
import shlex
import subprocess
import threading
import time
from collections import deque
from shutil import which

from winpty import PTY

from .screen import Screen

KEYMAP = {
    "enter": "\r", "esc": "\x1b", "tab": "\t", "space": " ", "backspace": "\x7f",
    "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D",
    "ctrl-c": "\x03", "ctrl-d": "\x04", "ctrl-z": "\x1a", "ctrl-l": "\x0c",
    "shift-tab": "\x1b[Z",
}

REPLAY_CAP = 200_000   # chars of raw output kept for xterm replay
IDLE_SLEEP = 0.004     # poll interval when the pty has nothing to read


def key_to_bytes(key):
    k = key.lower()
    if k in KEYMAP:
        return KEYMAP[k]
    if len(key) == 1:
        return key
    raise ValueError("unknown key: %s" % key)


def _spawn(cmd, cwd, env, cols, rows):
    argv = shlex.split(cmd, posix=False)
    exe = which(argv[0], path=env.get("PATH", os.defpath))
    if not exe:
        raise FileNotFoundError("command not found: %s" % argv[0])
    pty = PTY(cols, rows)
    envblock = "\0".join("%s=%s" % kv for kv in env.items()) + "\0"
    cmdline = " " + subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else None
    if cmdline:
        pty.spawn(exe, cwd=cwd or os.getcwd(), env=envblock, cmdline=cmdline)
    else:
        pty.spawn(exe, cwd=cwd or os.getcwd(), env=envblock)
    return pty


class PtySession:
    kind = "pty"

    def __init__(self, name, cmd="pwsh -NoLogo", cwd=None, cols=120, rows=35, on_output=None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.cols, self.rows = cols, rows
        self.mode = "auto"
        self.created_at = time.time()
        self.last_output_at = self.created_at
        self.current_prompt = None
        self.on_output = on_output
        self._lock = threading.Lock()
        self._screen = Screen(cols, rows)
        self._replay = deque()
        self._replay_len = 0
        self._dead = False
        # Don't leak a parent Claude Code session's markers into the child, or
        # `claude` refuses to start / disables transcripts.
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}
        env.setdefault("TERM", "xterm-256color")
        self.pty = _spawn(cmd, cwd or None, env, cols, rows)
        self.pid = self.pty.pid
        self._reader = threading.Thread(target=self._read_loop, name="pty-" + name, daemon=True)
        self._reader.start()

    # -- reading ---------------------------------------------------------
    def _read_loop(self):
        idle = IDLE_SLEEP
        while True:
            try:
                data = self.pty.read()          # non-blocking: "" when nothing is pending
            except Exception:
                data = ""
                if not self.alive():
                    break
            if data:
                idle = IDLE_SLEEP
                with self._lock:
                    self._screen.feed(data)
                    self._replay.append(data)
                    self._replay_len += len(data)
                    while self._replay_len > REPLAY_CAP and len(self._replay) > 1:
                        self._replay_len -= len(self._replay.popleft())
                self.last_output_at = time.time()
                if self.on_output:
                    try:
                        self.on_output(self, data)
                    except Exception:
                        pass
                continue
            if not self.alive():
                # drain anything left, then stop
                try:
                    tail = self.pty.read()
                except Exception:
                    tail = ""
                if tail and self.on_output:
                    with self._lock:
                        self._screen.feed(tail)
                        self._replay.append(tail)
                    try:
                        self.on_output(self, tail)
                    except Exception:
                        pass
                break
            time.sleep(idle)
            idle = min(idle * 1.5, 0.05)        # back off to 50 ms when idle
        self._dead = True
        if self.on_output:
            try:
                self.on_output(self, None)  # None = exited
            except Exception:
                pass

    def screen_text(self):
        with self._lock:
            return self._screen.text()

    def replay(self):
        with self._lock:
            return "".join(self._replay)

    # -- writing ---------------------------------------------------------
    def write(self, data):
        self.pty.write(data)

    def send_keys(self, keys):
        for k in keys:
            self.pty.write(key_to_bytes(k))
            time.sleep(0.03)

    def resize(self, cols, rows):
        if cols == self.cols and rows == self.rows:
            return
        self.cols, self.rows = cols, rows
        try:
            self.pty.set_size(cols, rows)
        except Exception:
            pass
        with self._lock:
            self._screen.resize(cols, rows)

    # -- lifecycle -------------------------------------------------------
    def alive(self):
        if self._dead:
            return False
        try:
            return bool(self.pty.isalive())
        except Exception:
            return False

    def kill(self):
        try:
            import psutil
            proc = psutil.Process(self.pid)
            for c in proc.children(recursive=True):
                try:
                    c.kill()
                except Exception:
                    pass
            proc.kill()
        except Exception:
            pass
        self._dead = True

    def info(self):
        return {
            "name": self.name, "kind": self.kind, "cmd": self.cmd, "cwd": self.cwd,
            "pid": self.pid, "alive": self.alive(), "mode": self.mode,
            "cols": self.cols, "rows": self.rows,
            "created_at": self.created_at, "last_output_at": self.last_output_at,
            "prompt": self.current_prompt.info() if self.current_prompt else None,
        }
