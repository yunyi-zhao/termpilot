"""Config file handling (config.json next to termpilot.py)."""
import json
import os
import secrets
import subprocess

import sys

FROZEN = bool(getattr(sys, "frozen", False))
# ROOT: where config.json / sessions.json / logs live. Next to the exe when frozen.
ROOT = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BUNDLE: where read-only bundled files (static/, templates) live.
BUNDLE = getattr(sys, "_MEIPASS", ROOT)
STATIC_DIR = os.path.join(BUNDLE, "static")
CONFIG_PATH = os.path.join(ROOT, "config.json")


def self_command():
    """How to invoke termpilot from a shell on this machine (used in the /termpilot command file)."""
    if FROZEN:
        return '& "%s"' % sys.executable
    return '& "%s" "%s"' % (sys.executable, os.path.join(ROOT, "termpilot.py"))

DEFAULTS = {
    "port": 8787,
    # "auto" = 127.0.0.1 + the Tailscale IPv4 address; or a list of addresses
    "hosts": "auto",
    "token": "",              # generated on first run
    "poll_interval": 0.3,     # seconds between screen checks (a full UIA read of 4 windows takes ~25 ms)
    "retry_cycles": 5,        # re-send Enter if the same approved prompt persists
    "max_retries": 3,
    "stale_lines": 1,         # more content lines below the prompt => already answered
    "action_cooldown": 2.5,   # seconds after a keypress before a changed screen counts as a new prompt
    "confirm_cycles": 2,      # identical screen for N polls before a prompt is acted on
    "default_shell": "pwsh -NoLogo",
    "default_cols": 120,
    "default_rows": 35,
    "rules": {},              # see rules.DEFAULT_RULES; *_extra keys append
    "reviewer": {"enabled": False, "command": "claude -p --model opus", "timeout": 90},
    # The approver: a Claude Code session hosted by termpilot that reviews the other
    # terminals via /loop EVERY /termpilot. Shown in the WebUI as a pinned card.
    "supervisor": {"autostart": True, "name": "approver", "model": "opus", "effort": "high", "every": "1m",
                   "permission_mode": "auto", "extra_args": ""},
    "log_file": "termpilot.log",
}


# Choices offered by the setup UI. Free text is always accepted too, so a model
# or effort level that appears in a future Claude Code release can be typed in.
SUPERVISOR_PRESETS = {
    "models": [
        ("opus", "Opus（默认，审查够用）"),
        ("sonnet", "Sonnet（更快更省）"),
        ("fable", "Fable（最强，最贵）"),
        ("haiku", "Haiku（最省）"),
    ],
    "efforts": ["low", "medium", "high", "xhigh", "max"],
    "permission_modes": ["auto", "acceptEdits", "dontAsk", "bypassPermissions", "manual", "plan"],
    "intervals": ["30s", "1m", "2m", "5m", "10m"],
}

SUPERVISOR_FIELDS = [
    # key, label, kind, choices-key
    ("model", "模型 --model", "choice+text", "models"),
    ("effort", "推理强度 --effort", "choice+text", "efforts"),
    ("every", "巡检间隔 /loop", "choice+text", "intervals"),
    ("permission_mode", "权限模式 --permission-mode", "choice", "permission_modes"),
    ("extra_args", "附加参数（原样加到 claude 命令后）", "text", None),
    ("name", "会话名", "text", None),
    ("autostart", "服务启动时自动拉起", "bool", None),
]


def supervisor_command(sc):
    """Build the claude command line for the approver from its config."""
    parts = ["claude"]
    if sc.get("permission_mode"):
        parts += ["--permission-mode", sc["permission_mode"]]
    if sc.get("model"):
        parts += ["--model", sc["model"]]
    if sc.get("effort"):
        parts += ["--effort", sc["effort"]]
    if sc.get("extra_args"):
        parts.append(sc["extra_args"].strip())
    return " ".join(parts)


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    changed = False
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(24)
        changed = True
    if changed or not os.path.exists(CONFIG_PATH):
        save(cfg)
    return cfg


def save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def tailscale_ip():
    for exe in (r"C:\Program Files\Tailscale\tailscale.exe", "tailscale"):
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5)
            ip = (out.stdout or "").strip().splitlines()
            if ip and ip[0].startswith("100."):
                return ip[0]
        except Exception:
            continue
    return None


def resolve_hosts(cfg):
    hosts = cfg.get("hosts", "auto")
    if hosts == "auto":
        hs = ["127.0.0.1"]
        ts = tailscale_ip()
        if ts:
            hs.append(ts)
        return hs
    if isinstance(hosts, str):
        return [hosts]
    return list(hosts)
