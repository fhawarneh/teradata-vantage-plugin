# Reading a Teradata EXPLAIN plan — step vocabulary, join kinds, spool and confidence phrases, and a triage checklist

Companion to the `tune` skill. Run `EXPLAIN <statement>` through `base_readQuery` (the read guard admits a
statement starting with `EXPLAIN`). The phrases below are the optimizer's own wording; they are stable across
releases but not frozen — (from Teradata documentation; verify against your release). Everything is
read-only; the plan is text, nothing executes.

## 1. How a plan is organised

1. **Locks** — one line per object: `We lock <db>.<t> for read`, `… for access` (dirty read, from
   `LOCKING … FOR ACCESS` or an access-locked view), `… for write`, `… for exclusive`. A `RowHash` lock means a
   single-row (unique-index) operation.
2. **Steps** — numbered `1)`, `2)` …; steps listed as `2) We execute the following steps in parallel.` run
   concurrently. Each step is a RETRIEVE, JOIN, SUM (aggregate), SORT, MERGE, or an INSERT/UPDATE/DELETE step.
3. **Spools** — numbered intermediate result files (`Spool 1`, `Spool 2` …). `Spool 1` is normally the answer
   set: `The contents of Spool 1 are sent back to the user as the result of statement 1.`
4. **Estimates** — per step `The size of Spool N is estimated with <confidence> to be R rows (B bytes).
   The estimated time for this step is S seconds.` and finally `The total estimated time is T seconds.`
   Estimated time is a relative cost unit for comparing plans, not wall-clock.

## 2. RETRIEVE step access paths (best → worst)

| Phrase | Meaning |
|---|---|
| `single-AMP RETRIEVE step from <t> by way of the unique primary index` | one row, one AMP — ideal point access |
| `single-AMP RETRIEVE step … by way of the primary index` (NUPI) | all rows for one PI value on one AMP |
| `two-AMP RETRIEVE step … by way of unique index # n` | USI lookup (index subtable then base row) |
| `all-AMPs RETRIEVE step from <t> by way of index # n` | NUSI scan on every AMP — good only if selective |
| `all-AMPs RETRIEVE step from a single partition of <t>` / `from n partitions of <t>` | **partition elimination** — the PPI is working |
| `all-AMPs RETRIEVE step from <t> by way of an all-rows scan` | **full table scan (FTS)** — acceptable on small tables or when most rows are needed; a red flag on the big fact table with a selective predicate |
| `with a residual condition of (…)` | the predicate is applied after the access path — the access path did NOT use it |
| `with no residual conditions` | the access path consumed the whole predicate |
| `by way of a traversal of index # n extracting row ids only` | NUSI bit-mapping / row-id extraction, then a base-table probe |
| `… by way of the sort key in spool field1` | a spool being read in sorted order (feeds a merge join) |
| `dynamic partition elimination` | the partitions to read are decided at run time from the other side of a join |

`into Spool N (all_amps)` / `(one-amp)` / `(group_amps)` describes where the result lands. `(Last Use)` after a
spool name means that spool is released after this step.

## 3. How a spool is prepared for the next join

| Phrase | Meaning | Cost |
|---|---|---|
| `which is built locally on the AMPs` | rows stay on their AMP — the join column is the PI (or a co-located spool) | cheapest |
| `which is redistributed by the hash code of (<cols>) to all AMPs` | every row is re-hashed and sent to the AMP owning that join value | one pass of the whole spool over the BYNET; fine for the SMALL side |
| `which is duplicated on all AMPs` | the whole spool is copied to every AMP | cheap for a small dimension, catastrophic for a large table |
| `Then we do a SORT to order Spool N by row hash` / `by the sort key in spool field1` | preparation for a merge join or an ORDER BY | proportional to spool size |
| `The result spool file will not be cached in memory` | the spool is larger than the cache — expect I/O | informational |
| `eliminating duplicate rows` | a DISTINCT/UNION/IN-subquery dedup step | sort cost |

Rule of thumb: the optimizer should redistribute or duplicate the **smaller** side. If the plan duplicates or
redistributes the largest table, the row estimates are wrong (stats) or the PIs are mis-aligned for this join.

## 4. JOIN step kinds

| Phrase | What it is | When it is good / bad |
|---|---|---|
| `joined using a merge join, with a join condition of (…)` | both sides sorted by row hash on the join columns, merged | the normal large-to-large join; good when both sides are already PI-aligned (no redistribution lines before it) |
| `joined using a single partition hash join` / `hash join` | the small side is hashed in memory, the large side probes it | good for small-to-large; `single partition` = the small side fit in memory |
| `joined using a product join, with a join condition of (…)` | every row of one side against every row of the other | acceptable only when one side is tiny (a few rows) — otherwise a missing/unsatisfiable equality condition, mismatched types, or no statistics |
| `joined using a product join, with a join condition of ("(1=1)")` | a **cross join** — no join condition survived | almost always a bug (missing ON, comma-join, OR across tables) |
| `joined using a nested join` / `RowHash match scan` | index-driven probe of the second table row by row | good for small outer sets with a unique index on the inner |
| `joined using an exclusion merge join` / `exclusion product join` | NOT IN / NOT EXISTS / EXCEPT | exclusion product joins on large sets are slow; NOT IN on a nullable column forces an exclusion product join — rewrite as NOT EXISTS |
| `joined using an inclusion merge join` | IN / EXISTS semi-join | fine |
| `sliding-window join` | merge join between partitioned and non-partitioned (or differently partitioned) tables, window by partition | a PPI table joined on a non-partitioning column; ok for few partitions, degrades with many |
| `dynamic hash join` | hash join without spooling the large side | good |
| `joined using a correlated …` | correlated subquery evaluation | often rewritable as a join |
| `left outer joined` / `right outer joined` | outer join preserved | note which side is preserved — an outer join can block predicate push-down |

## 5. Aggregation and other steps

- `We do an all-AMPs SUM step to aggregate from <t> by way of an all-rows scan, grouping by field1 (…)` —
  GROUP BY. `Aggregate Intermediate Results are computed locally` (each AMP finishes its groups — group
  key aligned with the PI, cheap) vs `computed globally` (a redistribution of partial aggregates follows).
- `We do an all-AMPs STAT FUNCTION step` — window/OLAP function (`ROW_NUMBER`, `QUALIFY`, `RANK`); needs a
  sort by the PARTITION BY / ORDER BY keys.
- `We do a SORT to order Spool N by the sort key in spool field1` — ORDER BY / DISTINCT / merge-join prep.
- `We do an all-AMPs MERGE into <t> from Spool N` / `MERGE DELETE` / `all-AMPs UPDATE` — the write phase of
  INSERT…SELECT / DELETE / UPDATE (in a plan for a write statement).
- `We spoil the parser's dictionary cache for the table` — a DDL side effect (statistics/DDL step).
- `We send out an END TRANSACTION step to all AMPs involved in processing the request.` — normal end.

## 6. Confidence phrases — the most important words in the plan

| Phrase | What it actually means |
|---|---|
| `estimated with high confidence` | statistics exist on the columns used in this step's predicate/join and the estimate is derived from them |
| `estimated with low confidence` | statistics exist for part of the estimate (e.g. one side of a join), or the predicate combination (`OR`, expressions, LIKE) or a sampled/stale collection reduces certainty |
| `estimated with no confidence` | **no statistics** on the relevant columns — the optimizer used heuristics (fixed selectivity fractions). Every downstream estimate inherits the error. |
| `estimated with index join confidence` | the estimate comes from a unique/nonunique index used in a nested/join step |

Confidence degrades along the plan: one no-confidence step early makes every later spool estimate a guess,
and a guess is what turns a hash join into a product join or duplicates the wrong side. Fix the FIRST
no/low-confidence step (collect statistics on its columns), re-EXPLAIN, repeat.

## 7. Triage checklist (in this order)

1. **Lock line**: `for write`/`exclusive` on a table you expected to read → the statement is a write, or a
   view with an update trigger.
2. **`no confidence` anywhere?** → note the step, the table, the columns in its condition → statistics
   recommendation.
3. **`product join`?** → is one side ≤ a few rows (fine) or is the condition `(1=1)` / a type-cast comparison
   (bug)?
4. **`all-rows scan` on the largest table with a residual condition?** → the predicate could not use an
   index/partition: check `PARTITION BY`, consider a PPI/NUSI/join index, or remove the function around the
   column in the predicate.
5. **`duplicated on all AMPs` / `redistributed` on the large side?** → PI alignment or bad estimates.
6. **Spool estimate vs reality**: compare the estimated rows of Spool 1 with the actual row count of the
   result; a 10× gap confirms stale/missing statistics.
7. **Total estimated time** — use it only to compare two plans for the same query, never across queries.

## 8. Reading a plan for a PPI table

- Good: `from a single partition of <t> with a condition of ("<t>.<date_col> = DATE '2024-06-01'")` or
  `from n partitions of <t>`.
- Bad: `all-rows scan` with the date predicate as a residual condition — usually because the predicate wraps
  the column in a function (`EXTRACT(YEAR FROM d) = 2024`, `CAST(d AS …)`, `d + 1 = …`) or compares to a
  different type. Rewrite as a range on the bare column: `d >= DATE '2024-01-01' AND d < DATE '2025-01-01'`.
- A join between a PPI fact and a non-partitioned dimension on the fact's PI is a **sliding-window join**;
  many populated partitions make it expensive — consider `PARTITION` statistics
  (`COLLECT STATISTICS COLUMN (PARTITION) ON <db>.<t>`) so the optimizer knows how many windows there are.

## 9. Two worked patterns

**Missing stats → product join**
```
3) We do an all-AMPs RETRIEVE step from <db>.customer_dim by way of an all-rows scan with no residual
   conditions into Spool 2 (all_amps), which is duplicated on all AMPs. The size of Spool 2 is estimated
   with no confidence to be 1,200,000 rows …
4) We do an all-AMPs JOIN step from Spool 2 (Last Use) by way of an all-rows scan, which is joined to
   <db>.sales_fact … joined using a product join, with a join condition of ("customer_id = CUSTOMER_ID") …
```
Read: the dimension is duplicated (fine if small) but the estimate is a no-confidence guess of 1.2M rows and
the join degraded to a product join although an equality condition exists → check the two `customer_id`
types (a VARCHAR vs INTEGER mismatch produces exactly this), then
`COLLECT STATISTICS COLUMN (customer_id) ON <db>.customer_dim` and on `sales_fact`, re-EXPLAIN.

**Selective predicate ignored**
```
2) We do an all-AMPs RETRIEVE step from <db>.sales_fact by way of an all-rows scan with a residual condition
   of ("EXTRACT(YEAR FROM sales_fact.sale_date) = 2024") into Spool 1 …
```
Read: the PPI on `sale_date` cannot prune because the column is wrapped in `EXTRACT`. Rewrite the predicate
as a date range; expect `from n partitions of` on the re-EXPLAIN.

## 10. Beyond EXPLAIN (mention, do not run from the `tune` skill)

- `DIAGNOSTIC HELPSTATS ON FOR SESSION;` then `EXPLAIN …` appends the optimizer's own list of recommended
  statistics to the plan. It is a session setting and not a SELECT, so it needs `base_writeQuery`, and with a
  pooled server connection the next call may land on a different session — run it and the EXPLAIN in one
  request, or from an interactive client.
- `DBC.QryLogExplainV` (when DBQL logs EXPLAIN text) and `DBC.QryLogStepsV` (per-step actual CPU/IO/rows)
  turn estimates into measured numbers — see `dbql-metrics.md`.
