"""Optional second-opinion reviewer that shells out to an LLM CLI (e.g. `claude -p`).

Config:
  "reviewer": {"enabled": false, "command": "claude -p", "timeout": 90}

The command receives the prompt on stdin and must answer with a first line of
APPROVE or HOLD, optionally followed by a reason.
"""
import subprocess

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
        self.command = cfg.get("command", "claude -p")
        self.timeout = int(cfg.get("timeout", 90))

    def review(self, context):
        """Return ('approve'|'hold', reason)."""
        try:
            p = subprocess.run(
                self.command, shell=True, input=REVIEW_PROMPT + context,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout,
            )
            out = (p.stdout or "").strip()
            for line in out.splitlines():
                s = line.strip()
                if s.upper().startswith("APPROVE"):
                    return "approve", "llm: " + s[7:].strip(" :-")
                if s.upper().startswith("HOLD"):
                    return "hold", "llm: " + s[4:].strip(" :-")
            return "hold", "llm: unparseable answer: %r" % out[:200]
        except subprocess.TimeoutExpired:
            return "hold", "llm: timeout after %ss" % self.timeout
        except Exception as e:
            return "hold", "llm: error %s" % e
