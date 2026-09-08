#!/usr/bin/env python3
"""Grounding cache and verified-query repository.

Two small stores that survive between sessions, so an agent does not re-derive the same schema
facts every time and does not re-invent SQL that has already been checked by a human.

Both are **accelerators, never authorities.** That distinction is the whole design:

* every recall reports how old the fact is, and marks it stale past a TTL;
* nothing is promoted into the verified repository without an explicit human confirmation;
* a recalled query is a starting point to re-check, never an answer to hand back.

**Matching is deliberately mechanical.** ``recall-query`` matches only on a normalised exact
string -- case, surrounding whitespace and internal runs of whitespace. It does NOT attempt to
decide whether two differently-worded questions mean the same thing. Deciding that is a judgement
about meaning, and this repository's rule is that such judgements belong to the model reading the
candidates, never to keyword or regex logic in a script. ``list`` exists for exactly that: it hands
the model everything so the model can choose.

Storage is JSON under ``<plugin data dir>/grounding/<fingerprint>/``. The fingerprint is a hash of
the connection target so two systems never share a cache; **no credential is stored, and no host is
stored in clear** -- only the digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks"))
try:
    from common import plugin_data_dir  # type: ignore
except Exception:  # pragma: no cover - exercised only when common.py is unavailable
    def plugin_data_dir() -> str:
        data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if data.strip():
            return data
        return os.path.expanduser(os.path.join("~", ".claude", "plugins", "data", "teradata-vantage"))


SCHEMA_TTL_SECONDS = int(os.environ.get("TERADATA_GROUNDING_TTL", str(7 * 24 * 3600)))
VERIFIED_TTL_SECONDS = int(os.environ.get("TERADATA_VERIFIED_TTL", str(90 * 24 * 3600)))
_WS = re.compile(r"\s+")


# ------------------------------------------------------------------ paths and fingerprinting


def fingerprint(target: str) -> str:
    """A stable, non-reversible id for one Teradata system.

    Takes whatever identifies the system -- typically host plus default database. Only the digest
    reaches disk, so a directory listing of the cache reveals nothing about where it points.
    """
    return hashlib.sha256(_WS.sub(" ", (target or "default").strip().lower()).encode()).hexdigest()[:16]


def store_dir(target: str) -> str:
    return os.path.join(plugin_data_dir(), "grounding", fingerprint(target))


def _path(target: str, kind: str) -> str:
    return os.path.join(store_dir(target), f"{kind}.json")


def _load(target: str, kind: str) -> Dict[str, Any]:
    try:
        with open(_path(target, kind), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # A corrupt or unreadable cache must behave exactly like an empty one. This store is an
        # accelerator; it is never allowed to be the reason a session fails.
        return {}


def _save(target: str, kind: str, data: Dict[str, Any]) -> None:
    directory = store_dir(target)
    os.makedirs(directory, exist_ok=True)
    tmp = _path(target, kind) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, _path(target, kind))
    try:
        os.chmod(_path(target, kind), 0o600)
    except OSError:
        pass


def _age(entry: Dict[str, Any], ttl: int, now: Optional[float] = None) -> Dict[str, Any]:
    """Attach age and staleness. Every read goes through here -- staleness is never optional."""
    now = time.time() if now is None else now
    captured = float(entry.get("captured_at", 0) or 0)
    age = max(0, int(now - captured)) if captured else None
    out = dict(entry)
    out["age_seconds"] = age
    out["stale"] = True if age is None else age > ttl
    return out


def normalise_question(text: str) -> str:
    return _WS.sub(" ", (text or "").strip()).lower()


# ------------------------------------------------------------------ schema grounding


def remember_schema(target: str, obj: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    data = _load(target, "schema")
    entry = dict(facts)
    entry["captured_at"] = time.time()
    entry["object"] = obj
    data[obj.lower()] = entry
    _save(target, "schema", data)
    return entry


def recall_schema(target: str, obj: str) -> Optional[Dict[str, Any]]:
    entry = _load(target, "schema").get(obj.lower())
    return _age(entry, SCHEMA_TTL_SECONDS) if entry else None


def forget_schema(target: str, obj: Optional[str] = None) -> int:
    data = _load(target, "schema")
    if obj is None:
        n = len(data)
        _save(target, "schema", {})
        return n
    removed = 1 if data.pop(obj.lower(), None) is not None else 0
    _save(target, "schema", data)
    return removed


def invalidate_for_statement(target: str, sql: str) -> List[str]:
    """Drop cached schema for any object a DDL statement names.

    A cached column list that survived an ``ALTER TABLE`` is worse than no cache at all, because it
    is confidently wrong. This is intentionally blunt: any cached object whose name appears in a
    schema-changing statement is dropped. Over-invalidating costs one re-derivation; under-
    invalidating costs a wrong answer.
    """
    if not re.search(r"\b(alter|drop|rename|create|replace)\b", sql or "", re.I):
        return []
    data = _load(target, "schema")
    lowered = (sql or "").lower()
    dropped = []
    for key in list(data):
        bare = key.split(".")[-1]
        if key in lowered or re.search(rf"\b{re.escape(bare)}\b", lowered):
            data.pop(key, None)
            dropped.append(key)
    if dropped:
        _save(target, "schema", data)
    return dropped


# ------------------------------------------------------------------ verified queries


class PromotionRefused(Exception):
    """The promotion gate said no. Never downgrade this to a warning."""


def verify_query(
    target: str,
    question: str,
    sql: str,
    *,
    confirmed_by: str,
    objects: Optional[List[str]] = None,
    note: str = "",
    executed_cleanly: bool = False,
) -> Dict[str, Any]:
    """Promote one question/SQL pair into the verified repository.

    The gate has two conditions and both are required:

    * ``executed_cleanly`` -- the statement actually ran without error;
    * ``confirmed_by`` -- a person said the ANSWER was right.

    The second is the one that matters. SQL that runs is not SQL that is correct: a wrong join that
    returns plausible numbers runs perfectly. Promotion on execution alone would fill this
    repository with confident mistakes and then hand them to the next session as trusted.
    """
    if not executed_cleanly:
        raise PromotionRefused("query did not execute cleanly; nothing to promote")
    if not (confirmed_by or "").strip():
        raise PromotionRefused(
            "promotion needs a human confirmation: running without error is not evidence the answer is right"
        )
    if not (question or "").strip() or not (sql or "").strip():
        raise PromotionRefused("both a question and a statement are required")

    data = _load(target, "verified")
    key = normalise_question(question)
    entry = {
        "question": question.strip(),
        "sql": sql.strip(),
        "objects": sorted(set(objects or [])),
        "note": note.strip(),
        "confirmed_by": confirmed_by.strip(),
        "captured_at": time.time(),
        "uses": int(data.get(key, {}).get("uses", 0)),
    }
    data[key] = entry
    _save(target, "verified", data)
    return entry


def recall_query(target: str, question: str) -> Optional[Dict[str, Any]]:
    """Exact normalised match ONLY. See the module docstring on why this does not fuzzy-match."""
    entry = _load(target, "verified").get(normalise_question(question))
    return _age(entry, VERIFIED_TTL_SECONDS) if entry else None


def list_queries(target: str) -> List[Dict[str, Any]]:
    return sorted(
        (_age(e, VERIFIED_TTL_SECONDS) for e in _load(target, "verified").values()),
        key=lambda e: e.get("question", ""),
    )


def note_use(target: str, question: str) -> None:
    data = _load(target, "verified")
    key = normalise_question(question)
    if key in data:
        data[key]["uses"] = int(data[key].get("uses", 0)) + 1
        _save(target, "verified", data)


def forget_query(target: str, question: Optional[str] = None) -> int:
    data = _load(target, "verified")
    if question is None:
        n = len(data)
        _save(target, "verified", {})
        return n
    removed = 1 if data.pop(normalise_question(question), None) is not None else 0
    _save(target, "verified", data)
    return removed


def stats(target: str) -> Dict[str, Any]:
    schema = _load(target, "schema")
    verified = _load(target, "verified")
    aged_s = [_age(e, SCHEMA_TTL_SECONDS) for e in schema.values()]
    aged_v = [_age(e, VERIFIED_TTL_SECONDS) for e in verified.values()]
    return {
        "fingerprint": fingerprint(target),
        "directory": store_dir(target),
        "schema_objects": len(schema),
        "schema_stale": sum(1 for e in aged_s if e["stale"]),
        "verified_queries": len(verified),
        "verified_stale": sum(1 for e in aged_v if e["stale"]),
        "schema_ttl_seconds": SCHEMA_TTL_SECONDS,
        "verified_ttl_seconds": VERIFIED_TTL_SECONDS,
    }


# ------------------------------------------------------------------ CLI


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="grounding.py",
        description="Grounding cache and verified-query repository. Both are accelerators, never authorities.",
    )
    ap.add_argument("--target", default=os.environ.get("TERADATA_GROUNDING_TARGET", "default"),
                    help="identifies the system; only its digest is stored")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("remember-schema", help="cache derived facts for one object")
    p.add_argument("object")
    p.add_argument("--json", required=True, help="facts as JSON, or - to read stdin")

    p = sub.add_parser("recall-schema", help="read cached facts, with their age")
    p.add_argument("object")

    p = sub.add_parser("forget-schema", help="drop cached facts")
    p.add_argument("object", nargs="?")

    p = sub.add_parser("invalidate", help="drop cached facts for objects a DDL statement names")
    p.add_argument("--sql", required=True)

    p = sub.add_parser("verify-query", help="promote a checked question/SQL pair")
    p.add_argument("--question", required=True)
    p.add_argument("--sql", required=True)
    p.add_argument("--confirmed-by", required=True, help="who confirmed the ANSWER was right")
    p.add_argument("--executed-cleanly", action="store_true", required=False)
    p.add_argument("--objects", nargs="*", default=[])
    p.add_argument("--note", default="")

    p = sub.add_parser("recall-query", help="exact normalised lookup; does not fuzzy-match")
    p.add_argument("question")

    sub.add_parser("list", help="every verified query, for the model to choose among")

    p = sub.add_parser("forget-query", help="drop a verified query")
    p.add_argument("question", nargs="?")

    sub.add_parser("stats", help="counts, staleness and where the store lives")

    args = ap.parse_args(argv)
    out: Any

    if args.cmd == "remember-schema":
        raw = sys.stdin.read() if args.json == "-" else args.json
        try:
            facts = json.loads(raw)
        except ValueError as exc:
            print(f"grounding: --json is not valid JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(facts, dict):
            print("grounding: --json must be an object", file=sys.stderr)
            return 2
        out = remember_schema(args.target, args.object, facts)
    elif args.cmd == "recall-schema":
        out = recall_schema(args.target, args.object)
        if out is None:
            print(json.dumps({"found": False, "object": args.object}))
            return 1
    elif args.cmd == "forget-schema":
        out = {"dropped": forget_schema(args.target, args.object)}
    elif args.cmd == "invalidate":
        out = {"dropped": invalidate_for_statement(args.target, args.sql)}
    elif args.cmd == "verify-query":
        try:
            out = verify_query(
                args.target, args.question, args.sql,
                confirmed_by=args.confirmed_by, objects=args.objects,
                note=args.note, executed_cleanly=args.executed_cleanly,
            )
        except PromotionRefused as exc:
            print(json.dumps({"promoted": False, "reason": str(exc)}))
            return 3
    elif args.cmd == "recall-query":
        out = recall_query(args.target, args.question)
        if out is None:
            print(json.dumps({"found": False, "hint": "run `list` and choose; this lookup is exact only"}))
            return 1
        note_use(args.target, args.question)
    elif args.cmd == "list":
        out = list_queries(args.target)
    elif args.cmd == "forget-query":
        out = {"dropped": forget_query(args.target, args.question)}
    else:
        out = stats(args.target)

    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
