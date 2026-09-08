"""Tests for scripts/hooks/destructive_tool_gate.py — unconditional ask on state-changing tools.

Also pins the wiring the gate depends on: the PreToolUse matcher in hooks/hooks.json must cover every
tool classified destructive by name in scripts/data/mcp_tools.yaml that the gate (rather than
write_gate.py) is responsible for — including the two core tools that create and drop tables,
sql_Execute_Full_Pipeline and rag_Execute_Workflow.
"""

from __future__ import annotations

import json
import os
import re

import pytest
import yaml

import destructive_tool_gate as gate
from conftest import PLUGIN_ROOT, decision_of, pre_tool_payload, reason_of, run_hook

PREFIX = "mcp__plugin_teradata-vantage_teradata__"

#: Destructive tools handled by write_gate.py (SQL text decides), not by destructive_tool_gate.py.
SQL_TEXT_GATED = {"base_writeQuery", "base_dynamicQuery"}


def _destructive_matcher() -> str:
    with open(os.path.join(PLUGIN_ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
        hooks = json.load(fh)
    for group in hooks["hooks"]["PreToolUse"]:
        args = group["hooks"][0].get("args") or []
        if any(a.endswith("destructive_tool_gate.py") for a in args):
            return str(group["matcher"])
    raise AssertionError("hooks.json has no PreToolUse group running destructive_tool_gate.py")


def _classified_destructive() -> list:
    with open(os.path.join(PLUGIN_ROOT, "scripts", "data", "mcp_tools.yaml"), encoding="utf-8") as fh:
        return list(yaml.safe_load(fh)["destructive_tools"])


@pytest.mark.parametrize(
    "tool,tool_input,expected",
    [
        ("tdvs_destroy", {"vs_name": "docs_vs"}, ["tdvs_destroy", "DESTROYS", "vector store=docs_vs"]),
        ("tdvs_update", {"vs_name": "docs_vs", "vs_update": {"chunk_size": 512}}, ["tdvs_update", "vector store=docs_vs"]),
        (
            "tdvs_grant_user_permission",
            {"vs_name": "docs_vs", "user_name": "analyst1", "permission": "USER"},
            ["tdvs_grant_user_permission", "vector store=docs_vs", "user=analyst1", "permission=USER"],
        ),
        (
            "tdvs_revoke_user_permission",
            {"vs_name": "docs_vs", "user_name": "analyst1", "permission": "ADMIN"},
            ["removes a user's access", "user=analyst1", "permission=ADMIN"],
        ),
        ("bar_manageJob", {"operation": "run", "job_name": "nightly_full"}, ["bar_manageJob", "operation=run", "job=nightly_full"]),
    ],
)
def test_ask_reason_names_the_object(tool, tool_input, expected):
    name, reason = gate.describe(pre_tool_payload(PREFIX + tool, tool_input))
    assert name == tool
    for fragment in expected:
        assert fragment in reason, reason


def test_missing_arguments_still_ask_with_a_generic_target():
    _, reason = gate.describe(pre_tool_payload(PREFIX + "tdvs_destroy", {}))
    assert "target not named in the arguments" in reason


def test_project_level_naming_is_handled():
    name, reason = gate.describe(pre_tool_payload("mcp__teradata__bar_manageJob", {"operation": "delete", "job_name": "j1"}))
    assert name == "bar_manageJob"
    assert "job=j1" in reason


def test_hook_always_emits_ask():
    rc, out, _ = run_hook("destructive_tool_gate.py", pre_tool_payload(PREFIX + "tdvs_destroy", {"vs_name": "docs_vs"}))
    assert rc == 0
    assert decision_of(out) == "ask"
    assert "docs_vs" in reason_of(out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_hook_asks_even_on_garbage_input():
    rc, out, _ = run_hook("destructive_tool_gate.py", None, raw_stdin="not json at all")
    assert rc == 0
    assert decision_of(out) == "ask"


def test_internal_crash_fails_to_ask(monkeypatch, capsys):
    def boom(_payload):
        raise KeyError("synthetic")

    monkeypatch.setattr(gate, "describe", boom)
    monkeypatch.setattr(gate.common, "read_hook_input", lambda stream=None: {})
    assert gate.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "destructive tool gate crashed: KeyError" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["sql_Execute_Full_Pipeline", "rag_Execute_Workflow"])
def test_table_creating_core_tools_are_classified_and_matched(tool):
    """The two bundled-server tools that DROP and CREATE tables must prompt before they run."""
    assert tool in _classified_destructive()
    assert re.match(_destructive_matcher(), PREFIX + tool), tool


@pytest.mark.parametrize("tool", ["sql_Execute_Full_Pipeline", "rag_Execute_Workflow"])
def test_table_creating_core_tools_ask(tool):
    rc, out, _ = run_hook("destructive_tool_gate.py", pre_tool_payload(PREFIX + tool, {}))
    assert rc == 0
    assert decision_of(out) == "ask"
    assert tool in reason_of(out)


def test_matcher_covers_every_name_classified_destructive():
    """No tool may be classified destructive in mcp_tools.yaml and left ungated."""
    matcher = _destructive_matcher()
    ungated = [
        t
        for t in _classified_destructive()
        if t not in SQL_TEXT_GATED and not re.match(matcher, PREFIX + t)
    ]
    assert ungated == [], f"classified destructive but no PreToolUse gate: {ungated}"


def test_read_only_sibling_tools_are_not_gated():
    """The read-side clustering tools must not be dragged into the prompt."""
    matcher = _destructive_matcher()
    for tool in ("sql_Analyze_Cluster_Stats", "sql_Retrieve_Cluster_Queries", "base_readQuery"):
        assert not re.match(matcher, PREFIX + tool), tool


# ---------------------------------------------------------------- file-writing and pipeline tools
# base_saveDDL is NAMED and ANNOTATED like a read tool but writes a .sql file to a caller-supplied
# output_dir on the machine running the MCP server (vendored 0.2.6, base_tools.py:handle_base_saveDDL).
# It is therefore in destructive_tools, excluded from the read-only profiles, and prompts here.

def test_save_ddl_is_classified_destructive():
    with open(os.path.join(PLUGIN_ROOT, "scripts", "data", "mcp_tools.yaml"), encoding="utf-8") as fh:
        inventory = yaml.safe_load(fh)
    assert "base_saveDDL" in inventory["destructive_tools"]


@pytest.mark.parametrize(
    "tool,expect",
    [
        ("base_saveDDL", "WRITES a .sql file"),
        ("sql_Execute_Full_Pipeline", "DROPS and re-creates"),
        ("rag_Execute_Workflow", "DROPS and re-creates"),
    ],
)
def test_pipeline_and_file_tools_have_a_specific_effect_sentence(tool, expect):
    payload = pre_tool_payload(tool, {})
    name, reason = gate.describe(payload)
    assert name == tool
    assert expect in reason, reason
    assert "changes or destroys state" not in reason, "generic fallback means the tool is undescribed"


def test_save_ddl_reason_names_the_output_directory():
    payload = pre_tool_payload(
        "base_saveDDL",
        {"database_name": "analytics", "table_name": "sales_fact", "output_dir": "/tmp/ddl"},
    )
    _, reason = gate.describe(payload)
    assert "/tmp/ddl" in reason and "analytics" in reason and "sales_fact" in reason


@pytest.mark.parametrize("tool", ["base_saveDDL", "sql_Execute_Full_Pipeline", "rag_Execute_Workflow"])
def test_these_tools_ask(tool):
    _rc, out, _err = run_hook("destructive_tool_gate.py", pre_tool_payload(tool, {}))
    assert decision_of(out) == "ask"
