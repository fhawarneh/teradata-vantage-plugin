"""Tests for scripts/hooks/common.py — tool naming, the relevance gate, emitters, masking."""

from __future__ import annotations

import io
import json

import pytest

import common

PLUGIN_NAME = "mcp__plugin_teradata-vantage_teradata__base_readQuery"
PROJECT_NAME = "mcp__teradata__base_readQuery"


# --------------------------------------------------------------------------- tool naming
@pytest.mark.parametrize("tool_name", [PLUGIN_NAME, PROJECT_NAME, "mcp__x__y__base_readQuery"])
def test_tool_suffix_takes_text_after_the_last_double_underscore(tool_name):
    assert common.tool_suffix(tool_name) == "base_readQuery"


def test_tool_suffix_leaves_builtin_names_alone():
    assert common.tool_suffix("Bash") == "Bash"
    assert common.tool_suffix(None) == ""


@pytest.mark.parametrize("tool_name", [PLUGIN_NAME, PROJECT_NAME])
def test_logical_tool_both_namings(tool_name):
    name, args = common.logical_tool(tool_name, {"sql": "SELECT 1"})
    assert name == "base_readQuery"
    assert args == {"sql": "SELECT 1"}
    assert common.sql_argument(tool_name, {"sql": "SELECT 1"}) == "SELECT 1"


def test_execute_tool_unwraps_nested_tool_name_and_arguments():
    tool_input = {"tool_name": "base_writeQuery", "arguments": {"sql": "DELETE FROM t"}}
    name, args = common.logical_tool("mcp__plugin_teradata-vantage_teradata__execute_tool", tool_input)
    assert name == "base_writeQuery"
    assert args == {"sql": "DELETE FROM t"}
    assert common.sql_argument("mcp__x__execute_tool", tool_input) == "DELETE FROM t"


def test_execute_tool_without_arguments_yields_empty_args():
    name, args = common.logical_tool("mcp__x__execute_tool", {"tool_name": "base_databaseList"})
    assert name == "base_databaseList"
    assert args == {}
    assert common.sql_argument("mcp__x__execute_tool", {"tool_name": "base_databaseList"}) is None


def test_logical_tool_tolerates_garbage_input():
    assert common.logical_tool(None, None) == ("", {})
    assert common.logical_tool("mcp__x__execute_tool", {"tool_name": 42, "arguments": "no"}) == ("", {})


# --------------------------------------------------------------------------- relevance gate
def test_is_set_rejects_empty_and_unexpanded_placeholders():
    assert common.is_set("teradata://x") is True
    assert common.is_set("") is False
    assert common.is_set("   ") is False
    assert common.is_set("${DATABASE_URI}") is False
    assert common.is_set(None) is False


def test_session_gate_is_off_by_default(isolated_env):
    assert common.session_is_teradata() is False


@pytest.mark.parametrize(
    "var", ["DATABASE_URI", "TERADATA_MCP_URL", "CLAUDE_PLUGIN_OPTION_DATABASE_URI", "CLAUDE_PLUGIN_OPTION_MCP_URL"]
)
def test_session_gate_opens_on_each_env_var(monkeypatch, var):
    monkeypatch.setenv(var, "set-by-test")
    assert common.session_is_teradata() is True


def test_session_gate_ignores_literal_placeholder(monkeypatch):
    monkeypatch.setenv("DATABASE_URI", "${DATABASE_URI}")
    assert common.session_is_teradata() is False


def test_session_gate_opens_on_credential_file(isolated_env):
    (isolated_env / "teradata.env").write_text("DATABASE_URI=placeholder\n")
    assert common.session_is_teradata() is True


def test_plugin_data_dir_defaults_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert common.plugin_data_dir() == str(tmp_path / ".claude" / "plugins" / "data" / "teradata-vantage")


def test_env_flag_parsing(monkeypatch):
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("X_FLAG", truthy)
        assert common.env_flag("X_FLAG") is True
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("X_FLAG", falsy)
        assert common.env_flag("X_FLAG", default=True) is (falsy == "")


# --------------------------------------------------------------------------- input + emitters
def test_read_hook_input_parses_json_and_tolerates_garbage():
    assert common.read_hook_input(io.StringIO('{"a": 1}')) == {"a": 1}
    assert common.read_hook_input(io.StringIO("")) == {}
    assert common.read_hook_input(io.StringIO("not json")) == {}
    assert common.read_hook_input(io.StringIO("[1, 2]")) == {}


def test_hook_event_name_falls_back_to_default():
    assert common.hook_event_name({"hook_event_name": "PostToolUseFailure"}, "PostToolUse") == "PostToolUseFailure"
    assert common.hook_event_name({}, "PreToolUse") == "PreToolUse"


def test_emitters_produce_the_hook_output_shape(capsys):
    common.emit_deny("no", "PreToolUse")
    common.emit_ask("sure?", "PreToolUse")
    common.emit_context("fyi", "PostToolUse")
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert lines[0] == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "no",
        }
    }
    assert lines[1]["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert lines[2] == {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "fyi"}}


# --------------------------------------------------------------------------- masking
def test_mask_preserves_length_and_offsets():
    sql = "SELECT a FROM t WHERE note = 'please drop this' -- tail\n/* block */"
    masked = common.mask_sql(sql)
    assert len(masked) == len(sql)
    assert "drop" not in masked
    assert "tail" not in masked
    assert "block" not in masked
    assert masked.startswith("SELECT a FROM t WHERE note = '")


def test_mask_handles_doubled_quotes_and_quoted_identifiers():
    assert "drop" not in common.mask_sql("SELECT 'it''s a drop' AS x")
    assert "drop" not in common.mask_sql('SELECT a AS "drop" FROM t')


@pytest.mark.parametrize("terminator", ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"])
def test_line_comment_ends_at_every_line_terminator(terminator):
    masked = common.mask_sql(f"SELECT 1 --x{terminator}DROP TABLE t")
    assert "DROP TABLE t" in masked


# ---------------------------------------------------------------- plugin data dir fallback
# REGRESSION 2026-09-08. Claude Code exports CLAUDE_PLUGIN_DATA to hook and MCP-server processes
# but NOT to a Bash tool call, so doctor.sh run from the setup skill falls back to this default.
# Under --plugin-dir the real directory is <plugin>-inline; the fallback pointed at <plugin>, so
# the hooks wrote to one directory and doctor.sh read another and reported the opposite state
# ("credentials=none, installed=0" in a session whose SessionStart line said "credentials=file").

def test_plugin_data_dir_prefers_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    assert common.plugin_data_dir() == str(tmp_path)


def test_plugin_data_dir_ignores_an_unexpanded_placeholder(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "${CLAUDE_PLUGIN_DATA}")
    assert common.plugin_data_dir() != "${CLAUDE_PLUGIN_DATA}"


def test_plugin_data_dir_falls_back_to_the_inline_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    base = tmp_path / "data" / "teradata-vantage"
    (tmp_path / "data" / "teradata-vantage-inline").mkdir(parents=True)
    monkeypatch.setattr(common, "PLUGIN_DATA_DEFAULT", str(base))
    assert common.plugin_data_dir() == str(base) + "-inline"


def test_plugin_data_dir_prefers_the_plain_directory_when_it_exists(monkeypatch, tmp_path):
    """An installed plugin already has the plain directory; the inline probe must not hijack it."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    base = tmp_path / "data" / "teradata-vantage"
    base.mkdir(parents=True)
    (tmp_path / "data" / "teradata-vantage-inline").mkdir(parents=True)
    monkeypatch.setattr(common, "PLUGIN_DATA_DEFAULT", str(base))
    assert common.plugin_data_dir() == str(base)
