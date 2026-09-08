"""Shared helpers for the teradata-vantage hook scripts.

Every hook in scripts/hooks/ is a small Python 3 (3.10+) program that reads one JSON
document from stdin (the Claude Code hook input), decides, and writes at most one JSON
document to stdout. This module holds the pieces they share:

* reading the hook input;
* resolving the LOGICAL tool name and its SQL argument — tools from the bundled server
  arrive as ``mcp__plugin_teradata-vantage_teradata__base_readQuery``, from a project-level
  server as ``mcp__teradata__base_readQuery``, and through progressive disclosure as
  ``execute_tool`` with the real name nested in ``tool_input.tool_name``;
* the project-relevance gate ``session_is_teradata()`` — hooks that fire on generic events
  (Bash, UserPromptSubmit) must return early unless a Teradata connection is configured;
* the JSON emitters for ``deny`` / ``ask`` and ``additionalContext``;
* the offset-preserving SQL masker used by both the read guard and the write gate.

Nothing here prints a credential, a connection string, or the SQL text.
Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional, Tuple

__all__ = [
    "PLUGIN_DATA_DEFAULT",
    "SESSION_ENV_VARS",
    "emit_ask",
    "emit_context",
    "emit_deny",
    "emit_json",
    "env_flag",
    "hook_event_name",
    "is_set",
    "logical_tool",
    "mask_sql",
    "plugin_data_dir",
    "read_hook_input",
    "read_hook_input_ex",
    "session_is_teradata",
    "sql_argument",
    "tool_suffix",
]

#: Where the connect helper writes ``teradata.env`` when CLAUDE_PLUGIN_DATA is not exported.
PLUGIN_DATA_DEFAULT = os.path.join("~", ".claude", "plugins", "data", "teradata-vantage")

#: Any one of these being set (non-empty, not a literal ``${...}`` placeholder) marks the
#: session as a Teradata session for the relevance gate.
SESSION_ENV_VARS = (
    "DATABASE_URI",
    "TERADATA_MCP_URL",
    "CLAUDE_PLUGIN_OPTION_DATABASE_URI",
    "CLAUDE_PLUGIN_OPTION_MCP_URL",
)


# --------------------------------------------------------------------------- input
def read_hook_input_ex(stream=None) -> Tuple[dict, bool]:
    """Parse the hook JSON from stdin as ``(payload, unreadable)``.

    ``unreadable`` is True when input ARRIVED but could not be read as a JSON object — malformed text,
    or valid JSON that is not a mapping (``[]`` collapses to ``{}`` exactly like garbage does). Genuinely
    empty stdin is NOT unreadable: there is simply nothing to check.

    The two SQL guards use this to fail to ASK, because a guard that cannot read its input cannot know
    the statement is safe, and one that silently passes has gone inert without saying so. The coaching
    hooks keep :func:`read_hook_input` and their documented fail-silent behaviour.
    """
    stream = stream or sys.stdin
    try:
        raw = stream.read()
    except Exception:  # noqa: BLE001 — a closed stdin must not crash the hook
        return {}, False
    if not raw or not raw.strip():
        return {}, False
    try:
        data = json.loads(raw)
    except ValueError:
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    return data, False


def read_hook_input(stream=None) -> dict:
    """Parse the hook JSON from stdin. Returns ``{}`` on empty or malformed input."""
    return read_hook_input_ex(stream)[0]


def hook_event_name(payload: dict, default: str) -> str:
    """The event name Claude Code put in the input, or ``default`` when absent."""
    name = payload.get("hook_event_name") if isinstance(payload, dict) else None
    return name if isinstance(name, str) and name else default


# --------------------------------------------------------------------------- tool naming
def tool_suffix(tool_name: Any) -> str:
    """Suffix after the last ``__`` — ``mcp__<server>__base_readQuery`` -> ``base_readQuery``.

    A name without ``__`` (``Bash``, ``Write``) is returned unchanged.
    """
    if not isinstance(tool_name, str):
        return ""
    return tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name


def logical_tool(tool_name: Any, tool_input: Any) -> Tuple[str, dict]:
    """Resolve the LOGICAL tool and its argument mapping.

    Returns ``(name, args)``. For ``execute_tool`` (progressive disclosure) the real tool is
    ``tool_input.tool_name`` and its arguments are ``tool_input.arguments``; for every other
    tool the suffix is the name and ``tool_input`` itself carries the arguments.
    """
    args = tool_input if isinstance(tool_input, dict) else {}
    name = tool_suffix(tool_name)
    if name == "execute_tool":
        nested = args.get("tool_name")
        nested_args = args.get("arguments")
        name = nested if isinstance(nested, str) else ""
        args = nested_args if isinstance(nested_args, dict) else {}
    return name, args


def sql_argument(tool_name: Any, tool_input: Any) -> Optional[Any]:
    """The SQL text handed to the tool (``sql``), through ``execute_tool`` if needed.

    Returns ``None`` when the call carries no ``sql`` argument at all (for example a
    ``base_readQuery`` call that names a registry tool instead).
    """
    _, args = logical_tool(tool_name, tool_input)
    return args.get("sql")



def tool_input_drifted(tool_name: Any, tool_input: Any) -> bool:
    """True when a guarded tool's arguments arrive in a shape the guards cannot read.

    A dict is a shape we understand -- even an empty one, and even one without ``sql``:
    a registry-tool call through ``base_readQuery`` legitimately carries no SQL. A
    NON-dict ``tool_input`` is not something the documented contract produces. It means
    the payload shape changed under us, and a guard that silently passes there has gone
    inert without saying so. The guards ask instead, so the condition is visible.
    """
    if tool_input is None:
        return False
    if isinstance(tool_input, dict):
        if tool_suffix(tool_name) == "execute_tool":
            nested = tool_input.get("arguments")
            return nested is not None and not isinstance(nested, dict)
        return False
    return True

# --------------------------------------------------------------------------- relevance gate
def is_set(value: Any) -> bool:
    """True for a non-empty string that is not an unexpanded ``${VAR}`` placeholder."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return False
    if v.startswith("${") and v.endswith("}"):
        return False
    return True


def plugin_data_dir() -> str:
    """``CLAUDE_PLUGIN_DATA`` when set, else the per-plugin default.

    Claude Code exports ``CLAUDE_PLUGIN_DATA`` to hook and MCP-server processes but not to a Bash
    tool call, so anything the setup skill runs through Bash lands here. Under ``--plugin-dir`` the
    directory is named ``<plugin>-inline``; probing for it keeps a Bash-invoked ``doctor.sh`` from
    reporting a different state than the hooks and the launcher see (measured 2026-09-08).
    """
    data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if is_set(data):
        return data
    default = os.path.expanduser(PLUGIN_DATA_DEFAULT)
    inline = default + "-inline"
    if not os.path.isdir(default) and os.path.isdir(inline):
        return inline
    return default


def session_is_teradata() -> bool:
    """Project-relevance gate: is a Teradata connection configured for this session?

    True when any of :data:`SESSION_ENV_VARS` is set, or when the credential file written
    by ``scripts/teradata-vantage-connect`` exists at ``<plugin data>/teradata.env``.
    Only names are inspected; no value is read or printed.
    """
    for key in SESSION_ENV_VARS:
        if is_set(os.environ.get(key, "")):
            return True
    try:
        return os.path.isfile(os.path.join(plugin_data_dir(), "teradata.env"))
    except Exception:  # noqa: BLE001 — an odd HOME must not crash a gate
        return False


def env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean-ish environment variable (1/true/yes/on)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- emitters
def emit_json(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _emit_permission(decision: str, reason: str, event: str) -> None:
    emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }
    )


def emit_deny(reason: str, event: str = "PreToolUse") -> None:
    """Block the tool call; ``reason`` is shown to the model so it can self-correct."""
    _emit_permission("deny", reason, event)


def emit_ask(reason: str, event: str = "PreToolUse") -> None:
    """Route the tool call through the permission prompt with ``reason`` as the explanation."""
    _emit_permission("ask", reason, event)


def emit_context(text: str, event: str) -> None:
    """Attach ``text`` to the conversation as ``additionalContext`` for ``event``."""
    emit_json({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


# --------------------------------------------------------------------------- SQL masking
# Every character a SQL parser may treat as end-of-line. A `--` comment terminates at ANY
# of these; restricting to "\n" was a real bypass: a bare CR left the masker erasing the
# rest of the statement while the database still parsed it.
LINE_TERMINATORS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def mask_sql(sql: str) -> str:
    """Blank out comments and quoted spans, preserving character offsets and length.

    The interiors of ``'…'`` (string literals) and ``"…"`` (quoted identifiers) become
    spaces — the quote characters stay so the statement keeps its shape. Doubled quotes
    (``''`` / ``""``) escape. ``--`` comments are erased up to (not including) the next line
    terminator; ``/* */`` comments are erased whole. Everything downstream (statement start,
    ``;`` scan, verb scan) runs on the masked text, so ``WHERE note = 'please drop this'``
    and a column named ``"drop"`` never trip a verb rule.
    """
    out = list(sql)
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]

        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # doubled quote = escaped
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1  # past the closing quote (or past the end if unterminated)
            continue

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = i + 2
            while j < n and sql[j] not in LINE_TERMINATORS:
                j += 1
            for k in range(i, j):
                out[k] = " "
            i = j
            continue

        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
            continue

        i += 1
    return "".join(out)
