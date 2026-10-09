"""Non-interactive model calls using the CLI's existing login."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

from .config import DEFAULTS, provider_info


def cli_argv(provider):
    info = provider_info(provider)
    executable = info["executable"]
    resolved = shutil.which(executable)
    if not resolved and provider == "antigravity" and os.name == "nt":
        native = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
        if native.is_file():
            resolved = str(native)
    if not resolved:
        login = "agy" if provider == "antigravity" else executable + " login"
        raise RuntimeError("%s CLI not found; install %s and sign in with %s" % (info["label"], executable, login))
    path = Path(resolved)
    if os.name == "nt" and path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        if provider == "antigravity":
            native = path.with_suffix(".exe")
            if native.is_file():
                return [str(native)]
            raise RuntimeError("Install the native Antigravity CLI (agy.exe); the antigravity IDE launcher is not supported")
        entry = "@openai/codex/bin/codex.js" if provider == "codex" else "@anthropic-ai/claude-code/cli.js"
        script = path.parent / "node_modules" / entry
        if provider == "codex":
            # Prefer the native npm payload so timeouts stop the model process itself.
            import platform
            arch, triple = ("arm64", "aarch64") if platform.machine().lower() in ("arm64", "aarch64") else ("x64", "x86_64")
            package = script.parent.parent
            roots = [package / "node_modules" / "@openai" / ("codex-win32-" + arch) / "vendor",
                     path.parent / "node_modules" / "@openai" / ("codex-win32-" + arch) / "vendor",
                     package / "vendor"]
            for root in roots:
                for folder in ("bin", "codex"):
                    native = root / (triple + "-pc-windows-msvc") / folder / "codex.exe"
                    if native.is_file():
                        return [str(native)]
        node = shutil.which("node")
        if not node or not script.is_file():
            raise RuntimeError("Cannot resolve %s npm shim; install the native CLI" % provider)
        return [node, str(script)]
    return [resolved]


def antigravity_result(stdout):
    """Read one completed turn, ignoring progress/tool events in the NDJSON stream."""
    results = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict) and event.get("event") == "result":
            results.append(event.get("result"))
    if len(results) != 1 or not isinstance(results[0], dict):
        raise RuntimeError("Antigravity must return exactly one result")
    result = results[0]
    if result.get("status") != "SUCCESS":
        raise RuntimeError("Antigravity failed: " + str(result.get("error") or result.get("status")))
    if result.get("structured_output") is not None:
        return json.dumps(result["structured_output"], ensure_ascii=False)
    response = result.get("response")
    if not isinstance(response, str):
        raise RuntimeError("Antigravity returned no text response")
    return response


def run_model(prompt, cfg, schema=None):
    provider = cfg.get("provider", "codex")
    info = provider_info(provider)
    cmd = cli_argv(provider)
    env = {k: v for k, v in os.environ.items()
           if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}
    with tempfile.TemporaryDirectory(prefix="termpilot-ai-") as workdir:
        output = Path(workdir) / "answer.txt"
        model_input = prompt
        if provider == "codex":
            cmd += ["exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                    "--color", "never", "-c", 'model_provider="openai"',
                    "--output-last-message", str(output)]
            if cfg.get("model"):
                cmd += ["--model", cfg["model"]]
            if cfg.get("effort"):
                cmd += ["-c", "model_reasoning_effort=" + json.dumps(cfg["effort"])]
            if schema:
                schema_path = Path(workdir) / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                cmd += ["--output-schema", str(schema_path)]
            if cfg.get("extra_args"):
                cmd += shlex.split(cfg["extra_args"], posix=True)
            cmd.append("-")
        elif provider == "claude":
            cmd += ["-p", "--model", cfg.get("claude_model") or DEFAULTS["reviewer"]["claude_model"]]
            if cfg.get("claude_effort"):
                cmd += ["--effort", cfg["claude_effort"]]
            if cfg.get("claude_extra_args"):
                cmd += shlex.split(cfg["claude_extra_args"], posix=True)
        else:
            cmd += ["--input-format", "stream-json", "--output-format", "stream-json",
                    "--mode", "plan", "--disable-slash-commands",
                    "--print-timeout", "%ss" % int(cfg.get("timeout", 180)),
                    "--model", cfg.get(info["model_key"]) or DEFAULTS["reviewer"][info["model_key"]]]
            if cfg.get(info["effort_key"]):
                cmd += ["--effort", cfg[info["effort_key"]]]
            if schema:
                schema_path = Path(workdir) / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                cmd += ["--json-schema", str(schema_path)]
            if cfg.get(info["extra_key"]):
                cmd += shlex.split(cfg[info["extra_key"]], posix=True)
            model_input = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"
        proc = subprocess.run(cmd, input=model_input, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, cwd=workdir,
                              timeout=int(cfg.get("timeout", 180)))
        if proc.returncode:
            raise RuntimeError("%s CLI exited %s: %s" % (provider, proc.returncode, proc.stderr[:500]))
        result = output.read_text(encoding="utf-8") if provider == "codex" and output.is_file() else ""
        if provider == "claude":
            result = proc.stdout
        elif provider == "antigravity":
            result = antigravity_result(proc.stdout)
        if not result.strip():
            raise RuntimeError("empty answer from %s CLI" % provider)
        return result.strip()
