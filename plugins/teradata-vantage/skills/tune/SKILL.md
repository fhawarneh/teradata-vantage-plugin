---
name: tune
description: Use when a Teradata query is slow, skewed, spool-hungry or expensive and you need to read its EXPLAIN plan, check statistics freshness, judge the primary index, partitioning and skew of the tables involved, pull the query's DBQL metrics (AMPCPUTime, skew ratios, PJI/UII, spool) and recommend concrete changes. Read-only - recommends COLLECT STATISTICS or index changes, never runs them.
when_to_use: why is this query slow; explain plan for; product join, full table scan or no confidence in the plan; high skew; spool space error 2646; which queries burn the most CPU; tune <db>.<table> queries; collect statistics recommendations; PI or PPI choice; DBQL for user <x> or table <t>; query clustering, sql_Execute_Full_Pipeline.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[<sql statement> | <db>.<table> | user <name> [days]]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__dba_tableSpace
  - mcp__plugin_teradata-vantage_teradata__dba_userSqlList
  - mcp__plugin_teradata-vantage_teradata__dba_tableSqlList
  - mcp__plugin_teradata-vantage_teradata__dba_tableUsageImpact
  - mcp__plugin_teradata-vantage_teradata__sql_Analyze_Cluster_Stats
  - Read
disallowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_writeQuery
---

# Tune a Teradata query

Read-only diagnosis: `EXPLAIN` and every catalog/DBQL query below run through `base_readQuery` (held
read-only by a PreToolUse hook — a statement starting with `EXPLAIN`, `SELECT`, `WITH`, `SHOW` or `HELP`
passes; anything else is denied). This skill does NOT run `COLLECT STATISTICS`, `CREATE INDEX`, `ALTER TABLE`
or `DIAGNOSTIC` — it writes the exact statements into the recommendations for the user to run in their own
SQL client (or through `base_writeQuery` via the `query` skill where the connected server provides that tool;
the bundled upstream 0.2.6 server registers no write tool). Plan vocabulary and catalog columns are from
Teradata documentation and field experience; verify against your release. Community plugin, not affiliated
with Teradata.

## Workflow

### 1. Get the statement and the tables
- From the user, or from DBQL: `dba_userSqlList(user_name, no_days)` / `dba_tableSqlList(table_name, no_days)`
  return `QueryID, ProcID, CollectTimeStamp, SqlTextInfo, UserName` (default 7 days). `user_name` is a
  required exact-equality match — there is no wildcard and no all-users value, so `*`, `%` and `all` return
  ZERO rows. For a workload-wide view run the un-filtered DBQL queries in `references/dbql-metrics.md` §5
  rather than guessing a user. (`dba_tableSqlList` differs: `table_name` is a `LIKE '%…%'` text match.
  `dba_sessionInfo` differs again: it does accept `*` for everyone.) An empty result can mean the name did
  not match OR that query logging is off for that user — verify the name before concluding logging is off.
- `base_tableDDL` for every table in the FROM clause: primary index (UPI/NUPI/NoPI), PARTITION BY, SET vs
  MULTISET, secondary/join indexes, column types. Qualify every name `<db>.<table>` (3807 otherwise).

### 2. EXPLAIN (single statement, the guard allows it)
```sql
EXPLAIN SELECT … ;        -- the user's statement verbatim, one statement, no trailing statements
```
Read the plan with `references/explain-plan.md`. Extract, in order: the lock level, every RETRIEVE step's
access path (unique PI / index / all-rows scan), every JOIN step's kind (merge / hash / product / nested /
exclusion) and how each side was prepared (redistributed / duplicated / local), the spool estimates and
their **confidence** (high / low / no / index join), aggregate steps, partition elimination, and the total
estimated time. The single fastest read: search the plan for `no confidence`, `product join`, `all-rows scan`
and `duplicated on all AMPs`.

### 3. Statistics freshness
```sql
HELP STATISTICS <db>.<table>;                       -- what is collected, unique values, last collect date
SELECT DatabaseName, TableName, ColumnName, StatsType, LastCollectTimeStamp, RowCount, UniqueValueCount, SampleSizePct
FROM DBC.StatsV WHERE DatabaseName='<db>' AND TableName='<table>' ORDER BY LastCollectTimeStamp;
```
Compare `RowCount` in StatsV with `SELECT COUNT(*)` — a large gap = stale statistics. Columns that appear
in joins, WHERE predicates or GROUP BY but not in StatsV → "no confidence" plans. Recommend (do not run):
```sql
COLLECT STATISTICS COLUMN (<join_col>), COLUMN (<filter_col>), COLUMN (PARTITION) ON <db>.<table>;
COLLECT STATISTICS ON <db>.<table>;                 -- refresh everything already defined
```
Sampled stats (`USING SAMPLE`) are fine on very large tables when the column is not heavily skewed.

### 4. Primary index, partitioning and skew
```sql
-- table skew across AMPs (>10% is worth a look, >30% is a problem)
SELECT DatabaseName, TableName, SUM(CurrentPerm) AS CurrentPermBytes, SUM(PeakPerm) AS PeakPermBytes,
       CAST((100 - (AVG(CurrentPerm) / NULLIFZERO(MAX(CurrentPerm)) * 100)) AS DECIMAL(5,2)) AS SkewPct
FROM DBC.AllSpaceV WHERE DatabaseName='<db>' AND TableName='<table>' GROUP BY 1,2;
-- PI value distribution (why it is skewed)
SELECT TOP 20 <pi_col>, COUNT(*) AS rows_per_value FROM <db>.<table> GROUP BY 1 ORDER BY 2 DESC;
SELECT COUNT(*) AS total_rows, COUNT(DISTINCT <pi_col>) AS distinct_pi FROM <db>.<table>;
-- partition pruning reality check
SELECT PARTITION, COUNT(*) FROM <db>.<table> GROUP BY 1 ORDER BY 1;
```
Judgement: a NUPI with few distinct values or a dominant value (NULL, 0, 'UNKNOWN') skews storage AND every
join on it; a join between tables with different PIs forces redistribution (fine if both sides are small or
stats are good); a PPI on the date column only helps when the query's predicate is on that column (equality,
range, or a `DATE` literal — not a function of the column). `dba_tableSpace` gives per-table sizes for the
"which side should be duplicated" question.

### 5. DBQL evidence for the query or the workload
```sql
SELECT TOP 20 QueryID, UserName, StartTime, AMPCPUTime, MaxAMPCPUTime, TotalIOCount, MaxAmpIO, SpoolUsage, NumSteps,
       NumOfActiveAMPs, ErrorCode, DelayTime,
       CASE WHEN AMPCPUTime < HashAmp()+1 OR (AMPCPUTime/(HashAmp()+1)) = 0 THEN 0
            ELSE MaxAMPCPUTime / (AMPCPUTime/(HashAmp()+1)) END (DECIMAL(8,2)) AS CPUSKW,
       CASE WHEN AMPCPUTime < HashAmp()+1 OR TotalIOCount = 0 THEN 0
            ELSE (AMPCPUTime*1000)/TotalIOCount END (DECIMAL(10,4)) AS PJI
FROM DBC.QryLogV
WHERE StartTime >= CURRENT_TIMESTAMP - INTERVAL '7' DAY AND UserName = '<user>'
ORDER BY AMPCPUTime DESC;
```
Definitions, thresholds (CPUSKW/IOSKW > 2 moderate, > 3 high, > 5 severe; AMPCPUTime > 100 s high, > 1000 s
very high; TotalIOCount > 1,000,000 high) and the full metric set are in `references/dbql-metrics.md`.
DBQL requires query logging to be enabled by a DBA (`BEGIN QUERY LOGGING … WITH SQL, OBJECTS`) — never
enable it from this skill.

### 6. Optional — workload clustering (`sql_*` tools)
`sql_Execute_Full_Pipeline` builds tables in a feature database (embeddings + `TD_KMeans`) — it WRITES, needs
in-database embedding models, `DBC.DBQLSqlTbl`/`DBC.DBQLogTbl` access and space, and can run for minutes:
propose it, explain the footprint, and let the user invoke it explicitly. The read-side tools
`sql_Analyze_Cluster_Stats` (rank clusters by `avg_cpu`, `avg_io`, `avg_cpuskw`, `avg_ioskw`, `avg_pji`,
`avg_uii`, `avg_numsteps`, `queries`) and `sql_Retrieve_Cluster_Queries` (sample statements per cluster) are
safe once the pipeline exists.

## Recommendation rules

- **No/low confidence on a large spool** → statistics on the named columns first; re-EXPLAIN before touching indexes.
- **Product join** with a real join condition present → usually missing stats or a type mismatch between the
  join columns (`CAST` in the ON clause, VARCHAR vs INTEGER); fix the types/stats, not the syntax.
- **Product join without a condition** → a missing ON clause or a cross join hidden in a comma FROM list.
- **All-rows scan on the biggest table with a selective predicate** → candidate for PPI on that column
  (dates) or a secondary index / join index (equality on a non-PI column); check `PARTITION` usage.
- **Redistribution of the large side** → align the PI of a frequently-joined pair, or pre-aggregate the
  large side; duplicating a small dimension is normal.
- **High CPUSKW / IOSKW** → skewed PI or a skewed join key value (NULL, 0); check the value distribution.
- **Spool errors (2646 / 3710)** → the plan is wrong (product join, no confidence) before it is a spool-size
  problem; fix the plan, then talk about `SPOOL` allocation.
- **`SELECT *` / no `TOP` / functions on the partitioning column in predicates** → rewrite suggestions with
  exact SQL.
- Never recommend a change without the EXPLAIN evidence line that motivates it.

## Errors — what the message actually means

| Code | Meaning → action |
|---|---|
| 2646 "No more spool space in <user>" | The plan's intermediate result exceeded the user's SPOOL — usually a product join or a no-confidence estimate; fix the plan first. |
| 3710 "Insufficient memory to parse this request" | The optimizer ran out of parser memory on a very large statement — split it or reduce IN-lists/CASE branches. |
| 3807 "Object '<x>' does not exist" | Unqualified name (or no privilege) — qualify `<db>.<table>`. |
| 3523 "The user does not have SELECT access to DBC.QryLogV" (or `DBC.DBQLogTbl`) | DBQL needs a grant, and TWO of them: the SQL here reads the `V` views, but six shipped tools (`dba_resusageSummary`, `dba_userDelay`, `dba_tableUsageImpact`, `base_tableUsage`, `base_tableAffinity`, `sql_Execute_Full_Pipeline`) read the BASE `DBC.DBQL*Tbl` tables, and `SELECT` on the view does not confer it on the table. Ask a DBA for both. |
| 3624 "There are no statistics defined for the table" | `HELP STATISTICS` on a table with none — that IS the finding. |
| 5315 "The user does not have STATISTICS access" | `COLLECT STATISTICS` needs the STATISTICS privilege — request it in the recommendation. |
| 3707 "Syntax error, expected something like …" | A reserved word used as an alias (`count`, `date`, `month`, `key`, `value`, `ct`, `cs`) — rename it before re-EXPLAINing. |
| 9128 / 3130 (too many spools/response rows) | Symptoms of the same wrong plan; do not raise limits first. |

## Report shape

`VERDICT` (one line: the dominant cause) → **Evidence** (the plan lines and metrics that prove it, as a
table) → **SQL run** (every EXPLAIN/catalog query) → **Actions**, each tagged `[WRITE]` when it changes the
system (`COLLECT STATISTICS …`, `CREATE JOIN INDEX …`, `ALTER TABLE … MODIFY PRIMARY INDEX …`) with its
expected effect on the plan → **Unknowns** (missing DBQL, missing privileges, stats you could not read).
Numbers verbatim from the tools; no estimated speed-ups without a re-EXPLAIN.
