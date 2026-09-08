# dbt against Teradata — the adapter-specific parts

Generic dbt knowledge transfers. This page is only the parts where Teradata differs, because those are
the parts that cost time.

## The profile

```yaml
<profile-name>:
  target: dev
  outputs:
    dev:
      type: teradata
      host: <host>
      user: <username>
      password: <password>
      schema: <database-name>
      tmode: ANSI
      threads: 4
      # optional: logmech, port, retries, and driver passthrough fields
```

Three things that trip people:

- **`schema` means DATABASE.** Teradata has no separate schema layer — a database *is* the namespace. A
  dbt user coming from Snowflake or BigQuery will expect `database` and `schema` to be two levels; here
  they collapse. `+schema:` overrides in `dbt_project.yml` therefore move models between databases.
- **`tmode` is not cosmetic.** ANSI and TERA differ in commit behaviour, in case sensitivity of
  comparisons, and in what counts as an error. Changing it mid-project changes results. The
  `client-development` skill covers the two modes; read it before switching.
- **The user needs `CREATE TABLE`, `CREATE VIEW` and `DROP` in the target database**, plus `SELECT` on
  every source. Permission failures surface as dbt errors that name the object, not the privilege — check
  with `sec_userDbPermissions` rather than guessing.

## Materializations

`view`, `table`, `ephemeral`, `incremental`.

`ephemeral` compiles into a CTE in the consuming model and creates nothing. It is the right choice for a
staging model used once, and the wrong choice for one used by six models — the CTE is then inlined six
times and Teradata optimises each copy separately.

## Incremental strategies

Five, set with `incremental_strategy`:

| Strategy | Behaviour | Needs |
|---|---|---|
| `append` | insert new rows, no matching. **The default** | nothing |
| `delete+insert` | delete matching keys, then insert | `unique_key` |
| `merge` | one `MERGE` statement, update-or-insert | `unique_key` |
| `valid_history` | maintain a temporal validity window | `valid_period`, `use_valid_to_time` |
| `microbatch` | process in time-based batches | an event-time column |

**`append` being the default is the trap.** A model written expecting upsert semantics but with no
`incremental_strategy` set will silently duplicate every row it reprocesses. The symptom is a row count
that grows by the batch size on every run while the key count stays flat:

```sql
SELECT COUNT(*) AS rows, COUNT(DISTINCT <unique_key>) AS keys FROM <db>.<model>;
```

If `rows` exceeds `keys`, the strategy is wrong or the key is not unique. That one query settles most
"my incremental model is broken" reports.

`valid_history` is Teradata's own and has no equivalent elsewhere:

```jinja
{{ config(
     materialized='incremental',
     unique_key='id',
     on_schema_change='fail',
     incremental_strategy='valid_history',
     valid_period='valid_period_col',
     use_valid_to_time='no'
) }}
```

Use it for slowly-changing dimensions where the validity window is the point. Do not reach for it as a
general upsert — `merge` is that.

## Physical design in the model config

The adapter picks a primary index if you do not, and the default is rarely the right one. This is the
highest-leverage Teradata-specific config there is, because the PI decides data distribution, and skew
decides whether the table is fast or unusable.

```jinja
{{ config(materialized="table", index="UNIQUE PRIMARY INDEX ( GlobalID )") }}
```

```jinja
{{ config(
     materialized="table",
     index="PRIMARY INDEX(id)
     PARTITION BY RANGE_N(create_date
                   BETWEEN DATE '2020-01-01'
                   AND     DATE '2021-01-01'
                   EACH INTERVAL '1' MONTH)"
) }}
```

```jinja
{{ config(
     materialized="table",
     index="PRIMARY INDEX(id)
     PARTITION BY RANGE_N(create_date
                   BETWEEN DATE '2020-01-01'
                   AND     DATE '2021-01-01'
                   EACH INTERVAL '1' MONTH)
     INDEX index_attrA (attrA) WITH LOAD IDENTITY"
) }}
```

⚠️ **No commas between index statements.** The clauses are concatenated as written into the `CREATE
TABLE`. A comma is the natural thing to type and it produces a syntax error that points at the generated
DDL rather than at the model.

Two more that matter:

- **A partition range that ends before your data does silently drops nothing but ages badly** — rows past
  the end land in the last partition or fail, depending on the `NO RANGE` clause. Extend the range when
  you extend the data.
- **Choose the PI for distribution first, access second.** A PI on a low-cardinality column concentrates
  rows on few AMPs. The `tune` skill's skew check is the confirmation.

## Verifying what the adapter actually built

Do not trust the config; read the DDL. This is the fastest way to prove a typo:

```
base_tableDDL(database_name="<db>", table_name="<model>")
```

Check three things: is it a table or a view, what is the primary index, and is the partitioning there.
A config that failed to parse frequently produces a valid table with a default PI and no error the user
ever saw.

Then the space and skew numbers:

```
dba_tableSpace(database_name="<db>")
```

Skew above roughly 10 percent on a large table is worth raising; above 30 percent it is the answer to
whatever performance question was asked.

## Things to say plainly

- `dbt run --full-refresh` on an incremental model **drops and rebuilds it**. On a large fact table that
  is an outage, not a refresh. It is a `[WRITE]` and the user runs it, not you.
- dbt's own lineage covers dbt models. Objects outside the project — a table loaded by an external job,
  a view someone made by hand — are invisible to it. The `lineage` skill's edge repository is what covers
  those, and the two are complementary rather than redundant.
- You cannot run dbt. Report what is in the database and let the user run the pipeline.
