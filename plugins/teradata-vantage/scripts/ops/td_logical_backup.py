#!/usr/bin/env python3
"""Logical, restorable backup of Teradata Vantage databases: DDL (SHOW TABLE / SHOW VIEW) plus data as CSV.

Part of the teradata-vantage community plugin for Claude Code. Reads COMMITTED data through the engine with
the standard teradatasql driver, so it is transaction-consistent per table and needs no outage. Restore =
recreate the DDL, then load each table's CSV (decode the two sentinels below). Per-object try/except so one
failure cannot abort the run; a MANIFEST.json records per-table row counts; --verify recounts every table on
the live system against the manifest.

Configuration is ENVIRONMENT ONLY. There are deliberately no defaults for host, user or password: a backup
script that silently points at the wrong system, or at loopback where nothing listens, produces no backup and
nobody notices until the day it is needed.

    TD_HOST            required   database node / listener address
    TD_USER            required   logon user (SELECT on the target databases and on DBC.TablesV)
    TD_PASSWORD        required   logon password (from a secret store or a 0600 env file, never argv)
    TD_PORT            1025       database port
    TD_LOGMECH         TD2        TD2 | LDAP | KRB5 | JWT
    TD_DATABASES       ALL_USER   comma-separated list, or ALL_USER = every non-system database holding objects
    TD_BACKUP_DIR      ./td_backups   root; each run writes <root>/<YYYYMMDDTHHMMSS>/
    TD_RETENTION_DAYS  0          after a successful run, delete sibling dated runs older than N days (0 = keep)

Usage:
    python3 td_logical_backup.py                 # backup
    python3 td_logical_backup.py --dry-run       # list scope, write nothing
    python3 td_logical_backup.py --verify DIR    # recount live tables against DIR/MANIFEST.json

What this does NOT capture (state it in every runbook): macros, stored procedures, UDFs/UDTs, triggers,
join/hash indexes, statistics, users, roles, grants, database sizing, authorization passwords, DBS Control.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as _dt
import json
import os
import re
import shutil
import sys

try:
    import teradatasql
except ImportError:  # pragma: no cover
    sys.stderr.write("td_logical_backup: the 'teradatasql' package is required (pip install teradatasql)\n")
    sys.exit(2)

GENERATOR = "teradata-vantage/td_logical_backup 1.0.0"
CSV_FORMAT = "v2"
NULL_SENTINEL = "\\N"      # SQL NULL; distinguishable from the empty string
B64_PREFIX = "\\b64:"      # binary values, base64-encoded
FETCH = 5000

# Teradata system databases excluded from ALL_USER discovery. Name one explicitly in TD_DATABASES to include it.
SYSDB = {
    "DBC", "SYSLIB", "SYSUDTLIB", "TD_SYSFNLIB", "SystemFe", "SYSUIF", "TDStats", "TDMaps",
    "Sys_Calendar", "SQLJ", "TDQCD", "SysAdmin", "tdwm", "TD_SERVER_DB", "SYSSPATIAL",
    "TD_SYSXML", "LockLogShredder", "TDBCMgmt", "dbcmngr", "SYSBAR", "TDaaS_DB",
    "TD_ANALYTICS_DB", "TD_SYSAI", "TD_MLDB", "TD_VAL", "TD_OTFDB", "PDCRDATA", "PDCRAdmin",
    "PDCRSTG", "PDCRADM", "PDCRINFO", "mldb", "val", "SYSJDBC",
}
_SYSDB_LOWER = {s.lower() for s in SYSDB}
_STAMP_RE = re.compile(r"^\d{8}T\d{6}$")


def _die(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"td_logical_backup: {msg}\n")
    sys.exit(code)


def _settings() -> dict:
    missing = [k for k in ("TD_HOST", "TD_USER", "TD_PASSWORD") if not os.environ.get(k)]
    if missing:
        _die("refusing to run: set " + ", ".join(missing) + " in the environment (no defaults on purpose)")
    return {
        "host": os.environ["TD_HOST"],
        "port": os.environ.get("TD_PORT", "1025"),
        "user": os.environ["TD_USER"],
        "password": os.environ["TD_PASSWORD"],
        "logmech": os.environ.get("TD_LOGMECH", "TD2"),
        "databases": os.environ.get("TD_DATABASES", "ALL_USER").strip(),
        "root": os.environ.get("TD_BACKUP_DIR", "./td_backups"),
        "retention_days": int(os.environ.get("TD_RETENTION_DAYS", "0") or 0),
    }


def _connect(s: dict):
    # connect_timeout is milliseconds and bounds only the TCP connect, not the logon handshake.
    return teradatasql.connect(host=s["host"], dbs_port=str(s["port"]), user=s["user"],
                               password=s["password"], logmech=s["logmech"], connect_timeout=20000)


def _q(ident: str) -> str:
    """Double-quote an identifier so names with odd characters survive."""
    return '"' + ident.replace('"', '""') + '"'


def _encode(row):
    out = []
    for v in row:
        if v is None:
            out.append(NULL_SENTINEL)
        elif isinstance(v, (bytes, bytearray, memoryview)):
            out.append(B64_PREFIX + base64.b64encode(bytes(v)).decode("ascii"))
        else:
            out.append(v)
    return out


def _discover_databases(conn, spec: str) -> list[str]:
    if spec.upper() == "ALL_USER":
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT TRIM(DatabaseName) FROM DBC.TablesV "
                    "WHERE TableKind IN ('T','O','V') ORDER BY 1")
        return [r[0] for r in cur.fetchall() if r[0].lower() not in _SYSDB_LOWER]
    return [d.strip() for d in spec.split(",") if d.strip()]


def _objects(conn, db: str):
    cur = conn.cursor()
    # T = table, O = NoPI / queue table (treated as table), V = view
    cur.execute("SELECT TRIM(TableName), TableKind FROM DBC.TablesV "
                "WHERE DatabaseName = ? AND TableKind IN ('T','O','V') ORDER BY 1", [db])
    return [(r[0].strip(), (r[1] or "").strip()) for r in cur.fetchall()]


def _show(conn, kind: str, db: str, name: str) -> str:
    cur = conn.cursor()
    cur.execute(f"SHOW {kind} {_q(db)}.{_q(name)}")
    return "".join(str(r[0]) for r in cur.fetchall())


def _dump_table(conn, db: str, name: str, path: str) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {_q(db)}.{_q(name)}")
    cols = [d[0] for d in cur.description]
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        while True:
            batch = cur.fetchmany(FETCH)
            if not batch:
                break
            w.writerows(_encode(r) for r in batch)
            n += len(batch)
    return n


def _prune(root: str, keep_dir: str, retention_days: int) -> list[str]:
    """Delete sibling dated run directories older than retention_days that look like ours (have a MANIFEST.json)."""
    if retention_days <= 0:
        return []
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=retention_days)
    removed = []
    for entry in sorted(os.listdir(root)):
        full = os.path.join(root, entry)
        if full == keep_dir or not os.path.isdir(full) or not _STAMP_RE.match(entry):
            continue
        if not os.path.exists(os.path.join(full, "MANIFEST.json")):
            continue
        try:
            when = _dt.datetime.strptime(entry, "%Y%m%dT%H%M%S").replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
        if when < cutoff:
            shutil.rmtree(full)
            removed.append(full)
    return removed


def backup(dry_run: bool) -> int:
    s = _settings()
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = os.path.join(s["root"], stamp)
    conn = _connect(s)
    databases = _discover_databases(conn, s["databases"])
    print(f"scope: {len(databases)} database(s) [{s['databases']}] -> {out}", flush=True)
    if not databases:
        _die("no databases in scope; nothing to back up", 1)

    if dry_run:
        for db in databases:
            objs = _objects(conn, db)
            nt = sum(1 for _, k in objs if k != "V")
            nv = sum(1 for _, k in objs if k == "V")
            print(f"  {db}: {nt} tables, {nv} views")
        print("dry-run: nothing written")
        return 0

    os.makedirs(out, exist_ok=False)
    manifest = {
        "generator": GENERATOR, "created_utc": stamp, "csv_format": CSV_FORMAT,
        "null_sentinel": NULL_SENTINEL, "binary_prefix": B64_PREFIX,
        "source": {"host": s["host"], "port": str(s["port"]), "user": s["user"], "logmech": s["logmech"]},
        "scope": s["databases"], "databases": {},
    }
    grand_rows = 0
    total_errors = 0
    for db in databases:
        dbdir = os.path.join(out, db)
        os.makedirs(dbdir, exist_ok=True)
        info = {"tables": {}, "views": [], "errors": []}
        try:
            objs = _objects(conn, db)
        except Exception as e:  # noqa: BLE001
            info["errors"].append(f"(list objects): {str(e)[:200]}")
            manifest["databases"][db] = info
            total_errors += 1
            print(f"  ! {db}: cannot list objects: {str(e)[:120]}", flush=True)
            continue
        for name, kind in objs:
            try:
                if kind == "V":
                    with open(os.path.join(dbdir, f"{name}.view.sql"), "w", encoding="utf-8") as f:
                        f.write(_show(conn, "VIEW", db, name))
                    info["views"].append(name)
                    continue
                with open(os.path.join(dbdir, f"{name}.table.sql"), "w", encoding="utf-8") as f:
                    f.write(_show(conn, "TABLE", db, name))
                n = _dump_table(conn, db, name, os.path.join(dbdir, f"{name}.csv"))
                info["tables"][name] = n
                grand_rows += n
                print(f"  {db}.{name}: {n} rows", flush=True)
            except Exception as e:  # noqa: BLE001
                msg = f"{name} ({kind}): {str(e)[:200]}"
                info["errors"].append(msg)
                total_errors += 1
                print(f"  ! {db}.{msg}", flush=True)
        manifest["databases"][db] = info
        print(f"== {db}: {len(info['tables'])} tables, {len(info['views'])} views, "
              f"{len(info['errors'])} errors ==", flush=True)

    manifest["database_count"] = len(manifest["databases"])
    manifest["grand_total_rows"] = grand_rows
    manifest["error_count"] = total_errors
    with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    removed = _prune(s["root"], out, s["retention_days"])
    # One summary line with the database count: a run covering fewer databases than the last one is a fault.
    print(f"DONE databases={manifest['database_count']} rows={grand_rows} errors={total_errors} "
          f"dir={out} pruned={len(removed)}", flush=True)
    return 1 if total_errors else 0


def verify(backup_dir: str) -> int:
    s = _settings()
    mpath = os.path.join(backup_dir, "MANIFEST.json")
    if not os.path.exists(mpath):
        _die(f"no MANIFEST.json in {backup_dir}")
    with open(mpath, encoding="utf-8") as f:
        manifest = json.load(f)
    conn = _connect(s)
    mismatches = 0
    missing_files = 0
    recorded_errors = 0
    checked = 0
    for db, info in manifest.get("databases", {}).items():
        recorded_errors += len(info.get("errors", []))
        for name, expected in info.get("tables", {}).items():
            checked += 1
            csv_path = os.path.join(backup_dir, db, f"{name}.csv")
            if not os.path.exists(csv_path):
                missing_files += 1
                print(f"  MISSING FILE {db}.{name}")
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {_q(db)}.{_q(name)}")
                live = int(cur.fetchone()[0])
            except Exception as e:  # noqa: BLE001
                mismatches += 1
                print(f"  UNKNOWN {db}.{name}: live recount failed: {str(e)[:120]}")
                continue
            if live != int(expected):
                mismatches += 1
                print(f"  MISMATCH {db}.{name}: manifest={expected} live={live}")
    verdict = "PASS" if (mismatches == 0 and missing_files == 0 and recorded_errors == 0) else "INCOMPLETE"
    print(f"VERIFY {verdict} tables={checked} mismatches={mismatches} missing_files={missing_files} "
          f"recorded_errors={recorded_errors} dir={backup_dir}")
    return 0 if verdict == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="list the databases and objects in scope; write nothing")
    ap.add_argument("--verify", metavar="DIR", help="recount live tables against DIR/MANIFEST.json")
    args = ap.parse_args()
    if args.verify:
        return verify(args.verify)
    return backup(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
