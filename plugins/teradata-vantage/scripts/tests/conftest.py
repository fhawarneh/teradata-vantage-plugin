"""Shared fixtures for the teradata-vantage hook tests.

Pure unit tests: no network, no Teradata, no MCP server. Each test module imports the hook it
covers straight from ``scripts/hooks`` (added to ``sys.path`` here) and, where the exit code or
the exact stdout JSON matters, runs the script as a subprocess with the current interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, Optional, Tuple

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HOOKS_DIR = os.path.join(os.path.dirname(TESTS_DIR), "hooks")
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))

if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

#: Every environment variable a hook reads. Cleared before each test so the host's own settings
#: (a real DATABASE_URI, a credential file under HOME) never leak into the assertions.
HOOK_ENV_VARS = (
    "DATABASE_URI",
    "TERADATA_MCP_URL",
    "CLAUDE_PLUGIN_OPTION_DATABASE_URI",
    "CLAUDE_PLUGIN_OPTION_MCP_URL",
    "CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD",
    "CLAUDE_PLUGIN_DATA",
    "CLAUDE_PLUGIN_ROOT",
    "TERADATA_SQL_GUARD_MODE",
    "TERADATA_ALLOW_WRITES",
    "TERADATA_MAX_RESULT_CHARS",
    "TERADATA_ERROR_CODES_FILE",
    "TERADATA_PROMPT_SECRET_GUARD",
    "TERADATA_SQL_LINT",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """No Teradata session, no credential file, empty plugin-data dir under tmp."""
    for key in HOOK_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return data_dir


@pytest.fixture
def fixtures_dir() -> str:
    return FIXTURES_DIR


@pytest.fixture
def hooks_dir() -> str:
    return HOOKS_DIR


def run_hook(
    script: str,
    payload: Optional[dict],
    env_overrides: Optional[Dict[str, str]] = None,
    raw_stdin: Optional[str] = None,
) -> Tuple[int, Optional[dict], str]:
    """Run ``scripts/hooks/<script>`` with ``payload`` on stdin; return (exit code, stdout JSON, stderr)."""
    env = dict(os.environ)
    env.update(env_overrides or {})
    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(payload or {})
    if script.endswith(".sh"):
        cmd = ["bash", os.path.join(HOOKS_DIR, script)]
    else:
        cmd = [sys.executable, os.path.join(HOOKS_DIR, script)]
    proc = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, env=env, timeout=30)
    out = proc.stdout.strip()
    parsed = json.loads(out) if out else None
    return proc.returncode, parsed, proc.stderr


@pytest.fixture
def hook_runner():
    return run_hook


def pre_tool_payload(tool_name: str, tool_input: dict, event: str = "PreToolUse") -> dict:
    return {
        "session_id": "test-session",
        "hook_event_name": event,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def decision_of(output: Optional[dict]) -> Optional[str]:
    if not output:
        return None
    return output.get("hookSpecificOutput", {}).get("permissionDecision")


def reason_of(output: Optional[dict]) -> str:
    if not output:
        return ""
    return output.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def context_of(output: Optional[dict]) -> str:
    if not output:
        return ""
    return output.get("hookSpecificOutput", {}).get("additionalContext", "")
