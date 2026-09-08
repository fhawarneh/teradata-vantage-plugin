#!/usr/bin/env python3
"""PreToolUse hook: hold ``base_readQuery`` read-only.

Matcher: ``mcp__.*__(base_readQuery|execute_tool)$``. The guard inspects the ``sql``
argument of a ``base_readQuery`` call (directly, or nested inside a progressive-disclosure
``execute_tool`` call) and DENIES anything that is not a single read statement. Other tools
reaching this script through ``execute_tool`` are passed through untouched.

This is mechanical syntactic validation, not intent inference: it answers "is this string
one read statement?" and nothing else. Rules, applied in order so the reported reason is the
first one violated:

1. ``not_a_select`` — the statement (after masking comments) must start with
   SELECT, WITH, EXPLAIN, SHOW or HELP. EXPLAIN/SHOW/HELP are a deliberate widening over the
   original SELECT|WITH guard: on Teradata they mutate nothing and are how you read a plan,
   a DDL text or a column list.
2. ``multi_statement`` — a ``;`` that is not merely trailing means a second statement is
   being smuggled in.
3. ``blocked_verb:<verb>`` — a word-boundary hit on :data:`BLOCKED_VERBS` outside quoted
   spans and comments, applied ONLY when the statement does not start with ``EXPLAIN``.
   ``EXPLAIN <request>`` returns the optimizer plan and runs nothing, for every request type,
   so ``EXPLAIN DELETE ...``, ``EXPLAIN MERGE ...`` and ``EXPLAIN INSERT ... SELECT`` are
   reads and are allowed. Rules 1 and 2 still apply to them, so an EXPLAIN can carry exactly
   one statement — ``EXPLAIN SELECT 1; DROP TABLE t`` is still denied ``multi_statement``,
   and only a leading ``EXPLAIN`` skips the scan, so no non-EXPLAIN verb can lead.

NEVER add ``end``, ``begin``, ``comment``, ``collect`` or ``lock`` to the blocklist: every
``CASE ... END`` expression contains ``end``, so blocking it rejects ordinary analytics SQL.
Two independent servers shipped that mistake and both removed it. Safety does not depend on
the list being exhaustive — a write cannot start with SELECT and must be a single statement;
the verb scan is the third, belt-and-braces layer.

Modes (``TERADATA_SQL_GUARD_MODE``): ``enforce`` (default, and what any unrecognised value
means — a misspelt mode must never silently disable the guard) or ``audit`` (run every check,
log ``WOULD_BLOCK`` to stderr, let the call through — use it to soak a new workload before
enforcing). On an internal crash the hook asks instead of allowing.

A passing statement produces no output: the normal permission flow applies. The hook never
widens permissions.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

GUARDED_TOOL = "base_readQuery"

#: Verbs that may never appear anywhere in a read-only statement (word boundaries, outside
#: quoted spans and comments). The original 14 plus ``truncate``. Keep it EXACTLY as-is
#: unless a failing read query proves an addition is needed — and read the CASE...END warning
#: in the module docstring first.
BLOCKED_VERBS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "drop",
        "create",
        "replace",
        "merge",
        "grant",
        "revoke",
        "alter",
        "rename",
        "call",
        "exec",
        "abort",
        "truncate",
    }
)

#: Verbs that must NEVER join the blocklist (see the module docstring).
NEVER_BLOCK = frozenset({"end", "begin", "comment", "collect", "lock"})

# One alternation over sorted() so the EARLIEST offending verb is reported deterministically.
_BLOCKED_RE = re.compile(r"\b(" + "|".join(sorted(BLOCKED_VERBS)) + r")\b", re.IGNORECASE)

#: Documented widening: EXPLAIN / SHOW / HELP are read-only on Teradata.
READ_PREFIXES = ("select", "with", "explain", "show", "help")
_STARTS_READ_RE = re.compile(r"^\s*(" + "|".join(READ_PREFIXES) + r")\b", re.IGNORECASE)

#: A leading EXPLAIN makes the whole request plan-only, so the verb scan does not apply to it.
_IS_EXPLAIN_RE = re.compile(r"^\s*explain\b", re.IGNORECASE)

#: WRITE_NOS is a SELECT-shaped table operator that WRITES objects to your store, so the prefix and
#: single-statement rules above cannot see it: `SELECT * FROM WRITE_NOS (ON (SELECT …) USING
#: LOCATION('/s3/bucket/path')) AS d` is one statement starting with SELECT, and the archive skill
#: renders exactly that. Match the INVOCATION (the opening paren), not the bare token, so
#: `HELP FUNCTION WRITE_NOS;` and a catalogue probe naming it in a quoted list stay legal reads.
_WRITE_NOS_RE = re.compile(r"\bWRITE_NOS\s*\(", re.IGNORECASE)


class SqlRejected(ValueError):
    """Raised when a statement is not a safe single read.

    ``reason`` is the machine-readable code (``not_a_select``, ``multi_statement``,
    ``blocked_verb:<verb>``, ``write_nos``); ``str(exc)`` is the human-facing message.
    """

    def __init__(self, reason: str, message: Optional[str] = None) -> None:
        self.reason: str = reason
        super().__init__(message or f"read-only: rejected ({reason})")


def check_sql(sql) -> None:
    """Validate that ``sql`` is a single read-only statement; raise :class:`SqlRejected` if not."""
    if not isinstance(sql, str):
        raise SqlRejected("not_a_select", "read-only: query must be a string")

    masked = common.mask_sql(sql)

    if not _STARTS_READ_RE.match(masked):
        raise SqlRejected(
            "not_a_select", "read-only: query must start with SELECT, WITH, EXPLAIN, SHOW or HELP"
        )

    # A trailing `;` (with optional whitespace/comments after it, already masked) is fine;
    # anything earlier means a second statement is being smuggled in.
    if ";" in masked.rstrip().rstrip(";").rstrip():
        raise SqlRejected("multi_statement", "read-only: multiple statements are not allowed")

    # An EXPLAIN produces a plan and executes nothing, so a blocked verb inside one is not a
    # write. Rules 1 and 2 above have already run, so the EXPLAIN is a single statement.
    if not _IS_EXPLAIN_RE.match(masked):
        hit = _BLOCKED_RE.search(masked)
        if hit:
            verb = hit.group(1).lower()
            raise SqlRejected(f"blocked_verb:{verb}", f"read-only: verb '{verb}' is not allowed")

        # Rule 4: a SELECT-shaped table operator that writes. Under EXPLAIN it plans only, so this
        # rule sits inside the same guard as the verb scan.
        if _WRITE_NOS_RE.search(masked):
            raise SqlRejected(
                "write_nos",
                "read-only: WRITE_NOS writes objects to your object store",
            )


def is_read_only(sql) -> bool:
    """Bool form of :func:`check_sql`; never raises — an internal failure counts as not read-only."""
    try:
        check_sql(sql)
        return True
    except SqlRejected:
        return False
    except Exception:  # noqa: BLE001 — fail closed
        return False


def guard_mode() -> str:
    """``audit`` only when spelled exactly; everything else (including typos) is ``enforce``."""
    raw = (os.environ.get("TERADATA_SQL_GUARD_MODE") or "").strip().lower()
    return "audit" if raw == "audit" else "enforce"


def deny_reason(exc: SqlRejected) -> str:
    """The deny text. The remedy follows the reason — NEVER send a multi-statement rejection
    to the write path: the statement may be a legal ``EXPLAIN`` and base_writeQuery runs it."""
    if exc.reason == "multi_statement":
        remedy = "send one statement per call"
    elif exc.reason == "write_nos":
        remedy = "run the export through base_writeQuery or your own SQL client"
    else:
        remedy = "rewrite as a single SELECT (use base_writeQuery for writes)"
    return f"{exc}. {GUARDED_TOOL} is read-only — {remedy}."


def decide(payload: dict) -> Tuple[str, str]:
    """Return ``(decision, reason)`` where decision is ``pass`` / ``deny`` / ``ask`` / ``audit``."""
    name, _ = common.logical_tool(payload.get("tool_name"), payload.get("tool_input"))
    if name != GUARDED_TOOL:
        return "pass", ""
    if common.tool_input_drifted(payload.get("tool_name"), payload.get("tool_input")):
        return "ask", (
            f"read guard cannot read the arguments of this {GUARDED_TOOL} call "
            "(unrecognised tool_input shape), so it cannot confirm the statement is read-only. "
            "Approve only if you know what it runs."
        )
    sql = common.sql_argument(payload.get("tool_name"), payload.get("tool_input"))
    if sql is None:
        # No SQL argument at all (registry-tool form of base_readQuery): nothing to check.
        return "pass", ""
    try:
        check_sql(sql)
    except SqlRejected as exc:
        if guard_mode() == "audit":
            return "audit", exc.reason
        return "deny", deny_reason(exc)
    return "pass", ""


def main() -> int:
    payload, unreadable = common.read_hook_input_ex()
    event = common.hook_event_name(payload, "PreToolUse")
    if unreadable:
        # Input arrived and could not be read as a JSON object. A guard that cannot see the call
        # cannot certify it, so it asks — the same direction as an internal crash below.
        common.emit_ask(
            "hook input could not be parsed, so the guard cannot confirm what this call runs. "
            "Approve only if you know what it does.",
            event,
        )
        return 0
    try:
        decision, reason = decide(payload)
        if decision == "deny":
            common.emit_deny(reason, event)
        elif decision == "ask":
            common.emit_ask(reason, event)
        elif decision == "audit":
            sql = common.sql_argument(payload.get("tool_name"), payload.get("tool_input"))
            # Mask before logging: audit mode is a rollout tool, not a reason to put statement
            # literals on stderr. mask_sql blanks quoted literals and comments (it preserves bare
            # identifiers, so this addresses values, not schema names).
            head = " ".join(common.mask_sql(str(sql)).split())[:200]
            sys.stderr.write(
                f"[teradata-vantage] AUDIT WOULD_BLOCK {GUARDED_TOOL} (reason={reason}): {head}\n"
            )
        return 0
    except Exception as exc:  # noqa: BLE001 — fail to ASK, never to allow
        common.emit_ask(f"read guard crashed: {type(exc).__name__}", event)
        return 0


if __name__ == "__main__":
    sys.exit(main())
