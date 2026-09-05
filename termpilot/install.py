"""First-run installation bits: the /termpilot Claude Code command file.

The approver runs `/loop 1m /termpilot` inside Claude Code, so that command must
exist in ~/.claude/commands on whatever machine termpilot runs on. We write it
from a bundled template, filling in how to invoke termpilot on this machine.
"""
import logging
import os

from . import config as config_mod

log = logging.getLogger("termpilot")

TEMPLATE = os.path.join(config_mod.BUNDLE, "templates", "termpilot.md")
TARGET = os.path.join(os.path.expanduser("~"), ".claude", "commands", "termpilot.md")
MARK = "<!-- termpilot-managed"


def render():
    with open(TEMPLATE, encoding="utf-8") as f:
        t = f.read()
    return t.replace("{{TP}}", config_mod.self_command()).replace("{{ROOT}}", config_mod.ROOT)


def ensure_command_file(force=False):
    """Write ~/.claude/commands/termpilot.md if missing or if we own it and it changed."""
    try:
        content = render()
        os.makedirs(os.path.dirname(TARGET), exist_ok=True)
        if os.path.exists(TARGET) and not force:
            with open(TARGET, encoding="utf-8") as f:
                cur = f.read()
            if MARK not in cur:
                log.info("keeping user's own %s (not termpilot-managed)", TARGET)
                return False
            if cur == content:
                return False
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(content)
        log.info("installed %s", TARGET)
        return True
    except Exception as e:
        log.warning("could not install command file: %s", e)
        return False
