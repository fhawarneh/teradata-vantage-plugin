"""Tests for scripts/hooks/sql_lint.py — optional sqlfluff lint of edited Teradata SQL files.

sqlfluff is NOT required to run these tests: the subprocess is monkeypatched with canned JSON
in the shape ``sqlfluff lint --format json`` produces, and the "sqlfluff missing" path is
covered by patching ``shutil.which``. Fixtures are synthetic and leak-free.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

import sql_lint as lint
from conftest import FIXTURES_DIR, context_of, run_hook

CLEAN_SQL = os.path.join(FIXTURES_DIR, "clean.sql")
LIMIT_NULL_SQL = os.path.join(FIXTURES_DIR, "limit_null.sql")
SAMPLE_BTEQ = os.path.join(FIXTURES_DIR, "sample.bteq")


def write_payload(file_path: str, tool: str = "Write") -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": {"file_path": file_path, "content": ""}}


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def canned(violations, path=LIMIT_NULL_SQL):
    return json.dumps([{"filepath": path, "violations": violations}])


VIOLATIONS = [
    {"start_line_no": 4, "start_line_pos": 7, "code": "PRS", "description": "Line 6, Position 1: Found unparsable section: 'LIMIT 10'", "name": ""},
    {"start_line_no": 2, "start_line_pos": 1, "code": "LT01", "description": "Expected single whitespace.", "name": "layout.spacing"},
    {"line_no": 3, "line_pos": 1, "code": "CP01", "description": "Keywords must be consistently upper case.", "name": "capitalisation.keywords"},
]


# --------------------------------------------------------------------------- filters
@pytest.mark.parametrize("path,expected", [
    ("a.sql", True), ("A.SQL", True), ("x/y.bteq", True), ("y.btq", True), ("z.ddl", True), ("w.dml", True),
    ("a.py", False), ("README.md", False), ("", False), (None, False), ("noext", False),
])
def test_suffix_filter(path, expected):
    assert lint.is_sql_file(path) is expected


def test_non_sql_file_exits_silently_without_touching_sqlfluff(monkeypatch):
    called = []
    monkeypatch.setattr(shutil, "which", lambda name: called.append(name) or "/usr/bin/sqlfluff")
    assert lint.run(write_payload(__file__)) is None
    assert called == []


def test_skips_when_sqlfluff_is_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a) or FakeProc(1, canned(VIOLATIONS)))
    assert lint.run(write_payload(LIMIT_NULL_SQL)) is None
    assert ran == []


def test_lint_off_switch(monkeypatch):
    monkeypatch.setenv("TERADATA_SQL_LINT", "off")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(1, canned(VIOLATIONS)))
    assert lint.run(write_payload(LIMIT_NULL_SQL)) is None


# --------------------------------------------------------------------------- config discovery
def test_plugin_config_is_used_when_the_repo_has_none(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin/root")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    f = repo / "q.sql"
    f.write_text("SELECT 1;\n")
    cmd = lint.build_command(str(f), "/usr/bin/sqlfluff")
    assert cmd[:5] == ["/usr/bin/sqlfluff", "lint", "--format", "json", "--disable-progress-bar"]
    assert "--config" in cmd and cmd[cmd.index("--config") + 1] == "/plugin/root/config/sqlfluff-teradata.cfg"
    assert cmd[cmd.index("--dialect") + 1] == "teradata"
    assert cmd[-1] == str(f)


@pytest.mark.parametrize("name,body", [
    (".sqlfluff", "[sqlfluff]\ndialect = snowflake\n"),
    ("setup.cfg", "[flake8]\nmax-line-length = 100\n\n[sqlfluff]\ndialect = ansi\n"),
    ("tox.ini", "[sqlfluff]\ndialect = teradata\n"),
    ("pyproject.toml", "[tool.sqlfluff.core]\ndialect = \"postgres\"\n"),
])
def test_user_config_with_a_dialect_wins_and_sqlfluff_runs_bare(monkeypatch, tmp_path, name, body):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin/root")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / name).write_text(body)
    sub = repo / "models" / "deep"
    sub.mkdir(parents=True)
    f = sub / "q.sql"
    f.write_text("SELECT 1;\n")
    assert lint.find_user_config(str(sub)) == str(repo / name)
    cmd = lint.build_command(str(f), "/usr/bin/sqlfluff")
    assert "--config" not in cmd and "--dialect" not in cmd


def test_config_without_a_dialect_key_does_not_count(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".sqlfluff").write_text("[sqlfluff]\nmax_line_length = 120\n")
    (repo / "pyproject.toml").write_text("[tool.black]\nline-length = 100\n")
    assert lint.find_user_config(str(repo)) is None


def test_walk_stops_at_the_git_root(tmp_path):
    outer = tmp_path / "outer"
    repo = outer / "repo"
    (repo / ".git").mkdir(parents=True)
    (outer / ".sqlfluff").write_text("[sqlfluff]\ndialect = ansi\n")  # above the repo: must be ignored
    assert lint.find_user_config(str(repo)) is None


# --------------------------------------------------------------------------- output
def test_violations_are_reported_with_dialect_hints(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["timeout"] = kwargs.get("timeout")
        return FakeProc(1, canned(VIOLATIONS))

    monkeypatch.setattr(subprocess, "run", fake_run)
    text = lint.run(write_payload(LIMIT_NULL_SQL))
    assert seen["timeout"] == 10
    assert seen["cmd"][-1] == LIMIT_NULL_SQL
    assert text.startswith("sqlfluff (teradata dialect) found 3 issue(s) in limit_null.sql:")
    assert "- L4:C7 PRS Line 6, Position 1: Found unparsable section: 'LIMIT 10'" in text
    assert "- L2:C1 LT01 Expected single whitespace." in text
    assert "- L3:C1 CP01 Keywords must be consistently upper case." in text  # legacy line_no keys
    assert "Teradata has no LIMIT clause — use SELECT TOP n (or SAMPLE n)." in text
    assert "use IS NULL / IS NOT NULL" in text


def test_clean_file_produces_no_hints_even_if_sqlfluff_reports_style_issues(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(1, canned(VIOLATIONS[1:], CLEAN_SQL)))
    text = lint.run(write_payload(CLEAN_SQL))
    assert "found 2 issue(s) in clean.sql" in text
    assert "LIMIT" not in text and "IS NULL" not in text


def test_bteq_drops_unparsable_findings(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(1, canned(VIOLATIONS, SAMPLE_BTEQ)))
    text = lint.run(write_payload(SAMPLE_BTEQ, tool="Edit"))
    assert "found 2 issue(s) in sample.bteq" in text
    assert "PRS" not in text


def test_bteq_with_only_unparsable_findings_is_silent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(1, canned(VIOLATIONS[:1], SAMPLE_BTEQ)))
    assert lint.run(write_payload(SAMPLE_BTEQ)) is None


def test_at_most_15_violations_are_shown(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    many = [{"start_line_no": i, "start_line_pos": 1, "code": "LT01", "description": f"issue {i}"} for i in range(1, 41)]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(1, canned(many, CLEAN_SQL)))
    text = lint.run(write_payload(CLEAN_SQL))
    assert text.count("- L") == 15
    assert "- ... 25 more not shown" in text


@pytest.mark.parametrize("rc", [0, 2])
def test_clean_and_fatal_exit_codes_are_silent(monkeypatch, rc):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(rc, "" if rc else "[]", "boom" if rc else ""))
    assert lint.run(write_payload(LIMIT_NULL_SQL)) is None


def test_timeout_is_silent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="sqlfluff", timeout=10)

    monkeypatch.setattr(subprocess, "run", slow)
    assert lint.run(write_payload(LIMIT_NULL_SQL)) is None


def test_garbage_json_is_silent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sqlfluff")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc(1, "not json"))
    assert lint.run(write_payload(LIMIT_NULL_SQL)) is None


def test_dialect_hints_ignore_comments_and_literals(tmp_path):
    f = tmp_path / "h.sql"
    f.write_text("-- LIMIT 10 is not Teradata\nSELECT 'x = NULL' AS s FROM t;\n")
    assert lint.dialect_hints(str(f)) == []
    assert len(lint.dialect_hints(LIMIT_NULL_SQL)) == 2


# --------------------------------------------------------------------------- hook process
def test_hook_process_is_silent_without_sqlfluff_on_path(tmp_path):
    """End-to-end: an empty PATH guarantees shutil.which finds nothing."""
    rc, out, err = run_hook("sql_lint.py", write_payload(LIMIT_NULL_SQL), {"PATH": str(tmp_path)})
    assert rc == 0 and out is None and err == ""


def test_hook_process_is_silent_for_non_sql_files():
    rc, out, err = run_hook("sql_lint.py", write_payload(__file__))
    assert rc == 0 and out is None and err == ""


def test_hook_emits_context_when_run_reports(monkeypatch, capsys):
    monkeypatch.setattr(lint, "run", lambda payload: "sqlfluff (teradata dialect) found 1 issue(s) in q.sql:\n- L1:C1 LT01 x")
    monkeypatch.setattr(lint.common, "read_hook_input", lambda stream=None: write_payload("q.sql"))
    assert lint.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert context_of(out).startswith("sqlfluff (teradata dialect)")


def test_internal_crash_is_silent(monkeypatch, capsys):
    def boom(_payload):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(lint, "run", boom)
    monkeypatch.setattr(lint.common, "read_hook_input", lambda stream=None: write_payload("q.sql"))
    assert lint.main() == 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------- hostile sqlfluff config
# REGRESSION 2026-09-08. When a repository ships its own sqlfluff config the hook ran sqlfluff BARE so
# the project's dialect and rules win. But sqlfluff resolves `library_path` / `load_macros_from_path`
# relative to that config and IMPORTS them, so linting a hostile checkout executed its Python on the
# first `.sql` write, with no prompt. The hook now declines to lint under such a config.

def _repo_with_config(tmp_path, body):
    (tmp_path / ".git").write_text("", encoding="utf-8")
    if body is not None:
        (tmp_path / ".sqlfluff").write_text(body, encoding="utf-8")
    sql = tmp_path / "q.sql"
    sql.write_text("SELECT 1;\n", encoding="utf-8")
    return str(sql)


@pytest.mark.parametrize(
    "config",
    [
        "[sqlfluff]\ndialect = teradata\ntemplater = jinja\n",
        "[sqlfluff]\ndialect = teradata\ntemplater = dbt\n",
        "[sqlfluff]\ndialect = teradata\n[sqlfluff:templater:jinja]\nlibrary_path = ./sqlmacros\n",
        "[sqlfluff]\ndialect = teradata\n[sqlfluff:templater:jinja]\nload_macros_from_path = ./macros\n",
    ],
)
def test_config_that_can_import_repo_code_is_refused(tmp_path, config):
    assert lint.build_command(_repo_with_config(tmp_path, config), "/usr/bin/sqlfluff") is None


@pytest.mark.parametrize(
    "config",
    [
        "[sqlfluff]\ndialect = teradata\n",
        "[sqlfluff]\ndialect = teradata\ntemplater = raw\n",
        "[sqlfluff]\ndialect = snowflake\ntemplater = RAW\n",
    ],
)
def test_benign_repo_config_is_still_honoured(tmp_path, config):
    cmd = lint.build_command(_repo_with_config(tmp_path, config), "/usr/bin/sqlfluff")
    assert cmd is not None
    assert "--config" not in cmd, "a benign project config must drive the lint, not the plugin's"


def test_no_repo_config_uses_the_plugin_config(tmp_path):
    cmd = lint.build_command(_repo_with_config(tmp_path, None), "/usr/bin/sqlfluff")
    assert cmd is not None and "--config" in cmd and "teradata" in cmd


def test_unreadable_config_is_treated_as_unsafe(tmp_path):
    assert lint.config_can_execute_code(str(tmp_path / "does-not-exist")) is True
