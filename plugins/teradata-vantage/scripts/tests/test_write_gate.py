"""Tests for scripts/hooks/write_gate.py — the approval prompt before destructive base_writeQuery."""

from __future__ import annotations

import json
import os

import pytest

import write_gate as gate
from conftest import PLUGIN_ROOT, decision_of, pre_tool_payload, reason_of, run_hook

WRITE_TOOL = "mcp__plugin_teradata-vantage_teradata__base_writeQuery"
PROJECT_WRITE_TOOL = "mcp__teradata__base_writeQuery"
EXEC_TOOL = "mcp__plugin_teradata-vantage_teradata__execute_tool"


def decide_sql(sql: str, tool: str = WRITE_TOOL):
    return gate.decide(pre_tool_payload(tool, {"sql": sql}))


# --------------------------------------------------------------------------- pass-through
@pytest.mark.parametrize(
    "sql",
    [
        "DROP FOREIGN TABLE archive_db.sales_2019_ft",
        "  drop foreign table archive_db.sales_2019_ft;",
        "/* cleanup */ DROP FOREIGN TABLE archive_db.sales_2019_ft",
    ],
)
def test_drop_foreign_table_is_exempt(sql):
    assert decide_sql(sql) == ("pass", "")


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE sales_db.sales_stage AS (SELECT * FROM sales_db.sales_fact) WITH NO DATA",
        "CREATE MULTISET TABLE sales_db.t (a INTEGER)",
        "CREATE VIEW sales_db.v_sales AS SELECT * FROM sales_db.sales_fact",
        "CREATE AUTHORIZATION archive_db.nos_auth AS DEFINER TRUSTED USER 'svc' PASSWORD 'x'",
        "COLLECT STATISTICS ON sales_db.sales_fact COLUMN (customer_id)",
        "SELECT * FROM WRITE_NOS (ON (SELECT * FROM sales_db.sales_fact) USING LOCATION('/s3/bucket/') ) AS d",
        "CREATE FOREIGN TABLE archive_db.sales_ft USING (LOCATION('/s3/bucket/'))",
        "SELECT 1",
        "",
        "   ",
    ],
)
def test_non_destructive_statements_pass(sql):
    assert decide_sql(sql) == ("pass", "")


def test_verbs_inside_literals_and_comments_do_not_prompt():
    assert decide_sql("CREATE VIEW v AS SELECT 'DELETE me later' AS note -- drop later") == ("pass", "")
    assert decide_sql('CREATE TABLE t ("delete" INTEGER, "drop" INTEGER)') == ("pass", "")


def test_other_tools_through_execute_tool_pass():
    payload = pre_tool_payload(EXEC_TOOL, {"tool_name": "base_readQuery", "arguments": {"sql": "DELETE FROM t"}})
    assert gate.decide(payload) == ("pass", "")


# --------------------------------------------------------------------------- ask
def test_where_less_delete_warns_and_names_the_table():
    decision, reason = decide_sql("DELETE FROM sales_db.sales_fact")
    assert decision == "ask"
    assert "DELETE on sales_db.sales_fact with NO WHERE clause — DELETE affects EVERY row" in reason
    assert reason.startswith("base_writeQuery will run 1 destructive statement: ")
    assert "TERADATA_ALLOW_WRITES=0" in reason


def test_delete_with_where_does_not_carry_the_warning():
    decision, reason = decide_sql("DELETE FROM sales_db.sales_fact WHERE order_date < DATE '2020-01-01'")
    assert decision == "ask"
    assert "DELETE on sales_db.sales_fact" in reason
    assert "NO WHERE" not in reason


def test_teradata_short_forms_del_and_all_are_recognised():
    decision, reason = decide_sql("DEL sales_db.sales_fact ALL")
    assert decision == "ask"
    assert "DELETE on sales_db.sales_fact with NO WHERE clause" in reason


def test_where_less_update_warns():
    decision, reason = decide_sql("UPDATE sales_db.customer_dim SET status_code = 'X'")
    assert decision == "ask"
    assert "UPDATE on sales_db.customer_dim with NO WHERE clause — UPDATE affects EVERY row" in reason


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("DROP TABLE sales_db.sales_fact", "DROP on sales_db.sales_fact"),
        ("DROP VIEW sales_db.v_sales", "DROP on sales_db.v_sales"),
        ("DROP DATABASE archive_db", "DROP on archive_db"),
        ("TRUNCATE TABLE sales_db.sales_stage", "TRUNCATE on sales_db.sales_stage"),
        ("INSERT INTO sales_db.sales_fact SELECT * FROM sales_db.sales_stage", "INSERT on sales_db.sales_fact"),
        ("INS sales_db.sales_fact (1, 2)", "INSERT on sales_db.sales_fact"),
        ("MERGE INTO sales_db.sales_fact AS t USING sales_db.sales_stage AS s ON t.id = s.id "
         "WHEN MATCHED THEN UPDATE SET amount = s.amount WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id)",
         "MERGE on sales_db.sales_fact"),
        ("ALTER TABLE sales_db.sales_fact ADD new_col INTEGER", "ALTER on sales_db.sales_fact"),
        ("MODIFY DATABASE archive_db AS PERM = 5000000000", "MODIFY on archive_db"),
        ("CREATE DATABASE archive_db FROM parent_db AS PERM = 200000000", "CREATE DATABASE on archive_db"),
        ("CREATE USER svc_loader FROM parent_db AS PASSWORD = (x) PERM = 0", "CREATE USER on svc_loader"),
        ("GRANT SELECT ON sales_db TO analyst_role", "GRANT on sales_db to analyst_role"),
        ("REVOKE ALL ON sales_db FROM analyst_role", "REVOKE on sales_db from analyst_role"),
        ("ABORT SESSION 1234,5678", "ABORT SESSION on 1234,5678"),
    ],
)
def test_each_destructive_verb_asks_and_names_the_target(sql, expected):
    decision, reason = decide_sql(sql)
    assert decision == "ask", sql
    assert expected in reason, reason


def test_merge_is_reported_as_merge_not_as_its_inner_update():
    _, reason = decide_sql(
        "MERGE INTO sales_db.sales_fact t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1"
    )
    assert "MERGE on sales_db.sales_fact" in reason
    assert "UPDATE on" not in reason


def test_compound_lists_only_the_destructive_part():
    sql = "DELETE FROM sales_db.sales_fact WHERE order_date < DATE '2020-01-01'; COLLECT STATISTICS ON sales_db.sales_fact COLUMN (order_date)"
    decision, reason = decide_sql(sql)
    assert decision == "ask"
    assert "1 destructive statement: DELETE on sales_db.sales_fact." in reason
    assert "COLLECT" not in reason.split("It also")[0]
    assert "1 non-destructive statement that runs with it" in reason


def test_compound_with_two_destructive_parts_lists_both():
    sql = "DELETE FROM sales_db.sales_stage; DROP TABLE sales_db.sales_stage"
    decision, reason = decide_sql(sql)
    assert decision == "ask"
    assert "2 destructive statements" in reason
    assert "DELETE on sales_db.sales_stage with NO WHERE clause" in reason
    assert "DROP on sales_db.sales_stage" in reason


def test_drop_foreign_table_exemption_is_per_statement():
    """REGRESSION 2026-09-05 — the exemption is per STATEMENT, never per call.

    The gate used to test the WHOLE sql string for a leading ``DROP FOREIGN TABLE`` and return
    "pass" for the entire call, so a compound whose FIRST statement was the exempt one ran every
    later DELETE / DROP / TRUNCATE with no prompt. Both orders must surface the destructive part.
    """
    # exempt statement FIRST — this is the order that used to skip the gate entirely
    decision, reason = decide_sql("DROP FOREIGN TABLE archive_db.ft; DELETE FROM sales_db.sales_fact")
    assert decision == "ask"
    assert "DELETE on sales_db.sales_fact" in reason
    assert "with NO WHERE clause" in reason
    assert "DROP on archive_db.ft" not in reason

    # exempt statement SECOND — was already correct, must stay correct
    decision, reason = decide_sql("DELETE FROM sales_db.sales_fact; DROP FOREIGN TABLE archive_db.ft")
    assert decision == "ask"
    assert "DELETE on sales_db.sales_fact" in reason
    assert "DROP on archive_db.ft" not in reason


def test_drop_then_recreate_foreign_table_still_passes_without_a_prompt():
    """The archive skill's register step re-points a deterministic foreign-table name.

    Narrowing the exemption must not turn that routine DROP-then-CREATE into a spurious prompt.
    """
    sql = (
        "DROP FOREIGN TABLE archive_db.ft; "
        "CREATE FOREIGN TABLE archive_db.ft USING (LOCATION('/s3/bucket/'))"
    )
    assert decide_sql(sql) == ("pass", "")


def test_drop_recreate_then_purge_asks_and_names_the_purged_table():
    """The realistic threat shape: register the archive, then delete the rows it replaced."""
    sql = (
        "DROP FOREIGN TABLE archive_db.sales_cold_ft; "
        "CREATE FOREIGN TABLE archive_db.sales_cold_ft USING (LOCATION('/s3/bucket/')); "
        "DELETE FROM sales_db.sales_fact WHERE order_id IN "
        "(SELECT order_id FROM archive_db.sales_cold_ft)"
    )
    decision, reason = decide_sql(sql)
    assert decision == "ask"
    assert "1 destructive statement: DELETE on sales_db.sales_fact." in reason
    assert "2 non-destructive statements that run with it" in reason


def test_project_level_server_naming_is_handled():
    decision, _ = decide_sql("DROP TABLE t", tool=PROJECT_WRITE_TOOL)
    assert decision == "ask"


def test_base_writeQuery_via_execute_tool_is_gated():
    payload = pre_tool_payload(EXEC_TOOL, {"tool_name": "base_writeQuery", "arguments": {"sql": "DROP TABLE t"}})
    decision, reason = gate.decide(payload)
    assert decision == "ask"
    assert "DROP on t" in reason


def test_always_ask_tools_via_execute_tool_prompt():
    payload = pre_tool_payload(EXEC_TOOL, {"tool_name": "tdvs_destroy", "arguments": {"vs_name": "docs_vs"}})
    decision, reason = gate.decide(payload)
    assert decision == "ask"
    assert "tdvs_destroy" in reason and "docs_vs" in reason


# --------------------------------------------------------------------------- writes disabled
@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_allow_writes_zero_denies_everything(monkeypatch, value):
    monkeypatch.setenv("TERADATA_ALLOW_WRITES", value)
    for sql in ("SELECT 1", "CREATE VIEW v AS SELECT 1", "DROP FOREIGN TABLE ft", "DELETE FROM t"):
        decision, reason = decide_sql(sql)
        assert decision == "deny", sql
        assert "TERADATA_ALLOW_WRITES=0" in reason


def test_allow_writes_default_is_on(monkeypatch):
    monkeypatch.delenv("TERADATA_ALLOW_WRITES", raising=False)
    assert gate.writes_allowed() is True
    monkeypatch.setenv("TERADATA_ALLOW_WRITES", "1")
    assert gate.writes_allowed() is True


# --------------------------------------------------------------------------- hook process
def test_hook_emits_ask_json():
    rc, out, _ = run_hook("write_gate.py", pre_tool_payload(WRITE_TOOL, {"sql": "DELETE FROM sales_db.sales_fact"}))
    assert rc == 0
    assert decision_of(out) == "ask"
    assert "NO WHERE clause" in reason_of(out)


def test_hook_is_silent_for_a_plain_create_table():
    rc, out, err = run_hook("write_gate.py", pre_tool_payload(WRITE_TOOL, {"sql": "CREATE TABLE t (a INTEGER)"}))
    assert rc == 0 and out is None and err == ""


def test_hook_denies_when_writes_are_disabled():
    rc, out, _ = run_hook("write_gate.py", pre_tool_payload(WRITE_TOOL, {"sql": "CREATE TABLE t (a INTEGER)"}), {"TERADATA_ALLOW_WRITES": "0"})
    assert decision_of(out) == "deny"


def test_internal_crash_fails_to_ask(monkeypatch, capsys):
    def boom(_payload):
        raise ValueError("synthetic")

    monkeypatch.setattr(gate, "decide", boom)
    monkeypatch.setattr(gate.common, "read_hook_input", lambda stream=None: {"tool_name": WRITE_TOOL})
    assert gate.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "write gate crashed: ValueError"


# --------------------------------------------------------------- payload-shape drift
# Same regression as the read guard (end-to-end harness, 2026-09-05): a non-dict
# ``tool_input`` meant the gate could not see the statement and passed silently.
@pytest.mark.parametrize(
    "tool_input",
    ["DELETE FROM sales_db.sales_fact", ["DROP TABLE sales_db.sales_fact"], 7],
)
def test_non_dict_tool_input_asks(tool_input):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": WRITE_TOOL,
        "tool_input": tool_input,
    }
    assert decision_of(run_hook("write_gate.py", payload)[1]) == "ask"


def test_dict_without_sql_still_passes():
    assert decision_of(run_hook("write_gate.py", pre_tool_payload(WRITE_TOOL, {}))[1]) in (None, "allow")


# ---------------------------------------------------------------- progressive-disclosure parity
# REGRESSION 2026-09-08. hooks/hooks.json matches the destructive tools by DIRECT name, but a
# progressive-disclosure call arrives as `execute_tool` with the real name nested in the arguments and
# lands in write_gate instead. ALWAYS_ASK_TOOLS held only the five tdvs/bar names, so
# sql_Execute_Full_Pipeline, rag_Execute_Workflow and base_saveDDL passed with no prompt.

def _hooks_json_destructive_names():
    with open(os.path.join(PLUGIN_ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
        hooks = json.load(fh)["hooks"]["PreToolUse"]
    for entry in hooks:
        m = entry["matcher"]
        if "tdvs_destroy" in m:
            inner = m[m.index("(") + 1 : m.rindex(")")]
            return {n for n in inner.split("|")}
    raise AssertionError("destructive-tool matcher not found in hooks.json")


def test_always_ask_tools_covers_every_directly_matched_destructive_tool():
    """A name the direct matcher guards but ALWAYS_ASK_TOOLS omits is a silent execute_tool bypass."""
    missing = _hooks_json_destructive_names() - set(gate.ALWAYS_ASK_TOOLS)
    assert not missing, f"reachable through execute_tool with no prompt: {sorted(missing)}"


@pytest.mark.parametrize(
    "tool", ["sql_Execute_Full_Pipeline", "rag_Execute_Workflow", "base_saveDDL", "tdvs_destroy"]
)
def test_destructive_tool_through_execute_tool_asks(tool):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__plugin_teradata-vantage_teradata__execute_tool",
        "tool_input": {"tool_name": tool, "arguments": {"database_name": "analytics"}},
    }
    decision, reason = gate.decide(payload)
    assert decision == "ask", reason
    assert tool in reason


def test_execute_tool_reason_names_a_target_for_the_file_and_pipeline_tools():
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__plugin_teradata-vantage_teradata__execute_tool",
        "tool_input": {"tool_name": "base_saveDDL", "arguments": {"output_dir": "/tmp/ddl"}},
    }
    _, reason = gate.decide(payload)
    assert "/tmp/ddl" in reason and "unnamed object" not in reason


# ---------------------------------------------------------------- RENAME / REPLACE / GIVE
# The read guard's BLOCKED_VERBS already contained `rename` and `replace`, so base_readQuery refused
# them while base_writeQuery ran them with no prompt. GIVE transfers a database and its space.

@pytest.mark.parametrize(
    "sql",
    [
        "RENAME TABLE analytics.a TO analytics.b",
        "RENAME VIEW analytics.v TO analytics.w",
        "REPLACE VIEW analytics.v AS SELECT 1 AS a",
        "REPLACE RECURSIVE VIEW analytics.v AS SELECT 1 AS a",
        "REPLACE MACRO analytics.m AS (SELECT 1;)",
        "REPLACE PROCEDURE analytics.p () BEGIN END;",
        "REPLACE AUTHORIZATION analytics.auth USER 'u' PASSWORD 'p'",
        "GIVE analytics_child TO other_user",
    ],
)
def test_rename_replace_give_ask(sql):
    payload = pre_tool_payload("base_writeQuery", {"sql": sql})
    decision, reason = gate.decide(payload)
    assert decision == "ask", f"{sql} -> {decision} {reason}"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT OREPLACE(col, 'a', 'b') FROM analytics.sales_fact",
        "SELECT REGEXP_REPLACE(col, 'a', 'b') FROM analytics.sales_fact",
        "CREATE TABLE analytics.t (a INTEGER)",
        "COLLECT STATISTICS COLUMN (a) ON analytics.t",
    ],
)
def test_replace_rule_does_not_fire_on_a_string_function_or_plain_ddl(sql):
    payload = pre_tool_payload("base_writeQuery", {"sql": sql})
    decision, _ = gate.decide(payload)
    assert decision == "pass", sql


# ---------------------------------------------------------------- unreadable stdin fails to ask
# Same correction as the read guard (2026-09-08). This path also silently disabled the operator
# kill-switch: with TERADATA_ALLOW_WRITES=0 the gate denies every write, but an unreadable payload
# short-circuited before that check and passed.

@pytest.mark.parametrize("payload", ["this is not json", "[]", '"a string"'])
def test_unreadable_stdin_fails_to_ask(payload):
    rc, out, _ = run_hook("write_gate.py", None, raw_stdin=payload)
    assert rc == 0
    assert decision_of(out) == "ask", out
    assert "could not be parsed" in reason_of(out)


@pytest.mark.parametrize("payload", ["", "   \n"])
def test_empty_stdin_stays_silent(payload):
    rc, out, _ = run_hook("write_gate.py", None, raw_stdin=payload)
    assert rc == 0 and out is None
