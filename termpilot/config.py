"""Config file handling (config.json next to termpilot.py)."""
import json
import base64
import copy
import os
import secrets
import shlex
import subprocess

import sys

FROZEN = bool(getattr(sys, "frozen", False))
# ROOT: where config.json / sessions.json / logs live. Next to the exe when frozen.
ROOT = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BUNDLE: where read-only bundled files (static/, templates) live.
BUNDLE = getattr(sys, "_MEIPASS", ROOT)
STATIC_DIR = os.path.join(BUNDLE, "static")
CONFIG_PATH = os.path.join(ROOT, "config.json")

# Fixed catalog, verified on 2026-10-09. No model-discovery requests at runtime.
MODEL_CATALOG_DATE = "2026-10-09"
PROVIDERS = {
    "codex": {"label": "Codex", "executable": "codex", "model_key": "model",
              "effort_key": "effort", "extra_key": "extra_args", "models_key": "models"},
    "claude": {"label": "Claude Code", "executable": "claude", "model_key": "claude_model",
               "effort_key": "claude_effort", "extra_key": "claude_extra_args", "models_key": "claude_models"},
    "antigravity": {"label": "Antigravity", "executable": "agy", "model_key": "antigravity_model",
                   "effort_key": "antigravity_effort", "extra_key": "antigravity_extra_args", "models_key": "antigravity_models"},
}


def provider_info(provider):
    if provider not in PROVIDERS:
        raise ValueError("provider must be codex, claude or antigravity")
    return PROVIDERS[provider]


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
    "reviewer": {"enabled": False, "provider": "codex", "model": "gpt-6.1-sol", "effort": "xhigh",
                 "claude_model": "claude-opus-5-5", "antigravity_model": "gemini-3.8-flash-high",
                 "claude_effort": "high", "antigravity_effort": "",
                 "claude_extra_args": "", "antigravity_extra_args": "", "command": "", "timeout": 90},
    # Codex and Antigravity use the local scheduler; Claude keeps /loop.
    "supervisor": {"autostart": True, "name": "approver", "provider": "codex",
                   "model": "gpt-6.1-sol", "claude_model": "claude-opus-5-5", "effort": "xhigh", "every": "1m",
                   "claude_effort": "high", "antigravity_effort": "",
                   "antigravity_model": "gemini-3.8-flash-high", "antigravity_extra_args": "",
                   "permission_mode": "auto", "extra_args": "", "claude_extra_args": ""},
    "log_file": "termpilot.log",
}


# Choices offered by the setup UI. Free text is always accepted too, so a model
# or effort level supported by the selected CLI can be typed in.
SUPERVISOR_PRESETS = {
    "providers": [(key, info["label"]) for key, info in PROVIDERS.items()],
    "models": [
        ("gpt-6.1-sol", "GPT-6.1 Sol（默认，日常编码主力）"),
        ("gpt-6-astra", "GPT-6 Astra（最强推理）"),
        ("gpt-6-sol", "GPT-6 Sol"),
        ("gpt-6-luna", "GPT-6 Luna（最省）"),
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
    ],
    "claude_models": [("claude-opus-5-5", "Opus 5.5（默认）"), ("claude-sonnet-5-5", "Sonnet 5.5"),
                      ("claude-haiku-5-5", "Haiku 5.5"), ("claude-fable-5-1", "Fable 5.1")],
    "antigravity_models": [
        ("gemini-3.8-flash-high", "Gemini 3.8 Flash High（默认）"),
        ("gemini-3.8-flash-medium", "Gemini 3.8 Flash Medium"),
        ("gemini-3.8-flash-low", "Gemini 3.8 Flash Low"),
        ("gemini-3.7-flash-high", "Gemini 3.7 Flash High"),
        ("gemini-3.7-flash-medium", "Gemini 3.7 Flash Medium"),
        ("gemini-3.7-flash-low", "Gemini 3.7 Flash Low"),
        ("gemini-3.6-flash-high", "Gemini 3.6 Flash High"),
        ("gemini-3.6-flash-medium", "Gemini 3.6 Flash Medium"),
        ("gemini-3.6-flash-low", "Gemini 3.6 Flash Low"),
        ("gemini-3.1-pro-high", "Gemini 3.1 Pro High"),
        ("gemini-3.1-pro-low", "Gemini 3.1 Pro Low"),
    ],
    "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "claude_efforts": ["low", "medium", "high", "xhigh", "max"],
    "antigravity_efforts": [("", "按模型预设"), "low", "medium", "high"],
    "permission_modes": ["auto", "acceptEdits", "dontAsk", "bypassPermissions", "manual", "plan"],
    "intervals": ["30s", "1m", "2m", "5m", "10m"],
}

# Models that left a CLI's catalog: a saved config pinning one is rewritten on
# load, otherwise the CLI rejects the --model value. Served models are left alone.
RETIRED_MODELS = {
    "model": {"gpt-5.3-codex-spark": "gpt-6.1-sol", "gpt-5.3-codex": "gpt-6.1-sol",
              "gpt-5.5": "gpt-6.1-sol"},
    "claude_model": {},
    "antigravity_model": {},
}

SUPERVISOR_FIELDS = [
    # key, label, kind, choices-key
    ("provider", "CLI", "choice", "providers"),
    ("model", "ChatGPT / Codex 模型", "choice+text", "models"),
    ("claude_model", "Claude 模型", "choice+text", "claude_models"),
    ("antigravity_model", "Antigravity 模型", "choice+text", "antigravity_models"),
    ("effort", "Codex 推理强度", "choice+text", "efforts"),
    ("claude_effort", "Claude 推理强度", "choice+text", "claude_efforts"),
    ("antigravity_effort", "Antigravity 推理强度（可留空）", "choice+text", "antigravity_efforts"),
    ("every", "巡检间隔（留空单次）", "choice+text", "intervals"),
    ("permission_mode", "Claude 权限模式", "choice", "permission_modes"),
    ("extra_args", "Codex 附加参数", "text", None),
    ("claude_extra_args", "Claude 附加参数", "text", None),
    ("antigravity_extra_args", "Antigravity 附加参数", "text", None),
    ("name", "会话名", "text", None),
    ("autostart", "服务启动时自动拉起", "bool", None),
]

FIELD_PROVIDERS = {
    info[key]: provider for provider, info in PROVIDERS.items()
    for key in ("model_key", "effort_key", "extra_key")
}
FIELD_PROVIDERS["permission_mode"] = "claude"


def session_command(sc, provider=None, model=None):
    """Interactive terminal command using the saved settings for this CLI."""
    provider = provider or sc.get("provider", "codex")
    info = provider_info(provider)
    parts = [info["executable"]]
    selected_model = sc.get(info["model_key"]) if model is None else model
    if selected_model:
        parts += ["--model", selected_model]
    effort = sc.get(info["effort_key"])
    if effort:
        parts += ["-c", "model_reasoning_effort=" + json.dumps(effort)] if provider == "codex" else ["--effort", effort]
    if provider == "claude" and sc.get("permission_mode"):
        parts += ["--permission-mode", sc["permission_mode"]]
    if sc.get(info["extra_key"]):
        parts += shlex.split(sc[info["extra_key"]], posix=True)
    return subprocess.list2cmdline(parts)


def supervisor_command(sc):
    """Build a scheduled Codex/Antigravity worker or an interactive Claude command."""
    provider = sc.get("provider", "codex")
    provider_info(provider)
    # Validate quoting before saving, even for settings consumed by the scheduler.
    session_command(sc)
    if provider in ("codex", "antigravity"):
        from .supervisor import interval_seconds
        interval_seconds(sc.get("every", "1m"))
        data = base64.urlsafe_b64encode(json.dumps(sc).encode("utf-8")).decode("ascii")
        argv = [sys.executable]
        if not FROZEN:
            argv.append(os.path.join(ROOT, "termpilot.py"))
        return subprocess.list2cmdline(argv + ["supervisor-run", "--settings", data])
    return session_command(sc)


def load():
    cfg = copy.deepcopy(DEFAULTS)
    stored = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            stored = json.load(f)
            cfg.update(stored)
    changed = False
    for key in ("supervisor", "reviewer"):
        old = stored.get(key) or {}
        merged = copy.deepcopy(DEFAULTS[key])
        merged.update(old)
        if "claude_effort" not in old and "effort" in old:
            merged["claude_effort"] = old["effort"]
        for field, retired in RETIRED_MODELS.items():
            if merged.get(field) in retired:
                merged[field] = retired[merged[field]]
                changed = True
        if old and "provider" not in old:
            if key == "supervisor":
                merged["claude_model"] = old.get("model") or "opus"
                merged["model"] = DEFAULTS[key]["model"]
                # Claude-specific extra flags remain available when switching back.
                merged["claude_extra_args"] = old.get("extra_args", "")
                merged["extra_args"] = ""
            elif old.get("command", "") in ("claude -p", "claude -p --model opus"):
                merged["command"] = ""
            changed = True
        cfg[key] = merged
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
