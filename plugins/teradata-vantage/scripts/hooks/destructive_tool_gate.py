#!/usr/bin/env python3
"""PreToolUse hook: always prompt before a state-changing vector-store or backup tool.

Matcher: ``mcp__.*__(tdvs_destroy|tdvs_update|tdvs_revoke_user_permission|
tdvs_grant_user_permission|bar_manageJob|base_saveDDL|sql_Execute_Full_Pipeline|
rag_Execute_Workflow)$``. The tool NAME is the classification (the
frozen ``destructive_tools`` list in scripts/data/mcp_tools.yaml); there is no argument that
makes these calls safe, so the decision is an unconditional ``ask`` whose reason names the
object from the arguments: ``vs_name`` / ``user_name`` / ``permission`` (vector store) or
``operation`` / ``job_name`` (DSA job). An internal crash asks as well.
"""

from __future__ import annotations

import os
import sys
from typing import Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

_EFFECT = {
    "tdvs_destroy": "DESTROYS the vector store and its indexed content",
    "tdvs_update": "changes the vector store definition (may re-embed or drop content)",
    "tdvs_revoke_user_permission": "removes a user's access to the vector store",
    "tdvs_grant_user_permission": "gives a user access to the vector store",
    "bar_manageJob": "creates, runs, aborts or deletes a DSA backup/restore job",
    "base_saveDDL": (
        "WRITES a .sql file to a directory on the machine running the MCP server "
        "(upstream annotates it read-only, but it creates files); base_tableDDL reads DDL without writing"
    ),
    "sql_Execute_Full_Pipeline": "DROPS and re-creates the query-clustering tables in the feature database",
    "rag_Execute_Workflow": "creates and INSERTs into the query table, then DROPS and re-creates the query-embedding table",
}


def _fmt(args: dict, key: str, label: str) -> str:
    value = args.get(key)
    if value is None or value == "":
        return ""
    return f"{label}={value}"


def describe(payload: dict) -> Tuple[str, str]:
    """Return ``(logical_tool_name, reason)``."""
    name, args = common.logical_tool(payload.get("tool_name"), payload.get("tool_input"))
    name = name or common.tool_suffix(payload.get("tool_name")) or "this tool"
    details = [
        s
        for s in (
            _fmt(args, "vs_name", "vector store"),
            _fmt(args, "user_name", "user"),
            _fmt(args, "permission", "permission"),
            _fmt(args, "operation", "operation"),
            _fmt(args, "job_name", "job"),
            _fmt(args, "output_dir", "output directory"),
            _fmt(args, "database_name", "database"),
            _fmt(args, "table_name", "object"),
        )
        if s
    ]
    effect = _EFFECT.get(name, "changes or destroys state")
    target = "; ".join(details) if details else "target not named in the arguments"
    reason = f"{name} {effect} ({target}). Confirm before it runs; this cannot be undone from the chat."
    return name, reason


def main() -> int:
    payload = common.read_hook_input()
    event = common.hook_event_name(payload, "PreToolUse")
    try:
        _, reason = describe(payload)
        common.emit_ask(reason, event)
        return 0
    except Exception as exc:  # noqa: BLE001 — fail to ASK
        common.emit_ask(f"destructive tool gate crashed: {type(exc).__name__}", event)
        return 0


if __name__ == "__main__":
    sys.exit(main())
