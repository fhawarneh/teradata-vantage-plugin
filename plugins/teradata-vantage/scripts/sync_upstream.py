#!/usr/bin/env python3
"""Re-import (or drift-check) content the plugin lifted from its private source repository.

Driven by ``scripts/data/sync_manifest.yaml``. Each entry names a source file (relative to ``--root``), the
sha256 it had when it was last imported or reviewed, a target inside the plugin, a ``mode`` and, for
``mode: import`` entries, an ordered list of transforms.

    sync_upstream.py --root <source checkout> --check
        Compare every source's sha256 with the manifest. Print a drift report; exit 1 on drift.
        Also reports import-mode targets whose content no longer equals a clean re-import (hand edits).

    sync_upstream.py --root <source checkout> [--only ID] [--out DIR]
        Real run for ``mode: import`` entries: copy the source, apply the transforms in order (every transform
        must hit exactly ``expected_count`` times), scan the result with scripts/data/forbidden_tokens.txt and
        refuse to write when anything is found, then write the target (or, with --out, the same relative
        path under DIR — the way to preview a re-import without touching the plugin).
        ``mode: track`` entries are never written; they are hand-ported and only drift-checked.

    sync_upstream.py --root <source checkout> --update-hashes
        After a reviewed re-port, record the current source sha256 values in the manifest.

Python 3 standard library + PyYAML.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("sync_upstream.py needs PyYAML: python3 -m pip install pyyaml\n")
    sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_no_leaks  # noqa: E402

PLUGIN_DIR = SCRIPT_DIR.parent
DEFAULT_MANIFEST = SCRIPT_DIR / "data" / "sync_manifest.yaml"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


class SyncError(RuntimeError):
    pass


@dataclass
class Entry:
    id: str
    mode: str
    source: str
    sha256: str
    target: str
    transforms: List[Dict[str, Any]]
    notes: str = ""
    license: str = ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(path: Path) -> Tuple[Dict[str, Any], List[Entry]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise SyncError(f"{path}: manifest must have an `entries` list")
    entries: List[Entry] = []
    seen = set()
    for raw in data["entries"]:
        for req in ("id", "mode", "source", "sha256", "target"):
            if req not in raw:
                raise SyncError(f"{path}: entry {raw.get('id', '?')!r} lacks `{req}`")
        if raw["mode"] not in ("import", "track"):
            raise SyncError(f"{path}: entry {raw['id']!r}: mode must be import|track")
        if raw["id"] in seen:
            raise SyncError(f"{path}: duplicate entry id {raw['id']!r}")
        seen.add(raw["id"])
        entries.append(
            Entry(
                id=str(raw["id"]), mode=str(raw["mode"]), source=str(raw["source"]), sha256=str(raw["sha256"]).lower(),
                target=str(raw["target"]), transforms=list(raw.get("transforms") or []), notes=str(raw.get("notes") or ""),
                license=str(raw.get("license") or ""),
            )
        )
    return data, entries


# --------------------------------------------------------------------------- transforms
def split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return None, text


def dump_frontmatter(fm: Dict[str, Any]) -> str:
    body = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=110, default_flow_style=False)
    return f"---\n{body}---\n"


def t_frontmatter_rewrite(text: str, spec: Dict[str, Any]) -> str:
    fm_text, body = split_frontmatter(text)
    if spec.get("strip"):
        if fm_text is None:
            raise SyncError("frontmatter_rewrite strip: source has no frontmatter")
        return body.lstrip("\n")
    fm = yaml.safe_load(fm_text) if fm_text else {}
    if not isinstance(fm, dict):
        raise SyncError("frontmatter_rewrite: frontmatter is not a mapping")
    for key in spec.get("drop_keys") or []:
        fm.pop(key, None)
    new_keys = spec.get("set") or {}
    # keys named in `set` come first, in the manifest's order; untouched keys keep their relative order after them
    ordered: Dict[str, Any] = {k: v for k, v in new_keys.items()}
    for key, value in fm.items():
        if key not in ordered:
            ordered[key] = value
    return dump_frontmatter(ordered) + body


def t_delete_section(text: str, spec: Dict[str, Any]) -> Tuple[str, int]:
    rx = re.compile(str(spec["heading"]))
    lines = text.splitlines(keepends=True)
    out: List[str] = []
    count = 0
    i = 0
    while i < len(lines):
        m = HEADING_RE.match(lines[i].rstrip("\n"))
        if m and rx.search(m.group(2)):
            level = len(m.group(1))
            count += 1
            i += 1
            while i < len(lines):
                m2 = HEADING_RE.match(lines[i].rstrip("\n"))
                if m2 and len(m2.group(1)) <= level:
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out), count


def t_replace(text: str, spec: Dict[str, Any]) -> Tuple[str, int]:
    flags = 0
    for ch in str(spec.get("flags") or ""):
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}.get(ch, 0)
    rx = re.compile(str(spec["pattern"]), flags)
    replacement = str(spec.get("replacement", ""))
    new, count = rx.subn(lambda _m: replacement, text)
    return new, count


def apply_transforms(text: str, transforms: Sequence[Dict[str, Any]], entry_id: str) -> str:
    for idx, spec in enumerate(transforms, start=1):
        ttype = spec.get("type")
        label = f"{entry_id} transform #{idx} ({ttype})"
        if ttype == "frontmatter_rewrite":
            text = t_frontmatter_rewrite(text, spec)
            continue
        if ttype == "delete_section":
            text, count = t_delete_section(text, spec)
        elif ttype == "replace":
            text, count = t_replace(text, spec)
        else:
            raise SyncError(f"{label}: unknown transform type")
        expected = spec.get("expected_count")
        if expected is None:
            raise SyncError(f"{label}: expected_count is required")
        if count != int(expected):
            raise SyncError(f"{label}: matched {count} time(s), expected {expected} — the source changed shape; review before re-importing")
    return text


# --------------------------------------------------------------------------- operations
def source_path(root: Path, entry: Entry) -> Path:
    return (root / entry.source).resolve()


def render_entry(root: Path, entry: Entry) -> str:
    src = source_path(root, entry)
    text = src.read_text(encoding="utf-8")
    return apply_transforms(text, entry.transforms, entry.id)


def leak_scan(text: str, tokens, label: str) -> List[str]:
    hits = check_no_leaks.scan_text(text, tokens, Path(label))
    return [h.format() for h in hits]


def do_check(root: Path, entries: List[Entry], plugin: Path, tokens) -> int:
    drift = 0
    for e in entries:
        src = source_path(root, e)
        if not src.is_file():
            print(f"DRIFT  {e.id}: source missing: {e.source}")
            drift += 1
            continue
        actual = sha256_bytes(src.read_bytes())
        if actual != e.sha256:
            print(f"DRIFT  {e.id}: {e.source} sha256 {actual[:12]}… != manifest {e.sha256[:12]}… ({e.mode})")
            drift += 1
        else:
            print(f"ok     {e.id}: {e.source} unchanged ({e.mode})")
        target = plugin / e.target
        if not target.exists():
            print(f"note   {e.id}: target not present yet: {e.target}")
            continue
        if e.mode == "import" and actual == e.sha256:
            try:
                rendered = render_entry(root, e)
            except SyncError as exc:
                print(f"note   {e.id}: transforms no longer apply cleanly: {exc}")
                continue
            if rendered != target.read_text(encoding="utf-8"):
                print(f"note   {e.id}: target {e.target} differs from a clean re-import (hand edits present; a real run would overwrite them)")
            hits = leak_scan(rendered, tokens, e.target)
            for h in hits:
                print(f"LEAK   {e.id}: {h}")
            drift += len(hits)
    print(f"\nsync --check: {'DRIFT' if drift else 'clean'} ({len(entries)} entries)")
    return 1 if drift else 0


def do_import(root: Path, entries: List[Entry], plugin: Path, out: Optional[Path], tokens, only: Optional[str]) -> int:
    failures = 0
    written = 0
    for e in entries:
        if only and e.id != only:
            continue
        if e.mode != "import":
            print(f"skip   {e.id}: mode=track (hand-ported; see notes)")
            continue
        src = source_path(root, e)
        if not src.is_file():
            print(f"ERROR  {e.id}: source missing: {e.source}")
            failures += 1
            continue
        actual = sha256_bytes(src.read_bytes())
        if actual != e.sha256:
            print(f"warn   {e.id}: source drifted since the manifest was written (sha256 {actual[:12]}…); importing the current source — run --update-hashes after review")
        try:
            rendered = render_entry(root, e)
        except SyncError as exc:
            print(f"ERROR  {e.id}: {exc}")
            failures += 1
            continue
        hits = leak_scan(rendered, tokens, e.target)
        if hits:
            for h in hits:
                print(f"LEAK   {e.id}: {h}")
            print(f"ERROR  {e.id}: refusing to write {e.target} — forbidden tokens remain; add transforms")
            failures += 1
            continue
        dest = ((out or plugin) / e.target).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
        written += 1
        print(f"wrote  {e.id}: {dest}")
    print(f"\nsync: {written} file(s) written, {failures} failure(s)")
    return 1 if failures else 0


def do_update_hashes(root: Path, manifest_path: Path, entries: List[Entry]) -> int:
    text = manifest_path.read_text(encoding="utf-8")
    changed = 0
    for e in entries:
        src = source_path(root, e)
        if not src.is_file():
            print(f"ERROR  {e.id}: source missing: {e.source}")
            return 1
        actual = sha256_bytes(src.read_bytes())
        if actual == e.sha256:
            continue
        pattern = re.compile(r"(- id: " + re.escape(e.id) + r"\n(?:(?!- id: ).*\n)*?\s*sha256: )" + re.escape(e.sha256))
        text, n = pattern.subn(lambda m: m.group(1) + actual, text)
        if n != 1:
            print(f"ERROR  {e.id}: could not locate the sha256 line to update")
            return 1
        changed += 1
        print(f"update {e.id}: sha256 -> {actual}")
    if changed:
        manifest_path.write_text(text, encoding="utf-8")
    print(f"\nsync --update-hashes: {changed} entry(ies) updated")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Re-import or drift-check content lifted from the private source repository.")
    ap.add_argument("--root", type=Path, default=os.environ.get("SYNC_SOURCE_ROOT"), help="source checkout (or env SYNC_SOURCE_ROOT)")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--plugin", type=Path, default=PLUGIN_DIR, help="plugin directory holding the targets")
    ap.add_argument("--check", action="store_true", help="drift report only; exit 1 on drift")
    ap.add_argument("--update-hashes", action="store_true", help="record current source sha256 values in the manifest")
    ap.add_argument("--only", help="entry id to process")
    ap.add_argument("--out", type=Path, help="write rendered targets under this directory instead of the plugin (preview)")
    args = ap.parse_args(argv)

    if not args.root:
        print("sync: --root <source checkout> is required (or set SYNC_SOURCE_ROOT)", file=sys.stderr)
        return 2
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"sync: not a directory: {root}", file=sys.stderr)
        return 2
    try:
        manifest, entries = load_manifest(args.manifest)
    except (SyncError, yaml.YAMLError, OSError) as exc:
        print(f"sync: {exc}", file=sys.stderr)
        return 2
    plugin = Path(args.plugin).resolve()
    tokens_rel = str(manifest.get("tokens_file") or "scripts/data/forbidden_tokens.txt")
    tokens_path = plugin / tokens_rel
    if not tokens_path.is_file():
        tokens_path = SCRIPT_DIR / "data" / "forbidden_tokens.txt"
    try:
        tokens = check_no_leaks.load_tokens(tokens_path)
    except (OSError, check_no_leaks.TokenFileError) as exc:
        print(f"sync: cannot load forbidden tokens: {exc}", file=sys.stderr)
        return 2

    if args.update_hashes:
        return do_update_hashes(root, args.manifest, entries)
    if args.check:
        return do_check(root, entries, plugin, tokens)
    return do_import(root, entries, plugin, args.out, tokens, args.only)


if __name__ == "__main__":
    sys.exit(main())
