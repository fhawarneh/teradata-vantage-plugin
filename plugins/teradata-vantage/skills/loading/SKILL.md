---
name: loading
description: Use when getting data INTO Teradata Vantage - choosing between BTEQ, FastLoad, MultiLoad, TPump, TPT and Native Object Store for a given volume and target, checking how many load slots the system allows and whether one is free, and the restrictions each utility places on the target table. Covers verifying a load landed correctly. The plugin cannot run a load; it plans, checks and verifies one.
when_to_use: how do I load data into Teradata; bulk load; ingest; fastest way to load; FastLoad; MultiLoad; TPump; FastExport; TPT; load a CSV; load a parquet file; insert millions of rows; my load is queued; load slot; utility session limit; why is FastLoad failing on a populated table; initial load; trickle feed; how do I get 500 million rows in.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[<rows> into <db>.<table> | slots | which utility | verify <db>.<table>]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__dba_tableSpace
  - mcp__plugin_teradata-vantage_teradata__dba_databaseSpace
  - mcp__plugin_teradata-vantage_teradata__qlty_columnSummary
  - mcp__plugin_teradata-vantage_teradata__qlty_missingValues
---

# Loading data into Teradata

Four questions decide everything, in this order: **how many rows, is the target empty, what does the
target carry, and is a load slot free.** Answer those and the utility chooses itself.

## What this plugin does here

It cannot run a load — there is no FastLoad tool and no file transfer, and the bundled server is
read-only through `base_readQuery`. What it does is the part that goes wrong: pick the right utility,
check the target will accept it, check a slot is available, and verify afterwards that what landed is
what was sent. The script itself is yours to run.

## 1. Choosing

The full decision table, the TPT operator names and the restrictions live in
**`teradata-vantage:sql-files` → `references/tpt.md`**. The short version:

| Rows | Target | Reach for |
|---|---|---|
| up to a few thousand, or DDL | anything | BTEQ `.IMPORT` |
| large | **empty** | FastLoad / TPT **Load** |
| large, mixed insert-update-delete | **populated** | MultiLoad / TPT **Update** |
| continuous trickle | populated | TPump / TPT **Stream** — no load slot |
| files already in object storage | anything | `READ_NOS` — see the `archive` skill |
| anything new you are writing | — | TPT |

**Object storage is a load path, not only an archive path.** If the data is already Parquet or CSV in S3
or Azure Blob, `READ_NOS` reads it directly and an `INSERT … SELECT FROM READ_NOS(…)` loads it with no
utility, no load slot and no client-side file handling. The `archive` skill owns that syntax. People
reach for FastLoad out of habit when the file is already in the object store.

## 2. Check the target BEFORE writing the script

FastLoad's restrictions are the most common cause of a failed load, and every one of them is visible in
the DDL first:

```
base_tableDDL(database_name="<db>", table_name="<table>")
```

**FastLoad requires the target to be empty, with no secondary indexes, join indexes, triggers or
referential integrity.** A populated target is not a FastLoad job — that is MultiLoad. Read the DDL and
say which of these apply rather than letting the utility discover it.

Then check the space will hold it:

```
dba_databaseSpace(database_name="<db>")
```

A child database is carved from its parent's unallocated space, so a load can fail with
`Error 3541 The request to assign new PERMANENT space is invalid` — which reads like syntax and means
"there is no room". The `health` skill covers that trap in full.

## 3. How many load slots do you actually get

`tpt.md` says the number of concurrent utilities is configurable and must be verified per system. **Here
is the query that verifies it** — the limits are TDWM Utility Session rules:

```sql
SELECT TRIM(i.RuleName), d.UtilSessions
FROM   TDWM.RuleInfoV i
JOIN   TDWM.RuleDefs  d ON i.RuleId = d.RuleId
WHERE  TRIM(i.RuleType) = 'Utility Session'
ORDER  BY 1;
```

Measured on one Vantage 20.00 — this is what the answer looks like:

```
FastLoad+MultiLoad-Default   4        FastExport-Default   4        ARC-Default   4
FastLoad+MultiLoad-Large     4        FastExport-Large     4        ARC-Large     6
FastLoad+MultiLoad-Small     2        FastExport-Small     2        BAR           1
```

⚠️ **The join is required.** `TDWM.RuleDefs.RuleType` is a numeric code (`4` = utility session), not
text; `TDWM.RuleInfoV` is the view that decodes it. Filtering `RuleDefs` on
`RuleType = 'Utility Session'` returns **zero rows with no error** — measured.

A job that cannot get a slot **waits**; it does not fail. That is why a load that "hangs" is usually a
workload question, not a load question — hand it to the `workload` skill, which reads the delay counters.
**TPump consumes no slot**, which is frequently the deciding factor for a feed that must run during
business hours.

## 4. Verify what landed

A load that reports success and loaded the wrong thing is the failure that survives to production.
Three checks, in order:

1. **Row count** — against what the source said it sent, not against what looks plausible.
2. **`qlty_missingValues`** on the columns that matter. A column that is 100% null usually means a
   column-order mismatch in the script, not missing source data.
3. **An aggregate** — a `SUM` over the money column compared to the source total. Row counts match far
   more often than sums do.

Error tables are part of the answer, not an afterthought: MultiLoad and TPT write rejected rows to their
error tables, and a job can report success with rows sitting in one. Check them and report the count.

Two charset traps this repo has hit, both of which look like corrupt data:

- **The default character set is LATIN.** A non-ASCII byte fails with
  `Error 6706 The string contains an untranslatable character`. An em-dash in a name is enough.
- **`TIMESTAMP(0)` rejects microseconds** with `Error 5404 Datetime field overflow`, which reads like an
  out-of-range year and is not.

## Reporting

- State the utility, the reason it was chosen, and the restriction that ruled out the alternative.
- Give the slot limit and where it came from; do not assert a number you did not query.
- After a load: rows in, rows landed, rejected rows, and one aggregate compared.
- The script and the load are `[WRITE]` and are the user's to run. Hand them over; do not narrate having
  run them.

## Related

- `teradata-vantage:sql-files` → `references/tpt.md` — the full decision table, TPT operator syntax,
  restrictions, and verifying a script without running it.
- `teradata-vantage:archive` — `READ_NOS` / `WRITE_NOS` and Iceberg, for data already in object storage.
- `teradata-vantage:workload` — why a load is queued rather than running.
- `teradata-vantage:pipelines` — running loads on a schedule through Airflow or dbt.
