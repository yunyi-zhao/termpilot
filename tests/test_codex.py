import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from termpilot import ai_cli, config, reviewer, supervisor


class ConfigTests(unittest.TestCase):
    def test_legacy_migration_preserves_settings_and_claude_model(self):
        legacy = {"token": "keep", "port": 9123, "supervisor": {
            "model": "sonnet", "every": "5m", "autostart": False, "extra_args": "--verbose"},
            "reviewer": {"enabled": True, "command": "claude -p --model opus"}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "CONFIG_PATH", os.path.join(tmp, "config.json")):
            Path(config.CONFIG_PATH).write_text(json.dumps(legacy), encoding="utf-8")
            cfg = config.load()
            self.assertEqual(cfg["token"], "keep")
            self.assertEqual(cfg["port"], 9123)
            self.assertEqual(cfg["supervisor"]["provider"], "codex")
            self.assertEqual(cfg["supervisor"]["model"], "gpt-5.3-codex-spark")
            self.assertEqual(cfg["supervisor"]["claude_model"], "sonnet")
            self.assertEqual(cfg["supervisor"]["claude_extra_args"], "--verbose")
            self.assertEqual(cfg["supervisor"]["every"], "5m")
            self.assertFalse(cfg["supervisor"]["autostart"])
            self.assertTrue(cfg["reviewer"]["enabled"])
            self.assertEqual(cfg["reviewer"]["command"], "")
            self.assertEqual(config.load(), cfg)

    def test_commands_keep_provider_flags_separate(self):
        cfg = copy.deepcopy(config.DEFAULTS["supervisor"])
        cmd = config.supervisor_command(cfg)
        self.assertIn("supervisor-run", cmd)
        decoded = json.loads(base64.urlsafe_b64decode(cmd.split("--settings ")[1]))
        self.assertEqual(decoded, cfg)
        cfg["provider"] = "claude"
        cmd = config.supervisor_command(cfg)
        self.assertIn("--model claude-opus-5", cmd)
        self.assertNotIn("gpt-", cmd)

    def test_interval_validation(self):
        for value, seconds in (("", 0), ("30s", 30), ("1m", 60), ("0.5h", 1800)):
            self.assertEqual(supervisor.interval_seconds(value), seconds)
        for value in ("0", "-1m", "nan", "1m;echo test"):
            with self.assertRaises(ValueError):
                supervisor.interval_seconds(value)


class ModelTests(unittest.TestCase):
    def test_codex_reads_final_file_and_uses_stdin(self):
        def fake(cmd, **kw):
            Path(cmd[cmd.index("--output-last-message") + 1]).write_text("HOLD 审核", encoding="utf-8")
            self.assertEqual(kw["input"], "screen")
            self.assertEqual(cmd[-1], "-")
            self.assertIn("read-only", cmd)
            self.assertNotIn("--permission-mode", cmd)
            return subprocess.CompletedProcess(cmd, 0, "APPROVE noise", "")
        with patch.object(ai_cli, "cli_argv", return_value=["fake"]), patch.object(ai_cli.subprocess, "run", side_effect=fake):
            self.assertEqual(ai_cli.run_model("screen", config.DEFAULTS["supervisor"]), "HOLD 审核")

    def test_reviewer_errors_and_ambiguous_answers_hold(self):
        r = reviewer.LlmReviewer({})
        for value in ("APPROVED something", "APPROVE yes\nHOLD no", "explanation\nAPPROVE", ""):
            with patch.object(reviewer, "run_model", return_value=value):
                self.assertEqual(r.review("screen")[0], "hold")
        with patch.object(reviewer, "run_model", side_effect=subprocess.TimeoutExpired("codex", 90)):
            self.assertEqual(r.review("screen")[0], "hold")
        with patch.object(reviewer, "run_model", return_value="APPROVE build is safe"):
            self.assertEqual(r.review("screen")[0], "approve")

    def test_failed_custom_reviewer_cannot_approve(self):
        r = reviewer.LlmReviewer({"command": "custom"})
        with patch.object(reviewer.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "APPROVE", "error")):
            self.assertEqual(r.review("screen")[0], "hold")


class SupervisorTests(unittest.TestCase):
    def client(self, changed=False, resolved=False):
        prompt = {"id": 7, "session": "worker", "status": "held"}
        self.gets = 0
        self.screens = 0
        def call(method, path, body=None):
            if method == "POST":
                return {}
            if path == "/api/prompts":
                self.gets += 1
                return [] if resolved and self.gets > 1 else [prompt]
            self.screens += 1
            return {"text": "changed" if changed and self.screens > 1 else "same screen"}
        return Mock(call=Mock(side_effect=call))

    def test_only_current_unchanged_prompts_are_acted_on(self):
        for changed, resolved in ((False, False), (True, False), (False, True)):
            client = self.client(changed, resolved)
            with patch.object(supervisor, "run_model", return_value=json.dumps({"decisions": [
                    {"id": 7, "action": "approve", "reason": "safe"}]})):
                supervisor.review_cycle(client, {})
            posts = [c for c in client.call.call_args_list if c.args[0] == "POST"]
            self.assertEqual(len(posts), 0 if changed or resolved else 1)

    def test_invalid_batch_applies_nothing(self):
        good = {"id": 7, "action": "approve", "reason": "safe"}
        for bad in ({"id": 8, "action": "approve", "reason": "unknown"}, good,
                    {"id": 7, "action": "execute", "reason": "invalid"}):
            client = self.client()
            with patch.object(supervisor, "run_model", return_value=json.dumps({"decisions": [good, bad]})):
                with self.assertRaises(ValueError):
                    supervisor.review_cycle(client, {})
            self.assertFalse(any(c.args[0] == "POST" for c in client.call.call_args_list))

    def test_no_pending_prompts_does_not_call_model(self):
        client = Mock(call=Mock(return_value=[]))
        with patch.object(supervisor, "run_model") as model:
            supervisor.review_cycle(client, {})
            model.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "ConPTY is Windows-only")
    def test_quoted_executable_and_arguments_reach_conpty(self):
        from termpilot import pty_session
        cmd = '"C:\\Program Files\\Python\\python.exe" "C:\\Project Name\\termpilot.py" supervisor-run --settings abc'
        with patch.object(pty_session, "which", return_value=r"C:\Program Files\Python\python.exe") as which, patch.object(pty_session, "PTY") as pty:
            pty_session._spawn(cmd, "C:\\", {"PATH": "test"}, 80, 24)
        which.assert_called_once_with(r"C:\Program Files\Python\python.exe", path="test")
        self.assertEqual(pty.return_value.spawn.call_args.kwargs["cmdline"],
                         ' "C:\\Project Name\\termpilot.py" supervisor-run --settings abc')


if __name__ == "__main__":
    unittest.main()
