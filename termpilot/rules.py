"""Prompt detection and approval policy (regex based)."""
import hashlib
import re

_ALNUM = re.compile(r"[0-9A-Za-z一-鿿]")
# lines that are part of a permission dialog rather than output printed after it
_PROMPT_LINE = re.compile(
    r"^\s*[│|]?\s*(?:[❯►>]\s*)?\d+\.\s"          # "❯ 1. Yes" option lines
    r"|Esc to cancel|\(esc\)|Enter to |Tab to |Space to |to toggle|to select|to confirm"
    r"|↑/↓|Navigate|ctrl\+g|Amend|Skip"
    r"|^\s*[│|]?\s*(?:Yes|No|Allow|Deny|Always|Never)\b"
    r"|y/n|\[Y/n\]|\[y/N\]",
    re.IGNORECASE,
)

DEFAULT_RULES = {
    # Something on screen is asking for a yes/no or allow/deny decision.
    "approval_patterns": [
        r"Do you want to (?:make this edit|proceed|create|run|allow|continue)",
        r"[❯►>]\s*1\.\s*Yes",
        r"Allow\s+(?:Bash|PowerShell|Edit|Write|Read|MultiEdit|mcp_|Glob|Grep|WebFetch|WebSearch|Update|Skill|Agent|NotebookEdit)",
        r"1\.\s*Yes[\s\S]{0,300}2\.\s*(?:Yes|No)",
        r"Esc to cancel[\s\S]{0,300}Yes",
        r"Allow (?:once|always)",
        r"Allow execution",                 # gemini-cli
        r"Approve\s*(?:this|command)?\?",   # codex
        r"\[y/N\]|\(y/n\)|\[Y/n\]",
    ],
    # Destructive or irreversible things: never auto-approve.
    "unsafe_patterns": [
        r"\brm\s+-r[f]?\b", r"\brm\s+-rf\b", r"\brm\s+", r"\brmdir\b", r"\bdel\b", r"\berase\b",
        r"\bunlink\b", r"\bshred\b", r"\bdelete\b", r"\bremove\b",
        r"Remove-Item", r"\bri\s+", r"\brd\s+", r"Clear-Content", r"Format-Volume", r"Clear-Disk",
        r"git\s+branch\s+-[dD]\b", r"git\s+push\s+(?:.*\s)?--force", r"git\s+push\s+-f\b",
        r"git\s+reset\s+--hard", r"git\s+clean\s+-[a-z]*f", r"git\s+checkout\s+--\s",
        r"\bdrop\s+(?:table|database|collection|schema)\b", r"\btruncate\b",
        r"Stop-Process", r"\btaskkill\b", r"\bkill\s+-9", r"\bshutdown\b", r"Restart-Computer",
        r"\breg\s+delete\b", r"\bdiskpart\b", r"\bdd\s+if=", r"\bmkfs\b", r"\bformat\s+[a-z]:",
        r"chmod\s+-R", r"icacls\s+.*/grant", r"Set-ExecutionPolicy",
    ],
    # The agent is asking the human to choose a direction: never auto-approve.
    "direction_patterns": [
        r"which\s+(?:approach|option|direction|implementation|method|way|path)",
        r"(?:choose|pick|select)\s+(?:an?\s+)?(?:approach|option|direction|implementation|method)",
        r"[Hh]ere\s+are\s+(?:some|a\s+few|the|several)\s+(?:options|approaches|directions|ways)",
        r"[Ww]hich\s+of\s+(?:these|the\s+following)",
        r"(?:two|three|several|multiple)\s+(?:possible\s+|potential\s+)?(?:approaches|options|ways|directions)",
        r"[Oo]ption\s+[ABC123]\s*:",
        r"[Aa]pproach\s+[ABC123]\s*:",
    ],
    # Matches here win over unsafe_patterns (e.g. flashing an FPGA board with dd).
    "safe_override_patterns": [],
}


class Rules:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        merged = {}
        for k in DEFAULT_RULES:
            base = cfg[k] if k in cfg else DEFAULT_RULES[k]
            merged[k] = list(base) + list(cfg.get(k + "_extra", []))
        self._c = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in merged.items()}

    def _first(self, kind, text):
        for rx in self._c[kind]:
            m = rx.search(text)
            if m:
                return m.group(0)
        return None

    def detect_prompt(self, tail):
        return self._first("approval_patterns", tail) is not None

    def find_live_prompt(self, tail, stale_lines=1):
        """Return True if an approval prompt is on screen and still waiting.

        A prompt that has already been answered usually stays in the scrollback with
        more output printed below it. We take the LAST match of any approval pattern
        and count *content* lines after it (lines with at least one alphanumeric
        character, so box-drawing borders don't count); more than `stale_lines`
        means the prompt is stale.
        """
        first_start, last_end = None, -1
        for rx in self._c["approval_patterns"]:
            for m in rx.finditer(tail):
                first_start = m.start() if first_start is None else min(first_start, m.start())
                last_end = max(last_end, m.end())
        if last_end < 0:
            return False
        lines = tail.splitlines(keepends=True)
        # line index of the last approval match
        pos, end_idx, start_idx = 0, 0, 0
        for i, l in enumerate(lines):
            if pos <= first_start < pos + len(l):
                start_idx = i
            if pos <= last_end - 1 < pos + len(l):
                end_idx = i
            pos += len(l)
        # the dialog usually ends with a footer/hint line ("Esc to cancel", "4. No",
        # "↑/↓ Navigate"); long option texts may wrap onto plain lines in between.
        # Extend the block to the last footer-ish line after the first match.
        for i in range(len(lines) - 1, start_idx, -1):
            if _PROMPT_LINE.search(lines[i]):
                end_idx = max(end_idx, i)
                break
        content = [l for l in lines[end_idx + 1:] if _ALNUM.search(l) and not _PROMPT_LINE.search(l)]
        return len(content) <= stale_lines

    def classify(self, context):
        """Return ('approve'|'hold', reason)."""
        m = self._first("safe_override_patterns", context)
        if m:
            return "approve", "safe override: %r" % m
        m = self._first("direction_patterns", context)
        if m:
            return "hold", "direction choice: %r" % m
        m = self._first("unsafe_patterns", context)
        if m:
            return "hold", "unsafe command: %r" % m
        return "approve", "no unsafe/direction pattern matched"


def prompt_hash(tail):
    norm = re.sub(r"\s+", " ", tail).strip()
    return hashlib.md5(norm.encode("utf-8", "replace")).hexdigest()[:12]


def snippet(tail, max_lines=8):
    lines = [l for l in tail.splitlines() if l.strip()]
    return "\n".join(lines[-max_lines:])
