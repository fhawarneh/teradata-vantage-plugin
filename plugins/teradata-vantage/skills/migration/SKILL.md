---
name: migration
description: Use when moving a workload onto Teradata Vantage or off it - inventorying what exists, sizing the target, ordering the move so dependencies land first, moving the data, proving the target matches the source, and cutting over. The SQL dialect half is handled by the teradata-sql skill; this one owns the project around it, including the checks that decide whether a migration is actually finished.
when_to_use: migrate to Teradata; migrate off Teradata; move from Oracle or Snowflake or Postgres or SQL Server to Teradata; migration plan; what order do I migrate these tables; how do I validate a migration; row counts do not match after migration; cutover; parallel run; how big will this be on Teradata; migration waves; lift and shift.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[plan | inventory <db> | order <db> | validate <src> vs <tgt> | cutover]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__base_databaseList
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__dba_tableSpace
  - mcp__plugin_teradata-vantage_teradata__dba_databaseSpace
  - mcp__plugin_teradata-vantage_teradata__qlty_columnSummary
  - mcp__plugin_teradata-vantage_teradata__qlty_univariateStatistics
  - Workflow
  - Workflow(teradata-vantage:drop-impact)
---

# Migrating onto or off Teradata

Most of a migration is not SQL translation. It is knowing what you have, moving it in an order that
works, and proving the target is right — and that last one is where migrations quietly fail.

**The dialect half is already covered.** `teradata-vantage:teradata-sql` →
`references/porting-sql.md` carries 54 constructs from Postgres, Oracle, Snowflake and SQL Server, each
executed against a live system, with the error code Teradata returns. Do not re-derive it; the things
that catch people are there — no `BOOLEAN`, no `IF EXISTS`, no `TRUNCATE`, no `LIMIT`, and `MERGE`
requiring the target's primary index in its `ON` clause.

## 1. Inventory — what is actually in scope

```
base_databaseList()
base_tableList(database_name="<db>")
base_tableDDL(database_name="<db>", table_name="<t>")
```

Three things people forget to count, and each of them turns up late:

- **Views, not just tables.** A view is a migration item with its own dialect problems.
- **Macros, stored procedures and triggers.** Query `DBC.TablesV.TableKind` — `M`, `P`, `G` — rather than
  assuming tables and views are all there is.
- **Anything loaded by an external job.** Structural inspection cannot see an ETL job, so a table that
  nothing appears to feed may still be fed nightly. The `lineage` skill is explicit about this blind
  spot; it applies squarely here.

## 2. Size the target before moving anything

```
dba_tableSpace(database_name="<db>")
dba_databaseSpace(database_name="<db>")
```

Teradata sizes differently from the source. Compression, the primary index and `SET` versus `MULTISET`
all change the answer, so **measure a representative table after loading it and extrapolate** rather than
trusting the source's byte count.

⚠️ **Size the parent database first.** A child is carved from its parent's unallocated `PERM`, so a
`CREATE DATABASE … AS PERM = n` fails with `Error 3541` when the parent has less than `n` free — a
message that reads like syntax and means "there is no room". This has bitten this project: a system
showing 97 GB free had a parent with 0.5 GB, and two `CREATE`s failed silently inside a script that
tolerated the wrong error codes. Diagnose on the parent, never on the system total.

## 3. Order the move — dependencies first

This is what the `lineage` skill's migration waves are for, and it is already built:

```
Workflow({name: "teradata-vantage:drop-impact", args: {objects: ["<db>.<table>"]}})
```

Group objects by `nearest_root` and `downstream_level`: wave 0 has no upstream dependency, wave N cannot
start until N-1 has landed. **An edge repository must exist first** — the `lineage` skill covers building
one from view text, which is exactly the situation a migration is in.

## 4. Move the data

The `loading` skill owns this. Briefly: an empty target is FastLoad or TPT Load; if the extract can land
in object storage, `READ_NOS` avoids the client entirely and costs no load slot.

## 5. Validate — the step that decides whether it is done

**Row counts agree far more often than data does.** Run all four, in this order, and report each:

1. **Row count** per table, source against target.
2. **Aggregates on every numeric column** — `SUM` and `AVG`. A sum mismatch with matching row counts
   means a type or precision problem, not a missing-row problem.
3. **Null and distinct profiles** — `qlty_columnSummary`, `qlty_univariateStatistics`. A column that is
   100% null in the target and not in the source is a column-order error in the load script.
4. **A business query run on both**, compared row for row. This is the one that catches semantic drift:
   the data is all there and an answer still differs.

Two Teradata-specific causes of a mismatch that survives the first three checks:

- **`DECIMAL/DECIMAL` division truncates its scale.** Measured in this project as 1,162 units of drift
  between a total and the sum of its parts. Cast to `FLOAT` before dividing, and derive money from
  full-precision terms rather than from already-rounded columns.
- **The default character set is LATIN**, so a non-ASCII value fails with `Error 6706` at load rather
  than arriving wrong. If rows are missing, check the load's error table before concluding they were
  never sent.

## 6. Cutover

- **Run in parallel before switching.** Both systems fed, both queried, answers compared daily. The
  comparison is the deliverable, not the parallel run itself.
- **Keep the rollback until the parallel run is clean**, not until the cutover date passes.
- **Tag the new workload with a query band** — `SET QUERY_BAND = 'app=<name>;phase=cutover;' FOR SESSION`
  — so its queries are findable in DBQL afterwards. The `workload` skill covers it.
- **Collect statistics on the migrated tables.** A migrated table with no statistics gets a bad plan and
  the migration gets blamed for being slow. The `tune` skill owns this.

## Reporting

- Say which direction the migration runs and which system you inspected. You can see Teradata; you
  usually cannot see the source, and an inventory built from one side must say so.
- Report validation as a per-table matrix — counts, aggregates, profile, business query — not a summary
  verdict. "Validated" without the four results is not a claim anyone can act on.
- Every `CREATE`, `INSERT` and `DROP` is a `[WRITE]` for the user to run.
- **Never report a migration as complete on row counts alone.** Say what was compared and what was not.

## Related

- `teradata-vantage:teradata-sql` → `references/porting-sql.md` — the dialect, measured construct by
  construct.
- `teradata-vantage:lineage` — dependency order and migration waves.
- `teradata-vantage:loading` — moving the data.
- `teradata-vantage:tune` — statistics and primary-index choice on the landed tables.
