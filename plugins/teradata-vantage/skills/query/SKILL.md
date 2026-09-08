---
name: query
description: Use when the user wants data from Teradata Vantage or wants to change it - answer a question with SQL, run a SELECT, count/aggregate/trend/top-N, fix a failing Teradata statement, or prepare INSERT/UPDATE/DELETE/DDL (run through base_writeQuery where the connected server exposes one; the bundled 0.2.6 server is read-only) with the read guard, the write gate and an error-driven repair loop.
when_to_use: run this query; how many; total by; top 10; trend by month; write SQL for; why does this SQL fail; error 3706 / 3707 / 5628 / 3807; update these rows; delete from; create a table; insert into; review these .sql files; explain plan
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[<question in plain English> | fix <error code> | <SQL to review>]"
allowed-tools:
  - Read
  - Grep
  - Glob
  - Workflow
  - Workflow(teradata-vantage:sql-review)
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__base_tablePreview
  - mcp__plugin_teradata-vantage_teradata__plot_line_chart
  - mcp__plugin_teradata-vantage_teradata__plot_pie_chart
---

# Query Teradata Vantage

`mcp__plugin_teradata-vantage_teradata__base_readQuery` carries every read; it is the only SQL executor the bundled
upstream 0.2.6 server registers, and the plugin holds it read-only. Writes go through
`mcp__plugin_teradata-vantage_teradata__base_writeQuery` ONLY when the server you are connected to provides that tool
(section 2). The plugin's hooks shape how both behave; work with them, never around them. Dialect rules live in the
`teradata-sql` skill (loaded automatically); the ones that fail most often are repeated in the stop-check below.

## 0. Before you write SQL, look at what is already known

Two stores persist between sessions under the plugin's data directory, driven by
`scripts/grounding.py`. **Both are accelerators, never authorities** — they save a round trip, they
never settle a question.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding.py" list                      # verified queries
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding.py" recall-schema <db>.<tbl>  # cached columns/PI
```

- **A verified query is a starting point to re-check, not an answer to hand back.** Read it, confirm
  the objects still exist and still mean what it assumed, then run it. Say that it came from the
  repository, who confirmed it, and how old it is.
- **`recall-query` matches on exact normalised text only** — case and whitespace. It will not decide
  that two differently-worded questions mean the same thing, because that is a judgement about
  meaning and it belongs to you, not to a script. When the exact lookup misses, run `list` and choose.
- **Every recall reports `age_seconds` and `stale`. State the age.** A cached column list that
  survived an `ALTER TABLE` is worse than no cache, because it is confidently wrong. Anything
  load-bearing gets re-derived with `base_tableDDL` regardless of what the cache says.
- After any DDL, invalidate: `grounding.py invalidate --sql '<the statement>'`.

Nothing enters the verified repository automatically. See section 3 for the gate.

## 1. Read path (`base_readQuery`)

- Exactly ONE statement, starting with `SELECT`, `WITH`, `EXPLAIN`, `SHOW` or `HELP`. The read guard denies anything
  else (`;`-separated batches, `CREATE VOLATILE TABLE`, `DELETE`, ...) with a reason - a denial is a signal to
  rewrite as a single SELECT or to move the statement to the write path, never to retry the same text.
- Parameters besides `sql`: `row_limit` (default 1000, ceiling 50000; the result metadata says `truncated: true` when
  more rows exist) and `persist: true`, which makes the SERVER wrap your SELECT in
  `CREATE VOLATILE TABLE vt_<id> AS (...) WITH DATA ON COMMIT PRESERVE ROWS`, returns a 10-row sample plus the
  table name in `metadata.volatile_table`, and bypasses the row limit. That is the scratch-table path on the bundled
  server (the guard sees only your SELECT). Volatile tables are session-scoped: with a connection pool larger than one,
  a later call can land on another connection and fail with `3807` - re-run the persist if that happens.
- `persist: true` DELETES everything from the first `ORDER BY` in your text to the end before it wraps the
  statement (case-insensitive, across newlines, nesting ignored). A persisted statement must therefore contain
  NO `ORDER BY` at all - not an outer sort, not inside `OVER (...)`, `QUALIFY`, `WITHIN GROUP`, a CTE or a
  derived table; moving the window into a subquery does NOT help. Two ways it bites: a window is cut
  mid-expression and the wrapped statement fails with `3706`; and `SELECT TOP n ... ORDER BY <measure> DESC`
  silently persists as `SELECT TOP n ...` - an arbitrary n rows, no error. ALWAYS persist the unranked,
  unordered result set (`WHERE` and `GROUP BY` are safe), then rank in a second, non-persist `base_readQuery`
  against `metadata.volatile_table`.
- Before the first query against an object you have not seen this session: `base_tableDDL` (tables) or
  `SHOW VIEW <db>.<view>;` / `base_columnDescription` (views). Column names and types come from there, not memory.
- ALWAYS qualify `<db>.<table>`. ALWAYS bound the result (`SELECT TOP n`, `GROUP BY`, a date filter) - see section 4.
- Anchor relative time to the data, not the calendar: `WHERE dt >= (SELECT MAX(dt) FROM <db>.<t>) - 90` beats
  `CURRENT_DATE - 90` on any dataset that is not loaded daily. Integer-day arithmetic is the house style;
  `INTERVAL '365' DAY` is valid Teradata too and needs no correction.
- Show the SQL you ran with the answer (the tool returns the rendered statement in its metadata).

### Stop-check before emitting any SQL

| forbidden | write instead | Teradata error if you forget |
|---|---|---|
| `LIMIT n`, `FETCH FIRST n ROWS ONLY` | `SELECT TOP n ...` or `QUALIFY ROW_NUMBER() OVER (...) <= n` | 3706 syntax |
| `col = NULL`, `col <> NULL` | `col IS NULL` / `col IS NOT NULL` | none - silently returns zero rows |
| alias `count`, `date`, `value`, `key`, `type`, `status`, `period`, `level`, `time`, `position` | `cnt`, `dt`, `val`, `k`, `type_name`, `status_val`, `period_val`, `lvl`, `tm`, `pos` | 3707 reserved word |
| `CAST(x AS VARCHAR)` | `CAST(x AS VARCHAR(100))` | 3706/3707 |
| non-aggregated column not in GROUP BY | add it to GROUP BY or aggregate it | 3504 |
| `CASE WHEN x IN (SELECT ...)` | scalar subquery in SELECT, or `LEFT JOIN ... IS NULL` | 3771 |
| unqualified column shared by two joined tables | `t.col` | 3809 ambiguous |
| `MONTH(dt)`, `YEAR(dt)` | `EXTRACT(MONTH FROM dt)`, `EXTRACT(YEAR FROM dt)`; on VARCHAR dates `SUBSTRING(col FROM 1 FOR 7)` | 3706 |
| `DECIMAL / DECIMAL` or `INTEGER / INTEGER` for a ratio | `CAST(a AS FLOAT) / NULLIFZERO(b)` | none - DECIMAL rounds to the wider input's scale, INTEGER truncates toward zero; a small ratio silently reads 0 |
| bare table name | `<db>.<table>` | 3807 |

## 2. Write contract (`base_writeQuery`) - always, in this order

**Availability first.** Upstream teradata-mcp-server 0.2.6 - the version bundled with this plugin - registers no
write tool at all; its `base_readQuery` would execute DDL/DML, which is exactly why the plugin's read guard denies
them. `base_writeQuery` is present only when the server you are connected to provides it (a bridge to a deployment
that adds one). Check the tool list; if it is absent, the agent has NO write path: prepare the statement, run the
preview (step 1), show the blast radius (step 2), then hand the final statement to the user to run in their own SQL
client. NEVER switch `TERADATA_SQL_GUARD_MODE` to `audit` to push a write through `base_readQuery` - in audit mode
nothing prompts and DML commits immediately.

1. **Read first.** DDL of the target; for DML a preview of the blast radius through `base_readQuery`:
   `SELECT COUNT(*) FROM <db>.<t> WHERE <same predicate>;` (and `TOP 5` of the rows for UPDATE/DELETE).
2. **State the blast radius** in one line: object, statement kind, row count that will change, and "no WHERE clause"
   in capital letters when that is the case.
3. **Confirm.** Wait for an explicit "yes" in chat. Then call `base_writeQuery` with exactly ONE statement.
   Claude Code will ALSO show a permission prompt for destructive statements (`DELETE DROP TRUNCATE INSERT UPDATE MERGE ALTER MODIFY RENAME REPLACE GIVE
   MERGE ALTER MODIFY CREATE DATABASE/USER GRANT REVOKE ABORT SESSION`) naming the object - that second gate is
   intentional; do not try to avoid it, and do not route writes through `base_readQuery` (the guard denies them).
   `TERADATA_ALLOW_WRITES=0` or profile `tv_readonly` means writes are disabled on purpose: say so and stop.
4. **Verify** with a `SELECT` (row count after, or the new object's DDL) and report before/after.

Write-path notes:
- `CREATE TABLE`, `CREATE VIEW`, `COLLECT STATISTICS`, `CREATE AUTHORIZATION`, `WRITE_NOS` pass without a prompt (not
  destructive); `DROP FOREIGN TABLE` also passes - it deletes metadata only, never the files behind it.
- DDL auto-commits; DML is committed by the tool. There is no rollback after the call returns.
- `CREATE TABLE ... AS (...) WITH DATA` needs a working database with PERM: in DBC it fails with `3524`; with no room
  in the parent it fails with `3541 The request to assign new PERMANENT space is invalid` (a space error that reads
  like syntax - check `DBC.DatabasesV.PermSpace` on the PARENT, not system totals). Session-scoped scratch work
  needs no write tool: `base_readQuery` with `persist: true` (section 1) creates the volatile table for you.
- Before any `DROP TABLE`/`DROP VIEW` on an object you did not create this session, run the `drop-impact` workflow
  from `/teradata-vantage:archive` (or at minimum `dba_tableUsageImpact`/`base_tableAffinity`) and show the result.
- The `dbscontrol`/`tpareset` class of host commands is not SQL and not this skill; see `teradata-recovery`.

## 3. Error-driven repair loop (max two retries, then stop)

When a tool result carries `Error NNNN`, the coaching hook appends what the code means. Act on it in the SAME turn -
emit the follow-up tool call, do not narrate "I will retry":

| code | actually means | same-turn action |
|---|---|---|
| 5628 | column not found in that table | `base_tableDDL` -> fix the column name -> re-run |
| 3810 | column/parameter does not exist (often a view or alias scope) | `SHOW VIEW` or `base_columnDescription` -> re-run |
| 3807 | object not visible under that exact name | `base_tableList` on the database -> qualify/correct -> re-run |
| 3706 | syntax: usually `LIMIT`, a `CAST` closed with an alias, a multi-word alias, or a reserved word | apply the stop-check row -> re-run |
| 3707 | reserved word used as an identifier | rename the alias -> re-run |
| 3504 | non-aggregate column missing from GROUP BY | fix GROUP BY -> re-run |
| 2616 | numeric overflow in an aggregate | `CAST(col AS FLOAT)` or `DECIMAL(18,2)` before SUM/multiply -> re-run |
| 2666 / 5407 | date typing (CAST of a timestamp string as DATE / SUBSTRING on a real DATE) | check `ColumnType` (`DA` vs `CV`) -> re-run |
| 3523 | no right on the object | report; do not work around it |
| 3524 / 3541 | DBC default database / parent out of space | report; the fix is administrative (`health` skill) |

Rules: exactly two correction attempts per statement, each shown the exact error text; on the third failure return
what you tried and the last error - NEVER an empty answer. **Refuse rather than substitute**: if the requested measure
cannot be built from the columns that exist, say what is missing instead of returning a plausible number under the
requested name. Deriving a measure by arithmetic across existing columns is a definition, not a substitute. Never
answer a 0-row result by silently dropping the user's filter; report the empty window and the data's actual
`MIN`/`MAX` of the filtered column.

**When the loop ends in a right answer, consider promoting it.** The gate is deliberately strict and
has two conditions, both required:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding.py" verify-query \
  --question "<the user's question>" --sql "<the statement>" \
  --executed-cleanly --confirmed-by "<who confirmed the ANSWER>" --objects <db>.<tbl>
```

`--executed-cleanly` alone is refused. **SQL that runs is not SQL that is correct** — a wrong join
returning plausible numbers runs perfectly, and promoting on execution would fill the repository with
confident mistakes for the next session to trust. Ask the user to confirm the answer was right, and
name them. If they have not confirmed it, do not promote it; there is no hurry.

## 4. Result-size discipline

- Results above `TERADATA_MAX_RESULT_CHARS` (120000) come back with a truncation note. That note is an instruction:
  re-run with `GROUP BY` the one dimension needed, a `WHERE` on the date column, or `SELECT TOP n`. Do not page
  through raw rows.
- Chart-ready shape: one dimension column plus 1-3 measures, <= ~15 rows for categories, <= ~60 points for a time
  series. "Which/top/worst" questions are `SELECT TOP n ... ORDER BY <measure> DESC` with a `GROUP BY` (without the
  GROUP BY a ranking collapses to one mislabeled row) - but when that result is headed for `persist: true`, drop the
  `TOP n`/`ORDER BY`, persist the whole aggregate and rank from the volatile table (section 1).
- Row-level requests: cap at 100 rows and say how many more exist (`COUNT(*)` in the same turn).
- Facts with millions of rows: ALWAYS filter on the partitioning/date column; never `SELECT *` on them.
- Every user-named filter value (year, status, region, id) MUST appear in the WHERE clause. Calling without the
  filter and labelling the answer with it is fabrication.

## 5. Presentation

- HEADLINE (one sentence) -> KEY FINDINGS (a table for 4+ rows, otherwise <= 5 bullets) -> optional one-line
  interpretation -> the SQL in a fenced block. No preamble, no restating the question.
- Numeric fidelity: copy numbers exactly as returned; for 7+ digits copy character by character; abbreviate
  >= 10,000,000 as `153.77M` / `2.27B` (2 decimals) next to the exact value. Thousand separators on counts. No
  currency symbol unless the column or the user says which currency it is.
- Say "the data shows" instead of naming tools.
- Charts: `plot_line_chart(table_name, labels, columns)`, `plot_pie_chart(table_name, labels, column)` (and polar/radar)
  read a TABLE or VIEW, not an ad-hoc result set - to chart an aggregate, first materialise it with
  `base_readQuery(persist: true)` and pass `metadata.volatile_table` as `table_name` (same pool caveat as section 1;
  the persisted SELECT must contain no `ORDER BY` - persist the whole aggregate and let the chart tool order it,
  never a `TOP n ... ORDER BY` ranking, which persists as an arbitrary n rows),
  or point the tool at an existing view. Say "I created a line chart of ..." rather than the tool name.

## 6. Reviewing SQL files

For 1-2 files, read them and review against the stop-check and the `teradata-sql` rules inline (offer `EXPLAIN` through
`base_readQuery` when a connection exists). For **3 or more** `.sql`/`.bteq` files, launch the `sql-review` workflow:
`Workflow({name: "teradata-vantage:sql-review", args: {files: [...], explain: false}})` - parallel per-file review,
adversarial verification, one ranked report. Before launching, check that the `Workflow` tool exists in this session;
if it does not, say that dynamic workflows are off (Claude Code setting "Dynamic workflows") and review inline.
Editing `.sql` files also triggers the plugin's sqlfluff lint hook when `sqlfluff` is installed.

## What this skill does NOT do

- Schema discovery beyond the object at hand -> `explore`. Data-quality statistics -> `profile`. EXPLAIN-plan reading,
  statistics and index advice -> `tune`. Space, sessions, flow control -> `health`. NOS/Iceberg archiving -> `archive`.
- Host-level recovery commands -> `teradata-recovery`.
- It never disables a guard, never asks for credentials, never pastes a connection string.
