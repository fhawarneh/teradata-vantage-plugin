# When a script should not be BTEQ — the Teradata bulk utilities, what each one is for, and the limits that decide between them.

Content here is *(from Teradata documentation; verify against your release)* except where it restates
behaviour the plugin's other skills confirm. Operator and utility names are stable; per-release limits
and defaults are the part to check.

## Choosing

BTEQ moves rows one at a time through the ordinary SQL path. The bulk utilities move blocks of rows
through a dedicated load path, which is why they are orders of magnitude faster and why they come with
restrictions BTEQ does not have.

| Volume / shape | Use | Why |
|---|---|---|
| Up to a few thousand rows, or DDL, or a report | **BTEQ** `.IMPORT` / `.EXPORT` | No restrictions, no work tables, no load-slot cost |
| Large volume into an **empty** table | **FastLoad**, or TPT **Load** operator | Fastest path. Target must be empty and can carry no secondary indexes, join indexes, triggers or referential integrity |
| Large volume of **inserts, updates and deletes** into a **populated** table | **MultiLoad**, or TPT **Update** operator | Handles a populated target and mixed DML; uses work tables and error tables |
| Continuous or trickle feed into a populated table | **TPump**, or TPT **Stream** operator | Row-at-a-time through ordinary SQL with checkpointing; no load slot, so it coexists with queries |
| Fast extract of many rows out | **FastExport**, or TPT **Export** operator | Block extract, far faster than `.EXPORT` |
| Anything new you are writing today | **TPT** | One scripting language over all of the above; the standalone utilities are the older interface |

**Load slots are a finite system resource.** FastLoad, MultiLoad and FastExport each occupy one for the
duration of the job, and the system permits a limited number concurrently *(configurable; verify the
value on your system)*. A job that cannot get a slot waits. TPump does not consume one — that is often
the deciding factor for a feed that must run during business hours.

## TPT in one page

Teradata Parallel Transporter runs a *job* built from **operators** connected by a data stream. Four
operator roles matter:

| Role | Operators | Does |
|---|---|---|
| Producer | `DataConnector` (files), `Export`, `Selector`, `ODBC` | Reads rows into the stream |
| Consumer | `Load`, `Update`, `Stream`, `DataConnector` (files) | Writes rows out of the stream |
| Standalone | `DDL` | Runs SQL — create, drop, set up error tables |
| Filter | `DataConnector` with attributes | Transforms in passing |

A job script declares the schema, declares the operators, then applies one to the other:

```
DEFINE JOB load_sales
DESCRIPTION 'Load daily sales into an empty staging table'
(
  DEFINE SCHEMA sales_schema
  (
    sale_id      INTEGER,
    region       VARCHAR(40),
    total_amount DECIMAL(18,2),
    load_date    ANSIDATE
  );

  DEFINE OPERATOR read_file
  TYPE DATACONNECTOR PRODUCER
  SCHEMA sales_schema
  ATTRIBUTES
  (
    VARCHAR FileName    = 'sales_20260905.csv',
    VARCHAR Format      = 'Delimited',
    VARCHAR TextDelimiter = '|',
    VARCHAR OpenMode    = 'Read'
  );

  DEFINE OPERATOR load_table
  TYPE LOAD
  SCHEMA *
  ATTRIBUTES
  (
    VARCHAR TdpId        = @tdpid,
    VARCHAR UserName     = @user,
    VARCHAR UserPassword = @password,
    VARCHAR TargetTable  = 'analytics.sales_stage',
    VARCHAR LogTable     = 'analytics.sales_stage_log',
    VARCHAR ErrorTable1  = 'analytics.sales_stage_e1',
    VARCHAR ErrorTable2  = 'analytics.sales_stage_e2'
  );

  APPLY ('INSERT INTO analytics.sales_stage (:sale_id, :region, :total_amount, :load_date);')
  TO OPERATOR (load_table)
  SELECT * FROM OPERATOR (read_file);
);
```

Run it, passing the credential from outside the script rather than embedding it:

```bash
tbuild -f load_sales.tpt -u "tdpid='prod', user='etl_service', password='"$TD_PASSWORD"'"
```

**Never hard-code a password in a `.tpt` file.** Use `-u` job variables, a job-variable file with
restricted permissions, or `-v <file>`. The same rule as BTEQ's `.LOGON`, for the same reason.

## The restrictions that surprise people

- **The Load operator (FastLoad) requires an empty target.** Not "recently truncated" — empty. It also
  refuses a table with secondary indexes, join indexes, hash indexes, triggers or referential integrity
  defined on it. The normal pattern is: load a bare staging table, then `INSERT ... SELECT` into the
  real one, then rebuild indexes.
- **A failed load leaves the target in a load-pending state.** Until the job is restarted to completion
  or the target and its error tables are dropped, the table is not usable. This is the single most
  common way a load leaves someone stuck.
- **Error tables are part of the contract.** `ErrorTable1` collects rows rejected during acquisition
  (conversion and constraint problems); `ErrorTable2` collects uniqueness violations. They must not
  already exist when the job starts, and reading them is how you diagnose a partial load. Do not drop
  them before you have looked.
- **The log table makes the job restartable.** Same name across restarts, or the job starts over.
- **Duplicate rows behave differently by target type.** A MULTISET target keeps duplicates; a SET target
  discards them — silently, from the load's point of view. If a count comes up short, check the table
  type before suspecting the file.
- **Character set matters at the boundary.** A default LATIN column rejects non-ASCII input with
  Teradata error `6706`. Declare the column `UNICODE`, or clean the source.

## Verifying a load without running it

Before a load, and to check one afterwards, the plugin's read path is enough:

- `base_tableDDL` on the target — confirms it is empty-loadable (no secondary indexes, no triggers) and
  shows SET versus MULTISET.
- `SELECT COUNT(*)` before and after, and a `SELECT COUNT(*)` on each error table.
- The `profile` skill on the loaded table — null and distinct-value counts catch a mis-aligned
  delimiter far faster than reading the file.

## What this plugin can and cannot do here

The bundled MCP server exposes **no bulk-load tool**, and `base_readQuery` is read-only by design. So
the plugin's role with loads is to **write and review the script**, explain the failure, and verify the
result with reads — not to run the load. Run `tbuild`, `fastload` or `bteq` yourself, from your own
shell, where the credential lives.
