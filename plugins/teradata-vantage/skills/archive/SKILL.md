---
name: archive
description: Use when moving cold or aged Teradata table data out of perm (block) storage into object storage — as Parquet via Native Object Store (WRITE_NOS + foreign table) or as an Apache Iceberg table in a DATALAKE (Open Table Format) — and when inventorying, federating, restoring or cleaning up such archives. Covers temperature classification, exact-percentage slices and verify-before-delete.
when_to_use: archive this table; offload cold rows to S3, MinIO or object storage; tier <db>.<table> 70/30; what is already archived; restore the cold slice; query hot and cold together; Iceberg, datalake, WRITE_NOS, READ_NOS, foreign table; hot warm cold classification; purge the archive; errors 6881 6953 4969 9134 4893 7825 7454 5404 3654 5407 2666 3706 on NOS or OTF statements.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[classify <db>.<table> | offload <db>.<table> [cold|warm|N%] [nos|iceberg] | inventory <db> | federate <db>.<table> | restore <db>.<table> | cleanup <db>.<table>]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__base_tableUsage
  - mcp__plugin_teradata-vantage_teradata__dba_tableSpace
  - mcp__plugin_teradata-vantage_teradata__dba_tableUsageImpact
  - Read
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/td_temperature.py:*)
  - Workflow
  - Workflow(teradata-vantage:drop-impact)
---

# Archive Teradata data to object storage (NOS Parquet or Iceberg)

Move a slice of a **block** table (a normal perm-space table) into **object storage**, keep it queryable with
SQL, verify it, and only then remove it from block. Every step is plain SQL: reads and verification through
`base_readQuery`; DDL/DML through `base_writeQuery`. Teradata Vantage 17.x/20.x dialect; NOS/OTF availability
and syntax vary by release — verify against yours. Community plugin, not affiliated with Teradata.

**Write availability.** The bundled upstream 0.2.6 server registers `base_readQuery` as its ONLY SQL executor;
`base_writeQuery` exists only when the connected server provides one (a bridged deployment that adds it). Without
it the agent has NO write path: generate each statement exactly, show its blast radius, hand it to the user to
run in their own SQL client, then verify with `base_readQuery`. NEVER push a write through `base_readQuery` or
switch the guard to audit mode.

**Safety model.** `base_readQuery` is held read-only by a PreToolUse hook (one statement starting
SELECT/WITH/EXPLAIN/SHOW/HELP). It **also denies `WRITE_NOS(`** — the export below is SELECT-shaped but writes
Parquet objects to your store, so it must go through `base_writeQuery` or your own SQL client. Destructive
`base_writeQuery` statements (DELETE, DROP, TRUNCATE, INSERT, UPDATE, MERGE, ALTER, RENAME, REPLACE, GIVE, …) raise an approval prompt;
`DROP FOREIGN TABLE` is exempt (metadata-only). `CREATE FOREIGN TABLE`, `CREATE AUTHORIZATION` and
`CREATE VIEW` run without a prompt. Guards prevent mistakes; they are not a security boundary.
`TERADATA_ALLOW_WRITES=0` disables all writes.

**Vocabulary.** Block = a perm-space table. NOS = S3/Azure/GCS or an on-prem S3-compatible store (MinIO);
`WRITE_NOS`/`READ_NOS` export/read objects; a foreign table is the SQL surface over exported Parquet.
DATALAKE/OTF = a Teradata object binding a catalog (Hive Metastore, Glue, Unity) + object storage whose tables
are native Apache Iceberg. AUTHORIZATION = the database object holding the store's key/secret.

## Decision table — NOS Parquet vs Iceberg

| | NOS Parquet | Iceberg (OTF) |
|---|---|---|
| Write | `WRITE_NOS … STOREDAS('PARQUET')` | `CREATE TABLE <datalake>.<otf_db>.<t>_cold AS (SELECT … WHERE <pred>) WITH DATA` |
| Register | `CREATE FOREIGN TABLE <db>.<t>_cold` | none — the Iceberg table IS the archive |
| Name | 2-level `<db>.<t>_cold` | 3-level `<datalake>.<otf_db>.<t>_cold` |
| Cleanup | `DROP FOREIGN TABLE` + delete objects out-of-band | `DROP TABLE … PURGE ALL` (removes files too) |
| Catalog / versioning | none | catalogued; snapshots + time travel (`TD_SNAPSHOTS`, `FOR SNAPSHOT AS OF`) |
| Needs | `EnableNOS`, two AUTHORIZATIONs, trusted TLS or HTTPS disabled | OTF installed + enabled, non-DBC service user, a DATALAKE — `references/otf-enablement.md` |

Default to NOS; choose Iceberg when other engines must read a catalogued, versioned table.

## Step 0 — Preconditions (read-only; STOP if any fails)

```sql
SELECT FunctionName FROM DBC.FunctionsV WHERE UPPER(FunctionName) IN ('READ_NOS','WRITE_NOS','READ_NOS_CONTRACT');
SELECT TRIM(TableName) AS auth_name, CreateTimeStamp FROM DBC.TablesV
  WHERE TableKind='X' AND DatabaseName='<db>' ORDER BY 1;   -- AUTHORIZATIONs are 'X'; 'A' is an aggregate UDF
SELECT DatalakeName, OTFTableFormat, CatalogType, StorageLocation FROM DBC.DatalakeInfoV;  -- Iceberg only
```
ALWAYS also: `base_tableDDL` on the source, `SELECT COUNT(*)`, `dba_tableSpace` (CurrentPerm before). NEVER run
`WRITE_NOS` while the LOCATION still holds `<placeholder>` text. No band or percentage named → classify first,
propose COLD. An EMPTY authorization list is the normal fresh-system case — create BOTH A1 objects, do not
halt; exactly one hit means its counterpart is missing. `SHOW AUTHORIZATION <db>.<auth_name>;` per hit is the
only way to tell SIMPLIFIED from DEFINER TRUSTED — DBC.TablesV records the object, never its kind.

## Step 1 — Classify by temperature (read-only)

Generate the SQL with the bundled script (it owns the type-adaptive cast and the date math):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/td_temperature.py lookup <db>.<table> <date_col>            # ColumnType code
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/td_temperature.py classify <db>.<table> --date-column <date_col> \
        --column-type TS --cold-days 365 --warm-days 90                                           # 4-bucket SQL
```
Rules it encodes (apply them if you hand-write):
- **Choose the date expression by the column's real `DBC.ColumnsV.ColumnType`, not its name.** `DA`/`TS`/`SZ`
  → `CAST(<col> AS DATE)`; `CV`/`CF`/unknown → `TRYCAST(SUBSTRING(<col> FROM 1 FOR 10) AS DATE)`. SUBSTRING
  on a datetime → **5407**; `CAST(<varchar with time> AS DATE)` → **2666**. Alias ColumnType as `coltype`
  (`ct` is reserved, **3707**).
- **AS_OF = `(SELECT MAX(<date_expr>) FROM <db>.<t>)`** by default (historical data is not "today");
  `CURRENT_DATE` only when the user asks for calendar age.
- **Cutoffs are integer-day subtraction: `AS_OF - 365`, `AS_OF - 90`.** House style, because it reads the same
  on a DATE and a TIMESTAMP cast to DATE and needs no precision thought. `AS_OF - INTERVAL '365' DAY` is also
  valid Teradata and computes correctly — do not "fix" it in someone else's SQL.
- Half-open bands: cold `< AS_OF - cold_days`; warm `>= AS_OF - cold_days AND < AS_OF - warm_days`; hot
  `>= AS_OF - warm_days`; plus **uncastable** `<date_expr> IS NULL`. ALWAYS assert cold + warm + hot +
  uncastable = COUNT(*). Uncastable rows stay in block.
- Never pick a column ending `id`/`_id` as the date column (TRYCAST of ids → all NULL → 0/0/0).
- Candidate tables: `base_tableUsage` / `dba_tableUsageImpact` (LastQueryDaysAgo, usage frequency).

## Step 2 — Choose the slice predicate

- **A band**: the predicate from Step 1.
- **An exact N% (of a band or the whole table)**: `ROW_NUMBER` in a derived table (an analytic function is
  rejected inside a WHERE subquery); whole-table slices order coldest-first, NULL dates last.
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/td_temperature.py slice <db>.<table> --percent 20 --id-column <id> [--band cold …]`
  ```sql
  <id> IN (SELECT id FROM (SELECT <id> AS id, ROW_NUMBER() OVER (ORDER BY <order_by>) AS rn
                           FROM <db>.<t> WHERE (<band_pred>)) d
           WHERE rn <= (SELECT CAST(COUNT(*) AS BIGINT)*20/100 FROM <db>.<t> WHERE (<band_pred>)))
  ```
- **A deterministic K% keep without a date**:
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/td_temperature.py hash-split <db>.<table> --key <key> --keep 70` →
  `WHERE MOD(HASHBUCKET(HASHROW(<key>)), 100) >= 70` offloads, `< 70` keeps. Stable per value; `ABS(HASHROW())`
  fails (BYTE); NOT uniform (a nominal 20% lands ~13-20%) — use ROW_NUMBER for exact figures.
ALWAYS echo the predicate and the expected counts before writing.

## Path A — NOS Parquet

### A1 Authorizations — TWO objects over the same credentials
```sql
CREATE AUTHORIZATION <db>.<auth>    USER '<ACCESS_KEY_ID>' PASSWORD '<SECRET_ACCESS_KEY>';                    -- SIMPLIFIED → WRITE_NOS
CREATE AUTHORIZATION <db>.<auth>_ft AS DEFINER TRUSTED USER '<ACCESS_KEY_ID>' PASSWORD '<SECRET_ACCESS_KEY>'; -- TRUSTED → foreign table
```
Trusted auth in WRITE_NOS → **6881**; simplified auth in a foreign table → **6953**. Qualification is inverted:
`AUTHORIZATION(<db>.<auth>)` in WRITE_NOS is qualified; `EXTERNAL SECURITY DEFINER TRUSTED <auth>_ft` MUST be
unqualified (**3706** otherwise), so the auth lives in the foreign table's database. `SHOW AUTHORIZATION`
omits the password.

### A2 Export (non-destructive; WRITE_NOS is a SELECT that returns the objects written)
```sql
SELECT NodeId, AmpId, Sequence, ObjectName, ObjectSize, RecordCount
FROM WRITE_NOS (
  ON ( SELECT * FROM <db>.<t> WHERE <pred> )
  USING LOCATION('<location_base>/<db>/<t>/cold/<run_token>/') AUTHORIZATION(<db>.<auth>)
        STOREDAS('PARQUET') COMPRESSION('SNAPPY') MAXOBJECTSIZE('256MB') NAMING('RANGE')
) AS d;
```
NEVER write twice to one path — WRITE_NOS does not overwrite (**9134**); use a fresh sub-path per run and a
deterministic foreign-table NAME. It mutates external state, so it goes through `base_writeQuery` (or the
user's client); `SUM(RecordCount)` = rows exported, `SUM(ObjectSize)` = bytes landed.

### A3 Register (idempotent DROP-then-CREATE, deterministic name, no column list)
```sql
DROP FOREIGN TABLE <db>.<t>_cold;            -- ignore 3807 if absent; metadata-only, no prompt
CREATE FOREIGN TABLE <db>.<t>_cold, EXTERNAL SECURITY DEFINER TRUSTED <auth>_ft
USING ( LOCATION('<location_base>/<db>/<t>/cold/<run_token>/') );
SELECT COUNT(*) FROM <db>.<t>_cold;          -- must equal the exported row count
```
A name regenerated between steps → **3807** later; a skipped DROP → **3803**. No `(Location VARCHAR(2048),
Payload DATASET STORAGE FORMAT PARQUET)` list for inferred Parquet — that form is for JSON/CSV (**3706**).

### A4 Verify, then delete from block (DESTRUCTIVE — the prompt is intentional)
```sql
SELECT COUNT(*) FROM <db>.<t>_cold;                                   -- proven readable, count matches
DELETE FROM <db>.<t> WHERE <id> IN (SELECT <id> FROM <db>.<t>_cold);  -- by MEMBERSHIP, not the slice predicate
COLLECT STATISTICS ON <db>.<t>;
```
NEVER re-run a ROW_NUMBER/hash/date predicate as the DELETE — it re-sorts the live table mid-delete (measured
minutes vs 0.3 s by membership); predicate form only when no stable id column exists.

### A5 Lifecycle
- **Inventory** — foreign tables report `TableKind` inconsistently across releases ('F', 'O', 'N', even 'T');
  NEVER filter on it:
  ```sql
  SELECT TRIM(TableName) FROM DBC.TablesV WHERE DatabaseName='<db>' AND RequestText LIKE '%FOREIGN TABLE%' ORDER BY 1;
  ```
  then `SELECT COUNT(*) FROM <db>.<t>_cold` per slice and `SHOW TABLE <db>.<t>_cold` for its LOCATION.
- **Objects and schema** — `READ_NOS` with `NOSREAD_KEYS` / `NOSREAD_PARQUET_SCHEMA`; it needs a dummy `ON`
  row. Exact statements: `references/nos-transport.md`.
- **Federated view** — explicit columns, NEVER `SELECT *` (type drift → **3706**); the foreign side re-reads
  NOS-inferred types (TIMESTAMP(6), DECIMAL, wider VARCHAR), so CAST the `_cold` side to the base types:
  ```sql
  CREATE VIEW <db>.<t>_all AS
    SELECT <c1>, <c2>, … FROM <db>.<t>
    UNION ALL SELECT CAST(<c1> AS <base_type>), CAST(<c2> AS <base_type>), … FROM <db>.<t>_cold;
  ```
- **Restore** (INSERT prompts): `INSERT INTO <db>.<t> (<cols>) SELECT <CAST(col AS base_type)…> FROM
  <db>.<t>_cold WHERE <id> NOT IN (SELECT <id> FROM <db>.<t>);` → verify the count grew → `DROP FOREIGN TABLE`
  → `COLLECT STATISTICS`.
- **Cleanup** — `DROP FOREIGN TABLE` removes ONLY metadata. **Teradata has no SQL that deletes the Parquet
  objects**; remove them out-of-band (`aws s3 rm`, `mc rm`, console) — ALWAYS say so; never a bucket-root
  prefix. Before dropping a block table launch `Workflow(teradata-vantage:drop-impact)`; if the tool is absent
  run `dba_tableUsageImpact` + `dba_tableSqlList` and report before any DROP.

### A6 On-prem S3-compatible LOCATION and transport
Path-style `/s3/<host>:<port>/<bucket>/<path>/` only; virtual-hosted → **4969**. TLS trust vs DBS Control NOS
101 `Disable HTTPS`, and internal 245 `ColumnarPurchased` for Parquet foreign tables (**3706**) — GDO
settings, lost on a rebuild: `references/nos-transport.md`.

## Path B — Iceberg (Open Table Format)

### B0 Preconditions (read-only; enablement runbook: `references/otf-enablement.md`)
```sql
SELECT TableName FROM DBC.TablesV WHERE DatabaseName='TD_OTFDB' AND TableKind='L';        -- TD_ICEBERG_READ/WRITE
SELECT FunctionName FROM DBC.FunctionsV WHERE UPPER(FunctionName) LIKE '%ICEBERG%';         -- 0 rows = not installed
SELECT DatalakeName, OTFTableFormat, CatalogType, CatalogLocation, StorageLocation FROM DBC.DatalakeInfoV;
```
The DATALAKE must exist, created by a **non-DBC service user** with `GRANT CREATE SERVER ON TD_SERVER_DB`
(authorizations cannot live in DBC, **3524**). A different connecting user needs the same authorizations in
`TD_SERVER_DB`. `CREATE VIEW` over an OTF table is rejected (**9134**).

### B1 Offload (four statements, this order; CTAS ONLY)
```sql
CREATE DATABASE <datalake>.<otf_db>;                                       -- idempotent; tolerate only "already exists"
DROP TABLE <datalake>.<otf_db>.<t>_cold PURGE ALL;                         -- best-effort pre-drop (7825 when absent)
CREATE TABLE <datalake>.<otf_db>.<t>_cold AS (SELECT * FROM <db>.<t> WHERE <pred>) WITH DATA;
SELECT COUNT(*) AS archived_count FROM <datalake>.<otf_db>.<t>_cold;       -- verify
```
NEVER `INSERT … SELECT` into an existing OTF table — on the builds measured it wrote duplicated rows; CTAS
wrote correctly. Add rows as a second slice table (`<t>_warm`). Then the block DELETE by
membership (prompts): `DELETE FROM <db>.<t> WHERE <id> IN (SELECT <id> FROM <datalake>.<otf_db>.<t>_cold);
COLLECT STATISTICS ON <db>.<t>;`

### B2 Lifecycle
- **Inventory** — `DBC.DatalakeInfoV` + `SELECT COUNT(*) FROM <datalake>.<otf_db>.<t>_cold`. "No such
  table" = nothing archived yet; do not speculate about paths or catalogs.
- **Snapshots:** `SELECT * FROM TD_SNAPSHOTS(ON <datalake>.<otf_db>.<t>_cold) AS d;` → `snapshotId`,
  `snapshotTimestamp`, `summary` (JSON: `operation`, `total-records`, `total-data-files`, `total-files-size`).
  **`SELECT *` ONLY** — a column subset or `COUNT(*)` raises **9723**.
- **Time travel:** `SELECT COUNT(*) FROM … FOR SNAPSHOT AS OF '<snapshot_id>';` / `SELECT TOP 5 * FROM … FOR
  SNAPSHOT AS OF '<snapshot_id>';` — quote the id; prefer it over `AS OF TIMESTAMP '…'` (session-vs-UTC ambiguity).
- **Federated read** — count each tier separately (two `COUNT(*)`s; no union can fail on types). Only when
  rows are wanted, a bridged UNION ALL with explicit aliased columns: TIMESTAMP →
  `CAST(CAST(<col> AS VARCHAR(19 + frac + 1)) AS TIMESTAMP(<frac>))` (a direct CAST overflows, **7454**);
  DATE → `CAST(<col> AS DATE)`; DECIMAL → `CAST(<col> AS DECIMAL(<p>,<s>))`; CHAR/VARCHAR/INT unchanged.
  Types: `SELECT TRIM(ColumnName) AS nm, TRIM(ColumnType) AS coltype, DecimalTotalDigits AS dtot,
  DecimalFractionalDigits AS dfrac FROM DBC.ColumnsV WHERE DatabaseName='<db>' AND TableName='<t>' ORDER BY
  ColumnId`. `SELECT *` unions → **3654**; bare `SELECT *` off the OTF reader → **5404**.
- **Restore (prompts):**
  ```sql
  INSERT INTO <db>.<t> (<c1>, <c2>, …)
  SELECT <bridged_c1>, <bridged_c2>, … FROM <datalake>.<otf_db>.<t>_cold a
  WHERE a.<id> NOT IN (SELECT <id> FROM <db>.<t>);            -- anti-join: re-running inserts nothing
  ```
- **Cleanup (prompts):** `DROP TABLE <datalake>.<otf_db>.<t>_cold PURGE ALL;` — **PURGE ALL is required for
  OTF tables (4893 without it)** and deletes the files in the same step. Datalake locations are immutable:
  changing endpoints = `DROP DATALAKE` + `CREATE DATALAKE`.

## Error table

Every code an archive or restore raises, with the step that raises it and the fix:
`references/error-table.md`. The four that cost the most time: **6881** (trusted auth passed to
WRITE_NOS), **6953** (simplified auth passed to a foreign table), **4969** (network/TLS, never SQL)
and **3706** on a DATALAKE (OTF not enabled — a DBS Control field, not a syntax error).

## Reporting — what moved

Report the OUTCOME as a **block-vs-object split**: rows and bytes now in object storage (WRITE_NOS
`ObjectSize` sum / NOSREAD_KEYS sizes, or Iceberg `total-files-size`) versus rows and `CurrentPerm` left in
block (`dba_tableSpace` before/after → perm reclaimed). NEVER present a re-classified hot/warm/cold percentage
of the now-smaller table as the outcome — the shrunken denominator reads "44% cold" right after archiving cold
rows; compute any post-archive breakdown against the ORIGINAL total. "Nothing was lost" only when block + object
equals the pre-archive count you measured. Measured numbers only; no cost claims.

## Operating rules

- ALWAYS: preconditions → classify → echo predicate + expected counts → write → verify count → delete → report.
- NEVER delete from block before the archive count is proven; NEVER delete by re-running the slice predicate.
- NEVER `INSERT INTO` an OTF table; NEVER `CREATE VIEW` over one; NEVER `SELECT *` across tiers.
- The approval prompt on DELETE/DROP/INSERT is intentional — show the exact statement and wait.
- Before any `DROP TABLE` of a block table launch `Workflow(teradata-vantage:drop-impact)`; "no usage in the
  DBQL window" is not "unused".
- NOS object cleanup is out-of-band and belongs in every NOS report.
- To the user, explain cause then fix; error codes are for building correct SQL.
