"""Tests for scripts/check_no_leaks.py — synthetic fixtures built at test time.

Forbidden tokens are assembled by concatenation so this file itself stays clean under the scan.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import check_no_leaks as cnl  # noqa: E402

TOKENS_FILE = SCRIPTS / "data" / "forbidden_tokens.txt"


@pytest.fixture(scope="module")
def tokens():
    return cnl.load_tokens(TOKENS_FILE)


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_token_file_compiles(tokens):
    # The shipped list holds only generic, class-based patterns; site-specific names live in a
    # private overlay outside the repository (see the split tests at the end of this file).
    assert len(tokens) >= 10
    assert all(t.regex for t in tokens)


def test_compile_token_flags():
    plain = cnl.compile_token("hello")
    assert plain.regex.search("say Hello!") and not plain.regex.search("othello")
    cs = cnl.compile_token("cs: Hello")
    assert cs.regex.search("Hello") and not cs.regex.search("hello")
    raw = cnl.compile_token("raw: \\d+\\.\\d+")
    assert raw.regex.search("x1.2y")
    with pytest.raises(cnl.TokenFileError):
        cnl.compile_token("cs: ")


def test_word_boundary_does_not_flag_genuinely(tokens):
    hits = cnl.scan_text("The agent genuinely tries to help.\n", tokens)
    assert hits == []


#: The site-specific patterns this project keeps in its PRIVATE overlay. Assembled at runtime so
#: this file, which the scanner also reads, never contains a literal banned token.
OVERLAY_PATTERNS = [
    "aif" + "actory(?:-\\S+)?",
    "gov" + "_demo",
    "raw: 194" + "\\.93\\.\\d+\\.\\d+",
]


@pytest.fixture()
def overlay_file(tmp_path):
    f = tmp_path / "private_tokens.txt"
    f.write_text("# private overlay\n" + "\n".join(OVERLAY_PATTERNS) + "\n", encoding="utf-8")
    return f


@pytest.fixture()
def merged_tokens(tokens, overlay_file):
    return tokens + cnl.load_tokens(overlay_file)


def test_project_and_demo_tokens_are_caught(merged_tokens):
    """With the overlay merged, the full site-specific corpus is still caught."""
    text = "\n".join(
        [
            "see " + "AIF" + "actory for details",
            "select * from " + "gov_" + "demo.t",
            "docker exec " + "aif" + "actory-redis redis-cli",
            "host " + "194.93." + "48.1 is reachable",
            "creds " + "dbc" + "/" + "dbc",
            "set " + "MCP_" + "TD_POOL_SIZE=20",
        ]
    )
    hits = cnl.scan_text(text + "\n", merged_tokens)
    lines = sorted({h.line for h in hits})
    assert lines == [1, 2, 3, 4, 5, 6]


def test_site_specific_tokens_are_NOT_caught_by_the_shipped_list_alone(tokens):
    """Proves the split is real: without the overlay these are invisible, which is why the
    overlay must be passed in CI and by anyone scanning before a publish."""
    text = "see " + "AIF" + "actory and " + "gov_" + "demo\n"
    assert cnl.scan_text(text, tokens) == []


def test_official_near_teradata_is_case_sensitive(tokens):
    word = "Offi" + "cial"
    vendor = "Tera" + "data"
    assert cnl.scan_text(f"The {word} {vendor} plugin\n", tokens)
    assert not cnl.scan_text(f"the {word.lower()} {vendor} plugin\n", tokens)  # lower-case stays a prose word
    assert not cnl.scan_text(f"{word} release notes\n", tokens)  # no vendor name on the line


def test_arabic_script_is_caught(tokens):
    assert cnl.scan_text("value: \u0633\u0644\u0627\u0645\n", tokens)


def test_hidden_unicode_is_caught(tokens):
    assert cnl.scan_text("plain\u200btext\n", tokens)
    assert cnl.scan_text("\ufeffbom at start\n", tokens)


def test_scan_repo_skips_and_exempts(tmp_path, tokens):
    bad = "host" + ".docker.internal"   # a pattern that IS in the shipped list
    _write(tmp_path, "README.md", f"# fine\n{bad} appears here\n")
    _write(tmp_path, "scripts/data/forbidden_tokens.txt", f"{bad}\n")  # exempt by name
    _write(tmp_path, "scripts/data/sync_manifest.yaml", f"source: {bad}\n")  # exempt by name
    _write(tmp_path, "servers/requirements-0.2.6.txt", f"{bad}==1\n")  # requirements skipped
    _write(tmp_path, ".git/config", f"{bad}\n")  # skipped dir
    _write(tmp_path, "dist/x.txt", f"{bad}\n")  # skipped dir
    (tmp_path / "servers" / "pkg.whl").write_bytes(b"PK\x03\x04" + bad.encode())  # binary suffix
    (tmp_path / "blob.bin").write_bytes(b"\x00" + bad.encode())  # NUL byte => binary
    hits = cnl.scan_repo(tmp_path, tokens)
    assert [h.path.relative_to(tmp_path).as_posix() for h in hits] == ["README.md"]
    assert hits[0].line == 2


def test_main_exit_codes(tmp_path, capsys):
    bad = "host" + ".docker.internal"   # a pattern that IS in the shipped list
    _write(tmp_path, "notes.md", f"connect to {bad}\n")
    assert cnl.main(["--repo", str(tmp_path), "--tokens", str(TOKENS_FILE)]) == 1
    out = capsys.readouterr().out
    assert "notes.md:1:" in out
    (tmp_path / "notes.md").write_text("table sales_fact\n", encoding="utf-8")
    assert cnl.main(["--repo", str(tmp_path), "--tokens", str(TOKENS_FILE)]) == 0
    assert cnl.main(["--self-test", "--tokens", str(TOKENS_FILE)]) == 0


def test_placeholders_are_clean(tokens):
    text = "SELECT TOP 10 * FROM <db>.<table>; sales_fact JOIN customer_dim; Teradata Vantage community plugin\n"
    assert cnl.scan_text(text, tokens) == []


# ------------------------------------------------------- underscore boundary + filenames
# Regression 2026-09-05: the token patterns were wrapped in \b, but "_" is a regex WORD
# character, so \b never fires between "_" and a token -- a banned name inside a snake_case
# identifier or filename was invisible. Paths were never scanned at all, so a banned token
# in a FILENAME shipped while the scan reported "clean".
# Fixture tokens are assembled at runtime, never written as a literal: this file is scanned
# by the very scanner it tests, and a literal banned token here is a real repository hit.
BANNED = "gov" + "_demo"          # a real entry in forbidden_tokens.txt
PREFIXY = "sa" + "ma"             # another, chosen because real words start with it


def test_token_adjacent_to_underscore_is_caught():
    tok = cnl.compile_token(BANNED)
    assert tok.regex.search(f"x_{BANNED}_y"), "underscore-adjacent token must match"
    assert tok.regex.search(BANNED)


def test_token_inside_a_longer_word_is_still_not_matched():
    """The widened boundary must not start matching substrings of real words."""
    tok = cnl.compile_token(PREFIXY)
    assert not tok.regex.search(PREFIXY + "tha")
    assert not tok.regex.search("a" + PREFIXY)


def test_filenames_are_scanned(tmp_path):
    (tmp_path / "plugins").mkdir()
    bad = tmp_path / "plugins" / f"sync_from_{BANNED}.py"
    bad.write_text("print('body is clean')\n", encoding="utf-8")
    tokens = [cnl.compile_token(BANNED)]
    hits = cnl.scan_repo(tmp_path, tokens)
    assert hits, "a banned token in a FILENAME must be reported"
    assert any(h.line == 0 for h in hits), "a path hit is reported at line 0"


# ------------------------------------------------------- public list vs private overlay
# The shipped token list is PUBLIC. A deny-list naming customers, demo databases and site
# addresses is also an INDEX of them, so those patterns live in an overlay kept outside the
# repository and merged at scan time. These tests pin the split and the merge.

def test_public_list_names_no_site_specific_identifier():
    """The shipped list must hold only generic, class-based patterns."""
    text = TOKENS_FILE.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ).lower()
    # assembled at runtime: a literal here would be a real hit in this very file
    forbidden_shapes = ["a" + "ifactory", "demo" + "_user", "gov" + "_demo", "vs" + "_example", "csa" + "-studio"]
    leaked = [s for s in forbidden_shapes if s in body]
    assert not leaked, f"site-specific identifiers must live in the private overlay, not here: {leaked}"


def test_public_list_still_catches_every_generic_leak_class(tmp_path):
    """Branding, private-LAN addresses, inline credentials and fork-only env names."""
    tokens = cnl.load_tokens(TOKENS_FILE)
    (tmp_path / "d").mkdir()
    probe = tmp_path / "d" / "leak.md"
    # assembled at runtime: a literal address or credential URI here is a real hit in this file
    lan = "192." + "168.10.5"
    other = "10." + "20.30.40"
    probe.write_text(
        f"host {lan} and teradata://svc:realpw@{other}:1025/db\n"
        "credentials " + "dbc" + "/" + "dbc" + " and MCP_TD" + "_POOL_SIZE=20\n"
        "This is the Offic" + "ial Teradata plugin.\n",
        encoding="utf-8",
    )
    hits = cnl.scan_repo(tmp_path, tokens)
    assert len(hits) >= 5, [h.format(tmp_path) for h in hits]


def test_documentation_placeholders_are_not_flagged(tmp_path):
    """The docs must be free to show example URIs, loopback and reserved names."""
    tokens = cnl.load_tokens(TOKENS_FILE)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "ok.md").write_text(
        "teradata://user:password@host:1025/your_db\n"
        "teradata://<user>:<password>@<host>:1025/<database>\n"
        "teradata://u:p@example.invalid and http://127.0.0.1:8001/mcp/\n",
        encoding="utf-8",
    )
    assert cnl.scan_repo(tmp_path, tokens) == []


def test_overlay_merges_and_env_var_is_honoured(tmp_path, monkeypatch):
    overlay = tmp_path / "private_tokens.txt"
    overlay.write_text("# private\nacme" + "corp\n", encoding="utf-8")
    monkeypatch.setenv(cnl.EXTRA_TOKENS_ENV, str(overlay))
    assert cnl.resolve_extra_tokens_path(None) == overlay
    merged = cnl.load_tokens(TOKENS_FILE) + cnl.load_tokens(overlay)
    assert len(merged) == len(cnl.load_tokens(TOKENS_FILE)) + 1


def test_missing_overlay_from_env_is_ignored_but_explicit_is_an_error(tmp_path, monkeypatch):
    """Exporting the variable globally must never break a checkout that lacks the file."""
    monkeypatch.setenv(cnl.EXTRA_TOKENS_ENV, str(tmp_path / "nope.txt"))
    assert cnl.resolve_extra_tokens_path(None) is None
    monkeypatch.delenv(cnl.EXTRA_TOKENS_ENV, raising=False)
    with pytest.raises(FileNotFoundError):
        cnl.resolve_extra_tokens_path(tmp_path / "nope.txt")
