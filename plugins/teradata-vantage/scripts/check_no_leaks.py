#!/usr/bin/env python3
"""Repository-wide forbidden-token scan for the teradata-vantage plugin.

Scans every text file in the repository against the regular expressions in
``scripts/data/forbidden_tokens.txt`` (one pattern per line, ``#`` comments) and prints
``file:line: <pattern> -> <matched text>`` for every hit. Exit status 1 when anything is found.

Why: the marketplace reviewer reads the WHOLE clone — scripts, tests, fixtures, docs. Content
lifted from an internal repository must never carry internal project names, demo database
names, hosts, credentials or private-fork environment variables. This gate runs in CI and from
``validate_plugin.py``.

Rules of the scan
- case-insensitive, word-boundary anchored (see the header of forbidden_tokens.txt for the two
  flag prefixes ``raw:`` and ``cs:``);
- skipped directories: .git dist node_modules __pycache__ .venv venv .pytest_cache;
- skipped files: binaries (*.whl *.zip *.pyc images, or any file containing a NUL byte) and
  ``requirements*.txt`` (hash-pinned dependency lists);
- exempt files: forbidden_tokens.txt itself and sync_manifest.yaml (they must name what they forbid).

Usage
    check_no_leaks.py [--repo PATH] [--tokens PATH] [--quiet]
    check_no_leaks.py --self-test         # compile the token file and exit

Python 3 standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence

SKIP_DIRS = frozenset({".git", "dist", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"})
BINARY_SUFFIXES = frozenset(
    {".whl", ".zip", ".gz", ".tgz", ".pyc", ".pyo", ".so", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".ttf"}
)
EXEMPT_BASENAMES = frozenset({"forbidden_tokens.txt", "sync_manifest.yaml"})
DEFAULT_TOKENS_REL = Path("scripts") / "data" / "forbidden_tokens.txt"


@dataclass(frozen=True)
class Token:
    source: str  # the line as written in the token file
    regex: "re.Pattern[str]"
    line_no: int


@dataclass(frozen=True)
class Hit:
    path: Path
    line: int
    token: str
    matched: str

    def format(self, root: Optional[Path] = None) -> str:
        p = self.path
        if root is not None:
            try:
                p = self.path.relative_to(root)
            except ValueError:
                pass
        return f"{p}:{self.line}: {self.token} -> {self.matched!r}"


class TokenFileError(ValueError):
    """A line in forbidden_tokens.txt could not be compiled."""


def compile_token(line: str, line_no: int = 0) -> Token:
    """Compile one token-file line into a Token (see the file header for the flag syntax)."""
    body = line.strip()
    flags = set()
    m = re.match(r"^((?:cs|raw)(?:,(?:cs|raw))*):\s*(.*)$", body)
    if m:
        flags = set(m.group(1).split(","))
        body = m.group(2).strip()
    if not body:
        raise TokenFileError(f"line {line_no}: empty pattern")
    pattern = body
    if "raw" not in flags:
        # NOT \b: "_" is a regex word character, so \b never fires between "_" and a token.
        # That blind spot hid a banned project name inside a snake_case FILENAME in this very
        # scripts/ directory until 2026-09-05 - the scan reported "clean" while it shipped.
        pattern = r"(?<![A-Za-z0-9])(?:" + body + r")"
        if body[-1].isalnum():
            pattern += r"(?![A-Za-z0-9])"
    re_flags = 0 if "cs" in flags else re.IGNORECASE
    try:
        regex = re.compile(pattern, re_flags)
    except re.error as exc:  # pragma: no cover - defensive
        raise TokenFileError(f"line {line_no}: cannot compile {body!r}: {exc}") from exc
    return Token(source=line.strip(), regex=regex, line_no=line_no)


def load_tokens(path: Path) -> List[Token]:
    tokens: List[Token] = []
    text = path.read_text(encoding="utf-8")
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(compile_token(line, i))
    if not tokens:
        raise TokenFileError(f"{path}: no patterns")
    return tokens


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return False
    try:
        with path.open("rb") as fh:
            chunk = fh.read(8192)
    except OSError:
        return False
    return b"\x00" not in chunk


def is_skipped_file(path: Path) -> bool:
    name = path.name
    if name in EXEMPT_BASENAMES:
        return True
    if name.lower().startswith("requirements") and name.lower().endswith(".txt"):
        return True
    return False


def iter_files(root: Path, extra_skip_dirs: Iterable[str] = ()) -> Iterator[Path]:
    skip = set(SKIP_DIRS) | set(extra_skip_dirs)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts[:-1]
        if any(part in skip for part in rel_parts):
            continue
        if is_skipped_file(path) or not is_text_file(path):
            continue
        yield path


def scan_text(text: str, tokens: Sequence[Token], path: Path = Path("<text>")) -> List[Hit]:
    hits: List[Hit] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for tok in tokens:
            for m in tok.regex.finditer(line):
                hits.append(Hit(path=path, line=line_no, token=tok.source, matched=m.group(0)))
    return hits


def scan_file(path: Path, tokens: Sequence[Token]) -> List[Hit]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_text(text, tokens, path)


def scan_paths(root: Path, path: Path, tokens: Sequence[Token]) -> List[Hit]:
    """Scan the repository-relative PATH itself. A banned token in a file or directory name
    ships in the clone exactly like one in a file body, and reads the same to a reviewer."""
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    hits: List[Hit] = []
    for token in tokens:
        for m in token.regex.finditer(rel):
            hits.append(Hit(path=path, line=0, token=token.source, matched=m.group(0)))
    return hits


def scan_repo(root: Path, tokens: Sequence[Token]) -> List[Hit]:
    hits: List[Hit] = []
    for path in iter_files(root):
        hits.extend(scan_paths(root, path, tokens))
        hits.extend(scan_file(path, tokens))
    return hits


def default_tokens_path(repo: Path, script_dir: Optional[Path] = None) -> Path:
    """Prefer the token file inside the scanned repository; fall back to the one beside this script."""
    candidates = [repo / "plugins" / "teradata-vantage" / DEFAULT_TOKENS_REL, repo / DEFAULT_TOKENS_REL]
    if script_dir is not None:
        candidates.append(script_dir / "data" / "forbidden_tokens.txt")
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError("forbidden_tokens.txt not found; pass --tokens")


#: Environment variable naming a private overlay token file kept OUTSIDE this repository.
EXTRA_TOKENS_ENV = "TERADATA_PLUGIN_TOKENS_EXTRA"


def resolve_extra_tokens_path(explicit: Optional[Path]) -> Optional[Path]:
    """The private overlay token file, from ``--tokens-extra`` or :data:`EXTRA_TOKENS_ENV`.

    The public list in ``scripts/data/forbidden_tokens.txt`` carries only GENERIC, class-based
    patterns, because a published deny-list is also a published index of what it hides. Anything
    site-specific -- the databases, hosts, customers and internal project names of whoever works on
    the plugin -- belongs in an overlay stored outside the repository and merged in at scan time.
    An explicitly named file that does not exist is an error; an env var pointing at a missing file
    is ignored, so exporting it globally cannot break a checkout that does not have it.
    """
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"--tokens-extra file not found: {explicit}")
        return explicit
    from_env = os.environ.get(EXTRA_TOKENS_ENV, "").strip()
    if from_env:
        candidate = Path(os.path.expanduser(from_env))
        if candidate.is_file():
            return candidate
    return None


def infer_repo(script_path: Path) -> Path:
    # scripts/check_no_leaks.py -> plugins/teradata-vantage -> plugins -> repo root
    return script_path.resolve().parent.parent.parent.parent


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=None, help="repository root (default: inferred from this script)")
    ap.add_argument("--tokens", type=Path, default=None, help="token file (default: scripts/data/forbidden_tokens.txt)")
    ap.add_argument(
        "--tokens-extra",
        type=Path,
        default=None,
        help=(
            "private overlay of extra patterns, merged with the public list "
            "(default: $TERADATA_PLUGIN_TOKENS_EXTRA when set)"
        ),
    )
    ap.add_argument("--quiet", action="store_true", help="print only the summary line")
    ap.add_argument("--self-test", action="store_true", help="compile the token file and exit")
    args = ap.parse_args(argv)

    here = Path(__file__).resolve()
    repo = (args.repo or infer_repo(here)).resolve()
    try:
        tokens_path = args.tokens or default_tokens_path(repo, here.parent)
        tokens = load_tokens(tokens_path)
        extra_path = resolve_extra_tokens_path(args.tokens_extra)
        if extra_path is not None:
            tokens = tokens + load_tokens(extra_path)
    except (FileNotFoundError, TokenFileError) as exc:
        print(f"check_no_leaks: {exc}", file=sys.stderr)
        return 2
    if args.self_test:
        where = str(tokens_path) + (f" + {extra_path}" if extra_path is not None else "")
        print(f"check_no_leaks: {len(tokens)} patterns compiled from {where}")
        return 0
    if not repo.is_dir():
        print(f"check_no_leaks: not a directory: {repo}", file=sys.stderr)
        return 2

    hits = scan_repo(repo, tokens)
    if not args.quiet:
        for h in hits:
            print(h.format(repo))
    if hits:
        print(f"check_no_leaks: {len(hits)} forbidden-token hit(s) in {repo} ({len(tokens)} patterns)")
        return 1
    print(f"check_no_leaks: clean ({len(tokens)} patterns, {repo})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
