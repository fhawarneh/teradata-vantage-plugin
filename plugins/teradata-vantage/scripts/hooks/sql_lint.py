#!/usr/bin/env python3
"""PostToolUse hook on ``Write|Edit|MultiEdit``: lint an edited Teradata SQL file with sqlfluff.

The extension filter lives here, not in the matcher: only ``.sql .bteq .btq .ddl .dml`` files
are considered; anything else exits silently. ``sqlfluff`` is never bundled or installed —
when it is not on PATH the hook exits 0 with no output (never a nag; ``doctor.sh`` mentions
``pip install sqlfluff``). ``TERADATA_SQL_LINT=off`` disables the hook.

Configuration precedence: walking up from the file's directory to the git root, a sqlfluff
config (``.sqlfluff``, ``setup.cfg``, ``tox.ini``, ``pep8.ini``, ``pyproject.toml``) that sets a
``dialect`` wins — sqlfluff runs bare so the repository's own settings apply. Otherwise the
plugin's ``config/sqlfluff-teradata.cfg`` is passed with ``--dialect teradata``. Nothing is
written into the repository.

``.bteq`` / ``.btq`` files: sqlfluff's teradata dialect knows only a small BTEQ subset, so
``PRS`` (unparsable) findings are dropped for those extensions.

sqlfluff exit codes: 0 clean (silent), 1 violations (up to 15 reported as ``additionalContext``),
2 fatal (silent). 10-second subprocess timeout. Any internal error: silent exit 0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SQL_SUFFIXES = frozenset({".sql", ".bteq", ".btq", ".ddl", ".dml"})
BTEQ_SUFFIXES = frozenset({".bteq", ".btq"})
CONFIG_FILES = (".sqlfluff", "setup.cfg", "tox.ini", "pep8.ini", "pyproject.toml")
MAX_VIOLATIONS = 15
TIMEOUT_SECONDS = 10

_DIALECT_KEY_RE = re.compile(r"^\s*dialect\s*=", re.MULTILINE)

# Two dialect slips models reliably make in files; mechanical, appended to the sqlfluff findings.
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_NULL_EQ_RE = re.compile(r"(?:=|!=|<>)\s*NULL\b", re.IGNORECASE)


def plugin_root() -> str:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if common.is_set(root):
        return root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def lint_enabled() -> bool:
    raw = (os.environ.get("TERADATA_SQL_LINT") or "").strip().lower()
    return raw not in ("off", "0", "false", "no")


def is_sql_file(path: Optional[str]) -> bool:
    if not isinstance(path, str) or not path:
        return False
    return os.path.splitext(path)[1].lower() in SQL_SUFFIXES


def _has_dialect(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return False
    name = os.path.basename(path)
    if name == ".sqlfluff":
        return bool(_DIALECT_KEY_RE.search(text))
    if name == "pyproject.toml":
        return "tool.sqlfluff" in text and bool(_DIALECT_KEY_RE.search(text))
    return "[sqlfluff" in text and bool(_DIALECT_KEY_RE.search(text))


#: sqlfluff config keys that make the linter LOAD AND RUN Python from the repository being linted.
#: `library_path` and `load_macros_from_path` are jinja-templater settings that import modules from a
#: path in the repo; a templater other than `raw` is what activates them. Linting a hostile checkout
#: would then execute its code on the first `.sql` write, with no prompt.
_EXEC_KEY_RE = re.compile(
    r"^\s*(library_path|load_macros_from_path|loader_search_path|apply_dbt_builtins)\s*=", re.IGNORECASE | re.MULTILINE
)
_TEMPLATER_RE = re.compile(r"^\s*templater\s*=\s*([A-Za-z0-9_.-]+)", re.IGNORECASE | re.MULTILINE)


def config_can_execute_code(path: str) -> bool:
    """True when a discovered sqlfluff config could make the linter import code from its own tree."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return True  # unreadable: assume the worst rather than run under it
    if _EXEC_KEY_RE.search(text):
        return True
    for m in _TEMPLATER_RE.finditer(text):
        if m.group(1).strip().lower() != "raw":
            return True
    return False


def find_user_config(start_dir: str) -> Optional[str]:
    """Walk up from ``start_dir`` to the git root (or filesystem root) for a config with a dialect."""
    cur = os.path.abspath(start_dir)
    while True:
        for name in CONFIG_FILES:
            candidate = os.path.join(cur, name)
            if os.path.isfile(candidate) and _has_dialect(candidate):
                return candidate
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(os.path.join(cur, ".git")):
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def build_command(file_path: str, sqlfluff_bin: str) -> Optional[List[str]]:
    """The sqlfluff invocation, or ``None`` when linting this file would run the repo's own code.

    Honouring a project's own sqlfluff config is the point of the bare-run branch: the project's dialect
    and rule selection win over the plugin's. But sqlfluff resolves `library_path` and friends RELATIVE
    TO THAT CONFIG and imports them, so a repository can execute arbitrary Python the first time a `.sql`
    file is written. When the discovered config carries such a key — or any templater other than `raw` —
    the hook declines to lint rather than run under it, and says so once.
    """
    cmd = [sqlfluff_bin, "lint", "--format", "json", "--disable-progress-bar"]
    user_config = find_user_config(os.path.dirname(os.path.abspath(file_path)))
    if user_config is None:
        cmd += ["--config", os.path.join(plugin_root(), "config", "sqlfluff-teradata.cfg"), "--dialect", "teradata"]
    elif config_can_execute_code(user_config):
        return None
    cmd.append(file_path)
    return cmd


def parse_violations(stdout: str, drop_unparsable: bool) -> List[Dict[str, Any]]:
    try:
        data = json.loads(stdout) if stdout.strip() else []
    except ValueError:
        return []
    out: List[Dict[str, Any]] = []
    entries = data if isinstance(data, list) else [data]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for v in entry.get("violations") or []:
            if not isinstance(v, dict):
                continue
            code = str(v.get("code") or "")
            if drop_unparsable and code.upper() == "PRS":
                continue
            out.append(
                {
                    "line": v.get("start_line_no", v.get("line_no")),
                    "col": v.get("start_line_pos", v.get("line_pos")),
                    "code": code,
                    "description": str(v.get("description") or v.get("name") or "").strip(),
                }
            )
    return out


def dialect_hints(file_path: str) -> List[str]:
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            text = common.mask_sql(fh.read())
    except OSError:
        return []
    hints: List[str] = []
    if _LIMIT_RE.search(text):
        hints.append("Teradata has no LIMIT clause — use SELECT TOP n (or SAMPLE n).")
    if _NULL_EQ_RE.search(text):
        hints.append("`col = NULL` / `<> NULL` is always UNKNOWN and matches no row — use IS NULL / IS NOT NULL.")
    return hints


def format_context(file_path: str, violations: List[Dict[str, Any]], hints: List[str]) -> str:
    shown = violations[:MAX_VIOLATIONS]
    lines = [f"sqlfluff (teradata dialect) found {len(violations)} issue(s) in {os.path.basename(file_path)}:"]
    for v in shown:
        loc = f"L{v['line']}:C{v['col']}" if v.get("line") is not None else "L?"
        lines.append(f"- {loc} {v['code']} {v['description']}".rstrip())
    if len(violations) > len(shown):
        lines.append(f"- ... {len(violations) - len(shown)} more not shown")
    lines.extend(f"- {h}" for h in hints)
    return "\n".join(lines)


def run(payload: dict) -> Optional[str]:
    """Return the additionalContext text, or ``None`` when there is nothing to report."""
    if not lint_enabled():
        return None
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not is_sql_file(file_path) or not os.path.isfile(file_path):
        return None
    sqlfluff_bin = shutil.which("sqlfluff")
    if not sqlfluff_bin:
        return None
    cmd = build_command(file_path, sqlfluff_bin)
    if cmd is None:
        return (
            "teradata-vantage: skipped the sqlfluff lint of this file. The sqlfluff config that governs it "
            "sets a templater other than `raw`, or a `library_path` / `load_macros_from_path` key, either of "
            "which makes sqlfluff import Python from this repository. Lint it yourself if you trust the "
            "checkout, or set TERADATA_SQL_LINT=off to stop seeing this."
        )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 1:
        return None  # 0 = clean, 2 = fatal (config/parse crash): both silent
    suffix = os.path.splitext(file_path)[1].lower()
    violations = parse_violations(proc.stdout, drop_unparsable=suffix in BTEQ_SUFFIXES)
    if not violations:
        return None
    return format_context(file_path, violations, dialect_hints(file_path))


def main() -> int:
    try:
        payload = common.read_hook_input()
        text = run(payload)
        if text:
            common.emit_context(text, common.hook_event_name(payload, "PostToolUse"))
        return 0
    except Exception:  # noqa: BLE001 — lint hook: stay silent
        return 0


if __name__ == "__main__":
    sys.exit(main())
