#!/usr/bin/env python3
"""Optional audit logger for the bundled Teradata MCP server (upstream ``HOOKS_MODULE`` contract).

This is an OBSERVER, not a control: the upstream ``ServerHooks`` contract is synchronous, observe-only and
cannot deny or mutate a call (an exception raised here is caught and logged by the server). Every guard the
plugin provides — the read-only SQL guard on ``base_readQuery``, the approval prompt on destructive
``base_writeQuery`` statements and on ``tdvs_destroy`` / ``tdvs_update`` / ``tdvs_*_permission`` /
``bar_manageJob`` — lives in the Claude Code PreToolUse hooks, not here.

Enable it by setting ``TERADATA_MCP_AUDIT_LOG`` (the launcher then passes
``HOOKS_MODULE=${CLAUDE_PLUGIN_ROOT}/scripts/td_server_hooks.py`` to the server). Off by default.

What is written — one JSON object per line, local file only, never transmitted:
  {"ts": "<UTC ISO-8601>", "event": "call" | "error", "tool": "<tool name>", "db_user": "<user or null>",
   "profile": "<profile or null>", "request_id": "<id or null>", "ok": true | false,
   "args": {...redacted...}                 # non-SQL tools: string literals redacted, values truncated
   "sql_sha256": "<hex>", "sql_len": N,     # base_readQuery / base_writeQuery / base_dynamicQuery / execute_tool
   "error_type": "<class>", "error": "<message, string literals redacted, truncated>"}   # event=error only

SQL text is NEVER written — only its sha256 and length. Argument values that look like credentials (keys named
password, token, pat, secret, uri, url, authorization, key) are replaced by "<redacted>". String literals in
other values ('...') are replaced by '<lit>' so predicates such as WHERE customer_name = '...' never leak.

Log location: $TERADATA_MCP_AUDIT_LOG, else $CLAUDE_PLUGIN_DATA/audit/tool-calls.jsonl, else
~/.claude/plugins/data/teradata-vantage/audit/tool-calls.jsonl. Directory 0700, file 0600.

Python 3 standard library only; imports the contract from ``teradata_mcp_server.hooks`` and falls back to a
structurally identical no-op definition so the module imports (and its tests run) without the server installed.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:  # the real contract (teradata-mcp-server >= 0.2.6)
    from teradata_mcp_server.hooks import ServerHooks, ToolCallContext  # type: ignore

    UPSTREAM_CONTRACT = True
except Exception:  # pragma: no cover - exercised only when the server is not installed
    UPSTREAM_CONTRACT = False

    @dataclass
    class ToolCallContext:  # type: ignore[no-redef]
        tool_name: str
        kwargs: dict
        request_context: object
        engine: object
        profile_name: Optional[str]
        db_user: Optional[str]

    @dataclass
    class ServerHooks:  # type: ignore[no-redef]
        on_tool_call: Optional[Callable[[ToolCallContext], None]] = None
        on_tool_result: Optional[Callable[[ToolCallContext, object], None]] = None
        on_tool_error: Optional[Callable[[ToolCallContext, Exception], None]] = None


SQL_TOOLS = frozenset({"base_readQuery", "base_writeQuery", "base_dynamicQuery", "execute_tool"})
SENSITIVE_KEY_RE = re.compile(r"(password|passwd|token|\bpat\b|secret|uri|url|authorization|api_key|\bkey\b)", re.IGNORECASE)
STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
MAX_VALUE_CHARS = 200
MAX_ERROR_CHARS = 300

_lock = threading.Lock()


def audit_log_path() -> Path:
    explicit = os.environ.get("TERADATA_MCP_AUDIT_LOG", "").strip()
    if explicit and explicit.lower() not in ("0", "off", "false", "no"):
        return Path(explicit).expanduser()
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    base = Path(data_dir).expanduser() if data_dir else Path.home() / ".claude" / "plugins" / "data" / "teradata-vantage"
    return base / "audit" / "tool-calls.jsonl"


def audit_enabled() -> bool:
    value = os.environ.get("TERADATA_MCP_AUDIT_LOG", "").strip().lower()
    return bool(value) and value not in ("0", "off", "false", "no")


def redact_text(text: str, limit: int = MAX_VALUE_CHARS) -> str:
    redacted = STRING_LITERAL_RE.sub("'<lit>'", text)
    if len(redacted) > limit:
        redacted = redacted[:limit] + f"…(+{len(redacted) - limit})"
    return redacted


def redact_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (kwargs or {}).items():
        if SENSITIVE_KEY_RE.search(str(key)):
            out[str(key)] = "<redacted>"
        elif isinstance(value, (int, float, bool)) or value is None:
            out[str(key)] = value
        elif isinstance(value, str):
            out[str(key)] = redact_text(value)
        else:
            out[str(key)] = redact_text(json.dumps(value, default=str))
    return out


def sql_fingerprint(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Hash + length of the SQL argument; the text itself is never returned."""
    sql: Any = None
    for key in ("sql", "query", "statement", "arguments", "args"):
        if key in (kwargs or {}):
            sql = kwargs[key]
            break
    if sql is None and kwargs:
        # execute_tool(tool_name=..., **anything): hash the whole payload
        sql = json.dumps(kwargs, sort_keys=True, default=str)
    if sql is None:
        return {"sql_sha256": None, "sql_len": 0}
    text = sql if isinstance(sql, str) else json.dumps(sql, sort_keys=True, default=str)
    return {"sql_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(), "sql_len": len(text)}


def _request_id(ctx: Any) -> Optional[str]:
    rc = getattr(ctx, "request_context", None)
    rid = getattr(rc, "request_id", None) if rc is not None else None
    return str(rid) if rid is not None else None


def build_record(ctx: Any, event: str, error: Optional[BaseException] = None) -> Dict[str, Any]:
    tool = str(getattr(ctx, "tool_name", "") or "")
    kwargs = getattr(ctx, "kwargs", None) or {}
    rec: Dict[str, Any] = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "event": event,
        "tool": tool,
        "db_user": getattr(ctx, "db_user", None),
        "profile": getattr(ctx, "profile_name", None),
        "request_id": _request_id(ctx),
        "ok": error is None,
    }
    if tool in SQL_TOOLS:
        rec.update(sql_fingerprint(kwargs))
        inner = kwargs.get("tool_name") if isinstance(kwargs, dict) else None
        if tool == "execute_tool" and isinstance(inner, str):
            rec["inner_tool"] = inner
    else:
        rec["args"] = redact_kwargs(kwargs if isinstance(kwargs, dict) else {})
    if error is not None:
        rec["error_type"] = type(error).__name__
        rec["error"] = redact_text(str(error), MAX_ERROR_CHARS)
    return rec


def write_record(rec: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or audit_log_path()
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line)


def _safe(fn: Callable[..., None]) -> Callable[..., None]:
    """Never let the audit logger raise into the server (it is caught upstream anyway, but keep the log quiet)."""

    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            try:
                sys.stderr.write(f"[teradata-vantage audit] disabled for this call: {type(exc).__name__}: {exc}\n")
            except Exception:
                pass

    return wrapper


@_safe
def on_tool_call(ctx: ToolCallContext) -> None:
    write_record(build_record(ctx, "call"))


@_safe
def on_tool_error(ctx: ToolCallContext, exc: Exception) -> None:
    write_record(build_record(ctx, "error", exc))


def get_hooks() -> ServerHooks:
    """Entry point required by the server's hook loader (``HOOKS_MODULE``)."""
    if not audit_enabled():
        # HOOKS_MODULE set but the audit variable is off/empty: install nothing.
        return ServerHooks()
    return ServerHooks(on_tool_call=on_tool_call, on_tool_error=on_tool_error)


if __name__ == "__main__":  # manual smoke: python3 td_server_hooks.py  -> prints the record it would write
    demo = ToolCallContext(
        tool_name="base_readQuery", kwargs={"sql": "SELECT * FROM sales_fact WHERE region = 'north'"},
        request_context=None, engine=None, profile_name="tv_all", db_user="analyst",
    )
    print(json.dumps(build_record(demo, "call"), indent=2))
    print(f"log path: {audit_log_path()}  enabled: {audit_enabled()}  upstream contract: {UPSTREAM_CONTRACT}")
