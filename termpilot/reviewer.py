"""Optional second-opinion reviewer using Codex, Claude Code, Antigravity or a custom CLI.

Config:
  "reviewer": {"enabled": false, "provider": "codex", "command": "", "timeout": 90}

The command receives the prompt on stdin and must answer with a first line of
APPROVE or HOLD, optionally followed by a reason.
"""
import subprocess
import re

from .ai_cli import run_model

REVIEW_PROMPT = """You are reviewing a terminal screen where an AI coding agent is asking for permission.
Decide whether a careful engineer would let it proceed WITHOUT asking the human.

Answer APPROVE only if the pending action is clearly safe: reading files, searching,
editing or creating project files, building, testing, installing packages, git commit/add,
navigation. Answer HOLD if it deletes or overwrites data, force-pushes or resets git,
kills processes, changes system config or permissions, touches disks/devices, sends data
to third parties, or if the agent is really asking the human to choose between approaches,
or if you are unsure.

Reply with exactly one line: APPROVE <short reason>  or  HOLD <short reason>.

--- SCREEN ---
"""


class LlmReviewer:
    def __init__(self, cfg):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.cfg = cfg
        self.command = cfg.get("command", "")
        self.timeout = int(cfg.get("timeout", 90))

    def review(self, context):
        """Return ('approve'|'hold', reason)."""
        try:
            if self.command:
                p = subprocess.run(
                    self.command, shell=True, input=REVIEW_PROMPT + context,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=self.timeout,
                )
                if p.returncode:
                    return "hold", "llm: command exited %s" % p.returncode
                out = (p.stdout or "").strip()
            else:
                out = run_model(REVIEW_PROMPT + context, dict(self.cfg, timeout=self.timeout))
            match = re.fullmatch(r"(APPROVE|HOLD)(?:[ :\-]+([^\r\n]*))?", out, re.IGNORECASE)
            if match:
                return match[1].lower(), "llm: " + (match[2] or "")
            return "hold", "llm: unparseable answer: %r" % out[:200]
        except subprocess.TimeoutExpired:
            return "hold", "llm: timeout after %ss" % self.timeout
        except Exception as e:
            return "hold", "llm: error %s" % e
