"""Tests for scripts/hooks/sql_read_guard.py — the read-only guard on base_readQuery.

Vector corpus ported from the guard this hook is a faithful port of, adapted to the module
functions, plus the documented EXPLAIN/SHOW/HELP widening and the hook-level behaviour
(deny JSON, audit mode, pass-through, crash -> ask). Pure unit tests.
"""

from __future__ import annotations

import pytest

import sql_read_guard as guard
from conftest import decision_of, pre_tool_payload, reason_of, run_hook

READ_TOOL = "mcp__plugin_teradata-vantage_teradata__base_readQuery"
EXEC_TOOL = "mcp__plugin_teradata-vantage_teradata__execute_tool"


def reason_for(sql) -> str:
    with pytest.raises(guard.SqlRejected) as exc:
        guard.check_sql(sql)
    return exc.value.reason


# --------------------------------------------------------------------------- allowed
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from sales_db.sales_fact",
        "SeLeCt TOP 10 a, b FROM sales_db.customer_dim",
        "   \n\t SELECT 1",
        "SELECT 1;",
        "SELECT 1;   ",
        "WITH cte AS (SELECT a FROM t) SELECT * FROM cte",
        "with cte as (select 1 as x) select x from cte",
        "-- a leading comment\nSELECT 1",
        "/* block comment */ SELECT 1",
        "/* multi\n   line */\n-- and a line comment\nSELECT 1",
        "SELECT 1 -- trailing comment",
    ],
)
def test_allowed_statements(sql):
    assert guard.check_sql(sql) is None
    assert guard.is_read_only(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN SELECT 1",
        "explain select a from sales_db.sales_fact where b = 1",
        "SHOW TABLE sales_db.sales_fact",
        "SHOW VIEW sales_db.v_sales",
        "HELP TABLE sales_db.sales_fact",
        "HELP COLUMN sales_db.sales_fact.*",
        "help database sales_db",
    ],
)
def test_explain_show_help_are_allowed_documented_widening(sql):
    """EXPLAIN / SHOW / HELP mutate nothing on Teradata; the original guard rejected them."""
    assert guard.check_sql(sql) is None
    assert guard.is_read_only(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN DELETE FROM sales_db.sales_fact WHERE order_date < DATE '2020-01-01'",
        "explain delete from sales_db.sales_fact",
        "EXPLAIN INSERT INTO sales_db.sales_fact SELECT * FROM sales_db.sales_stage",
        "EXPLAIN UPDATE sales_db.customer_dim SET status_code = 'X' WHERE customer_id = 1",
        "EXPLAIN MERGE INTO sales_db.sales_fact AS t USING sales_db.sales_stage AS s "
        "ON t.order_id = s.order_id WHEN MATCHED THEN UPDATE SET amount = s.amount",
        "EXPLAIN CREATE TABLE sales_db.sales_stage (order_id INTEGER)",
        "EXPLAIN DROP TABLE sales_db.sales_stage",
        "  /* plan only */ EXPLAIN DELETE FROM sales_db.sales_fact;",
    ],
)
def test_explain_of_a_write_request_is_allowed(sql):
    """REGRESSION 2026-09-05 — the guard used to deny EXPLAIN DELETE/INSERT/UPDATE/MERGE.

    Measured on a live Vantage system: ``EXPLAIN CREATE TABLE <db>.<probe>`` returned the full
    optimizer plan (including "we create the table header") and the table did not exist
    afterwards. EXPLAIN produces a plan and executes nothing, for every request type — and it
    is the tuner agent's only way to touch a user statement, so denying it broke that agent.
    """
    assert guard.check_sql(sql) is None
    assert guard.is_read_only(sql) is True


def test_explain_cannot_smuggle_a_second_statement():
    """The multi-statement rule runs BEFORE the EXPLAIN exemption and still applies to it."""
    for sql in (
        "EXPLAIN SELECT 1; DROP TABLE sales_db.sales_fact",
        "EXPLAIN DELETE FROM sales_db.sales_fact; DROP TABLE sales_db.customer_dim",
        "EXPLAIN SELECT 1;--x\rDROP TABLE sales_db.sales_fact",
    ):
        assert reason_for(sql) == "multi_statement", sql


def test_only_a_LEADING_explain_skips_the_verb_scan():
    """EXPLAIN must not become a way to lead with a non-EXPLAIN verb."""
    assert reason_for("SELECT a FROM t EXPLAIN DELETE") == "blocked_verb:delete"
    assert reason_for("-- EXPLAIN\nDELETE FROM sales_db.sales_fact") == "not_a_select"
    assert reason_for("DELETE FROM sales_db.sales_fact EXPLAIN") == "not_a_select"
    assert reason_for("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte WHERE x = 1 DELETE") == (
        "blocked_verb:delete"
    )


def test_case_end_expression_is_allowed_regression_2026_08():
    """REGRESSION GUARD — do not "harden" the blocklist by adding these five verbs.

    Every ``CASE ... END`` expression contains ``end``; blocking it rejects ordinary analytics
    SQL. If this test fails, someone re-added them — revert that change, not this test.
    """
    sql = (
        "SELECT customer_id, "
        "CASE WHEN tenure_days > 7 THEN 'long' ELSE 'short' END AS tenure_band "
        "FROM sales_db.customer_dim"
    )
    guard.check_sql(sql)
    assert guard.is_read_only(sql) is True
    for verb in ("end", "begin", "comment", "collect", "lock"):
        assert verb not in guard.BLOCKED_VERBS, f"{verb!r} must never be blocked"
        assert verb in guard.NEVER_BLOCK


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT created_at FROM t",
        "SELECT end_date FROM t",
        "SELECT update_ts FROM t",
        "SELECT comment_text FROM t",
        "SELECT a FROM t ORDER BY dropped_flag",
        "SELECT insertion_order FROM t",
        "SELECT execution_ms FROM t",
        "SELECT alteration_count, mergers FROM t",
        "SELECT truncated_flag FROM t",
    ],
)
def test_blocked_verbs_do_not_match_inside_identifiers(sql):
    guard.check_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a FROM t WHERE note = 'please drop this'",
        "SELECT a FROM t WHERE note = 'delete; insert; update'",
        "SELECT 'it''s a drop' AS x",
        'SELECT a AS "drop" FROM t',
    ],
)
def test_blocked_verbs_inside_quoted_spans_are_ignored(sql):
    guard.check_sql(sql)


def test_semicolon_inside_a_string_literal_is_not_a_second_statement():
    guard.check_sql("SELECT a FROM t WHERE note = 'a;b'")


# --------------------------------------------------------------------------- rejected
@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "DELETE FROM t",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DROP TABLE t",
        "CALL sp_do_thing()",
        "COLLECT STATISTICS ON t COLUMN a",
        "-- only a comment",
        "/* only a block comment */",
    ],
)
def test_not_a_select(sql):
    assert reason_for(sql) == "not_a_select"
    assert guard.is_read_only(sql) is False


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE t",
        "SELECT 1; SELECT 2",
        "SELECT 1 ;\nDELETE FROM t;",
        "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte; DROP TABLE t",
        "EXPLAIN SELECT 1; DROP TABLE t",
    ],
)
def test_multi_statement(sql):
    assert reason_for(sql) == "multi_statement"
    assert guard.is_read_only(sql) is False


@pytest.mark.parametrize("verb", sorted(guard.BLOCKED_VERBS))
def test_every_blocked_verb_is_rejected(verb):
    sql = f"SELECT a FROM t WHERE b = (SELECT 1) {verb} c"
    assert reason_for(sql) == f"blocked_verb:{verb}"
    assert reason_for(sql.upper()) == f"blocked_verb:{verb}"


def test_blocked_verb_reason_reports_the_first_offender():
    assert reason_for("SELECT a FROM t WHERE drop_it AND drop x AND delete y") == "blocked_verb:drop"


def test_blocked_verb_hidden_behind_a_comment_is_still_caught():
    assert reason_for("SELECT a FROM t /* c */ DELETE") == "blocked_verb:delete"


def test_comment_cannot_smuggle_a_leading_write():
    assert reason_for("-- SELECT 1\nDELETE FROM t") == "not_a_select"


def test_blocklist_contents_are_exactly_the_agreed_set():
    assert guard.BLOCKED_VERBS == frozenset(
        {
            "insert", "update", "delete", "drop", "create", "replace", "merge", "grant",
            "revoke", "alter", "rename", "call", "exec", "abort", "truncate",
        }
    )


def test_rules_apply_in_order():
    """not_a_select beats multi_statement beats blocked_verb beats write_nos."""
    assert reason_for("DELETE FROM t; DROP TABLE u") == "not_a_select"
    assert reason_for("SELECT 1; DROP TABLE u") == "multi_statement"
    assert reason_for("SELECT * FROM WRITE_NOS ( ON (SELECT 1) ) AS d") == "write_nos"
    # a blocked verb still wins over the WRITE_NOS rule, which runs after it
    assert reason_for(
        "SELECT * FROM WRITE_NOS ( ON (DELETE FROM t) ) AS d"
    ) == "blocked_verb:delete"


# ---------------------------------------------------------------- WRITE_NOS (rule 4)
# WRITE_NOS is a SELECT-shaped TABLE OPERATOR that writes Parquet objects to your store. The
# prefix rule and the single-statement rule cannot see it -- the archive skill renders exactly
# `SELECT * FROM WRITE_NOS (ON (SELECT ...) USING LOCATION(...)) AS d`, one statement starting
# with SELECT -- so before rule 4 the "read-only" tool could mutate object storage.

WRITE_NOS_DENIED = [
    "SELECT * FROM WRITE_NOS ( ON (SELECT * FROM db.t) USING LOCATION('/s3/b/p') ) AS d",
    "SELECT * FROM WRITE_NOS(ON (SELECT 1) USING LOCATION('/s3/b/p')) AS d",
    "SELECT * FROM\nWRITE_NOS (\n  ON (SELECT 1)\n) AS d",
    "select * from write_nos ( on (select 1) ) as d",
    "WITH x AS (SELECT 1) SELECT * FROM WRITE_NOS ( ON (SELECT * FROM x) ) AS d",
]

WRITE_NOS_ALLOWED = [
    "HELP FUNCTION WRITE_NOS;",                      # a documented read that names the operator
    "SELECT * FROM READ_NOS( ON (SELECT 1) USING LOCATION('/s3/b/p') ) AS d",
    "SELECT FunctionName FROM DBC.FunctionsV WHERE UPPER(FunctionName) IN ('READ_NOS','WRITE_NOS')",
    "SELECT 1 AS WRITE_NOS_ENABLED",                 # the token as an identifier, no invocation
]


@pytest.mark.parametrize("sql", WRITE_NOS_DENIED)
def test_write_nos_invocation_is_denied(sql):
    assert guard.is_read_only(sql) is False, sql
    assert reason_for(sql) == "write_nos"


@pytest.mark.parametrize("sql", WRITE_NOS_ALLOWED)
def test_write_nos_merely_named_is_allowed(sql):
    assert guard.is_read_only(sql) is True, sql


def test_explain_over_write_nos_is_allowed():
    """EXPLAIN produces a plan and executes nothing -- the same widening the verb scan uses."""
    assert guard.is_read_only("EXPLAIN SELECT * FROM WRITE_NOS ( ON (SELECT 1) ) AS d") is True


def test_write_nos_deny_message_points_at_the_write_tool():
    with pytest.raises(guard.SqlRejected) as exc:
        guard.check_sql("SELECT * FROM WRITE_NOS ( ON (SELECT 1) ) AS d")
    assert "object store" in str(exc.value)
    assert "base_writeQuery" in guard.deny_reason(exc.value)


def test_sql_rejected_carries_a_human_message_and_is_a_value_error():
    with pytest.raises(ValueError) as exc:
        guard.check_sql("DELETE FROM t")
    assert isinstance(exc.value, guard.SqlRejected)
    assert "SELECT, WITH, EXPLAIN, SHOW or HELP" in str(exc.value)


def test_is_read_only_never_raises_on_bad_input():
    for bad in (None, 42, b"SELECT 1", ["SELECT 1"]):
        assert guard.is_read_only(bad) is False


class TestCommentTerminatorBypass:
    """A `--` comment must end at ANY line terminator, not just LF."""

    @pytest.mark.parametrize(
        "terminator,label",
        [
            ("\r", "carriage return"),
            ("\x0b", "vertical tab"),
            ("\x0c", "form feed"),
            ("\x1c", "file separator"),
            ("\x1d", "group separator"),
            ("\x1e", "record separator"),
            ("\x85", "next line (NEL)"),
            ("\u2028", "unicode line separator"),
            ("\u2029", "unicode paragraph separator"),
            ("\n", "line feed (was already correct)"),
        ],
    )
    def test_comment_does_not_swallow_a_following_statement(self, terminator, label):
        sql = f"SELECT 1;--x{terminator}DROP TABLE sales_db.sales_fact"
        with pytest.raises(guard.SqlRejected):
            guard.check_sql(sql)

    @pytest.mark.parametrize("terminator", ["\r", "\x0b", "\x0c", "\u2028"])
    def test_verb_after_a_non_lf_terminator_is_still_seen(self, terminator):
        with pytest.raises(guard.SqlRejected):
            guard.check_sql(f"SELECT a FROM t --note{terminator}DELETE FROM t")

    def test_a_legitimate_trailing_comment_still_passes(self):
        guard.check_sql("SELECT a FROM t -- just a note about the column")
        guard.check_sql("SELECT a FROM t --no space before the note")

    def test_a_multiline_comment_block_still_passes(self):
        guard.check_sql("SELECT a\n-- first note\n-- second note\nFROM t")


# --------------------------------------------------------------------------- hook behaviour
def test_hook_denies_with_the_documented_reason_text():
    rc, out, _ = run_hook("sql_read_guard.py", pre_tool_payload(READ_TOOL, {"sql": "SELECT 1; DROP TABLE t"}))
    assert rc == 0
    assert decision_of(out) == "deny"
    reason = reason_of(out)
    assert reason.startswith("read-only: multiple statements are not allowed. ")
    assert "base_readQuery is read-only" in reason
    # A multi-statement rejection must NOT send the caller to base_writeQuery: the statement
    # may be a perfectly legal EXPLAIN, and base_writeQuery would RUN what it explains.
    assert reason.endswith("send one statement per call.")
    assert "base_writeQuery" not in reason
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_a_non_read_statement_is_still_pointed_at_the_write_path():
    rc, out, _ = run_hook("sql_read_guard.py", pre_tool_payload(READ_TOOL, {"sql": "DELETE FROM t"}))
    assert decision_of(out) == "deny"
    assert "rewrite as a single SELECT (use base_writeQuery for writes)." in reason_of(out)


def test_hook_allows_explain_of_a_delete_end_to_end():
    payload = pre_tool_payload(READ_TOOL, {"sql": "EXPLAIN DELETE FROM sales_db.sales_fact WHERE a = 1"})
    rc, out, err = run_hook("sql_read_guard.py", payload)
    assert rc == 0
    assert out is None
    assert err == ""


def test_hook_passes_a_clean_select_with_no_output():
    rc, out, err = run_hook("sql_read_guard.py", pre_tool_payload(READ_TOOL, {"sql": "EXPLAIN SELECT 1"}))
    assert rc == 0
    assert out is None
    assert err == ""


def test_hook_applies_to_base_readQuery_nested_in_execute_tool():
    payload = pre_tool_payload(EXEC_TOOL, {"tool_name": "base_readQuery", "arguments": {"sql": "DELETE FROM t"}})
    rc, out, _ = run_hook("sql_read_guard.py", payload)
    assert decision_of(out) == "deny"
    assert "read-only: query must start with" in reason_of(out)


def test_hook_ignores_other_tools_routed_through_execute_tool():
    payload = pre_tool_payload(EXEC_TOOL, {"tool_name": "base_writeQuery", "arguments": {"sql": "DELETE FROM t"}})
    rc, out, _ = run_hook("sql_read_guard.py", payload)
    assert rc == 0 and out is None


def test_hook_passes_when_no_sql_argument_is_present():
    rc, out, _ = run_hook("sql_read_guard.py", pre_tool_payload(READ_TOOL, {"tool_name": "registry_tool"}))
    assert rc == 0 and out is None


def test_audit_mode_allows_and_logs_would_block():
    payload = pre_tool_payload(READ_TOOL, {"sql": "DELETE FROM sales_db.sales_fact"})
    rc, out, err = run_hook("sql_read_guard.py", payload, {"TERADATA_SQL_GUARD_MODE": "audit"})
    assert rc == 0
    assert out is None
    assert "WOULD_BLOCK" in err
    assert "reason=not_a_select" in err


@pytest.mark.parametrize("mode", ["", "enforce", "ENFORCE", "banana", "read_only", "off", "0"])
def test_unknown_modes_mean_enforce(mode):
    payload = pre_tool_payload(READ_TOOL, {"sql": "DELETE FROM t"})
    rc, out, _ = run_hook("sql_read_guard.py", payload, {"TERADATA_SQL_GUARD_MODE": mode})
    assert decision_of(out) == "deny"


def test_guard_mode_helper(monkeypatch):
    monkeypatch.setenv("TERADATA_SQL_GUARD_MODE", "AUDIT")
    assert guard.guard_mode() == "audit"
    monkeypatch.setenv("TERADATA_SQL_GUARD_MODE", "audit-ish")
    assert guard.guard_mode() == "enforce"


# CHANGED 2026-09-08. This used to assert silence on malformed stdin. That was the wrong direction: a
# guard whose input arrived and could not be read cannot certify the call is read-only, and passing
# silently makes it inert without saying so. It now ASKS, matching its documented fail direction and the
# behaviour destructive_tool_gate already had. Genuinely EMPTY stdin still passes — nothing to check.

@pytest.mark.parametrize("payload", ["this is not json", "[]", '"a string"', "42"])
def test_unreadable_stdin_fails_to_ask(payload):
    rc, out, _ = run_hook("sql_read_guard.py", None, raw_stdin=payload)
    assert rc == 0
    assert decision_of(out) == "ask", out
    assert "could not be parsed" in reason_of(out)


@pytest.mark.parametrize("payload", ["", "   \n"])
def test_empty_stdin_stays_silent(payload):
    rc, out, _ = run_hook("sql_read_guard.py", None, raw_stdin=payload)
    assert rc == 0 and out is None


def test_audit_mode_masks_literals_in_the_logged_statement(monkeypatch, capsys):
    """Audit mode is a rollout tool; it must not put statement literals on stderr."""
    secret = "123-45-" + "6789"
    monkeypatch.setenv("TERADATA_SQL_GUARD_MODE", "audit")
    rc, out, err = run_hook(
        "sql_read_guard.py",
        pre_tool_payload("base_readQuery", {"sql": f"DELETE FROM analytics.t WHERE ssn = '{secret}'"}),
        env_overrides={"TERADATA_SQL_GUARD_MODE": "audit"},
    )
    assert rc == 0 and out is None
    assert "WOULD_BLOCK" in err
    assert secret not in err, err


def test_internal_crash_fails_to_ask(monkeypatch, capsys):
    def boom(_payload):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(guard, "decide", boom)
    monkeypatch.setattr(guard.common, "read_hook_input", lambda stream=None: {"tool_name": READ_TOOL})
    assert guard.main() == 0
    import json

    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "read guard crashed: RuntimeError"


# --------------------------------------------------------------- payload-shape drift
# Regression, found by the end-to-end harness 2026-09-05: when a guarded call's
# ``tool_input`` arrived in a shape the guard could not read, the guard passed SILENTLY.
# Nothing legitimate produces a non-dict ``tool_input``, so that state means the payload
# contract changed under us and the guard has gone inert. It must ask, not pass.
@pytest.mark.parametrize(
    "tool_input",
    [
        "DROP TABLE sales_db.sales_fact",          # a bare string instead of the argument map
        ["DROP TABLE sales_db.sales_fact"],        # a list
        42,                                        # anything else non-dict
    ],
)
def test_non_dict_tool_input_asks_instead_of_passing_silently(tool_input):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": READ_TOOL,
        "tool_input": tool_input,
    }
    out = run_hook("sql_read_guard.py", payload)[1]
    assert decision_of(out) == "ask"
    assert "read-only" in reason_of(out).lower() or "tool_input" in reason_of(out)


def test_execute_tool_with_non_dict_arguments_asks():
    payload = pre_tool_payload(
        EXEC_TOOL, {"tool_name": "base_readQuery", "arguments": "DROP TABLE t"}
    )
    assert decision_of(run_hook("sql_read_guard.py", payload)[1]) == "ask"


@pytest.mark.parametrize(
    "tool_input",
    [
        {},                                        # no arguments at all
        {"tool": "some_registry_tool"},            # registry form: legitimately carries no sql
    ],
)
def test_dict_without_sql_still_passes(tool_input):
    """A dict is a shape we understand; a registry-tool call carries no ``sql`` by design."""
    payload = pre_tool_payload(READ_TOOL, tool_input)
    assert decision_of(run_hook("sql_read_guard.py", payload)[1]) in (None, "allow")


def test_missing_tool_input_key_passes():
    out = run_hook("sql_read_guard.py", {"hook_event_name": "PreToolUse", "tool_name": READ_TOOL})[1]
    assert decision_of(out) in (None, "allow")
