"""Unsafe agent commands: reject once, ask why, judge the re-request, then a human."""
import copy
import io
from contextlib import redirect_stdout
from pathlib import Path
import re
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from termpilot import config, rules, supervisor
from termpilot.manager import EXPLAIN_MESSAGE, Manager

CLAUDE_DIALOG = """● I'll clear the old build output first.

 Bash command
   rm -rf build
   Remove the build directory

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands
   3. No, and tell Claude what to do differently (esc)

 Esc to cancel"""

INTERRUPTED = """● I'll clear the old build output first.
  ⎿  Interrupted · What should Claude do instead?

>"""

EXPLANATION = """> [termpilot] your last command was blocked as potentially unsafe
● build/ only holds compiler output from the previous run; stale objects break the link step.
  rm -rf build deletes nothing else, and the next build recreates it."""

SHELL_CONFIRM = """PS C:\\work> Remove-Item C:\\data -Recurse
Confirm
Are you sure you want to delete C:\\data? [y/N]"""


class FakeSession:
    kind = "pty"

    def __init__(self, name="agent"):
        self.name = name
        self.mode = "auto"
        self.current_prompt = None
        self.screen = ""
        self.keys = []
        self.typed = []

    def screen_text(self):
        return self.screen

    def send_keys(self, keys):
        self.keys.extend(keys)

    def write(self, data):
        self.typed.append(data)

    def alive(self):
        return True

    def info(self):
        return {"name": self.name, "kind": self.kind, "mode": self.mode, "created_at": 0,
                "prompt": self.current_prompt.info() if self.current_prompt else None}


def wait_for(cond, timeout=6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ExplainFlowTests(unittest.TestCase):
    def setUp(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg.update(confirm_cycles=1, action_cooldown=0)
        self.cfg = cfg
        self.mgr = Manager(cfg)
        self.s = FakeSession()
        self.mgr.sessions[self.s.name] = self.s

    def ask_and_answer(self):
        """First unsafe request -> Esc -> question typed -> agent explains."""
        s, mgr = self.s, self.mgr
        s.screen = CLAUDE_DIALOG
        mgr._check(s)
        first = s.current_prompt
        self.assertEqual(first.status, "explaining")
        self.assertTrue(wait_for(lambda: s.keys == ["esc"]))
        s.screen = INTERRUPTED
        mgr._check(s)
        self.assertEqual(first.status, "resolved")
        self.assertTrue(wait_for(lambda: s.explain and s.explain["asked_at"]))
        self.assertEqual(s.keys, ["esc", "enter"])
        self.assertEqual(s.typed, [EXPLAIN_MESSAGE])
        s.screen = EXPLANATION
        mgr._check(s)
        return first

    def rerequest(self, verdict):
        s, mgr = self.s, self.mgr
        s.screen = EXPLANATION + "\n\n" + CLAUDE_DIALOG.split("\n\n", 1)[1]
        with patch.object(mgr.reviewer, "judge_explanation", return_value=verdict) as judge:
            mgr._check(s)
            second = s.current_prompt
            self.assertTrue(wait_for(lambda: second.status != "judging"))
        reason, original, answer, pending = judge.call_args.args
        self.assertEqual(reason, "unsafe command: 'rm -rf'")
        self.assertIn("rm -rf build", original)
        self.assertIn("stale objects break the link step", answer)
        self.assertIn("Do you want to proceed?", pending)
        self.assertIsNone(s.explain)
        return second

    def test_convincing_explanation_is_approved(self):
        self.ask_and_answer()
        second = self.rerequest(("approve", "llm: only removes build output"))
        self.assertEqual(second.status, "approved")
        self.assertEqual(second.decided_by, "explain")
        self.assertFalse(second.escalated)
        self.assertEqual(self.s.keys, ["esc", "enter", "enter"])

    def test_still_unsafe_goes_to_a_human_only(self):
        self.ask_and_answer()
        second = self.rerequest(("hold", "llm: explanation does not cover the target"))
        self.assertEqual(second.status, "held")
        self.assertTrue(second.escalated)
        self.assertIn("needs a human", second.reason)
        self.assertEqual(self.s.keys, ["esc", "enter"])   # no Enter on the second dialog
        self.assertIn(second.info(), self.mgr.snapshot()["pending"])
        # The scheduled approver must not pick it up.
        client = Mock()
        client.call.return_value = [second.info()]
        out = io.StringIO()
        with patch.object(supervisor, "run_model") as run_model, redirect_stdout(out):
            supervisor.review_cycle(client, {"name": "approver"})
        run_model.assert_not_called()
        self.assertIn("NO_PROMPT", out.getvalue())

    def test_shell_confirmations_are_never_typed_into(self):
        self.s.screen = SHELL_CONFIRM
        self.mgr._check(self.s)
        self.assertEqual(self.s.current_prompt.status, "held")
        self.assertEqual(self.s.current_prompt.decided_by, "rules")
        time.sleep(0.3)
        self.assertEqual((self.s.keys, self.s.typed), ([], []))

    def test_disabled_keeps_the_old_hold(self):
        self.cfg["explain"] = {"enabled": False}
        self.s.screen = CLAUDE_DIALOG
        self.mgr._check(self.s)
        self.assertEqual(self.s.current_prompt.status, "held")
        self.assertEqual(self.s.current_prompt.reason, "unsafe command: 'rm -rf'")
        self.assertEqual(self.s.keys, [])

    def test_direction_choices_and_manual_mode_are_unchanged(self):
        self.s.screen = CLAUDE_DIALOG.replace("Do you want to proceed?", "Which approach should I take? Do you want to proceed?")
        self.mgr._check(self.s)
        self.assertEqual(self.s.current_prompt.reason, "direction choice: 'Which approach'")
        other = FakeSession("manual-agent")
        other.mode = "manual"
        other.screen = CLAUDE_DIALOG
        self.mgr._check(other)
        self.assertEqual(other.current_prompt.decided_by, "manual")
        time.sleep(0.3)
        self.assertEqual(self.s.keys + other.keys, [])

    def test_agent_that_quits_on_esc_gets_no_typed_text(self):
        s = self.s
        s.screen = CLAUDE_DIALOG
        self.mgr._check(s)
        self.assertTrue(wait_for(lambda: s.keys == ["esc"]))
        s.screen = "[fake-agent] CANCELLED by Esc\n(base) PS C:\\work> "
        self.mgr._check(s)
        self.assertTrue(wait_for(lambda: s.explain is None))
        self.assertEqual((s.keys, s.typed), (["esc"], []))
        self.assertIn("exited to a shell", self.mgr.events[-1]["msg"])

    def test_agent_that_reasks_at_once_is_not_pressed_in_a_loop(self):
        s, mgr = self.s, self.mgr
        s.screen = CLAUDE_DIALOG
        mgr._check(s)
        self.assertTrue(wait_for(lambda: s.keys == ["esc"]))
        s.screen = CLAUDE_DIALOG.replace("first", "first (retrying)")   # re-asked, no question taken
        mgr._check(s)
        second = s.current_prompt
        self.assertEqual((second.status, second.decided_by), ("held", "rules"))
        self.assertTrue(wait_for(lambda: s.explain is None))
        s.screen = CLAUDE_DIALOG.replace("first", "first (third try)")
        mgr._check(s)
        self.assertEqual((s.current_prompt.status, s.current_prompt.decided_by), ("held", "rules"))
        time.sleep(0.3)
        self.assertEqual((s.keys, s.typed), (["esc"], []))

    def test_human_answer_during_rejection_cancels_the_question(self):
        s = self.s
        s.screen = CLAUDE_DIALOG
        self.mgr._check(s)
        s.current_prompt.status = "approved"     # a human clicked approve right away
        self.assertTrue(wait_for(lambda: s.explain is None))
        self.assertEqual(s.typed, [])

    def test_no_rerequest_within_window_expires(self):
        self.ask_and_answer()
        self.s.explain["asked_at"] -= self.mgr.explain_cfg()["window"] + 1
        self.mgr._check(self.s)
        self.assertIsNone(self.s.explain)
        # A later unsafe request starts a fresh round instead of being judged.
        self.s.screen = CLAUDE_DIALOG
        self.mgr._check(self.s)
        self.assertEqual(self.s.current_prompt.status, "explaining")


class ExplainRuleTests(unittest.TestCase):
    def test_agent_dialogs_are_recognised(self):
        r = rules.Rules()
        codex = "Would you like to run the following command?\n$ rm -rf build\n› 1. Yes, proceed (y)\n" \
                "  3. No, and tell Codex what to do differently (esc)"
        for text in (CLAUDE_DIALOG, codex, "Allow execution of 'rm'?\n 3. No, suggest changes (esc)",
                     "Run rm -rf out?\nPress enter to confirm or esc to cancel"):
            self.assertTrue(r.is_agent_dialog(text), text)
        self.assertFalse(r.is_agent_dialog(SHELL_CONFIRM))

    def test_classify_output_is_unchanged(self):
        r = rules.Rules()
        self.assertEqual(r.classify("rm -rf build"), ("hold", "unsafe command: 'rm -rf'"))
        self.assertEqual(r.classify("npm test"), ("approve", "no unsafe/direction pattern matched"))

    def test_question_avoids_shifted_keys_and_trigger_words(self):
        self.assertRegex(EXPLAIN_MESSAGE, r"^[a-z0-9 \[\]'.,/=-]+$")
        # It stays on screen, so it must not make the next dialog look unsafe.
        self.assertEqual(rules.Rules().classify(EXPLAIN_MESSAGE), ("approve", "no unsafe/direction pattern matched"))


if __name__ == "__main__":
    unittest.main()
