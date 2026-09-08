# DBQL metrics for query tuning — views, columns, CPUSKW / IOSKW / PJI / UII definitions with exact SQL, thresholds, and how the sql_* clustering tools use them

Companion to the `tune` skill. All queries are `SELECT`s against the Database Query Log views and run through
`base_readQuery`. Column names are from Teradata documentation and the bundled server's own SQL; thresholds
are the defaults shipped in the server's `sql_opt_config.yml` (from Teradata documentation; verify against
your release — DBQL columns vary slightly by version).

## 1. Prerequisites

- Query logging must be **enabled by a DBA** (`BEGIN QUERY LOGGING WITH SQL, OBJECTS, STEPINFO ON ALL;` or
  per user/account). No logging → the views are empty for that user; report "not logged", not "no activity".
  Enabling it is a DBA write — outside the `tune` skill.
- Reading the views needs `SELECT` on `DBC.QryLogV`, `DBC.QryLogSqlV` (and `DBC.QryLogStepsV`,
  `DBC.QryLogObjectsV`, `DBC.QryLogExplainV` if used); a `3523` here is a grant problem.
- Rows land in the log tables with a delay (cache flush, typically minutes) — the last few minutes are incomplete.
- The base tables are `DBC.DBQLogTbl`, `DBC.DBQLSqlTbl`, `DBC.DBQLStepTbl`, `DBC.DBQLObjTbl`,
  `DBC.DBQLExplainTbl`; the `V` views (`DBC.QryLogV` …) are the supported interface, and the SQL in this
  reference and in the `tune` skill uses them.
- **The shipped tools are split, and the grant is not the same.** Measured against the vendored 0.2.6 wheel:
  only `dba_userSqlList` and `dba_tableSqlList` read a `V` view. `dba_resusageSummary`, `dba_userDelay`,
  `dba_tableUsageImpact`, `base_tableUsage`, `base_tableAffinity` and `sql_Execute_Full_Pipeline` read the
  BASE `DBC.DBQL*Tbl` tables. `SELECT` on `DBC.QryLogV` does NOT confer `SELECT` on `DBC.DBQLogTbl`, so an
  account granted only the views gets `Error 3523` from those six while the two view-based tools work — which
  reads like a broken tool and is a missing grant. `base_tableUsage` and `base_tableAffinity` are in
  `tv_readonly`, so this reaches read-only users too.

## 2. The views and the columns that matter

| View | Key columns |
|---|---|
| `DBC.QryLogV` | `QueryID`, `ProcID`, `UserName`, `AcctString`, `AppID`, `ClientID`, `SessionID`, `StatementType`, `StartTime`, `FirstStepTime`, `FirstRespTime`, `ElapsedTime`, `DelayTime`, `AMPCPUTime`, `MaxAMPCPUTime`, `MinAmpCPUTime`, `ParserCPUTime`, `TotalIOCount`, `MaxAmpIO`, `MinAmpIO`, `SpoolUsage`, `NumSteps`, `NumResultRows`, `NumOfActiveAMPs`, `ErrorCode`, `ErrorText`, `WDID`, `QueryBand`, `QueryText` (first characters) |
| `DBC.QryLogSqlV` | `QueryID`, `ProcID`, `CollectTimeStamp`, `SqlRowNo`, `SqlTextInfo` (full text, one row per 32 KB chunk; join on `QueryID` AND `ProcID`, take `SqlRowNo = 1` for the head) |
| `DBC.QryLogStepsV` | per step: `StepLev1Num`, `StepName`, `StepStartTime`, `StepStopTime`, `CPUTime`, `IOCount`, `RowCount`, `EstRowCount`, `EstCPUCost`, `SpoolUsage` — the **actual vs estimated** rows per step |
| `DBC.QryLogObjectsV` | `ObjectDatabaseName`, `ObjectTableName`, `ObjectColumnName`, `ObjectType`, `FreqofUse` — which tables/columns a query touched |
| `DBC.QryLogExplainV` | `ExplainText` when EXPLAIN logging is on |
| `DBC.QryLogSummaryV` | summarised buckets when `WITH SUMMARY` logging is used (no per-query rows) |

`HashAmp() + 1` is the number of AMPs on the system — used to normalise totals to a per-AMP average.

## 3. Metric definitions (exact SQL)

Computed per query from `DBC.QryLogV` (the server's `sql_Execute_Full_Pipeline` computes the same four over
`DBC.DBQLSqlTbl a JOIN DBC.DBQLOgTbl b`):

```sql
SELECT
  b.QueryID, b.ProcID, b.UserName, b.StartTime, b.AMPCPUTime, b.TotalIOCount, b.NumSteps, b.SpoolUsage,
  -- CPU skew: the busiest AMP's CPU vs the per-AMP average (1.0 = perfectly even)
  CASE WHEN b.AMPCPUTime < HashAmp()+1 OR (b.AMPCPUTime / (HashAmp()+1)) = 0 THEN 0
       ELSE b.MaxAMPCPUTime / (b.AMPCPUTime / (HashAmp()+1)) END (DECIMAL(8,2))   AS CPUSKW,
  -- I/O skew: the busiest AMP's I/O vs the per-AMP average
  CASE WHEN b.AMPCPUTime < HashAmp()+1 OR (b.TotalIOCount / (HashAmp()+1)) = 0 THEN 0
       ELSE b.MaxAmpIO / (b.TotalIOCount / (HashAmp()+1)) END (DECIMAL(8,2))      AS IOSKW,
  -- Product Join Indicator: CPU milliseconds per logical I/O (high = CPU-bound relative to I/O)
  CASE WHEN b.AMPCPUTime < HashAmp()+1 OR b.TotalIOCount = 0 THEN 0
       ELSE (b.AMPCPUTime * 1000) / b.TotalIOCount END (DECIMAL(10,4))            AS PJI,
  -- Unnecessary I/O Indicator: logical I/Os per CPU millisecond (high = I/O-bound / scan-heavy)
  CASE WHEN b.AMPCPUTime < HashAmp()+1 OR b.AMPCPUTime = 0 THEN 0
       ELSE b.TotalIOCount / (b.AMPCPUTime * 1000) END (DECIMAL(10,4))            AS UII,
  -- response time in seconds
  (EXTRACT(HOUR   FROM ((b.FirstRespTime - b.StartTime) HOUR(3) TO SECOND(6))) * 3600
 + EXTRACT(MINUTE FROM ((b.FirstRespTime - b.StartTime) HOUR(3) TO SECOND(6))) * 60
 + EXTRACT(SECOND FROM ((b.FirstRespTime - b.StartTime) HOUR(3) TO SECOND(6))))             AS response_secs,
  b.DelayTime
FROM DBC.QryLogV b
WHERE b.StartTime >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
  AND LOWER(b.StatementType) IN ('select', 'create table')
ORDER BY b.AMPCPUTime DESC;
```

Notes on the definitions:
- The `AMPCPUTime < HashAmp()+1` guard opens ALL FOUR expressions — CPUSKW, IOSKW, PJI and UII — in the
  server's own SQL (`tools/sql_opt/sql_opt_tools.py` in the vendored 0.2.6 wheel). It suppresses meaningless
  ratios on sub-second queries: a query that used 0.4 CPU seconds on one AMP is "100% skewed" but irrelevant,
  and its PJI/UII are noise for the same reason. If you hand-write these expressions, carry the guard on all
  four or your results will not match the tool's.
- Prefer integer-day arithmetic on a DATE for a window of any length:
  `CAST(StartTime AS DATE) >= CURRENT_DATE - 365`. It is shorter and reads identically whatever the column type.
  `INTERVAL '365' DAY` is NOT wrong: an interval LITERAL is sized from its own digits up to four, so 365 and
  even 9999 compute correctly (measured on Vantage 20.0); five or more digits is `3706`. The 2-digit default
  belongs to a DECLARED `INTERVAL DAY` column or CAST target — `CAST(INTERVAL '365' DAY AS INTERVAL DAY)` is the
  statement that raises `7453`, and `DAY(4)` fixes it.
- **PJI / UII are relative indicators**, not absolutes: compare a query against the workload's median, or
  the same query across days.

## 4. Interpretation

| Metric | Meaning | Threshold (server defaults) |
|---|---|---|
| `AMPCPUTime` | total CPU seconds across all AMPs — the primary optimisation target | high > 100 s; very high > 1000 s |
| `CPUSKW` | CPU skew ratio | 1–2 normal; > 2.0 moderate; > 3.0 high; > 5.0 severe |
| `IOSKW` | I/O skew ratio | same bands |
| `TotalIOCount` | logical I/Os — scan intensity | high > 1,000,000 |
| `PJI` | CPU-intensity per I/O | high PJI ⇒ product joins, heavy expressions/UDFs, many-step plans |
| `UII` | I/O-intensity per CPU | high UII ⇒ full scans of large tables with little work per block — index/partition candidates |
| `NumSteps` | plan complexity | > 30 steps: nested views / correlated subqueries |
| `SpoolUsage` | peak spool bytes | compare with the user's SPOOL allocation; 2646 when exceeded |
| `DelayTime` | seconds queued by workload management | not a tuning problem — a concurrency/TASM one |
| `ErrorCode` | non-zero = failed (2646 spool, 3156 aborted, 3110 user abort, 2631 deadlock, 3130 response limit) | |

**Decision framework** (as encoded in the bundled clustering tool):
- **High CPU + many executions** → the maximum-impact candidates; tune these first.
- **High skew + moderate CPU** → distribution or statistics problems (skewed PI / join key, NULL-heavy join columns).
- **High I/O + low PJI** → scan-heavy: missing partition elimination or index; review predicates.
- **High PJI** → CPU-bound: product joins, expression-heavy predicates, UDFs, DISTINCT/OLAP over huge spools.
- **Many steps** → over-nested views or repeated subqueries; consider materialising an intermediate.

Category labelling the server applies to clusters: `HIGH_CPU_SKEW` (avg_cpuskw > 3), `HIGH_IO_SKEW`
(avg_ioskw > 3), `HIGH_CPU_USAGE` (avg_cpu > 100), `HIGH_IO_USAGE` (avg_io > 1,000,000), else `NORMAL`.

## 5. Ready-made queries

```sql
-- Top CPU consumers, last 7 days, with the head of the SQL text
SELECT TOP 20 b.QueryID, b.UserName, b.StartTime, b.AMPCPUTime, b.TotalIOCount, b.NumSteps, b.SpoolUsage, b.ErrorCode,
       SUBSTRING(a.SqlTextInfo FROM 1 FOR 300) AS sql_head
FROM DBC.QryLogV b JOIN DBC.QryLogSqlV a ON a.QueryID = b.QueryID AND a.ProcID = b.ProcID AND a.SqlRowNo = 1
WHERE b.StartTime >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
  AND a.SqlTextInfo NOT LIKE '%SET QUERY_BAND%' AND LOWER(a.SqlTextInfo) NOT LIKE '%dbc.%'
ORDER BY b.AMPCPUTime DESC;

-- Skewed queries (ratio > 3) that also cost something.
-- The row cap is ROW_NUMBER(), not TOP: TOP in the same SELECT as QUALIFY is Error 6916, and a QUALIFY
-- with no ordered analytical function anywhere in the statement is Error 3706.
SELECT QueryID, UserName, StartTime, AMPCPUTime, MaxAMPCPUTime,
       CASE WHEN AMPCPUTime < HashAmp()+1 OR (AMPCPUTime/(HashAmp()+1)) = 0 THEN 0
            ELSE MaxAMPCPUTime / (AMPCPUTime/(HashAmp()+1)) END (DECIMAL(8,2)) AS CPUSKW
FROM DBC.QryLogV
WHERE StartTime >= CURRENT_TIMESTAMP - INTERVAL '7' DAY AND AMPCPUTime > 10
QUALIFY CPUSKW > 3 AND ROW_NUMBER() OVER (ORDER BY AMPCPUTime DESC) <= 20
ORDER BY AMPCPUTime DESC;

-- Everything that touched one table (what dba_tableSqlList does; LIKE is a text match, include the db prefix)
SELECT t1.QueryID, t1.ProcID, t1.CollectTimeStamp, t1.SqlTextInfo, t2.UserName
FROM DBC.QryLogSqlV t1 JOIN DBC.QryLogV t2 ON t1.QueryID = t2.QueryID
WHERE t1.CollectTimeStamp >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
  AND t1.SqlTextInfo LIKE '%<db>.<table>%'
ORDER BY t1.CollectTimeStamp DESC;

-- Failures by error code, last 24 h
SELECT ErrorCode, COUNT(*) AS n, MIN(ErrorText) AS example
FROM DBC.QryLogV WHERE StartTime >= CURRENT_TIMESTAMP - INTERVAL '1' DAY AND ErrorCode <> 0
GROUP BY 1 ORDER BY 2 DESC;

-- Actual vs estimated rows per step for one query (the stale-statistics proof)
SELECT StepLev1Num, StepName, RowCount, EstRowCount, CPUTime, IOCount, SpoolUsage
FROM DBC.QryLogStepsV WHERE QueryID = <queryid> AND ProcID = <procid> ORDER BY StepLev1Num;

-- Objects a query touched (join/filter columns to collect statistics on)
SELECT ObjectDatabaseName, ObjectTableName, ObjectColumnName, ObjectType, FreqofUse
FROM DBC.QryLogObjectsV WHERE QueryID = <queryid> AND ProcID = <procid> ORDER BY 1,2,3;

-- Hourly CPU profile for a user (capacity view)
SELECT CAST(StartTime AS DATE) AS log_date, EXTRACT(HOUR FROM StartTime) AS hr,
       COUNT(*) AS queries, SUM(AMPCPUTime) AS cpu_secs, SUM(TotalIOCount) AS ios
FROM DBC.QryLogV WHERE UserName = '<user>' AND StartTime >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1,2 ORDER BY 1,2;
```

The bundled `dba_userSqlList(user_name, no_days)` and `dba_tableSqlList(table_name, no_days)` tools run the
`QryLogSqlV ⨝ QryLogV` join above with `CAST(CollectTimeStamp AS DATE) >= CURRENT_DATE - <no_days>` (default
7) — whole calendar days back from midnight, not a rolling timestamp window, so today's queries are always
included. `dba_tableSqlList` is a `LIKE '%<table>%'` text match — it also returns queries that merely mention
the name in a comment or a similarly named table. `dba_userSqlList` matches `user_name` with `=`: it is
required, and there is no wildcard or all-users value — use the un-filtered queries above for a whole
workload.

## 6. Table-level skew (storage, not query)

```sql
SELECT DatabaseName, TableName, SUM(CurrentPerm) AS CurrentPerm1, SUM(PeakPerm) AS PeakPerm,
       CAST((100 - (AVG(CurrentPerm) / MAX(NULLIFZERO(CurrentPerm)) * 100)) AS DECIMAL(5,2)) AS SkewPct
FROM DBC.AllSpaceV
WHERE DatabaseName = '<db>'
GROUP BY DatabaseName, TableName ORDER BY CurrentPerm1 DESC;
```
`SkewPct` is how far the average AMP is below the fullest AMP: 0 = even; > 10 worth a look; > 30 the PI is a
poor distribution key (few distinct values, or a dominant NULL/0/'UNKNOWN'). Storage skew and CPUSKW on
joins usually share the cause.

## 7. The `sql_*` clustering tools (optional, heavy)

`sql_Execute_Full_Pipeline` (WRITES; propose, do not run silently): extracts the top-N queries by
`AMPCPUTime` (`max_queries` default 10,000, `statementtype IN ('select','create table')`, filters out
`SET QUERY_BAND`, `ParamValue`, `SELECT CURRENT_TIMESTAMP` and anything touching `dbc.`), computes the four
metrics above, tokenises and embeds the SQL text in-database (`ivsm.tokenizer_encode` → `ivsm.IVSM_score` →
`ivsm.vector_to_columns`, model `bge-small-en-v1.5`, 384 dims, max 512 tokens), clusters with `TD_KMeans`
(k default 14, seed 10, stop threshold 0.0395, ≤ 100 iterations), scores with `TD_Silhouette`, and writes
`sql_query_clusters` + `query_cluster_stats` into a **feature database** that must exist with space and hold
the embedding model/tokenizer tables. Then, read-only:
- `sql_Analyze_Cluster_Stats(sort_by_metric='avg_cpu', limit_results=None)` — `avg_cpu | avg_io | avg_cpuskw |
  avg_ioskw | avg_pji | avg_uii | avg_numsteps | queries | cluster_silhouette_score`, with the
  `performance_category` labels above and a `RANK()`. `limit_results` caps the clusters returned.
- `sql_Retrieve_Cluster_Queries(cluster_ids, metric='ampcputime', limit_per_cluster=250)` — sample statements
  per cluster (`ampcputime | logicalio | cpuskw | ioskw | pji | uii | numsteps | response_secs | delaytime`).
  **`cluster_ids` is REQUIRED and is a LIST of integers**, not a single id — a scalar fails validation. The 250
  is a PER-CLUSTER `QUALIFY` cap, not a total, so asking for five clusters can return 1,250 statements.

Use clusters to find **families** of similar SQL (the same report run with different literals) — the tuning
win is then one rewrite or one statistics collection that fixes hundreds of queries.

## 8. Errors — what the message actually means

| Code | Meaning → action |
|---|---|
| 3523 on `DBC.QryLogV` | No SELECT on the DBQL views — ask for the grant; do not fall back to the base `DBQLogTbl`. |
| 0 rows for a user known to be active | Query logging is not enabled for that user/account (or only `WITH SUMMARY`). Report "not logged". |
| 2646 in `ErrorCode` | The query exceeded the user's spool — tune the plan (product join / no confidence) before raising SPOOL. |
| 3156 / 3110 | Aborted by workload management / by the user — a symptom of runaway CPU, check `AMPCPUTime` of the aborted run. |
| 2631 | Deadlock — concurrent writers on the same rows; a scheduling problem, not a plan problem. |
| 7453 / 2666 on the response-time expression | Interval overflow on very long runs — use `HOUR(4)` precision or subtract DATEs. |
