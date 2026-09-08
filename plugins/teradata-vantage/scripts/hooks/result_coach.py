#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure hook: coach the model on oversized results and error codes.

Matcher: ``mcp__.*__(base_readQuery|base_writeQuery|base_tablePreview|qlty_.*|dba_.*|tdvs_.*|
execute_tool)$`` on both events (PostToolUse fires only on success; PostToolUseFailure carries
the server's error). The hook never blocks and never rewrites the result; it only attaches
``additionalContext``:

* result text longer than ``TERADATA_MAX_RESULT_CHARS`` (default 120000) -> a self-correcting
  note asking for a SMALL, aggregated re-run (GROUP BY / WHERE / TOP N);
* a Teradata ``Error NNNN`` / ``[Error NNNN]`` whose code is listed in
  ``scripts/data/error_codes.yaml`` -> "what it means / do this / see skill" from that single
  source. The YAML is parsed by a tiny stdlib reader (no PyYAML) that understands the file's
  list-of-maps format; unknown codes and a missing file are silently ignored.

Fail direction: any internal error -> silent exit 0.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

DEFAULT_MAX_RESULT_CHARS = 120000
DEFAULT_ERROR_CODES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "error_codes.yaml"
)

_ERROR_CODE_RE = re.compile(r"\bError\s+(\d{3,5})\b")

_KEY_ALIASES = {
    "code": ("code", "error_code", "number"),
    "message": ("message", "message_fragment", "fragment", "text"),
    "meaning": ("meaning", "actually_means", "means", "what_it_means"),
    "fix": ("fix", "do_this", "do", "action", "remedy"),
    "skill": ("skill", "skill_link", "see", "see_skill"),
}


# --------------------------------------------------------------------------- tiny YAML reader
def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        inner = v[1:-1]
        if v[0] == '"':
            try:
                return json.loads(v)
            except ValueError:
                return inner
        return inner.replace("''", "'")
    return v


def _strip_comment(line: str) -> str:
    """Remove a trailing ``# comment`` that is not inside quotes."""
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _parse_scalar(value: str) -> Any:
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_unquote(x) for x in inner.split(",") if x.strip()] if inner else []
    return _unquote(v)


def load_error_codes(path: str) -> Dict[str, Dict[str, Any]]:
    """Read ``error_codes.yaml`` into ``{code: {field: value}}`` with a tolerant stdlib parser.

    Accepted shapes: a top-level list of maps (``- code: 3541`` ...), the same list under one
    top-level key (``codes:`` / ``errors:``), or a top-level map keyed by code. Block scalars
    (``|`` / ``>``), inline lists and nested ``- item`` lists are understood; anything else is
    skipped, never raised.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}

    entries: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    entry_indent = -1  # indent of the "- " (or "NNNN:") that opened the current entry
    pending_key: Optional[str] = None  # last key seen with an empty value (nested list parent)
    block_key: Optional[str] = None
    block_indent = -1
    block_lines: List[str] = []
    block_fold = False

    def flush_block() -> None:
        nonlocal block_key, block_lines, block_indent, block_fold
        if current is not None and block_key is not None:
            non_empty = [ln for ln in block_lines if ln.strip()]
            dedent = min((len(ln) - len(ln.lstrip(" ")) for ln in non_empty), default=0)
            body = [ln[dedent:] if ln.strip() else "" for ln in block_lines]
            text = "\n".join(body).strip("\n")
            if block_fold:
                text = " ".join(part.strip() for part in text.split("\n") if part.strip())
            current[block_key] = text.strip()
        block_key, block_lines, block_indent, block_fold = None, [], -1, False

    for raw in lines:
        if block_key is not None:
            indent = len(raw) - len(raw.lstrip(" "))
            if raw.strip() == "" or indent > block_indent:
                block_lines.append(raw)
                continue
            flush_block()

        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if stripped == "-" or stripped.startswith("- "):
            item = stripped[1:].strip()
            if current is not None and pending_key is not None and indent > entry_indent:
                # nested list under the previous key (e.g. false_leads: / related:)
                lst = current.get(pending_key)
                if not isinstance(lst, list):
                    lst = []
                    current[pending_key] = lst
                lst.append(_parse_scalar(item))
                continue
            current = {}
            entries.append(current)
            entry_indent = indent
            pending_key = None
            if not item:
                continue
            stripped = item

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().strip('"').strip("'")
        value = value.strip()

        if indent == 0 and not value:
            # top-level container key ("codes:") OR a map keyed by code ("3541:")
            if key.isdigit():
                current = {"code": key}
                entries.append(current)
                entry_indent = 0
            else:
                current = None
            pending_key = None
            continue

        if current is None:
            continue

        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            block_key = key
            block_indent = indent
            block_fold = value.startswith(">")
            block_lines = []
            pending_key = None
            continue

        if not value:
            pending_key = key
            current[key] = []
            continue

        pending_key = None
        current[key] = _parse_scalar(value)

    flush_block()

    out: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        code = _field(entry, "code")
        if code is None:
            continue
        code_s = str(code).strip()
        if code_s.isdigit():
            out[code_s] = entry
    return out


def _field(entry: Dict[str, Any], logical: str) -> Optional[Any]:
    for alias in _KEY_ALIASES[logical]:
        if alias in entry and entry[alias] not in (None, ""):
            return entry[alias]
    return None


# --------------------------------------------------------------------------- coaching
def truncation_note(total: int, cap: int) -> str:
    return (
        f"[RESULT TRUNCATED: {total:,} characters exceeded the {cap:,}-character cap. A result this large "
        "cannot be reasoned over or charted. Re-run the query so it returns a SMALL, aggregated result "
        "— GROUP BY the dimension you need, add a WHERE filter, or select TOP N — instead of fetching "
        "raw rows.]"
    )


def error_note(code: str, entry: Dict[str, Any]) -> str:
    meaning = _field(entry, "meaning") or "(no description recorded)"
    fix = _field(entry, "fix")
    skill = _field(entry, "skill")
    text = f"Teradata error {code} — what it means: {meaning}"
    if fix:
        text += f" Do this: {fix}"
    if skill:
        skill_s = str(skill)
        if not skill_s.startswith("/"):
            skill_s = "/teradata-vantage:" + skill_s.replace("teradata-vantage:", "")
        text += f" (see {skill_s})"
    return text


def result_text(payload: dict) -> str:
    """The model-facing text of the result (success) or the error (failure)."""
    for key in ("tool_response", "error", "tool_result", "result"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return ""


def max_result_chars() -> int:
    raw = (os.environ.get("TERADATA_MAX_RESULT_CHARS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_RESULT_CHARS
    return value if value > 0 else DEFAULT_MAX_RESULT_CHARS


def coach(payload: dict, codes: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[str]:
    """Return the additionalContext text for this payload, or ``None`` when there is nothing to say."""
    text = result_text(payload)
    if not text:
        return None
    notes: List[str] = []
    cap = max_result_chars()
    if len(text) > cap:
        notes.append(truncation_note(len(text), cap))

    if codes is None:
        codes = load_error_codes(os.environ.get("TERADATA_ERROR_CODES_FILE") or DEFAULT_ERROR_CODES_FILE)
    if codes:
        seen: List[str] = []
        for m in _ERROR_CODE_RE.finditer(text[: cap if len(text) > cap else len(text)]):
            code = m.group(1)
            if code in codes and code not in seen:
                seen.append(code)
                notes.append(error_note(code, codes[code]))
            if len(seen) >= 3:
                break
    return "\n".join(notes) if notes else None


def main() -> int:
    try:
        payload = common.read_hook_input()
        note = coach(payload)
        if note:
            common.emit_context(note, common.hook_event_name(payload, "PostToolUse"))
        return 0
    except Exception:  # noqa: BLE001 — coaching hook: stay silent
        return 0


if __name__ == "__main__":
    sys.exit(main())
