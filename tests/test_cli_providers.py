"""Provider switching, command routing and Antigravity output contract tests."""
import base64
import asyncio
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


def agy_output(response="HOLD needs review", status="SUCCESS", **extra):
    return json.dumps({"event": "init"}) + "\n" + json.dumps({
        "event": "result", "result": dict(status=status, response=response, **extra)}) + "\n"


class ProviderTests(unittest.TestCase):
    def test_switching_and_reloading_keeps_all_provider_settings(self):
        from termpilot.manager import Manager
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["token"] = "test-token"
        cfg["supervisor"].update(model="custom-codex", effort="xhigh", claude_model="custom-claude",
                                 claude_effort="medium", claude_extra_args='--agent "Review Bot"',
                                 antigravity_model="gemini-3.8-flash-low", antigravity_effort="low")
        mgr = object.__new__(Manager)
        mgr.cfg, mgr.emit = cfg, Mock()
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "CONFIG_PATH", str(Path(tmp) / "config.json")):
            config.save(cfg)
            for provider in ("antigravity", "claude", "codex"):
                before = copy.deepcopy(mgr.cfg["supervisor"])
                saved = mgr.save_supervisor_cfg({"provider": provider})
                before["provider"] = provider
                self.assertEqual(saved, before)
                self.assertEqual(config.load()["supervisor"], before)
                self.assertEqual(config.load()["token"], "test-token")

    def test_antigravity_scheduler_is_not_a_claude_loop(self):
        from termpilot.manager import Manager
        mgr = object.__new__(Manager)
        mgr.cfg = {"supervisor": dict(config.DEFAULTS["supervisor"], provider="antigravity")}
        mgr.sessions, mgr.emit, mgr.create = {}, Mock(), Mock()
        with patch("termpilot.install.ensure_command_file") as install, patch("termpilot.manager.threading.Thread") as thread:
            mgr.start_supervisor()
        cmd = mgr.create.call_args.args[1]
        settings = json.loads(base64.urlsafe_b64decode(cmd.split("--settings ")[1]))
        self.assertEqual(settings["provider"], "antigravity")
        self.assertEqual(mgr.create.call_args.kwargs["mode"], "manual")
        install.assert_not_called()
        thread.assert_not_called()

    def test_interactive_commands_use_independent_models_and_efforts(self):
        sc = dict(config.DEFAULTS["supervisor"], effort="xhigh", claude_effort="medium", antigravity_effort="low")
        for provider, model, effort in (("codex", "gpt-5.3-codex-spark", "xhigh"),
                                         ("claude", "claude-opus-5", "medium"),
                                         ("antigravity", "gemini-3.8-flash-high", "low")):
            with self.subTest(provider=provider):
                cmd = config.session_command(sc, provider)
                self.assertTrue(cmd.startswith(config.PROVIDERS[provider]["executable"] + " "))
                self.assertIn(model, cmd)
                self.assertIn(effort, cmd)
                self.assertEqual("--permission-mode" in cmd, provider == "claude")
                override = config.session_command(sc, provider, "custom-model")
                self.assertIn("--model custom-model", override)
                self.assertEqual(sc[config.PROVIDERS[provider]["model_key"]], model)

    def test_antigravity_uses_stdin_schema_and_own_flags(self):
        prompt = 'Review this screen:\n"Unicode 审核"' * 1000
        sc = dict(config.DEFAULTS["supervisor"], provider="antigravity", effort="xhigh",
                  antigravity_effort="medium", antigravity_extra_args='--agent "Review Bot"')
        def fake(cmd, **kw):
            self.assertNotIn(prompt, cmd)
            self.assertEqual(json.loads(kw["input"])["message"]["content"], prompt)
            self.assertEqual(cmd[cmd.index("--model") + 1], "gemini-3.8-flash-high")
            self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
            self.assertEqual(cmd[cmd.index("--agent") + 1], "Review Bot")
            self.assertEqual(json.loads(Path(cmd[cmd.index("--json-schema") + 1]).read_text()), supervisor.SCHEMA)
            self.assertNotIn("--permission-mode", cmd)
            self.assertNotIn("xhigh", cmd)
            return subprocess.CompletedProcess(cmd, 0, agy_output(structured_output={"decisions": []}), "")
        with patch.object(ai_cli, "cli_argv", return_value=["agy"]), patch.object(ai_cli.subprocess, "run", side_effect=fake):
            self.assertEqual(json.loads(ai_cli.run_model(prompt, sc, schema=supervisor.SCHEMA)), {"decisions": []})

    def test_claude_review_uses_only_claude_options(self):
        sc = dict(config.DEFAULTS["supervisor"], provider="claude", effort="xhigh", claude_effort="medium",
                  extra_args="--codex-only", antigravity_extra_args="--agy-only", claude_extra_args="--verbose")
        with patch.object(ai_cli, "cli_argv", return_value=["claude"]), patch.object(ai_cli.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, "HOLD review", "")) as run:
            self.assertEqual(ai_cli.run_model("screen", sc), "HOLD review")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        self.assertIn("--verbose", cmd)
        for unexpected in ("xhigh", "--codex-only", "--agy-only"):
            self.assertNotIn(unexpected, cmd)

    def test_failed_or_ambiguous_antigravity_results_hold(self):
        cfg = dict(config.DEFAULTS["reviewer"], provider="antigravity")
        llm = reviewer.LlmReviewer(cfg)
        for out in (agy_output("APPROVE", "ERROR"), agy_output("APPROVE", "WAITING"),
                    agy_output("APPROVE") * 2, '{"event":"init"}', "not json", agy_output("")):
            with self.subTest(output=out), patch.object(ai_cli, "cli_argv", return_value=["agy"]), \
                    patch.object(ai_cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, out, "")):
                self.assertEqual(llm.review("screen")[0], "hold")

    def test_antigravity_resolves_agy_not_ide_launcher(self):
        with patch.object(ai_cli.shutil, "which", return_value="C:/tools/agy.exe") as which:
            self.assertEqual(ai_cli.cli_argv("antigravity"), ["C:/tools/agy.exe"])
        which.assert_called_once_with("agy")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_policy = asyncio.get_event_loop_policy()
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop_policy(cls.original_policy)

    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from termpilot.server import build_app
        self.mgr = Mock()
        self.mgr.sessions = {}
        self.mgr.supervisor_cfg.return_value = copy.deepcopy(config.DEFAULTS["supervisor"])
        self.mgr.create.return_value.info.return_value = {"name": "worker"}
        self.mgr.start_supervisor.return_value.info.return_value = {"name": "approver"}
        self.client = TestClient(TestServer(build_app({"token": "test-token"}, self.mgr)))
        await self.client.start_server()
        self.headers = {"X-Token": "test-token"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_catalog_does_not_call_cli_or_network(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("no model discovery")):
            response = await self.client.get("/api/config/supervisor", headers=self.headers)
            data = await response.json()
        self.assertEqual(set(data["providers"]), {"codex", "claude", "antigravity"})
        self.assertEqual(data["catalog_date"], "2026-09-10")

    async def test_create_all_three_clis_with_per_session_model(self):
        for provider in config.PROVIDERS:
            response = await self.client.post("/api/sessions", headers=self.headers,
                json={"name": provider, "provider": provider, "model": "custom-model", "cwd": "C:/project"})
            self.assertEqual(response.status, 200)
            self.assertIn("--model custom-model", self.mgr.create.call_args.args[1])
            self.assertEqual(self.mgr.create.call_args.args[2], "C:/project")

    async def test_reject_conflicting_or_unknown_provider(self):
        for body in ({"provider": "unknown"}, {"provider": "codex", "cmd": "pwsh"}):
            response = await self.client.post("/api/sessions", headers=self.headers, json=dict(name="worker", **body))
            self.assertEqual(response.status, 400)
        self.mgr.create.assert_not_called()

    async def test_restart_forwards_antigravity_options(self):
        body = {"provider": "antigravity", "antigravity_model": "gemini-3.1-pro-high", "antigravity_effort": "low"}
        response = await self.client.post("/api/supervisor", headers=self.headers, json=body)
        self.assertEqual(response.status, 200)
        self.mgr.start_supervisor.assert_called_once_with(restart=True, **body)


if __name__ == "__main__":
    unittest.main()
