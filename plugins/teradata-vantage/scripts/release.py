#!/usr/bin/env python3
"""Cut a release of the teradata-vantage plugin.

    release.py --bump patch|minor|major [--dry-run] [--no-tag] [--skip-validate]

Steps, in order:
  1. preflight: `validate_plugin.py` must pass (skip with --skip-validate; never skip for a real release)
  2. bump `version` in plugins/teradata-vantage/.claude-plugin/plugin.json — the ONLY place a version lives.
     The marketplace entry must not carry one (asserted; a version there would pin users to a stale string).
  3. prepend a dated section to CHANGELOG.md (repository root, and the plugin's own CHANGELOG.md if present);
     an existing "## <new version> (unreleased)" heading is re-dated instead of duplicated.
  4. build dist/teradata-vantage-<version>.zip from the plugin directory (everything except __pycache__,
     *.pyc, .pytest_cache), so `claude --plugin-dir dist/teradata-vantage-<version>.zip` works offline.
  5. run `claude plugin tag --push` from the plugin directory when the claude binary is available (it creates
     the `teradata-vantage--v<version>` tag after its own consistency checks); otherwise print the manual
     command. Skip with --no-tag.

--dry-run prints every action without writing. Python 3 standard library only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent
REPO_DIR = PLUGIN_DIR.parent.parent
PLUGIN_NAME = "teradata-vantage"
ZIP_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
ZIP_EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ReleaseError(RuntimeError):
    pass


def bump_version(current: str, part: str) -> str:
    m = SEMVER_RE.match(current)
    if not m:
        raise ReleaseError(f"current version {current!r} is not plain semver x.y.z")
    major, minor, patch = (int(g) for g in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"unknown bump part {part!r}")


def read_plugin_json(path: Path) -> Tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return json.loads(text), text


def write_plugin_json_version(path: Path, text: str, old: str, new: str) -> str:
    # textual replacement keeps the file's formatting and key order intact
    pattern = re.compile(r'("version"\s*:\s*")' + re.escape(old) + r'(")')
    new_text, n = pattern.subn(lambda m: m.group(1) + new + m.group(2), text, count=1)
    if n != 1:
        raise ReleaseError("could not locate the version field for a textual update")
    path.write_text(new_text, encoding="utf-8")
    return new_text


def assert_marketplace_has_no_version(repo: Path) -> None:
    mp = repo / ".claude-plugin" / "marketplace.json"
    if not mp.is_file():
        raise ReleaseError(f"{mp} not found")
    data = json.loads(mp.read_text(encoding="utf-8"))
    for entry in data.get("plugins") or []:
        if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME and "version" in entry:
            raise ReleaseError("marketplace entry carries a `version`; remove it — plugin.json is the single source of truth")


def changelog_update(path: Path, version: str, date: str, dry_run: bool) -> str:
    heading = f"## {version} ({date})"
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = "# Changelog\n\n"
    unreleased = re.compile(r"^## " + re.escape(version) + r" \((?:unreleased|UNRELEASED)\)\s*$", re.MULTILINE)
    if unreleased.search(text):
        new_text = unreleased.sub(heading, text, count=1)
        action = f"re-date '## {version} (unreleased)' -> '{heading}'"
    elif re.search(r"^## " + re.escape(version) + r"\b", text, re.MULTILINE):
        new_text = text
        action = f"section for {version} already present; unchanged"
    else:
        lines = text.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("# "):
                insert_at = i + 1
                break
        block = f"\n{heading}\n\n- (describe the changes in this release)\n"
        new_text = "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])
        action = f"prepend '{heading}'"
    if not dry_run and new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return action


def build_zip(plugin: Path, dist: Path, version: str, dry_run: bool) -> Path:
    out = dist / f"{PLUGIN_NAME}-{version}.zip"
    files = []
    for p in sorted(plugin.rglob("*")):
        rel_parts = p.relative_to(plugin).parts
        if any(part in ZIP_EXCLUDE_DIRS for part in rel_parts):
            continue
        if p.is_file() and p.suffix not in ZIP_EXCLUDE_SUFFIXES:
            files.append(p)
    if dry_run:
        print(f"  would write {out} ({len(files)} files)")
        return out
    dist.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=str(Path(PLUGIN_NAME) / p.relative_to(plugin)))
    print(f"  wrote {out} ({len(files)} files, {out.stat().st_size} bytes)")
    return out


def run_validate(repo: Path) -> None:
    cmd = [sys.executable, str(SCRIPT_DIR / "validate_plugin.py"), "--repo", str(repo), "--quiet"]
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        raise ReleaseError("validate_plugin.py failed; fix the report before releasing (or --skip-validate for a dry run)")


def tag_release(plugin: Path, version: str, dry_run: bool) -> None:
    claude = shutil.which("claude")
    manual = f"cd {plugin} && claude plugin tag --push    # creates {PLUGIN_NAME}--v{version}"
    if not claude:
        print(f"  claude binary not on PATH — tag manually:\n    {manual}")
        return
    cmd = [claude, "plugin", "tag", "--push"]
    if dry_run:
        print(f"  would run: {' '.join(cmd)}  (cwd {plugin})")
        return
    proc = subprocess.run(cmd, cwd=str(plugin), text=True)
    if proc.returncode != 0:
        raise ReleaseError(f"`claude plugin tag --push` exited {proc.returncode}; commit the bump first (it needs a clean tree), then rerun:\n    {manual}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Bump, changelog, zip and tag a plugin release.")
    ap.add_argument("--bump", choices=("patch", "minor", "major"),
                    help="required unless --zip-only is given")
    ap.add_argument("--zip-only", action="store_true",
                    help="build dist/<plugin>-<current version>.zip from the version already in "
                         "plugin.json and stop: no bump, no CHANGELOG edit, no tag. This is the one "
                         "code path that builds the archive, so CI and a local build cannot diverge.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-tag", action="store_true", help="do not run `claude plugin tag --push`")
    ap.add_argument("--skip-validate", action="store_true", help="skip the validate_plugin.py preflight")
    ap.add_argument("--repo", type=Path, default=REPO_DIR)
    ap.add_argument("--date", default=_dt.date.today().isoformat(), help="date for the CHANGELOG heading (default: today)")
    args = ap.parse_args(argv)

    if not args.bump and not args.zip_only:
        ap.error("--bump is required unless --zip-only is given")
    if args.bump and args.zip_only:
        ap.error("--bump and --zip-only are mutually exclusive: --zip-only packages the current version")

    repo = args.repo.resolve()
    plugin = repo / "plugins" / PLUGIN_NAME
    pj_path = plugin / ".claude-plugin" / "plugin.json"
    try:
        if not pj_path.is_file():
            raise ReleaseError(f"{pj_path} not found")
        data, text = read_plugin_json(pj_path)
        current = str(data.get("version", ""))

        if args.zip_only:
            # Package what is already committed. The release workflow calls this so the archive it
            # publishes is byte-for-byte the one a maintainer builds locally.
            out = build_zip(plugin, repo / "dist", current, args.dry_run)
            print(f"release: packaged {PLUGIN_NAME} {current} -> {out}")
            return 0

        new = bump_version(current, args.bump)
        assert_marketplace_has_no_version(repo)
        print(f"release: {PLUGIN_NAME} {current} -> {new}{'  (dry run)' if args.dry_run else ''}")

        if not args.skip_validate:
            print("step 1  validate_plugin.py")
            run_validate(repo)
        else:
            print("step 1  validation SKIPPED (--skip-validate)")

        print(f"step 2  plugin.json version {current} -> {new}")
        if not args.dry_run:
            write_plugin_json_version(pj_path, text, current, new)

        print("step 3  CHANGELOG")
        for cl in (repo / "CHANGELOG.md", plugin / "CHANGELOG.md"):
            if cl == plugin / "CHANGELOG.md" and not cl.is_file():
                continue
            print(f"  {cl}: {changelog_update(cl, new, args.date, args.dry_run)}")

        print("step 4  dist zip")
        build_zip(plugin, repo / "dist", new, args.dry_run)

        if args.no_tag:
            print("step 5  tag SKIPPED (--no-tag)")
        else:
            print("step 5  claude plugin tag --push")
            tag_release(plugin, new, args.dry_run)
    except ReleaseError as exc:
        print(f"release: ERROR: {exc}", file=sys.stderr)
        return 1
    print("release: done" + (" (nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
