---
name: docs
description: Use when the question is "where do I look this up" - what release this system is, whether a feature or function is actually installed here, and what a table or column means according to the people who built it. Teaches reading and writing COMMENT, the closest thing Teradata has to a built-in data catalogue and invisible to SHOW TABLE.
when_to_use: where is this documented; what does this column mean; what version of Teradata is this; is this feature available on my system; is this function installed; data catalogue; data dictionary; COMMENT ON; add a description to a table; document this table; which manual applies; is this release specific; what does the dictionary know about this object.
license: MIT
metadata:
  skill_type: documentation
  category: teradata
  version: "1.0.0"
argument-hint: "[version | is <feature> installed | what is <db>.<table> | document <db>.<table>]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__dba_databaseVersion
---

# Looking things up on Teradata

**Ask the database before you reach for a manual.** Most "where is this documented" questions are
answered authoritatively by the dictionary, and a dictionary answer describes *this* system while a
manual describes a release. When they disagree, the dictionary is right.

The order below is the whole skill: version, then availability, then meaning, then — only for what is
left — documentation.

## 1. Which release is this, and therefore which manual applies

```sql
SELECT InfoKey, InfoData FROM DBC.DBCInfoV;
```

Returns `VERSION`, `RELEASE` and `LANGUAGE SUPPORT MODE` — measured `20.00.28.81` on the system used
throughout this plugin. `dba_databaseVersion` is the tool form.

**A version-agnostic answer about Teradata is usually a wrong answer.** Function availability, syntax
support and defaults all move between releases. Whenever you cite documentation, say which release it
applies to and confirm it matches what the query above returned.

## 2. Is the thing actually installed

Presence in the manual is not presence on the system. Generalising the `analytics` skill's Rule 0 beyond
`TD_*`:

```sql
SELECT DatabaseName, FunctionName, FunctionType
FROM   DBC.FunctionsV WHERE UPPER(FunctionName) = 'TD_KMEANS';       -- a function

SELECT DatabaseName, TableName, TableKind
FROM   DBC.TablesV WHERE DatabaseName = 'TD_MLDB';                   -- a feature's objects
```

`FunctionType` distinguishes what you found: `L` a table operator, `F` a scalar function, `C` an internal
`_contract` companion you never call, `R` a table function that must be wrapped in `TABLE(...)`.

⚠️ **And installed is still not working.** The `analytics` skill documents three functions that are
registered, callable, and return zero rows while echoing your input columns back — no error. The
dictionary cannot tell you that; only a smoke test can. `analytics` → Rule 1 has the method.

## 3. What the object means — `COMMENT`, the part nobody uses

Teradata carries its own documentation in the dictionary, and almost nobody reads or writes it. Measured
on one system: **16,410 populated column comments and 866 table comments.**

```sql
SELECT TRIM(CommentString) FROM DBC.TablesV
WHERE  DatabaseName = '<db>' AND TableName = '<table>';

SELECT TRIM(ColumnName), TRIM(CommentString) FROM DBC.ColumnsV
WHERE  DatabaseName = '<db>' AND TableName = '<table>' ORDER BY ColumnId;
```

⚠️ **`SHOW TABLE` does NOT include comments** — verified. That means `base_tableDDL`, which runs
`SHOW TABLE`, will never show you them either. If you read only the DDL you will conclude an object is
undocumented when it is not. Query the two views above; `base_columnDescription` is the tool that surfaces
column comments.

Writing them is a one-line `[WRITE]`, verified round-trip:

```sql
COMMENT ON TABLE  <db>.<table>       IS 'daily settlement totals';
COMMENT ON COLUMN <db>.<table>.<col> IS 'net of fees, EUR';
```

This is worth recommending. It is the closest thing Teradata has to a data catalogue, it travels with the
object through backup and restore, it needs no extra product, and it is where the next person will look.
When you have just worked out what an ambiguous column means, offer the `COMMENT ON` that records it —
that is how the answer survives the conversation.

Comments are documentation, not contracts: nothing enforces that one is current. Treat a comment as a
strong hint and say where it came from.

## 4. What the dictionary will not tell you

Some things genuinely require the manual, and recognising them quickly is the point:

- **Table-operator signatures.** `DBC.FunctionParametersV` does not exist, and `HELP FUNCTION` on a table
  operator returns **zero rows with a full column header** — it succeeds and tells you nothing. Error
  `7810` names a missing or unsupported argument when you call the function, which is the fastest route
  short of documentation; `AI_AskLLM`'s input aliases are the case where even that is not enough.
- **Semantics of a system view's columns** beyond its own `COMMENT`.
- **Licensing and what a feature costs**, which is commercial rather than technical.
- **Procedures for anything outside SQL** — installation, patching, hardware.

For these, say plainly that the answer needs documentation for release *X*, having already established
what *X* is in step 1. That is a better answer than a confident guess, and this plugin's rule is that a
wrong fact is worse than a missing one.

## Reporting

- Say where the answer came from: dictionary view, tool, comment, or documentation.
- **Quote the release** whenever the answer could be release-specific — which is most of the time.
- Distinguish "not installed here" from "does not exist in Teradata". They lead to different next steps.
- When you find an object with no comment and you have just worked out what it is, offer the
  `COMMENT ON`. Do not run it; it is a `[WRITE]`.

## Related

- `teradata-vantage:teradata-sql` → `references/dbc-dictionary.md` — the catalog views in depth.
- `teradata-vantage:analytics` — Rule 0 and Rule 1: installed, and installed but not working.
- `teradata-vantage:explore` — mapping objects when the question is what exists, not what it means.
