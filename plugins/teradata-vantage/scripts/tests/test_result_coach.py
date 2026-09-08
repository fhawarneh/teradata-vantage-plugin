"""Tests for scripts/hooks/result_coach.py — result-size and error-code coaching."""

from __future__ import annotations

import json
import os

import pytest

import result_coach as coach
from conftest import FIXTURES_DIR, PLUGIN_ROOT, context_of, run_hook

SAMPLE_YAML = os.path.join(FIXTURES_DIR, "error_codes_sample.yaml")
READ_TOOL = "mcp__plugin_teradata-vantage_teradata__base_readQuery"


def post_payload(tool_response=None, error=None, event="PostToolUse"):
    payload = {"hook_event_name": event, "tool_name": READ_TOOL, "tool_input": {"sql": "SELECT 1"}}
    if tool_response is not None:
        payload["tool_response"] = tool_response
    if error is not None:
        payload["error"] = error
    return payload


# --------------------------------------------------------------------------- yaml reader
def test_tiny_yaml_reader_parses_the_sample():
    codes = coach.load_error_codes(SAMPLE_YAML)
    assert set(codes) == {"3541", "3706", "5628"}
    e = codes["3541"]
    assert e["area"] == "space"
    assert e["message"] == "The request to assign new PERMANENT space is invalid"
    assert e["meaning"].startswith("The PARENT database has no unallocated permanent space")
    assert "\n" not in e["meaning"]  # folded scalar
    assert e["fix"].startswith("Check the parent with SELECT PermSpace")
    assert "\nthen MODIFY DATABASE" in e["fix"]  # literal scalar keeps its line break
    assert e["false_leads"] == ["system-wide free space in DBC.DiskSpaceV (irrelevant: the parent is what matters)"]
    assert e["related"] == ["3524", "2644"]
    assert e["skill"] == "health"
    assert codes["3706"]["meaning"] == "The statement is not valid Teradata SQL at the position reported."
    assert codes["5628"]["fix"] == "Read the DDL first with base_tableDDL, then re-issue."


def test_yaml_reader_accepts_a_top_level_list_and_a_code_keyed_map(tmp_path):
    as_list = tmp_path / "list.yaml"
    as_list.write_text("- code: 2644\n  meaning: no more room in the database\n  fix: purge log tables\n- code: 3524\n  meaning: no privilege\n")
    codes = coach.load_error_codes(str(as_list))
    assert codes["2644"]["fix"] == "purge log tables"
    assert codes["3524"]["meaning"] == "no privilege"

    as_map = tmp_path / "map.yaml"
    as_map.write_text('3803:\n  meaning: "Table already exists"\n  fix: use CREATE TABLE only after checking DBC.TablesV\n')
    codes = coach.load_error_codes(str(as_map))
    assert codes["3803"]["meaning"] == "Table already exists"


def test_yaml_reader_returns_empty_for_missing_or_garbage_files(tmp_path):
    assert coach.load_error_codes(str(tmp_path / "missing.yaml")) == {}
    garbage = tmp_path / "g.yaml"
    garbage.write_text("::: not yaml at all\n\t- - -\n")
    assert coach.load_error_codes(str(garbage)) == {}


# --------------------------------------------------------------------------- coaching logic
def test_no_note_for_a_small_clean_result(monkeypatch):
    monkeypatch.setenv("TERADATA_ERROR_CODES_FILE", SAMPLE_YAML)
    assert coach.coach(post_payload(tool_response={"status": "success", "results": [{"a": 1}]})) is None
    assert coach.coach({}) is None


def test_truncation_note_text_is_exact(monkeypatch):
    monkeypatch.setenv("TERADATA_MAX_RESULT_CHARS", "100")
    monkeypatch.setenv("TERADATA_ERROR_CODES_FILE", SAMPLE_YAML)
    note = coach.coach(post_payload(tool_response="x" * 250))
    assert note == (
        "[RESULT TRUNCATED: 250 characters exceeded the 100-character cap. A result this large cannot be "
        "reasoned over or charted. Re-run the query so it returns a SMALL, aggregated result — GROUP BY the "
        "dimension you need, add a WHERE filter, or select TOP N — instead of fetching raw rows.]"
    )


def test_default_cap_is_120000_and_bad_values_fall_back(monkeypatch):
    monkeypatch.delenv("TERADATA_MAX_RESULT_CHARS", raising=False)
    assert coach.max_result_chars() == 120000
    monkeypatch.setenv("TERADATA_MAX_RESULT_CHARS", "abc")
    assert coach.max_result_chars() == 120000
    monkeypatch.setenv("TERADATA_MAX_RESULT_CHARS", "-5")
    assert coach.max_result_chars() == 120000
    monkeypatch.setenv("TERADATA_MAX_RESULT_CHARS", "500")
    assert coach.max_result_chars() == 500


def test_structured_result_is_measured_as_json(monkeypatch):
    monkeypatch.setenv("TERADATA_MAX_RESULT_CHARS", "50")
    note = coach.coach(post_payload(tool_response={"rows": list(range(100))}))
    assert note is not None and note.startswith("[RESULT TRUNCATED:")


@pytest.mark.parametrize(
    "text",
    [
        "[Teradata Database] [Error 3541] The request to assign new PERMANENT space is invalid.",
        "Error 3541: The request to assign new PERMANENT space is invalid",
        '{"status": "error", "message": "[Error 3541] The request ..."}',
    ],
)
def test_known_error_code_is_explained(monkeypatch, text):
    monkeypatch.setenv("TERADATA_ERROR_CODES_FILE", SAMPLE_YAML)
    note = coach.coach(post_payload(tool_response=text))
    assert note.startswith("Teradata error 3541 — what it means: The PARENT database has no unallocated permanent space")
    assert "Do this: Check the parent with SELECT PermSpace" in note
    assert note.endswith("(see /teradata-vantage:health)")


def test_error_on_failure_event_is_read_from_the_error_field(monkeypatch):
    monkeypatch.setenv("TERADATA_ERROR_CODES_FILE", SAMPLE_YAML)
    payload = post_payload(error="[Error 5628] Column status not found in sales_db.sales_fact.", event="PostToolUseFailure")
    note = coach.coach(payload)
    assert note.startswith("Teradata error 5628 — what it means: The column does not exist")
    assert "(see" not in note  # no skill recorded for this code


def test_unknown_codes_are_ignored(monkeypatch):
    monkeypatch.setenv("TERADATA_ERROR_CODES_FILE", SAMPLE_YAML)
    assert coach.coach(post_payload(tool_response="[Error 9999] something odd")) is None


def test_multiple_codes_are_deduplicated_and_capped(monkeypatch):
    monkeypatch.setenv("TERADATA_ERROR_CODES_FILE", SAMPLE_YAML)
    text = "Error 3541 ... Error 3541 again ... Error 3706 ... Error 5628 ... Error 3706"
    note = coach.coach(post_payload(tool_response=text))
    assert note.count("Teradata error ") == 3
    assert note.count("Teradata error 3541") == 1


def test_missing_catalogue_means_no_error_notes(monkeypatch, tmp_path):
    monkeypatch.setenv("TERADATA_ERROR_CODES_FILE", str(tmp_path / "nope.yaml"))
    assert coach.coach(post_payload(tool_response="[Error 3541] x")) is None


# --------------------------------------------------------------------------- hook process
def test_hook_emits_additional_context_with_the_incoming_event_name():
    env = {"TERADATA_ERROR_CODES_FILE": SAMPLE_YAML}
    rc, out, _ = run_hook("result_coach.py", post_payload(error="[Error 3706] Syntax error", event="PostToolUseFailure"), env)
    assert rc == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
    assert context_of(out).startswith("Teradata error 3706 — what it means:")
    assert context_of(out).endswith("(see /teradata-vantage:teradata-sql)")


def test_hook_is_silent_on_a_normal_result():
    rc, out, err = run_hook("result_coach.py", post_payload(tool_response="ok"), {"TERADATA_ERROR_CODES_FILE": SAMPLE_YAML})
    assert rc == 0 and out is None and err == ""


def test_hook_is_silent_on_garbage_stdin():
    rc, out, err = run_hook("result_coach.py", None, raw_stdin="{not json")
    assert rc == 0 and out is None and err == ""


def test_internal_crash_is_silent(monkeypatch, capsys):
    def boom(_payload, codes=None):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(coach, "coach", boom)
    monkeypatch.setattr(coach.common, "read_hook_input", lambda stream=None: {"tool_response": "x"})
    assert coach.main() == 0
    assert capsys.readouterr().out == ""
    json.dumps({})  # keep the import honest for readers of the module


# ---------------------------------------------------------------- matcher coverage
# REGRESSION 2026-09-08. The PostToolUse / PostToolUseFailure matchers named only base_readQuery,
# base_writeQuery, base_tablePreview and the qlty_/dba_/tdvs_ groups: 31 of the 65 tools the bundled
# server registers. A 3523 or 3807 from base_tableDDL, base_databaseList or base_tableList — the tools a
# newcomer hits first — produced no coaching at all.

def test_result_coach_matchers_cover_every_shipped_tool():
    import json as _json
    import re as _re

    import yaml as _yaml

    with open(os.path.join(PLUGIN_ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
        hooks = _json.load(fh)["hooks"]
    with open(os.path.join(PLUGIN_ROOT, "scripts", "data", "mcp_tools.yaml"), encoding="utf-8") as fh:
        inventory = _yaml.safe_load(fh)

    tools = {
        t
        for group in inventory["groups"].values()
        for t in ((group.get("tools") if isinstance(group, dict) else group) or [])
    }
    assert tools, "inventory produced no tools"

    for event in ("PostToolUse", "PostToolUseFailure"):
        matchers = [
            e["matcher"]
            for e in hooks.get(event, [])
            if "result_coach" in _json.dumps(e)
        ]
        assert matchers, f"no result_coach registration for {event}"
        compiled = [_re.compile(m) for m in matchers]
        uncovered = sorted(
            t
            for t in tools
            if not any(rx.match(f"mcp__plugin_teradata-vantage_teradata__{t}") for rx in compiled)
        )
        assert not uncovered, f"{event} coaches nothing for: {uncovered}"
