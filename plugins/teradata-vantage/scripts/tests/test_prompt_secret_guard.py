"""Tests for scripts/hooks/prompt_secret_guard.py — opt-in warning on pasted connection strings."""

from __future__ import annotations

import pytest

import prompt_secret_guard as guard
from conftest import context_of, run_hook

# Synthetic strings only: the placeholder scheme/user/password/host are not real.
SECRET_PROMPT = "please connect with teradata://svc_user:pl4ceh0lder@db.example.internal:1025/sales_db and list tables"
CLEAN_PROMPT = "connect to db.example.internal as svc_user and list the tables in sales_db"


def prompt_payload(prompt: str) -> dict:
    return {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": "/tmp"}


@pytest.mark.parametrize(
    "text",
    [
        SECRET_PROMPT,
        "postgresql://u:p@host/db",
        "x teradata+td2://alice:secret@host",
        "wrapped (teradata://a:b@h:1025)",
    ],
)
def test_uri_with_userinfo_password_is_detected(text):
    assert guard.contains_secret_uri(text) is True


@pytest.mark.parametrize(
    "text",
    [
        CLEAN_PROMPT,
        "https://example.com/path",
        "teradata://svc_user@host:1025/db",  # user but no password
        "http://host:8001/mcp/",
        "the password is hunter2",  # deliberately NOT matched: no broad PASSWORD= regex
        "",
        None,
    ],
)
def test_other_text_is_not_detected(text):
    assert guard.contains_secret_uri(text) is False


def test_off_by_default_even_with_a_teradata_session():
    rc, out, err = run_hook("prompt_secret_guard.py", prompt_payload(SECRET_PROMPT), {"DATABASE_URI": "set-by-test"})
    assert rc == 0 and out is None and err == ""


def test_off_when_enabled_but_no_teradata_session():
    env = {"CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD": "true"}
    rc, out, err = run_hook("prompt_secret_guard.py", prompt_payload(SECRET_PROMPT), env)
    assert rc == 0 and out is None and err == ""


@pytest.mark.parametrize("flag", ["1", "true", "yes", "TRUE"])
def test_warns_when_enabled_in_a_teradata_session(flag):
    env = {"CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD": flag, "DATABASE_URI": "set-by-test"}
    rc, out, err = run_hook("prompt_secret_guard.py", prompt_payload(SECRET_PROMPT), env)
    assert rc == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    ctx = context_of(out)
    assert ctx == guard.WARNING
    # never echo the match
    assert "pl4ceh0lder" not in ctx and "svc_user" not in ctx and "example.internal" not in ctx
    assert err == ""


def test_clean_prompt_is_silent_when_enabled():
    env = {"CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD": "1", "DATABASE_URI": "set-by-test"}
    rc, out, _ = run_hook("prompt_secret_guard.py", prompt_payload(CLEAN_PROMPT), env)
    assert rc == 0 and out is None


def test_block_mode_exits_2_with_a_stderr_line_and_no_echo():
    env = {
        "CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD": "1",
        "DATABASE_URI": "set-by-test",
        "TERADATA_PROMPT_SECRET_GUARD": "block",
    }
    rc, out, err = run_hook("prompt_secret_guard.py", prompt_payload(SECRET_PROMPT), env)
    assert rc == 2
    assert out is None
    assert "prompt blocked" in err
    assert "pl4ceh0lder" not in err


def test_action_defaults_to_warn(monkeypatch):
    monkeypatch.delenv("TERADATA_PROMPT_SECRET_GUARD", raising=False)
    assert guard.action() == "warn"
    monkeypatch.setenv("TERADATA_PROMPT_SECRET_GUARD", "BLOCK")
    assert guard.action() == "block"
    monkeypatch.setenv("TERADATA_PROMPT_SECRET_GUARD", "something-else")
    assert guard.action() == "warn"


def test_internal_crash_allows(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ENABLE_PROMPT_SECRET_GUARD", "1")
    monkeypatch.setenv("DATABASE_URI", "set-by-test")

    def boom(stream=None):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(guard.common, "read_hook_input", boom)
    assert guard.main() == 0
    assert capsys.readouterr().out == ""
