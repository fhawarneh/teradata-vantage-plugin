---
name: pipelines
description: Use when building or debugging a data pipeline that targets Teradata Vantage - dbt models through the dbt-teradata adapter, or Airflow DAGs through the Teradata provider. Covers the connection profile, the materializations and incremental strategies the adapter supports, the Teradata-specific config for primary index and partitioning, and which Airflow operator to use.
when_to_use: dbt with Teradata; dbt-teradata; profiles.yml for Teradata; incremental model on Teradata; primary index in a dbt model; my dbt model rebuilds everything every run; Airflow DAG against Teradata; apache-airflow-providers-teradata; BteqOperator; SQLExecuteQueryOperator Teradata; orchestrate a Teradata load; compute cluster operator.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[dbt setup | incremental <model> | index config | airflow dag | which operator]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__dba_tableSpace
---

# Pipelines into Teradata — dbt and Airflow

Two tools, two jobs. **dbt owns the transformation** — models, tests and lineage as version-controlled
SQL. **Airflow owns the schedule and everything around it** — landing files, triggering the dbt run,
waiting on dependencies. They compose; they do not compete.

## What this plugin can and cannot do here

Be clear about the boundary, because it shapes every answer.

- **It can read the warehouse.** Inspect what a model produced, check the DDL the adapter generated,
  compare row counts before and after, look at space and skew. That is the useful half.
- **It cannot run your pipeline.** There is no dbt tool and no Airflow tool. `dbt run` and `airflow dags
  test` are shell commands the user runs, and the bundled MCP server is read-only through
  `base_readQuery`.
- **So the division of labour is:** the user runs the pipeline, and you diagnose the result against the
  database. That is a genuinely strong position — most pipeline bugs are visible in the output table.

## dbt — the parts that are Teradata-specific

Connection profile. Note that dbt's `schema` is a Teradata **database**, and that `tmode` matters:

```yaml
<profile-name>:
  target: dev
  outputs:
    dev:
      type: teradata
      user: <username>
      password: <password>
      schema: <database-name>      # a DATABASE in Teradata terms
      tmode: ANSI                  # see the client-development skill before changing this
      threads: 4
```

`tmode` decides transaction semantics and even error behaviour. The `client-development` skill covers
ANSI versus TERA properly — send the user there rather than guessing, because switching it changes
commit behaviour under running models.

**Materializations**: `view`, `table`, `ephemeral`, `incremental`.

**Incremental strategies**: `append` (the default), `delete+insert`, `merge`, `valid_history`,
`microbatch`. Two are worth knowing by name:

- `merge` is usually what someone means by "upsert", and it needs a `unique_key`.
- `valid_history` is Teradata's own, for temporal tables with a validity period — it takes
  `valid_period` and `use_valid_to_time`. Nothing equivalent exists on other warehouses, so it is
  invisible in generic dbt documentation.

**The physical design belongs in the model.** This is the single highest-leverage Teradata-specific
config, because the adapter otherwise picks a primary index for you and a bad one skews the whole table:

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

⚠️ **There are no commas between index statements.** Adding one is a natural thing to do and it fails.

`references/dbt-teradata.md` carries the incremental strategies in full, the index config forms, and how
to check what the adapter actually built.

## Airflow — which operator

The provider is `apache-airflow-providers-teradata`, connection type `teradata`, conventionally
`teradata_conn_id="teradata_default"`.

| Job | Reach for |
|---|---|
| Run SQL | `SQLExecuteQueryOperator(conn_id="teradata_default", sql=...)` from `common.sql` |
| Run a BTEQ script | `BteqOperator` — use it when you need BTEQ's own control flow and return codes |
| Load from object storage | `S3ToTeradataOperator`, `AzureBlobStorageToTeradataOperator` |
| Copy between systems | `TeradataToTeradataOperator` |
| Cloud Lake compute | `TeradataComputeClusterProvision/Resume/Suspend/Decommission` operators |

**Prefer `SQLExecuteQueryOperator` over a dedicated `TeradataOperator`.** Airflow has been consolidating
per-database SQL operators onto the common one across every provider; the dedicated operators are the
deprecated path. If the user's DAG uses `TeradataOperator`, it probably still works — say that it is the
older form rather than that it is broken.

⚠️ **The compute-cluster operators are Vantage Cloud Lake only.** They provision and suspend Cloud Lake
compute groups and have no meaning against on-premises Vantage or Vantage Express. Check which the user
is on before recommending them; the `dba_databaseVersion` tool answers that.

`references/airflow.md` has the DAG shapes, the connection fields, and the failure modes.

## Diagnosing a pipeline from the database side

This is where the plugin earns its place. When the user says the model is wrong, look at what landed:

1. **Did it build at all?** `base_tableList` on the target database, then `base_tableDDL` on the model.
2. **Is it a table or a view?** A model that was supposed to be `table` and came back a view means the
   config did not take.
3. **Did the incremental actually increment?** Compare the row count against the previous run. "It
   rebuilds everything every time" is usually a missing or non-unique `unique_key`, and it shows up as a
   full row count on a `merge` model.
4. **Is the primary index sane?** Skew from a bad PI is the most common "dbt made my table slow". The
   `tune` skill owns the skew check; `dba_tableSpace` gives the numbers.
5. **Is the DDL what the config asked for?** `base_tableDDL` shows the PI and partitioning the adapter
   actually emitted, which is the fastest way to prove a config typo.

## Reporting

- Say whether you are reading the pipeline code, the database, or both. They disagree more often than
  anyone expects, and which one you looked at changes what the answer means.
- Quote the model or task name and the target object, fully qualified.
- Never claim a pipeline ran. You did not run it. Report what is in the database and when it was built.
- Anything you propose that changes data is a `[WRITE]` for the user — including `dbt run --full-refresh`,
  which is far more destructive than it looks on an incremental model.

## References

- `references/dbt-teradata.md` — the profile, materializations, all five incremental strategies, the
  index and partitioning config, and verifying what the adapter built.
- `references/airflow.md` — the provider's operators, connection setup, and the Cloud Lake boundary.
