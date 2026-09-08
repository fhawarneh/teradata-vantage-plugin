---
name: teradata-sql
description: Use when writing, correcting or reviewing any SQL that runs on Teradata Vantage, through base_readQuery/base_writeQuery or in a file. Teradata dialect rules (TOP/QUALIFY not LIMIT, IS NULL, reserved-word aliases, CAST shape, GROUP BY 3504, date math, INTERVAL literals, db.table qualification) plus error-code-driven repair.
when_to_use: Any Teradata SQL authoring or repair; a tool result containing "Error NNNN" (3706, 3707, 3504, 3807, 5628, 3810, 2666, 5407, 2616, 6706, 6916, 7453, 3541); "top 10 rows", "last 90 days", "rows where X is null", "why does LIMIT fail", "Syntax error, expected something like", "reserved word", "Selected non-aggregate values"; porting SQL from Postgres, Oracle or Snowflake; "MERGE fails 5758".
license: MIT
user-invocable: false
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
metadata:
  skill_type: documentation
  category: teradata
  version: "1.0.0"
---

# Teradata SQL dialect

Teradata Vantage is not Postgres, not MySQL and not ANSI-strict. Small habits from those dialects fail
here with a syntax error, or worse, run and return the wrong answer silently. Apply the rules below to
every statement before it is sent. Depth lives in `references/` (listed at the end); this file is the
part that must be right on the first try.

The tool contract this skill assumes: `base_readQuery` is held read-only by a PreToolUse hook (one
statement, starting `SELECT`, `WITH`, `EXPLAIN`, `SHOW` or `HELP`; other verbs are denied). Destructive
`base_writeQuery` statements raise an approval prompt. Both are mistake-prevention, not a security
boundary. Details: the `query` and `setup` skills.

## 1. Absolute rules (imperative)

**Row limiting**

- NEVER emit `LIMIT n` or `FETCH FIRST n ROWS ONLY`. Both fail with `Error 3706 Syntax error`.
- ALWAYS use `SELECT TOP n ...` (TOP goes immediately after `SELECT`, before the column list) or
  `QUALIFY ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) <= n`.
- Prefer `QUALIFY` for top-N per group; it composes with `GROUP BY` and other window functions.
- `TOP n` does not combine with `DISTINCT`, `QUALIFY`, `SAMPLE` or a `WITH ... BY` subtotal clause in
  the same SELECT. Each fails with `Error 6916 TOP N Syntax error`, and the message names which one
  (verified on Vantage 20.0). `ORDER BY` with `TOP` is fine. For top-N drop `TOP` and keep `QUALIFY
  ROW_NUMBER()` alone; to keep `TOP`, move the `DISTINCT`, `SAMPLE` or `WITH` select into a derived
  table and apply `TOP` outside it.
- `TOP n` with `ORDER BY` is deterministic; `SAMPLE n` is random. Do not confuse them.

```sql
-- FAILS: SELECT a, b FROM <db>.<table> ORDER BY x DESC FETCH FIRST 10 ROWS ONLY
-- FAILS: SELECT a, b FROM <db>.<table> LIMIT 10
SELECT TOP 10 a, b FROM <db>.<table> ORDER BY x DESC;
SELECT a, b FROM <db>.<table>
QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY x DESC) <= 3;
```

**NULL**

- NEVER write `col = NULL`, `col != NULL` or `col <> NULL`. They are never true and silently return
  zero rows. ALWAYS `col IS NULL` / `col IS NOT NULL`.
- To break a result down by a column that may be NULL, `GROUP BY` it directly; NULL becomes a bucket.
- `WHERE col = 'X'` already excludes NULLs. To include them: `WHERE col = 'X' OR col IS NULL`.

**Keywords and identifiers**

- ALWAYS spell `SELECT` in full. `SEL` is accepted by Teradata but the read guard checks the leading
  keyword and denies it.
- NEVER use a reserved word as an alias. The ones that bite most: `value key date time position type
  status count sum min max period level ct cs mode month year zone hour rank percent`. Use `val
  metric_value dt time_val pos type_name status_val cnt total lo hi period_val lvl coltype scheme
  mode_val mth yr tz hr rnk pct`. Failure: `Error 3707 Syntax error, expected something like ...`.
  Full list and quoting rules: `references/reserved-words.md`.
- A reserved word that is a real column name is written in double quotes: `"month"`, `"date"`.
- Aliases are one token: `AS avg_value`, never `AS avg value`.
- ALWAYS qualify objects as `<db>.<table>` — on every statement, including retries. A bare table name
  fails with `Error 3807 Object '<name>' does not exist` unless the session default database happens
  to match. Use one deterministic name across the steps of a multi-statement task.
- In a join, qualify every column that exists on more than one table, or Teradata raises
  `Error 3809 Column '<c>' is ambiguous`. Alias every table. NEVER write `JOIN ... USING (col)`.

**CAST and types**

- A `CAST` closes with a data type, then the alias sits outside:
  `CAST(SUM(x) AS DECIMAL(18,2)) AS total_x`. `CAST(SUM(x) AS total_x)` fails with
  `Error 3706 ... does not match a defined Type name`.
- `VARCHAR` needs a length: `CAST(x AS VARCHAR(100))`. `CAST(x AS VARCHAR)` fails with `Error 3707
  expected '(' between 'VARCHAR' and ')'`.
- Money and totals: `DECIMAL(18,2)`, never `DECIMAL(5,2)`. A small precision overflows with
  `Error 2616 Numeric overflow occurred during computation`. Statistics over DECIMAL (`STDDEV_POP`,
  z-scores, wide SUMs) — cast the input to `FLOAT` first.
- Division loses precision in TWO different ways, and they behave differently. `DECIMAL/DECIMAL` takes the
  WIDER input's scale and ROUNDS to it (`DEC(9,1) 2/3` -> `0.7`, `DEC(9,2) 10/3` -> `3.33`).
  `INTEGER/INTEGER` (BIGINT included) yields an INTEGER and TRUNCATES TOWARD ZERO — not floor:
  `5/2` -> `2` and `-5/2` -> `-2`. Either way `SUM(a) / SUM(b)` over whole-number columns is scale 0, so a
  small ratio comes back as exactly 0.
  A scale-n multiplier buys exactly n decimals and is not the fix — `1.0 *` buys ONE (`3/7` → 0.4),
  `1.000 *` buys three (0.429). ALWAYS `CAST(<num> AS FLOAT) / NULLIFZERO(<den>)` for ratios, then
  CAST to the display type.
- Guard every denominator: `NULLIFZERO(x)` (Teradata-native) or `NULLIF(x, 0)`.
- Every `THEN`/`ELSE` in a `CASE` must have compatible types. Count with
  `SUM(CASE WHEN <cond> THEN 1 ELSE 0 END)`.
- NEVER `CASE WHEN x IN (SELECT ...)`: `Error 3771 Illegal expression in WHEN clause`. Use a scalar
  subquery in the select list or a `LEFT JOIN ... WHERE right.key IS NULL`.
- Strings concatenate with `||`, not `CONCAT()`. `SUBSTRING(col FROM 1 FOR n)`, never
  `SUBSTRING(col, 1, n)`. Case-insensitive compare: `WHERE col = 'x' (NOT CASESPECIFIC)` or `UPPER()`.
- CHAR(n) pads with spaces; compare with `TRIM()` or use VARCHAR.
- Text with non-Latin characters into a plain `VARCHAR` column fails with `Error 6706 The string
  contains an untranslatable character` (session/column charset LATIN). Declare `CHARACTER SET
  UNICODE` on the column or fold the characters before the write.

**Aggregation**

- Every non-aggregated column in the select list must be in `GROUP BY`, or:
  `Error 3504 Selected non-aggregate values must be part of the associated group`. Positional
  `GROUP BY 1, 2` is accepted and safe.
- An aggregate cannot be a GROUP BY key. A ranking query with no `GROUP BY` collapses to one
  mislabelled row: for "top N groups by metric" ALWAYS `GROUP BY <dim>` + `ORDER BY <agg_alias> DESC` +
  `SELECT TOP N`.
- A query with no `GROUP BY` and one un-aggregated column returns one row per source row; a consumer
  then reads row 1 and calls it the total. Wrap every column reference in an aggregate.
- Do not add a `WHERE status = ...` filter the user did not ask for. "Total" means all rows.

**Dates and time** (full treatment: `references/date-time.md`)

- There is no `MONTH()`, `YEAR()`, `DATEADD`, `DATEDIFF`, `NOW()` or `GETDATE()`. Use
  `EXTRACT(MONTH FROM d)`, `EXTRACT(YEAR FROM d)`, `CURRENT_DATE`, `CURRENT_TIMESTAMP`, `ADD_MONTHS`,
  `MONTHS_BETWEEN`, `TRUNC(d, 'MM'|'Q'|'YYYY')`, `TD_WEEK_BEGIN(d)`.
- A DATE minus an INTEGER is that many days: `CURRENT_DATE - 90`. `DATE - DATE` is an INTEGER day count.
- An INTERVAL literal is sized from its own digits, up to four: `INTERVAL '365' DAY` and
  `INTERVAL '9999' DAY` work; `INTERVAL '10000' DAY` fails with `Error 3706 Invalid INTERVAL Literal`.
  NEVER attach a precision to a literal (`INTERVAL '365' DAY(4)` is `Error 3706`) — `DAY(4)` qualifies
  a CAST target, where `Error 7453 Interval field overflow` means widen the type. NEVER add a time
  INTERVAL to a DATE: `CURRENT_DATE + INTERVAL '120' MINUTE` is `Error 5407`. Prefer `d - 365` for day
  offsets and `ADD_MONTHS()` for month offsets.
- Pick the date expression by the column's real type (check with `base_tableDDL` or `DBC.ColumnsV`):
  - DATE/TIMESTAMP column: `CAST(col AS DATE)`. `SUBSTRING` on a TIMESTAMP/TIME/INTERVAL fails with
    `Error 5407 Invalid operation for DateTime or Interval`; on a DATE it does NOT fail — it renders
    the date in the session DATEFORM (`IntegerDate` gives `'26/09/05'`), so the bucket is silently
    wrong. NEVER `SUBSTRING` a DATE: use `TRUNC(col, 'MM')` or `TO_CHAR(col, 'YYYY-MM')`.
  - VARCHAR holding `'YYYY-MM-DD HH:MI:SS'`: `TRYCAST(SUBSTRING(col FROM 1 FOR 10) AS DATE)`. A bare
    `CAST(col AS DATE)` fails with `Error 2666 Invalid date supplied`. `TRYCAST` yields NULL on junk
    instead of aborting; count those NULLs so nothing is silently dropped.
- Anchor "last N days/months" to the data, not the calendar, unless the user says "today":
  `WHERE d >= (SELECT MAX(d) FROM <db>.<table>) - 90`. State the anchor in the answer.
- Weekly buckets: `TD_WEEK_BEGIN(d)`. NEVER `EXTRACT(WEEK ...)`, `WEEK()`, or `TRUNC(d, 'IW')`.
  Monthly bucket of a string date: `SUBSTRING(col FROM 1 FOR 7)` gives sortable `'YYYY-MM'`.
- `TIMESTAMP(0)` rejects microseconds: `Error 5404 Datetime field overflow`. Truncate before insert,
  or declare `TIMESTAMP(6)`.

## 2. Objects and physical design (what matters when reading or writing DDL)

- Table shape, distribution and scratch space — `SET` vs `MULTISET`, choosing a `PRIMARY INDEX`,
  `PARTITION BY RANGE_N`, volatile and global temporary tables, `CREATE TABLE ... AS`: see
  `references/physical-design.md`. The one rule to carry inline: a low-cardinality primary index
  (status, country) skews the table across AMPs and is the most common cause of a slow join.
- Volatile tables and `CREATE TABLE ... AS` go through `base_writeQuery`; they are not destructive and
  do not prompt. `CREATE DATABASE`/`USER`, `DROP`, `DELETE`, `UPDATE`, `INSERT`, `MERGE`, `ALTER`,
  `GRANT`, `REVOKE`, `ABORT SESSION` prompt.
- Nobody can `CREATE TABLE` in `DBC` (`Error 3524 The user does not have CREATE TABLE access to
  database DBC`). Use a working database; DBC is for the dictionary.
- A child database is carved from its PARENT's unallocated PERM, so `CREATE DATABASE ... AS PERM = n`
  fails with `Error 3541` when the parent has less than n free — it reads like syntax and means "no
  room". Diagnose on the parent, not the system total; the `health` skill has the full treatment.
- `SAMPLE n` returns RANDOM rows — for profiling, never for a "top" question.
- `LOCKING ROW FOR ACCESS SELECT ...` (dirty read) reads a table under load. It does not start with
  `SELECT`, so `base_readQuery` denies it; use `base_writeQuery` (no prompt) or a script.
- Formatting belongs to display: `col (FORMAT 'zz9.99%')`, `d (FORMAT 'YYYY-MM-DD')`, or `TO_CHAR`.
- After loading or changing a large table: `COLLECT STATISTICS COLUMN (pi_col), COLUMN (join_col) ON
  <db>.<table>;`. An EXPLAIN that says "no confidence" is the optimizer telling you stats are missing.
- Metadata: `SHOW TABLE <db>.<t>` (DDL; `base_tableDDL` runs exactly this, so on a view it fails with
  `Error 3853` — use `SHOW VIEW`), `HELP TABLE <db>.<t>`, `HELP STATISTICS <db>.<t>`, `EXPLAIN
  <select>`. Catalog SQL: `references/dbc-dictionary.md`.

## 3. Canonical recipes

Percentage / rate (FLOAT cast on the numerator, guarded denominator, CAST closed before the alias):

```sql
SELECT CAST(CAST(SUM(CASE WHEN <condition> THEN 1 ELSE 0 END) AS FLOAT) * 100
            / NULLIFZERO(COUNT(*)) AS DECIMAL(5,2)) AS pct
FROM <db>.<table>;
```

`* 100.0` is a DECIMAL literal, not a float: it buys one decimal (`TYPE` says `DECIMAL(15,1)`), the
division keeps that scale, and the outer `DECIMAL(5,2)` dresses it as two — 0.90 where the answer is
0.92.

Ratio of two sums (DECIMAL-scale trap avoided):

```sql
SELECT CAST(CAST(SUM(net_income) AS FLOAT) / NULLIFZERO(SUM(total_assets)) AS DECIMAL(9,6)) AS roa
FROM <db>.<table>;
```

Top N groups by a metric:

```sql
SELECT TOP 10 region, CAST(SUM(amount) AS DECIMAL(18,2)) AS total_amount
FROM <db>.sales_fact
GROUP BY region
ORDER BY total_amount DESC;
```

Top N per group:

```sql
SELECT region, product, total_amount
FROM (SELECT region, product, SUM(amount) AS total_amount
      FROM <db>.sales_fact GROUP BY region, product) t
QUALIFY ROW_NUMBER() OVER (PARTITION BY region ORDER BY total_amount DESC) <= 3;
```

Entities matching a multi-row condition (HAVING inside, COUNT outside):

```sql
SELECT COUNT(*) AS cnt
FROM (SELECT customer_id FROM <db>.orders GROUP BY customer_id HAVING COUNT(*) > 1) sub;
```

Year-over-year by period (data-anchored, typed date column):

```sql
SELECT TRUNC(order_date, 'MM') AS period_start,
       CAST(SUM(amount) AS DECIMAL(18,2)) AS total_amount,
       CAST(SUM(amount) - LAG(SUM(amount), 12) OVER (ORDER BY TRUNC(order_date, 'MM'))
            AS DECIMAL(18,2)) AS yoy_delta
FROM <db>.sales_fact
WHERE order_date >= ADD_MONTHS((SELECT MAX(order_date) FROM <db>.sales_fact), -24)
GROUP BY 1
ORDER BY 1;
```

Two-period delta with a string date column:

```sql
SELECT CAST(SUM(CASE WHEN SUBSTRING(order_date FROM 1 FOR 4) = '2026'
                     THEN CAST(amount AS DECIMAL(18,2)) ELSE 0 END)
          - SUM(CASE WHEN SUBSTRING(order_date FROM 1 FOR 4) = '2025'
                     THEN CAST(amount AS DECIMAL(18,2)) ELSE 0 END) AS DECIMAL(18,2)) AS yoy_delta
FROM <db>.sales_fact;
```

Weekly trend:

```sql
SELECT TD_WEEK_BEGIN(CAST(order_date AS DATE)) AS week_start, COUNT(*) AS cnt
FROM <db>.sales_fact
GROUP BY 1
ORDER BY 1 DESC;
```

Numeric comparison on a VARCHAR column: CAST on both sides, in SELECT and WHERE alike —
`WHERE CAST(amount AS DECIMAL(18,2)) > 1000`, never `WHERE amount > 1000` (string compare: `'100' >
'1000'`). Flag columns stored as `'0'`/`'1'` strings are quoted; BYTEINT flags are not (a string
literal compared to a BYTEINT column fails with `Error 3535`). Check the column type; do not guess.

## 4. Error-driven repair discipline

A Teradata error message is precise. Read the code, fix exactly that, and re-run — do not rewrite the
whole query, do not switch tables, do not narrate the retry.

1. Read the `Error NNNN` and its text. The table below and `references/error-codes.md` say what it
   actually means.
2. Fix only the named defect. Keep the same tables, aggregation and grouping.
3. On `5628` / `3810` (column not found) or `3807` (object not found): in the SAME turn, call
   `base_tableDDL` (or `base_columnDescription`) on the object that errored, then retry with the real
   name. Never guess a second column name.
4. At most two retries. A third failure is a report to the user with the SQL and the verbatim error —
   not a fourth guess.
5. Refuse rather than substitute: if the object lacks a quantity the request depends on, say what is
   missing. A plausible substitute carrying the requested name is worse than nothing.
6. Surface the code and message verbatim in the answer; never paraphrase a Teradata error.

| Code | What the message actually means | Do this |
|---|---|---|
| 3706 | Syntax error: a token is where a data type/keyword should be (alias inside CAST, `LIMIT`, alias with a space, missing `)`), or the feature is not enabled on this system (the text says which). | Fix that token; close `CAST(... AS <type>)` then alias. |
| 3707 | Lexical error: a reserved word used as an identifier, `VARCHAR` without a length, comma-form `SUBSTRING`, `MONTH()`/`YEAR()`. | Rename the alias; add the length; use `FROM ... FOR`; use `EXTRACT`. |
| 3504 | A selected column is neither aggregated nor in `GROUP BY`. | Add it to `GROUP BY` or wrap it in an aggregate. |
| 3807 | The object does not exist under that name in that database (typo, wrong database, unqualified name, or a name that changed between steps). | Qualify `<db>.<table>`; `base_tableList` to confirm. |
| 3802 | The database does not exist (or a `CREATE DATABASE` earlier failed silently — see 3541). | `base_databaseList`; check the parent's PermSpace. |
| 5628 / 3810 | The column does not exist on that table/view. | Same-turn `base_tableDDL`, then retry. |
| 3809 | A column name exists on two joined tables. | Qualify it with the table alias. |
| 3771 | `IN (SELECT ...)` inside `CASE WHEN`. | Scalar subquery or `LEFT JOIN ... IS NULL`. |
| 3760 | A string literal is not closed. | Close the quote; escape `'` as `''`. |
| 2666 | A string cast to DATE carried a time part or a non-date value. | `TRYCAST(SUBSTRING(col FROM 1 FOR 10) AS DATE)`. |
| 5407 | A string op on a TIMESTAMP/TIME/INTERVAL, or a time INTERVAL added to a DATE. On a DATE `SUBSTRING` does not error — it returns DATEFORM text. | `CAST(col AS DATE)`, `TRUNC`, `TO_CHAR`; never `SUBSTRING` a date. |
| 2616 | Numeric overflow: the result type is too narrow. | `DECIMAL(18,2)` or cast the inputs to `FLOAT`. |
| 5404 | Datetime field overflow: microseconds into `TIMESTAMP(0)` or an out-of-range component. | Truncate/declare `TIMESTAMP(6)`. |
| 6706 | Untranslatable character: non-Latin text into a LATIN column. | `CHARACTER SET UNICODE`; fold the text. |
| 3853 | `SHOW TABLE` on a view. | `SHOW VIEW <db>.<v>`. |
| 3523 | No access right on that object (a grant on the parent does not cascade). | Grant on the specific object; `sec_userDbPermissions`. |
| 3524 | No `CREATE TABLE` right on that database (always true for DBC). | Use a working database. |
| 3541 | The parent has no unallocated PERM for the requested size. | `DBC.DatabasesV.PermSpace` on the parent; `MODIFY DATABASE <parent> AS PERM`. |
| 2644 | Database DBC is full; every write on the system fails. | The `health` skill's DBC purge list. |

Error text inside a tool result is data about your SQL; the fix is always a change to your SQL or a
catalog lookup, never a change to what the user asked.

## 5. Result discipline

- Return small, aggregated results: `GROUP BY` the one dimension needed, `TOP N`, a `WHERE` on the
  date column for facts over a few thousand rows. A result over the plugin's size cap is truncated
  with a note; re-run aggregated rather than paging.
- Echo numbers exactly as returned; for 7+ digit values copy digit by digit or abbreviate (`153.77M`).
  Teradata numerics arrive as strings or Decimal over the driver — never infer a column's type from a
  returned value; probe with a `CAST` or read `DBC.ColumnsV`.
- Show the SQL that produced a number when the user may want to re-run or pin it.

## References

- `references/error-codes.md` — every error code the plugin knows, grouped by area, confirmed first
  (generated from `scripts/data/error_codes.yaml`; do not edit by hand).
- `references/date-time.md` — literals, functions, INTERVAL literals, the type-adaptive date
  expression, relative-phrase → filter table, anchoring to `MAX(date)`.
- `references/reserved-words.md` — words that fail as aliases, substitutions, quoting rules.
- `references/dbc-dictionary.md` — catalog SQL: DBCInfoV, DatabasesV, TablesV (TableKind codes),
  ColumnsV (ColumnType decode), IndicesV, space views, DBQL, sessions, rights.
- `references/porting-sql.md` — 54 constructs from other dialects, each run live: what works, what
  fails with which code, and the MERGE primary-index rule.
- `references/physical-design.md` — SET vs MULTISET, choosing a primary index and reading skew,
  `PARTITION BY RANGE_N` and partition elimination, volatile / global temporary tables, statistics.
