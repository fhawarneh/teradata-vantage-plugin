#!/usr/bin/env python3
"""PreToolUse hook: approval prompt before a destructive ``base_writeQuery`` statement.

Matcher: ``mcp__.*__(base_writeQuery|execute_tool)$``. ``base_writeQuery`` exists precisely
to write, so it is never held read-only. Instead this gate raises the permission prompt (an
``ask`` decision) when the statement would mutate or destroy state, and tells the operator
the blast radius: the target object, whether a DELETE/UPDATE has no WHERE clause, and each
destructive part of a compound statement.

Classification (mechanical, on the statement with comments and quoted spans masked):

* a statement that STARTS with ``DROP FOREIGN TABLE`` is exempt — it is metadata-only, the
  object-store data is untouched. The exemption is per STATEMENT, never per call: a compound
  string that opens with ``DROP FOREIGN TABLE`` is still classified statement by statement;
* ``DELETE DROP TRUNCATE INSERT UPDATE MERGE ALTER MODIFY RENAME REPLACE GIVE``, ``CREATE DATABASE``,
  ``CREATE USER``, ``GRANT``, ``REVOKE``, ``ABORT SESSION`` -> ``ask``;
* everything else (CREATE TABLE / VIEW / AUTHORIZATION, COLLECT STATISTICS, WRITE_NOS,
  CREATE FOREIGN TABLE, ...) passes with no output — the normal permission flow applies.

``TERADATA_ALLOW_WRITES=0`` denies every ``base_writeQuery`` call. An internal crash asks.
Guards are mistake-prevention, not a security boundary: the database's own privileges are.

Through ``execute_tool`` the gate also asks before the always-prompt tools
(``tdvs_destroy``, ``tdvs_update``, ``tdvs_*_permission``, ``bar_manageJob``), because the
dedicated destructive-tool matcher cannot see a name nested inside ``execute_tool``.
"""

from __future__ import annotations

import os
import re
import sys
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

WRITE_TOOL = "base_writeQuery"

#: Tools that always prompt; mirrors ``destructive_tools`` in scripts/data/mcp_tools.yaml.
#: Must stay in step with the destructive-tool matcher in hooks/hooks.json. That matcher covers a DIRECT
#: call; a progressive-disclosure call arrives as ``execute_tool`` with the real name nested in the
#: arguments, and lands HERE instead. A name present there but missing here is a silent bypass.
ALWAYS_ASK_TOOLS = frozenset(
    {
        "tdvs_destroy",
        "tdvs_update",
        "tdvs_revoke_user_permission",
        "tdvs_grant_user_permission",
        "bar_manageJob",
        "base_saveDDL",
        "sql_Execute_Full_Pipeline",
        "rag_Execute_Workflow",
    }
)

# Object-type words that may sit between the verb and the object name.
_TYPE_WORDS = {
    "TABLE", "VIEW", "DATABASE", "USER", "MACRO", "PROCEDURE", "FUNCTION", "INDEX",
    "TRIGGER", "ROLE", "PROFILE", "AUTHORIZATION", "FOREIGN", "ZONE", "HASH", "JOIN",
    "STATISTICS", "STATS", "TEMPORARY", "VOLATILE", "MULTISET", "SET", "GLOBAL", "TYPE",
    "METHOD", "CAST", "ORDERING", "TRANSFORM", "GLOP", "REPLICATION", "GROUP", "CONSTRAINT",
    "SPECIFIC", "EXTERNAL", "SERVER", "MAP", "SCHEMA", "DATALAKE", "IF", "EXISTS",
    "INTO", "FROM", "ALL", "ON", "TO", "SESSION",
}

_IDENT = r'(?:"[^"]*"|[A-Za-z_][\w$#]*)(?:\.(?:"[^"]*"|[A-Za-z_][\w$#]*))*'

_STARTS_DROP_FOREIGN_RE = re.compile(r"^\s*DROP\s+FOREIGN\s+TABLE\b", re.IGNORECASE)

# (label, regex that must hit, needs-WHERE flag)
_RULES: List[Tuple[str, "re.Pattern[str]", bool]] = [
    ("DELETE", re.compile(r"\bDEL(?:ETE)?\b", re.IGNORECASE), True),
    ("UPDATE", re.compile(r"\bUPD(?:ATE)?\b", re.IGNORECASE), True),
    ("DROP", re.compile(r"\bDROP\b", re.IGNORECASE), False),
    ("TRUNCATE", re.compile(r"\bTRUNCATE\b", re.IGNORECASE), False),
    ("INSERT", re.compile(r"\bINS(?:ERT)?\b", re.IGNORECASE), False),
    ("MERGE", re.compile(r"\bMERGE\b", re.IGNORECASE), False),
    ("ALTER", re.compile(r"\bALTER\b", re.IGNORECASE), False),
    ("MODIFY", re.compile(r"\bMODIFY\b", re.IGNORECASE), False),
    ("CREATE DATABASE", re.compile(r"\bCREATE\s+DATABASE\b", re.IGNORECASE), False),
    ("CREATE USER", re.compile(r"\bCREATE\s+USER\b", re.IGNORECASE), False),
    ("GRANT", re.compile(r"\bGRANT\b", re.IGNORECASE), False),
    ("REVOKE", re.compile(r"\bREVOKE\b", re.IGNORECASE), False),
    ("ABORT SESSION", re.compile(r"\bABORT\s+SESSION\b", re.IGNORECASE), False),
    # sql_read_guard already treats RENAME and REPLACE as writes; the write gate waved all three through.
    ("RENAME", re.compile(r"\bRENAME\b", re.IGNORECASE), False),
    # Anchored on the object-type word so the OREPLACE() string function cannot match.
    (
        "REPLACE",
        re.compile(
            r"\bREPLACE\s+(?:RECURSIVE\s+)?"
            r"(?:VIEW|MACRO|PROCEDURE|FUNCTION|TRIGGER|METHOD|TYPE|CAST|AUTHORIZATION)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    # GIVE transfers ownership of a database, and its space, to another user.
    ("GIVE", re.compile(r"\bGIVE\b", re.IGNORECASE), False),
]


def normalize(sql: str) -> str:
    """Collapse whitespace and uppercase (used for the per-statement head check)."""
    return " ".join(sql.split()).upper()


def split_statements(masked: str) -> List[str]:
    """Split on ``;`` (comments and literals are already masked) and drop empty parts."""
    return [part for part in masked.split(";") if part.strip()]


def _object_after(masked: str, match_end: int) -> Optional[str]:
    """First identifier after ``match_end`` that is not an object-type keyword."""
    tail = masked[match_end:]
    for m in re.finditer(_IDENT + r"|\S+", tail):
        tok = m.group(0)
        if not re.match(_IDENT + r"$", tok):
            return None  # punctuation such as "(" before any name
        if tok.upper() in _TYPE_WORDS:
            continue
        return tok
    return None


def _target_for(label: str, masked: str, hit: "re.Match[str]") -> Optional[str]:
    if label == "GRANT" or label == "REVOKE":
        on = re.search(r"\bON\s+(" + _IDENT + r")", masked[hit.end():], re.IGNORECASE)
        who = re.search(r"\b(?:TO|FROM)\s+(" + _IDENT + r")", masked[hit.end():], re.IGNORECASE)
        parts = []
        if on:
            parts.append(on.group(1))
        if who:
            parts.append(f"{'to' if label == 'GRANT' else 'from'} {who.group(1)}")
        return " ".join(parts) or None
    if label == "ABORT SESSION":
        sess = re.search(r"\bABORT\s+SESSION\s+([^\s;]+(?:\s*,\s*[^\s;]+)*)", masked, re.IGNORECASE)
        return sess.group(1).strip() if sess else None
    return _object_after(masked, hit.end())


def describe_statement(stmt: str) -> Optional[str]:
    """One human-readable line for a destructive statement, or ``None`` when it is not one.

    ``stmt`` is a single masked statement (no ``;``). The verb that appears EARLIEST in the
    statement names it (so ``MERGE ... WHEN MATCHED THEN UPDATE`` is a MERGE, not an UPDATE);
    ties fall back to :data:`_RULES` order.
    """
    if not stmt.strip():
        return None
    if _STARTS_DROP_FOREIGN_RE.match(stmt):
        return None  # metadata-only exemption
    best = None
    for label, rx, needs_where in _RULES:
        hit = rx.search(stmt)
        if hit and (best is None or hit.start() < best[2].start()):
            best = (label, needs_where, hit)
    if best is None:
        return None
    label, needs_where, hit = best
    target = _target_for(label, stmt, hit)
    text = f"{label} on {target}" if target else label
    if needs_where and not re.search(r"\bWHERE\b", stmt, re.IGNORECASE):
        text += f" with NO WHERE clause — {label} affects EVERY row"
    return text


def classify(sql: str) -> Tuple[List[str], int]:
    """Return ``(destructive_parts, non_destructive_count)`` for a (possibly compound) SQL string."""
    masked = common.mask_sql(sql)
    parts: List[str] = []
    others = 0
    for stmt in split_statements(masked):
        line = describe_statement(stmt)
        if line:
            parts.append(line)
        else:
            others += 1
    return parts, others


def ask_reason(parts: List[str], others: int) -> str:
    plural = "s" if len(parts) != 1 else ""
    reason = f"{WRITE_TOOL} will run {len(parts)} destructive statement{plural}: " + "; ".join(parts) + "."
    if others:
        plural_o = "" if others == 1 else "s"
        reason += f" It also carries {others} non-destructive statement{plural_o} that run{'s' if others == 1 else ''} with it."
    reason += " Confirm the target object is the intended one. (TERADATA_ALLOW_WRITES=0 disables writes entirely.)"
    return reason


def writes_allowed() -> bool:
    raw = (os.environ.get("TERADATA_ALLOW_WRITES") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def decide(payload: dict) -> Tuple[str, str]:
    """Return ``(decision, reason)`` where decision is ``pass`` / ``ask`` / ``deny``."""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    name, args = common.logical_tool(tool_name, tool_input)

    if name in ALWAYS_ASK_TOOLS:
        # Reached only via execute_tool (the direct matcher is destructive_tool_gate.py).
        obj = (
            args.get("vs_name")
            or args.get("job_name")
            or args.get("operation")
            or args.get("output_dir")
            or args.get("database_name")
            or args.get("table_name")
            or "unnamed object"
        )
        return "ask", f"{name} changes or destroys state (target: {obj}). Confirm before it runs."

    if name != WRITE_TOOL:
        return "pass", ""

    if common.tool_input_drifted(tool_name, tool_input):
        return "ask", (
            f"write gate cannot read the arguments of this {WRITE_TOOL} call "
            "(unrecognised tool_input shape), so it cannot tell whether the statement is "
            "destructive. Approve only if you know what it runs."
        )

    if not writes_allowed():
        return "deny", (
            f"{WRITE_TOOL} is disabled in this session (TERADATA_ALLOW_WRITES=0). "
            "Use base_readQuery for reads."
        )

    sql = args.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return "pass", ""

    # NEVER exempt a whole call because its FIRST statement is exempt: the metadata-only
    # exemption is applied PER STATEMENT in describe_statement(), so every later DELETE /
    # DROP / TRUNCATE in the same string is still classified and still prompts.
    parts, others = classify(sql)
    if parts:
        return "ask", ask_reason(parts, others)
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
        if decision == "ask":
            common.emit_ask(reason, event)
        elif decision == "deny":
            common.emit_deny(reason, event)
        return 0
    except Exception as exc:  # noqa: BLE001 — fail to ASK, never to allow
        common.emit_ask(f"write gate crashed: {type(exc).__name__}", event)
        return 0


if __name__ == "__main__":
    sys.exit(main())
