# Airflow against Teradata — the provider

Package `apache-airflow-providers-teradata`. Connection type `teradata`, conventionally
`teradata_conn_id="teradata_default"`.

## The connection

Configure it as an Airflow connection, not in DAG code. Host, login, password, and the database in
`schema`; driver options such as `tmode` and `logmech` go in the connection's extra field. Credentials in
a DAG file end up in git and in the rendered-template view of every task instance.

## Which operator

| Job | Operator |
|---|---|
| Run SQL | `SQLExecuteQueryOperator` from `airflow.providers.common.sql.operators` |
| Run a BTEQ script | `BteqOperator` from `airflow.providers.teradata.operators.bteq` |
| Load from S3 | `S3ToTeradataOperator` |
| Load from Azure Blob | `AzureBlobStorageToTeradataOperator` |
| Copy between Teradata systems | `TeradataToTeradataOperator` |
| Cloud Lake compute groups | `TeradataComputeClusterProvisionOperator`, `…ResumeOperator`, `…SuspendOperator`, `…DecommissionOperator` |

```python
from airflow.providers.common.sql.operators import SQLExecuteQueryOperator

run_sql = SQLExecuteQueryOperator(
    task_id="build_daily_agg",
    conn_id="teradata_default",
    sql="sql/build_daily_agg.sql",     # a path ending .sql is templated from the DAG folder
    autocommit=False,
)
```

**Prefer `SQLExecuteQueryOperator` to a dedicated `TeradataOperator`.** Airflow has been consolidating
per-database SQL operators onto the common one across all providers, and the dedicated ones are the
deprecated path. An existing DAG using `TeradataOperator` is old, not broken — say that rather than
raising an alarm.

`BteqOperator` is not a lesser `SQLExecuteQueryOperator`. Reach for it when you need BTEQ itself: its
`.IF ERRORCODE` control flow, its return codes, or an existing `.btq` script you do not want to rewrite.
The `sql-files` skill covers BTEQ structure and return codes.

⚠️ **The compute-cluster operators are Teradata Vantage Cloud Lake only.** They manage Cloud Lake compute
groups and profiles, and mean nothing on on-premises Vantage, Vantage Express, or a developer VM.
Confirm the platform with `dba_databaseVersion` before recommending them — suggesting a Cloud Lake
operator to an on-premises team reads as not knowing the product.

## The shape that works

Land, transform, verify — with the verification as a real task rather than a comment:

```python
with DAG(dag_id="daily_load", start_date=..., schedule="@daily", catchup=False) as dag:
    land = S3ToTeradataOperator(
        task_id="land_raw",
        teradata_conn_id="teradata_default",
        s3_source_key="s3://<bucket>/<prefix>/",
        public_bucket=False,
        teradata_table="<db>.raw_events",
    )
    transform = SQLExecuteQueryOperator(
        task_id="build_marts", conn_id="teradata_default", sql="sql/build_marts.sql",
    )
    verify = SQLExecuteQueryOperator(
        task_id="verify_rowcount", conn_id="teradata_default",
        sql="SELECT CASE WHEN COUNT(*) = 0 THEN 1/0 ELSE 1 END FROM <db>.daily_agg "
            "WHERE load_date = DATE '{{ ds }}';",
    )
    land >> transform >> verify
```

The divide-by-zero verification task is deliberate: it turns an empty result into a failed task instead
of a green run over a table nobody loaded. A pipeline that cannot fail is not a pipeline.

## Failure modes worth knowing

- **A green DAG over an empty table.** The most common real failure. SQL that inserts zero rows succeeds.
  Add a row-count assertion task; do not rely on the load task's exit status.
- **`catchup=True` on a first deploy** backfills every interval since `start_date` at once. Against a
  warehouse that means dozens of concurrent sessions and, usually, flow control. Set it to `False`
  unless a backfill is what you want, and bound it with `max_active_runs`.
- **Tag the pipeline with a query band.** `SET QUERY_BAND = 'app=airflow;dag={{ dag.dag_id }};' FOR
  SESSION;` as the first statement of a task puts the DAG id into `DBC.QryLogV.QueryBand`, which turns
  "which job ran this" from guesswork into a `WHERE` clause. See the `workload` skill.
- **Parallelism against the warehouse, not against Airflow.** Task concurrency is limited by the
  warehouse's throughput and its workload rules, not by the worker count. `dba_flowControl` and
  `dba_userDelay` show whether Teradata is throttling the load — if it is, more workers make it worse.
- **Long-running SQL and task timeouts.** A task killed by Airflow does not necessarily abort the
  statement in Teradata; the session can keep running the query. `dba_sessionInfo` shows what is still
  live after a task failure.
- **Templating collides with SQL.** Jinja `{{ }}` in a `.sql` file is rendered by Airflow. A literal
  brace in the SQL needs escaping, and a `%` in a `LIKE` pattern combined with paramstyle substitution is
  a classic silent corruption — the `client-development` skill covers the qmark parameter style.

## Diagnosing from the database side

The plugin cannot see Airflow. It can see what Airflow did:

- `base_tableList` and `base_tableDDL` — did the object get built, and as what.
- `dba_sessionInfo` — is a task's session still running after the task was marked failed.
- `dba_flowControl`, `dba_userDelay` — is the warehouse throttling the pipeline.
- `dba_userSqlList` — what SQL the pipeline's user actually submitted, when DBQL is on.

That last one deserves a caveat: **if query logging is off, every DBQL probe returns zero for every
object**, and zero rows there means "nothing was logged", never "nothing ran". Confirm logging is on
before drawing any conclusion from an empty DBQL result. The `lineage` skill carries the same warning for
the same reason.
