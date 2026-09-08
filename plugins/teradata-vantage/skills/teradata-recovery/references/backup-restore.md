# Backup and restore: the logical backup script, what it does not capture, cold images, DSA, and restore ordering

Purpose: how to run and verify `scripts/ops/td_logical_backup.py`, an honest statement of what a logical backup misses, when a cold image or DSA is required instead, and the restore-order lessons that only surface on a rebuilt system.

## The lesson first

A backup script that exists and has never run is worth nothing. In the incident this runbook comes from, the logical backup script had existed for months, had never been scheduled, and its hardcoded default host could not even connect; the corruption that followed was a total loss instead of an inconvenience. Rules:

- ALWAYS schedule the backup (cron or the site scheduler) the day it is written, and log the database count per run so a shrinking backup is visible.
- ALWAYS verify by recounting against the live system; a directory of CSV files is not a backup until the counts match.
- NEVER let a curated database list be the scope; discover every non-system database (`ALL_USER`) so new databases are covered automatically.

## `scripts/ops/td_logical_backup.py`

A zero-downtime logical backup over the standard `teradatasql` driver: DDL via `SHOW TABLE` / `SHOW VIEW`, every table's rows streamed to CSV, a `MANIFEST.json` with per-table row counts. It reads committed data through the engine, so it is transaction-consistent per table and needs no outage. Python 3.10+, `pip install teradatasql`.

Configuration is environment only; the script refuses to run without the first three (no defaults for host, user or password):

| Variable | Required | Meaning |
|---|---|---|
| `TD_HOST` | yes | Database node or listener address |
| `TD_USER` | yes | Logon user (needs SELECT on the target databases and on `DBC.TablesV`) |
| `TD_PASSWORD` | yes | Logon password (from a secret store or an `0600` env file, never a command line argument) |
| `TD_PORT` | no (1025) | Database port |
| `TD_LOGMECH` | no (TD2) | `TD2`, `LDAP`, `KRB5`, `JWT` as the site requires |
| `TD_DATABASES` | no (`ALL_USER`) | Comma-separated database list, or `ALL_USER` to discover every non-system database that holds tables or views |
| `TD_BACKUP_DIR` | no (`./td_backups`) | Root directory; each run writes a dated subtree `<root>/<YYYYMMDDTHHMMSS>` |
| `TD_RETENTION_DAYS` | no (0 = keep all) | After a successful run, delete sibling dated subtrees older than N days (only ones that contain a `MANIFEST.json`) |

Run and verify:

```bash
set -a; . /path/to/td-backup.env; set +a       # TD_HOST, TD_USER, TD_PASSWORD, ... mode 0600
python3 scripts/ops/td_logical_backup.py                       # backup
python3 scripts/ops/td_logical_backup.py --verify <backup_dir> # recount every table on the live system against the manifest
python3 scripts/ops/td_logical_backup.py --dry-run             # list the databases and objects in scope, write nothing
```

Layout: `<run>/<database>/<table>.table.sql`, `<table>.csv`, `<view>.view.sql`, `<run>/MANIFEST.json`.

CSV fidelity (format `v2`, recorded in the manifest): binary values are written as `\b64:<base64>`; SQL NULL is written as the sentinel `\N`; everything else is the driver's text form. An earlier format wrote `str(value)` for everything, which made a BLOB an unusable Python repr and made NULL indistinguishable from the empty string, so a backup that "succeeded" was not restorable. A restorer must decode both sentinels.

System databases are excluded from `ALL_USER` discovery by a fixed list (DBC, SYSLIB, SYSUDTLIB, TD_SYSFNLIB, SystemFe, SYSUIF, TDStats, TDMaps, Sys_Calendar, SQLJ, TDQCD, SysAdmin, tdwm, TD_SERVER_DB, SYSSPATIAL, TD_SYSXML, LockLogShredder, TDBCMgmt, dbcmngr, SYSBAR, TDaaS_DB, TD_ANALYTICS_DB, TD_SYSAI, TD_MLDB, TD_VAL, TD_OTFDB, PDCRDATA, PDCRAdmin, PDCRSTG, PDCRADM, PDCRINFO, mldb, val, SYSJDBC). Name one explicitly in `TD_DATABASES` if you really want it.

Per-object errors are caught, recorded in the manifest under `errors`, and do not abort the run; `--verify` reports `INCOMPLETE` if any exist. Exit status is non-zero on refusal, connection failure, or a verify mismatch.

## Honesty block: what a logical backup does NOT capture

A DDL+CSV backup captures table shapes, view text and rows. It does NOT capture:

- macros, stored procedures, UDFs/UDTs, triggers, join indexes, hash indexes
- collected statistics
- users, roles, profiles, grants (`DBC.AllRightsV`) and role memberships
- `CREATE DATABASE` sizing (PERM/SPOOL), the ownership tree, accounts
- authorization objects (`SHOW AUTHORIZATION` omits the password even for the ones it lists)
- DBS Control / GDO settings (see `dbscontrol-fields.md`)
- identity-column state, row-level security constraints, queue-table state
- anything in the excluded system databases

A partial backup that looks complete is worse than none. If the site needs any of the above, it needs a cold image or DSA in addition, not instead.

## Cold image backup (VM- or volume-hosted systems)

An image-level copy captures everything above, but only if the database is STOPPED: images are written continuously while the engine runs, and a copy taken from a running system is crash-consistent at best, which for a database means a possibly unrecoverable image. Procedure shape, hypervisor details out of scope:

1. `tpa stop` inside the node, then a graceful guest shutdown; wait until the platform reports the machine off.
2. Record the disk-to-device mapping and each image's size and format BEFORE copying; on a rebuild, map images by SIZE, never by device letter or filename (letters observed inside the guest did not match the hypervisor's, and the numbering was inverted between two hosts). Getting this wrong is unrecoverable.
3. Copy sparse, preserving timestamps; run the image tool's integrity check on the COPY; write checksums next to the copies.
4. Preserve damaged images by moving them aside (`broken-<date>/`) rather than deleting; expert recovery may still be possible.

The cold image and the logical backup are complements: the logical one needs the engine running, the cold one needs it stopped, and neither substitutes for the other.

## DSA through the MCP server (`bar_*` tools)

When the site runs Teradata Data Stream Architecture, the bundled server can expose the `bar` tool group (install the `bar` extra and set `DSA_BASE_URL` or `DSA_HOST`/`DSA_PORT`): `bar_manageTeradataSystem`, `bar_manageMediaServer`, `bar_manageDsaDiskFileSystem`, `bar_manageAWSS3Operations`, `bar_manageDiskFileTargetGroup`, `bar_manageJob`. `bar_manageJob` starts, aborts and deletes backup/restore jobs and always raises the plugin's approval prompt. DSA is the supported route to a full, consistent, online backup including all the objects in the honesty block; this runbook does not replace the DSA documentation.

## Restore from the logical backup

No restore script ships with this plugin (flagged as unverified work). The procedure is:

1. Size and create databases FIRST, from the parent with the room (`health` skill, `space-management.md`): `CREATE DATABASE <db> FROM <parent> AS PERM = <bytes>, SPOOL = <bytes>;` `[WRITE]`. A `3541` here is the parent, not syntax.
2. Replay `<table>.table.sql` for every table, then `<view>.view.sql` for views (views reference tables; cross-database views need the owner grant in `space-management.md` section 5). Before replaying DDL for a COPY, rewrite `GENERATED ALWAYS AS IDENTITY` to `GENERATED BY DEFAULT`; otherwise the engine regenerates keys on load and the copy has correct counts and different ids.
3. Load each CSV, decoding `\N` to NULL and `\b64:` to bytes. Truncate fractional seconds for `TIMESTAMP(0)` columns (`Error 5404: Datetime field overflow` reads like a bad year and is microseconds). Non-ASCII bytes into a LATIN column fail with `Error 6706: The string contains an untranslatable character`; declare `CHARACTER SET UNICODE` where the data needs it.
4. Re-create everything from the honesty block from the site's own DDL library (macros, procedures, grants, statistics with `COLLECT STATISTICS`).
5. Verify by recount: `SELECT COUNT(*) FROM "<db>"."<table>"` against `MANIFEST.json` for every table.

## Restore ordering and idempotency lessons

- Derived databases fail with a bare `Object does not exist` when restored before the databases they are built from. Restore parents and independent databases first, then overlays, then anything cloned from them. Write the order down; it was reconstructed from failures the first time.
- Re-running a loader against a populated MULTISET table DOUBLES every row silently (an idempotency key used for reprocessing is not a uniqueness constraint). Restore means "make it this", not "add to it": empty the target first (`DELETE FROM <db>.<table> ALL;` `[WRITE]`) or load into a fresh database.
- Point loaders at the system you mean. A loader that defaults to a cloud sandbox host will silently seed the wrong system when run against a rebuilt on-premises node.
- After the restore, re-apply DBS Control fields, re-create authorizations with their passwords, and restart the MCP server (stale pooled connections).
