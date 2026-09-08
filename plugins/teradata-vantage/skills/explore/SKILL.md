---
name: explore
description: Use when the user asks what databases, tables, views or columns exist on a Teradata Vantage system, wants DDL, data types, sample rows, row counts, table usage or relationships, or says "describe the schema" - read-only discovery through the plugin's base_* tools with the DBC catalog as backup. Not for computing business metrics.
when_to_use: what databases exist; list tables in a database; show me the schema; describe this table; what columns does it have; DDL for; preview the data; which tables are related to; which tables are used most; is this a view or a table; explore the database; map the data model
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
allowed-tools:
  - Read
  - Grep
  - mcp__plugin_teradata-vantage_teradata__base_databaseList
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__base_columnMetadata
  - mcp__plugin_teradata-vantage_teradata__base_tablePreview
  - mcp__plugin_teradata-vantage_teradata__base_tableUsage
  - mcp__plugin_teradata-vantage_teradata__base_tableAffinity
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
disallowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_writeQuery
---

# Explore a Teradata Vantage schema

Read-only discovery. Every answer here comes from a tool result or a DBC catalog query; nothing is inferred from
names alone. Tool names below are the plugin's MCP tools, `mcp__plugin_teradata-vantage_teradata__<tool>`.

## The ladder - go one rung at a time, stop when the question is answered

| rung | tool | what it runs | answers |
|---|---|---|---|
| 1 | `base_databaseList` | `DBC.DatabasesVX` minus ~45 system databases (`scope='all'` includes them) | which databases exist (the `VX` views show only what your user has rights on) |
| 2 | `base_tableList(database_name)` | `DBC.TablesVX` with `TableKind IN ('T','V','O','Q')`; empty name = every database | which tables/views a database holds (returns DatabaseName + TableName) |
| 3 | `base_tableDDL(database_name, table_name)` | `SHOW TABLE <db>.<table>` | exact DDL: types, PI, partitioning, constraints (`base_saveDDL` writes it to a `.sql` file instead - only when the user asks to export) |
| 3b | `base_columnDescription(database_name, table_name)` | `DBC.ColumnsVX` with the type-code decode | column names + types for tables AND views; both arguments default to `%` and are matched with `LIKE`, so `table_name` accepts `%` wildcards |
| 3c | `base_columnMetadata(database_name, object_name)` | `DBC.ColumnsVX` + `DBC.IndicesVX` (views via HELP) | exact type codes, LATIN/UNICODE, precision/scale, nullability, index role; bulk across many objects |
| 4 | `base_tablePreview(database_name, table_name)` | `SELECT TOP 5 * ...` | the shape of the data, never its domain |
| 5 | `base_tableUsage(database_name)` / `base_tableAffinity(database_name, table_name)` | DBQL object logs | hot/cold ranking; which tables are queried together (relationship inference) - `base_tableAffinity` matches `table_name` exactly, so `%` there returns nothing |
| 6 | `base_readQuery` | your own `SELECT`/`WITH`/`EXPLAIN`/`SHOW`/`HELP` | counts, DISTINCT domains, catalog joins - `references/dbc-catalog.md` |

Skip rungs when the user already named the object ("describe sales.sales_fact" starts at rung 3). Emit
independent calls in ONE turn (DDL for three named tables = three parallel calls); keep dependent calls sequential.

## Rules (each one is a failure that has actually happened)

1. ALWAYS call a tool before answering. NEVER describe a table you have not read. If a tool returns nothing, say
   exactly "No data returned from <tool>" and what that implies; do not fill the gap.
2. ALWAYS qualify objects as `<db>.<table>` in SQL and in tool arguments. The session default database is whatever
   the connection URI says; a bare name fails with `Error 3807 Object '<name>' does not exist` and wastes a turn.
3. NEVER enumerate a domain from a preview. `base_tablePreview` is `SELECT TOP 5 *` - five arbitrary rows that
   usually share one status/category value. For "which values/categories/regions exist", run
   `SELECT DISTINCT <col> FROM <db>.<table>` (or a `GROUP BY <col>` with `COUNT(*)`) through `base_readQuery`, and
   report EVERY value returned.
4. `base_tableDDL` is `SHOW TABLE`. On a view it fails with `Error 3853 '<name>' is not a table` - that message
   means "wrong statement for this object kind", not "missing object". Run `SHOW VIEW <db>.<view>;` through
   `base_readQuery` (SHOW passes the read guard), or use `base_columnDescription` for the column list.
5. Use the exact names the user or a previous result gave. Pronouns ("that table", "the same database") resolve to the
   names already in the conversation; never guess or scan unrelated databases.
6. Return complete tool results for catalog questions (all tables, all columns). Summarise only when the user asks for
   a summary, and then say how many items you are summarising.
7. `base_tableUsage` and `base_tableAffinity` read `DBC.DBQLObjTbl`/`DBC.DBQLogTbl`. They need DBQL object logging
   enabled and readable. An empty result means "no logged usage in the DBQL window", NEVER "unused". Before drawing
   conclusions from an empty result, check the precondition once:
   `SELECT COUNT(*) FROM DBC.DBQLObjTbl SAMPLE 1;` (any error or 0 -> say DBQL is not available and stop inferring).
8. NEVER modify anything. This skill has `base_writeQuery` disallowed (and the bundled 0.2.6 server registers no
   write tool at all); if the user asks for a change, hand over to the `query` skill's write contract.
9. Numbers from results are copied exactly (7+ digit numbers character by character); row counts get thousand separators.

## Column types

`base_columnDescription` returns `CType` decoded from `DBC.ColumnsVX.ColumnType` (e.g. `CV`=VARCHAR, `DA`=DATE,
`TS`=TIMESTAMP, `D`=DECIMAL, `I`=INTEGER). When the type matters (dates stored as VARCHAR, DECIMAL scale, LATIN vs
UNICODE, which columns form the primary index), call `base_columnMetadata` or run the `DBC.ColumnsV` query in
`references/dbc-catalog.md` for `ColumnLength`, `DecimalTotalDigits`, `DecimalFractionalDigits`, `CharType`. The full
decode table and the `TableKind` codes are in that reference.

## Relationships without foreign keys

Teradata schemas rarely declare FKs. Combine, in this order:

1. `base_tableAffinity` - tables that appear together in the same logged queries (needs DBQL).
2. Naming: a column `<x>_id` in table A and a table named `<x>`/`<x>s` with an `id`/`<x>_id` column is a candidate join.
   Say "candidate", then confirm with a join count:
   `SELECT COUNT(*) FROM <db>.a JOIN <db>.x ON a.x_id = x.x_id;` vs `SELECT COUNT(*) FROM <db>.a;`
3. `SHOW VIEW` on the semantic views - view SQL is the most reliable record of how the site joins its tables.
4. If the system follows the AI-Native Data Products standard, its `<Name>_Semantic.table_relationship` and
   `v_relationship_paths` already hold the join graph - `references/ai-native-data-products.md`.

## Scope control

- One database, <= 20 tables: walk the ladder here.
- More than 20 tables, or "profile/document the whole database": do not loop through tables in the main
  conversation. Delegate to the `teradata-vantage:explorer` agent (Agent tool) for a structured walk, or run the
  `profile-database` workflow from `/teradata-vantage:profile` for a parallel per-table profile with a report.
- Cross-database questions ("where is customer data?"): `base_databaseList` -> `base_tableList` per candidate
  database in one parallel turn -> `base_columnDescription` with `table_name='%'` and a `LIKE` on the column name via
  `DBC.ColumnsV` (`references/dbc-catalog.md`, "find a column by name") instead of previewing everything.
- Data-quality questions (nulls, distributions) belong to the `profile` skill; performance questions to `tune`.

## Presentation

- Lead with a one-line answer, then a markdown table for 4+ rows (Database | Table | Kind, or Column | Type | Nullable).
- Say "the catalog shows" rather than naming tools; show the SQL you ran when you ran your own SQL.
- For long lists give the full list; if the user asked for an overview, give counts per kind plus the first 20 with
  "and N more".
- End with at most ONE follow-up question, only when the next step is truly ambiguous.

## Errors you will meet here (open with what the message means)

| code | actually means | do |
|---|---|---|
| 3807 | the object is not visible under that exact `<db>.<name>` (unqualified, misspelled, or no SELECT right) | `base_tableList` on the database; check spelling; qualify |
| 3853 | `SHOW TABLE` on a view (or macro) | `SHOW VIEW <db>.<view>;` via `base_readQuery` |
| 3523 | you lack the right on that object (a GRANT on the parent database does not cascade to a child) | report the missing right; `sec_userDbPermissions` if available |
| 5628 | column not in the table | re-read the DDL, fix the name, re-run in the same turn |
| 6706 | untranslatable character (LATIN session/columns) | qualify with `TRANSLATE`/`CHARACTER SET UNICODE`, or filter on an ASCII key |
