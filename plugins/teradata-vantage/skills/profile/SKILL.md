---
name: profile
description: Use when assessing the data quality or statistical shape of a Teradata table or database — null/blank/zero/negative counts, distinct categories, univariate statistics, rows with missing values, completeness scores — via the seven qlty_* tools or their in-database TD_ClearScape SQL equivalents. Read-only; never writes.
when_to_use: profile <db>.<table>; data quality of <db>; which columns have nulls, blanks or negatives; distribution of <column>; distinct values of <column>; mean and standard deviation; completeness score; find rows with missing <column>; is this table fit for analytics; run a data-quality assessment on the whole database; generate test data; synthetic data; make me a test table.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[<db>.<table> [column] | <db>]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__qlty_columnSummary
  - mcp__plugin_teradata-vantage_teradata__qlty_missingValues
  - mcp__plugin_teradata-vantage_teradata__qlty_negativeValues
  - mcp__plugin_teradata-vantage_teradata__qlty_distinctCategories
  - mcp__plugin_teradata-vantage_teradata__qlty_standardDeviation
  - mcp__plugin_teradata-vantage_teradata__qlty_univariateStatistics
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__base_tablePreview
  - Workflow
  - Workflow(teradata-vantage:profile-database)
disallowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_writeQuery
---

# Profile a Teradata table or database (data quality)

Everything here is **read-only**: the seven `qlty_*` tools wrap Teradata's in-database analytic functions
(`TD_ColumnSummary`, `TD_UnivariateStatistics`, `TD_CategoricalSummary`, `TD_GetRowsWithMissingValues`),
and every fallback below is a `SELECT … FROM TD_<Function>(…) AS dt` that runs through `base_readQuery`
(held read-only by a PreToolUse hook). `base_writeQuery` is disallowed on this skill. Function syntax and
output columns below are from Teradata documentation (Database Analytic Functions, 17.20) — verify against
your release. This is a community plugin, not affiliated with Teradata.

## The seven tools and the SQL each one runs

| Tool | Arguments | Runs (paraphrased from the server) | Returns |
|---|---|---|---|
| `qlty_columnSummary` | database_name, table_name | `SELECT * FROM TD_ColumnSummary (ON <db>.<t> AS InputTable USING TargetColumns('[:]')) AS dt` | one row per column: `ColumnName, DataType, NonNullCount, NullCount, BlankCount, ZeroCount, PositiveCount, NegativeCount, NullPercentage, NonNullPercentage` |
| `qlty_missingValues` | database_name, table_name | same function, projecting `ColumnName, NullCount, NullPercentage … ORDER BY NullCount DESC` | null profile per column |
| `qlty_negativeValues` | database_name, table_name | same function, projecting `ColumnName, NegativeCount … ORDER BY NegativeCount DESC` | negatives per numeric column (NULL for non-numeric) |
| `qlty_distinctCategories` | database_name, table_name, column_name | `SELECT * FROM TD_CategoricalSummary (ON <db>.<t> AS InputTable USING TargetColumns('<col>')) AS dt` | `ColumnName, DistinctValue, DistinctValueCount` — one row per distinct value |
| `qlty_standardDeviation` | database_name, table_name, column_name | `SELECT * FROM TD_UnivariateStatistics (ON <db>.<t> AS InputTable USING TargetColumns('<col>') Stats('MEAN','STD')) AS dt ORDER BY 1,2` | `Attribute, StatsName, StatsValue` rows |
| `qlty_univariateStatistics` | database_name, table_name, column_name | same with `Stats('ALL')` | ~30 statistics per numeric column |
| `qlty_rowsWithMissingValues` | database_name, table_name, column_name | `SELECT * FROM TD_GetRowsWithMissingValues (ON <db>.<t> AS InputTable USING TargetColumns('[<col>]')) AS dt` | the **full rows** where that column is NULL |

Rules the tools imply:
- `qlty_distinctCategories` is for **CHAR/VARCHAR** columns (TD_CategoricalSummary input contract). On a
  numeric column use the univariate statistics, or `SELECT <col>, COUNT(*) FROM <db>.<t> GROUP BY 1 ORDER BY 2 DESC`.
- `qlty_standardDeviation` / `qlty_univariateStatistics` are for **numeric** columns (TD_UnivariateStatistics
  input contract). A character column raises a function-argument error — that is the contract, not a bug.
- `qlty_rowsWithMissingValues` returns whole rows: on a wide or large table it can exceed the result budget.
  Prefer `qlty_missingValues` for counts and reach for the rows only when the user needs examples; when you
  must, add `TOP` via the SQL fallback.
- The server interpolates names straight into SQL: pass exactly one `<db>` and one `<table>`; never pass
  expressions or quotes in `database_name`/`table_name`/`column_name`.
- Every table reference must be qualified `<db>.<table>` (3807 "does not exist" is usually a missing qualifier).

## Table workflow (one table)

1. **Shape first**: `base_tableDDL` (types, PI, `SET`/`MULTISET`) and `SELECT COUNT(*) FROM <db>.<t>`.
   Expect `qlty_*` calls on tables with millions of rows to take seconds to minutes — they scan the table.
2. **Column summary**: `qlty_columnSummary` → `NullPercentage`, `BlankCount` (CHAR/VARCHAR only),
   `ZeroCount`/`NegativeCount` (numeric only). Flag: NullPercentage > 20%, a column that is 100% NULL or 100%
   blank, negatives in a column whose name implies a quantity/amount, zeros in an identifier.
3. **Numeric depth** on the columns that matter: `qlty_univariateStatistics` (MIN/MAX/MEAN/MEDIAN/STD/
   SKEWNESS/KURTOSIS/TOP5/BOTTOM5/percentiles/UEC…). Flag: MIN below a plausible floor, MAX orders of
   magnitude above the 99th percentile, `UEC` = row count on a non-key column (a key in disguise), STD = 0.
4. **Categorical depth**: `qlty_distinctCategories` on low-cardinality text columns. Flag: case/whitespace
   variants of the same value, a category with 1 row, an unexpected sentinel (`'N/A'`, `'-'`, `'NULL'`).
5. **Examples** only when useful: `qlty_rowsWithMissingValues` (or the TOP-limited fallback).
6. **Completeness score** (state the formula you used):
   - column completeness = `NonNullPercentage` per column (from step 2);
   - table completeness = weighted mean of column completeness over the columns the user cares about, or
     row-level: `1 - (rows with any NULL in the required columns / total rows)` via
     `SELECT COUNT(*) FROM <db>.<t> WHERE <c1> IS NULL OR <c2> IS NULL …`.
   Present both the number and what it counts. Never invent a score without the underlying counts.

## Database workflow (many tables)

For a whole database: Phase 1 `base_tableList(<db>)` → the `<db>.<table>` list; Phase 2 per table:
`base_tableDDL` (derive a one-line business description from column names) → `qlty_columnSummary` →
`qlty_univariateStatistics` on the key numeric columns → `qlty_rowsWithMissingValues` only where a required
column has nulls; Phase 3 one report with the same section shape for every table. This is the flow of the
server's own `qlty_databaseQuality` prompt (a slash command, as exposed by `/mcp`).

**More than 5 tables → launch the workflow** `Workflow(teradata-vantage:profile-database)` with
`{database: "<db>"}` (optionally `tables: [...]`, `maxColumns`). It profiles tables in parallel with read-only
explorer agents and grades each one. If the `Workflow` tool is absent, say so (it needs the "Dynamic
workflows" setting) and fall back to the sequential flow with a table cap the user agrees to.

## SQL fallbacks (when a qlty_* tool is unavailable or a profile is hidden)

```sql
-- all columns at once (what qlty_columnSummary runs)
SELECT * FROM TD_ColumnSummary (ON <db>.<t> AS InputTable USING TargetColumns('[:]')) AS dt;
-- selected columns / a column range
SELECT * FROM TD_ColumnSummary (ON <db>.<t> AS InputTable USING TargetColumns('amount','qty','[3:7]')) AS dt;
-- univariate, chosen statistics and centiles (numeric columns only)
SELECT * FROM TD_UnivariateStatistics (ON <db>.<t> AS InputTable
   USING TargetColumns('amount') Stats('MIN','MAX','MEAN','MEDIAN','STD','SKEWNESS','KURTOSIS','PERCENTILES')
         Centiles(1,5,50,95,99)) AS dt ORDER BY 1,2;
-- per-group statistics
SELECT * FROM TD_UnivariateStatistics (ON <db>.<t> AS InputTable
   USING TargetColumns('amount') PartitionColumns('region') Stats('MEAN','STD')) AS dt ORDER BY 1,2,3;
-- distinct values (CHAR/VARCHAR)
SELECT * FROM TD_CategoricalSummary (ON <db>.<t> AS InputTable USING TargetColumns('status')) AS dt ORDER BY 3 DESC;
-- rows missing any of the required columns, bounded
SELECT TOP 100 * FROM TD_GetRowsWithMissingValues (ON <db>.<t> AS InputTable USING TargetColumns('customer_id','amount')) AS dt;
-- plain SQL when the analytic functions are not installed / not licensed
SELECT COUNT(*) AS total_rows,
       SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS amount_nulls,
       SUM(CASE WHEN amount < 0 THEN 1 ELSE 0 END)     AS amount_negatives,
       COUNT(DISTINCT status) AS status_distinct
FROM <db>.<t>;
```

`Stats` legal values (aliases in parentheses): SUM, COUNT (CNT), MAXIMUM (MAX), MINIMUM (MIN), MEAN,
UNCORRECTED SUM OF SQUARES (USS), NULL COUNT (NLC), POSITIVE VALUES COUNT (PVC), NEGATIVE VALUES COUNT (NVC),
ZERO VALUES COUNT (ZVC), TOP5 (TOP), BOTTOM5 (BTM), RANGE (RNG), GEOMETRIC MEAN (GM), HARMONIC MEAN (HM),
VARIANCE (VAR), STANDARD DEVIATION (STD), STANDARD ERROR (SE), SKEWNESS (SKW), KURTOSIS (KUR),
COEFFICIENT OF VARIATION (CV), CORRECTED SUM OF SQUARES (CSS), MODE, MEDIAN (MED), UNIQUE ENTITY COUNT (UEC),
INTERQUARTILE RANGE (IQR), TRIMMED MEAN (TM), PERCENTILES (PRC), ALL (default). `Centiles` 1..100 (default
1,5,10,25,50,75,90,95,99); `TrimPercentile` 1..50 (default 20).

## Errors — what the message actually means

| Code / text | Meaning → action |
|---|---|
| 3807 "Object '<x>' does not exist" | Unqualified or misspelled name, OR no SELECT privilege (indistinguishable). Qualify `<db>.<t>`; confirm via `DBC.TablesV`. |
| 3523 "The user does not have SELECT access to …" | Privilege. Ask for `GRANT SELECT ON <db> TO <user>` — grants on a parent do not cascade to child databases. |
| 5628 "Column <x> not found in <t>" | Wrong column name; run `base_columnDescription` in the same turn and retry once. |
| 9134 / "invalid TargetColumns" from a TD_ function | A column of the wrong data type for that function (numeric-only vs character-only). Pick the right function. |
| 5589 "Function 'TD_ColumnSummary' does not exist" | The analytic functions are not installed/exposed on this release — use the plain-SQL fallback. |
| 2646 / 3710 (spool / insufficient memory) | The scan is too large for the session's spool. Profile a sample: `ON (SELECT * FROM <db>.<t> SAMPLE 100000) AS InputTable`. |
| Result truncated (the plugin's result coach notes it) | Reduce scope: fewer columns via `TargetColumns`, `TOP` on row-returning functions. |

## Reporting

Per table: row count → column table (type, null %, blank/zero/negative counts) → numeric outliers → category
anomalies → completeness score with its formula → three concrete recommendations (e.g. "make `customer_id`
NOT NULL", "normalise `status` casing", "investigate 412 negative `amount` rows"). Show the SQL or tool call
behind every number. Do not present a sample-based figure as a population figure — say "on a 100k-row
sample". Never soften a finding: a 100%-NULL column is a broken column, say so.

## References
- `references/test-data.md` — generating realistic test volume in-database with `Sys_Calendar` and `RANDOM`.
