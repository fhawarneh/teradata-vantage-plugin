# AI-Native Data Products - the six-module Teradata design standard (github.com/Teradata/ai-native-data-products) and the discovery flow an agent runs against a product that follows it.

Source: https://github.com/Teradata/ai-native-data-products (MIT). Use this reference when a system exposes databases
named `<Name>_Semantic`, `<Name>_Memory`, `<Name>_Observability` or a `data_product_map` table - the product is
self-describing and the catalog queries in `dbc-catalog.md` become the fallback, not the first stop. Everything here
is a convention of that standard; verify table and column names on the system you are on (`SHOW TABLE`).

## The six modules

| module | database pattern | purpose |
|---|---|---|
| **Domain** | `<Name>_Domain` (or `<Name>`) | authoritative business data; SCD-2 history tables suffixed `_H` with `is_current BYTEINT` and `valid_to_dts DATE '9999-12-31'` |
| **Semantic** | `<Name>_Semantic` | the "table of contents": `entity_metadata`, `column_metadata`, `table_relationship`, `naming_standard`, `data_product_map`, plus the view `v_relationship_paths` (recursive CTE that pre-builds multi-hop JOIN SQL) |
| **Memory** | `<Name>_Memory` (may be co-located in `_Semantic`) | design memory as data: `Module_Registry`, `Design_Decision` (`DD-<MODULE>-<NNN>`), `Business_Glossary` (`BG-...`), `Query_Cookbook` (`QC-...`), `Change_Log` (`CL-...`) |
| **Observability** | `<Name>_Observability` (may be co-located) | append-only `usage_event`, `audit_event` |
| **Search** | `<Name>_Search` | `<entity>_embedding` tables (VECTOR column + FK to Domain) |
| **Prediction** | `<Name>_Prediction` | feature store |

Not every deployment ships all six; `data_product_map` says which exist and where they physically live (a product may
consolidate Memory and Observability into the Semantic database).

## Universal conventions (MUST follow when writing SQL against such a product)

| rule | why |
|---|---|
| Booleans are `BYTEINT NOT NULL DEFAULT 0/1`, named `is_*`; filter with `= 1` / `= 0` | filtering a BYTEINT with a string literal raises `Error 3535 A character string failed conversion to a numeric value` |
| Timestamps are `TIMESTAMP(6) WITH TIME ZONE` | microsecond precision + zone |
| Open-ended validity uses the sentinel `DATE '9999-12-31'` | "currently valid" rows: `valid_to_dts = DATE '9999-12-31'` or `is_current = 1` |
| `COMMENT ON TABLE` / `COMMENT ON COLUMN` are mandatory | agents read them at discovery time (`DBC.TablesV.CommentString`, `DBC.ColumnsV.CommentString`) |
| Surrogate keys: `BIGINT GENERATED ALWAYS AS IDENTITY` outside Domain; Domain `_H` tables use a keymap so one surrogate spans SCD versions | join on the surrogate, not the natural key, across versions |
| `table_relationship` must register EVERY join edge | `v_relationship_paths` is only as complete as its edges; a missing edge is an unreachable path |
| Prefer `sem_*` pre-aggregated objects where `naming_standard` lists them | they carry the product's arithmetic; recomputing from raw facts is how denominators get mixed up |

## Bootstrap discovery flow (run this before writing any SQL against the product)

```sql
-- 1. which modules are deployed, and where
SELECT module_name, database_name
FROM <Name>_Semantic.data_product_map
WHERE is_active = 1;

-- 2. which business entities exist (and their physical location)
SELECT business_name, physical_database, physical_table, module, description
FROM <Name>_Semantic.entity_metadata
WHERE is_active = 1
ORDER BY module, business_name;

-- 3. how to join two entities: the pre-built JOIN SQL, shortest path first
SELECT depth, path, join_sql
FROM <Name>_Semantic.v_relationship_paths
WHERE source_table = '<from_table>' AND target_table = '<to_table>'
ORDER BY depth;

-- 4. what a term means before guessing from training data
SELECT term, definition, synonyms
FROM <Name>_Memory.Business_Glossary
WHERE LOWER(term) LIKE LOWER('%<term>%');

-- 5. is there a verified query template for this question shape?
SELECT question_pattern, sql_template, category, success_count
FROM <Name>_Memory.Query_Cookbook
WHERE is_current = 1
  AND (category = '<category>' OR LOWER(question_pattern) LIKE LOWER('%<keyword>%'))
ORDER BY success_count DESC;

-- 6. column-level hints for a table (PII flags, validation rules the agent should honour)
SELECT em.physical_table, cm.column_name, cm.business_meaning, cm.is_pii, cm.is_sensitive, cm.validation_rule
FROM <Name>_Semantic.column_metadata cm
JOIN <Name>_Semantic.entity_metadata em ON em.entity_metadata_id = cm.entity_metadata_id
WHERE em.physical_table = '<table>';
```

7. THEN generate SQL from the discovered metadata. Paste `join_sql` straight into the FROM clause - no guessing FK
columns, no missing intermediate hop. If a cookbook template matches the question intent, prefer adapting it over fresh
SQL: templates are verified. Row limits in these queries are `SELECT TOP n`, never `LIMIT`/`FETCH FIRST`.

## The Semantic module in detail

- `entity_metadata` - one row per business-meaningful table or view: `business_name`, `physical_database`,
  `physical_table`, `module`, `description` (plain English including enum hints), `is_active`.
- `column_metadata` - one row per meaningful column, FK to `entity_metadata_id`; carries `is_pii`, `is_sensitive`,
  `validation_rule` (an agent-actionable hint such as "0/1 flag stored as VARCHAR - quote it").
- `table_relationship` - `source_table`, `target_table`, `source_column`, `target_column`, `cardinality`
  (`1:1`/`1:M`/`M:1`/`M:M`), `relationship_type` (`FK`/`HIERARCHY`/`ASSOCIATIVE`). Drives `v_relationship_paths`
  (paths up to 5 hops with the JOIN SQL pre-built).
- `naming_standard` - the site's conventions (e.g. `sem_*` = pre-aggregated, `*_id` = FK on the matching name,
  flags stored as VARCHAR vs BYTEINT).
- `data_product_map` - the bootstrap table; query it FIRST.

## The Memory module in detail

- `Module_Registry` - one row per deployed module with `deployment_status` (`DEPLOYED`/`PLANNED`/`DEPRECATED`).
- `Design_Decision` - every architectural choice with `category`, `rationale`, `alternatives_considered`, temporal
  columns `valid_from`/`valid_to`/`is_current`; superseded rows are preserved.
- `Business_Glossary` - `term`, `definition`, `synonyms`, `domain`.
- `Query_Cookbook` - verified question-to-SQL templates: `question_pattern`, `sql_template`, `category`,
  `success_count`, `last_used`; seed rows `QC-<MODULE>-<NNN>`, agent-written rows `QC-<PRODUCT>-AUTO-<uuid8>`.
  Writing back a new verified template is an INSERT (write-gated) - only do it when the user asks.
- `Change_Log` - append-only history of module changes.

## The Observability module in detail

- `usage_event` - append-only log of agent invocations: `event_ts`, `agent_id`, `user_id`, `session_id`,
  `query_text`, `tool_calls_json`, `latency_ms`, `success_flag BYTEINT`.
- `audit_event` - compliance-flagged accesses, populated when a query touched a column with `is_pii = 1` or
  `data_classification = 'RESTRICTED'`.

```sql
-- recent activity against the product (SQL-queryable telemetry, independent of any tracing tool)
SELECT TOP 20 event_ts, agent_id, query_text, latency_ms, success_flag
FROM <Name>_Observability.usage_event
ORDER BY event_ts DESC;
```

## When a system only partly follows the standard

Common deviations: Memory/Observability tables physically inside `_Semantic`; `Business_Glossary` absent (the
cookbook covers the use case); flags stored as VARCHAR `'0'`/`'1'` in Domain tables that predate the standard (quote
them - `naming_standard` will say so). Read `entity_metadata.description` and `column_metadata.validation_rule`
before assuming; those two columns are where the product's authors left the hints an agent needs.
