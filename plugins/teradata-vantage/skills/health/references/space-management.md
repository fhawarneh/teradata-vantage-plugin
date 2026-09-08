# Space management on Teradata Vantage: the parent/child PERM model, 3541, 2644, 3524, 3523

Purpose: the space and permission failures behind "writes stopped" and "CREATE DATABASE failed", each opened with what the message actually means, with the exact SQL to diagnose and fix. Fixes marked `[WRITE]` are for a human to run: they need `base_writeQuery`, which the bundled upstream 0.2.6 server does not register - it exists only on a bridged server that adds one, where the plugin's write gate prompts before each. On the bundled server, hand the statement to the DBA to run in their own SQL client. `base_readQuery` is held read-only and will deny them.

## 1. How PERM space works (from Teradata documentation; verify against your release)

- Every database and user owns a fixed `PermSpace` allocation. `DBC` owns all of the system's permanent space at install; every other database is carved, directly or through intermediates, out of `DBC`.
- `CREATE DATABASE <child> FROM <parent> AS PERM = <bytes>` subtracts `<bytes>` from `<parent>`'s allocation and gives it to `<child>`. The parent must have that many bytes UNALLOCATED (not used by its own tables and not already given to other children). Dropping the child returns the space.
- `MODIFY DATABASE <db> AS PERM = <bytes>` resizes; growing a database takes the difference from its immediate owner, shrinking gives it back.
- `SpoolSpace` and `TempSpace` are limits, not carved allocations; a child's spool limit cannot exceed the parent's.
- The dictionary views are per-AMP: `DBC.DiskSpaceV`, `DBC.AllSpaceV`, `DBC.TableSizeV` return one row per AMP per object. ALWAYS aggregate with `SUM` (total) or `MAX` (the fullest AMP, which is what actually limits a table).
- `MaxPerm` in `DiskSpaceV` is the per-AMP share of `PermSpace`; `CurrentPerm` is what is used; `PeakPerm` is the high-water mark since the last reset.

Vocabulary table:

| View | Grain | Use it for |
|---|---|---|
| `DBC.DiskSpaceV` | database x AMP | used / allocated / free per database (sum over AMPs) |
| `DBC.DatabasesV` | database | `PermSpace`, `SpoolSpace`, `OwnerName`, `CreatorName` |
| `DBC.TableSizeV` | table x AMP | table size (sum) and per-AMP skew (max vs avg) |
| `DBC.AllSpaceV` | database+table x AMP | both of the above in one view; `TableName = 'All'` rows are the database roll-up |
| `DBC.ChildrenV` | parent/child pairs | the full ownership tree |

## 2. Error 3541: `The request to assign new PERMANENT space is invalid`

What the message actually means: the parent named in `FROM <parent>` (or the owner of the database you are trying to grow) does not have that many unallocated bytes. The SQL is valid. It is a capacity error that reads like a syntax error. It fires even when the system total shows plenty of free space, because free space that belongs to a different branch of the tree is not available to this parent. Measured case: the system showed 97 GB free while the parent had 0.5 GB unallocated, its eleven children having already taken the rest.

Diagnose on the PARENT, never on system totals:

```sql
-- What the parent owns and how much of it is still unallocated
SELECT DatabaseName, PermSpace, SpoolSpace, OwnerName
FROM DBC.DatabasesV WHERE DatabaseName = '<parent>';

SELECT CAST((SUM(MaxPerm) - SUM(CurrentPerm))/1e9 AS DECIMAL(10,3)) AS Unallocated_GB
FROM DBC.DiskSpaceV WHERE DatabaseName = '<parent>';

-- Where the parent's space went
SELECT DatabaseName, CAST(PermSpace/1e9 AS DECIMAL(10,3)) AS Perm_GB
FROM DBC.DatabasesV WHERE OwnerName = '<parent>' ORDER BY PermSpace DESC;
```

Fix, in this order:

1. Size the request from what will actually go into the child, not from the source system's allocation:
   ```sql
   SELECT CAST(SUM(CurrentPerm) AS BIGINT) AS Bytes_In_Use
   FROM DBC.TableSizeV WHERE DatabaseName = '<source_db>' AND TableName IN ('sales_fact','customer_dim');
   ```
   Allow roughly 2.5x the measured bytes for growth and spool churn; a small lookup database is 200 MB, not gigabytes. Over-allocating children is what walks a parent into 3541 later.
2. If the parent is short, grow the parent FIRST from its own owner (the chain ends at `DBC`; check the owner's headroom the same way):
   ```sql
   MODIFY DATABASE <parent> AS PERM = <new_total_bytes>;   -- [WRITE]
   ```
3. Then create the child with both PERM and SPOOL stated:
   ```sql
   CREATE DATABASE <child> FROM <parent> AS PERM = <bytes>, SPOOL = <bytes>;   -- [WRITE]
   ```
4. Re-run the headroom query and confirm the child exists in `DBC.DatabasesV`.

Pre-flight guard for any script that creates databases (raise, do not suppress):

```sql
SELECT CAST((SUM(MaxPerm) - SUM(CurrentPerm)) AS BIGINT) FROM DBC.DiskSpaceV WHERE DatabaseName = '<parent>';
-- refuse when requested_bytes > 0.9 * result
```

Rules:

- NEVER carve from `DBC` on a small system; `DBC` is often the tightest parent and starving it causes the outage in section 3. Leave `DBC` headroom on purpose.
- NEVER rely on an "already exists" ignore list (`5612`) to make `CREATE DATABASE` idempotent: `3541` is a different code, is not caught, and the script continues with a database that exists but is empty or missing. The downstream symptom is a bare `[Error 3802] Database '<db>' does not exist` days later.
- NEVER match error codes by substring inside a message; a four-digit number can appear in unrelated text.

## 3. Error 2644: `No more room in database DBC` (or `... in database <db>`)

What the message actually means: the named database has no free PERM left on at least one AMP for the row or journal the statement needs. When the database named is `DBC`, the failing statement can be in ANY database: the transient journal that every write needs lives in `DBC`, so once `DBC` cannot grow, every data-writing statement on the system fails with this code while reads keep working. Observed at 98.2 percent DBC used; act at 70.

Diagnose:

```sql
SELECT CAST(100.0*SUM(CurrentPerm)/NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(6,2)) AS DBC_Pct_Used
FROM DBC.DiskSpaceV WHERE DatabaseName = 'DBC';

-- What is filling DBC (sum over AMPs)
SELECT TOP 15 TableName, CAST(SUM(CurrentPerm)/1e9 AS DECIMAL(10,3)) AS GB
FROM DBC.TableSizeV WHERE DatabaseName = 'DBC'
GROUP BY 1 ORDER BY 2 DESC;
```

There are TWO distinct causes, and the onset tells them apart.

**Sudden — a long-open transaction.** The transient journal keeps every before-image until the transaction that wrote them commits or rolls back, so ONE session that opened a transaction and never closed it (a leaked pooled connection, an ANSI-mode batch job with no COMMIT, a retry loop) grows the journal until DBC's PERM is gone. Nothing changed in the application and writes stopped this morning is that shape. The space is reclaimed the moment the transaction ends, so the fix is to find and end the session, not to delete anything:

```sql
SELECT SessionNo, UserName, AccountName, LogonDate, LogonTime, LogonSource
FROM DBC.SessionInfoV ORDER BY LogonDate, LogonTime;   -- oldest logons first
```

Then `ABORT SESSION` the culprit — `[WRITE]`, and the write gate prompts for it. Confirm with the DBC percentage query above afterwards; it should drop immediately.

**Gradual — accumulated log tables.** Pure log tables that regrow: `ResUsageSpma`, `ResUsageSvpr`, `ResUsageSps`, other `ResUsage*` tables, `EventLog`, `SW_Event_Log`, `TDWMSummaryLog` (about 2.6 GB total on the day this was measured). This builds over weeks, so it fits a slow slide into the threshold rather than an overnight stop.

Purge, ONLY these classes of table, one statement each, after the size query has shown they are the problem:

```sql
DELETE FROM DBC.ResUsageSpma ALL;      -- [WRITE]
DELETE FROM DBC.ResUsageSvpr ALL;      -- [WRITE]
DELETE FROM DBC.EventLog ALL;          -- [WRITE]
DELETE FROM DBC.SW_Event_Log ALL;      -- [WRITE]
DELETE FROM DBC.TDWMSummaryLog ALL;    -- [WRITE]
```

Rules:

- NEVER delete from `DBC.TVM`, `DBC.TVFields`, `DBC.TransientJournal`, `DBC.DBase`, `DBC.AccessRights` or any other dictionary table; those are the catalog, not logs. If a table is not in the list above and you cannot show from Teradata documentation that it is a log, do not touch it.
- A `DELETE FROM DBC...` is exactly the pattern generic SQL denylists block. In this plugin it is a human-approved exception: where the connected server exposes a write tool the write gate prompts and the operator approves each statement by name; on the bundled server the operator runs them in their own SQL client. NEVER widen this into a general bypass.
- They regrow. Re-check the headroom query after the purge and schedule a recurring check (the plugin's health monitor, or the site's own PDCR/DBQL maintenance). Purging is a stopgap; the durable fix is ResUsage/DBQL retention.
- If DBC is full AND logons fail, the purge is unreachable through SQL; see the recovery skill's crash-loop section before spending time racing the logon window.

## 4. Error 3524: `The user does not have CREATE TABLE access to database DBC`

What the message actually means: the statement created an object without a database qualifier and the session's default database is `DBC`. Nothing, not even the `dbc` user, may create tables in `DBC`. It is a default-database problem, not a grant problem, and it hits three layers independently: a connection string whose path ends in `/dbc`, a vector-store or analytics tool that materialises temp tables in the logon user's default database, and any client library that omits a working database.

Fix:

- Point the MCP server's `DATABASE_URI` at a working database: `teradata://<user>:<password>@<host>:1025/<working_db>` (never `/dbc`). The connection tests in `/teradata-vantage:setup` check this.
- Qualify every created object (`CREATE TABLE <db>.<table> ...`) or set the session database first: `DATABASE <db>;`.
- For vector stores pass an explicit `target_database`; for `teradataml` set both `database=` and `temp_database_name=`.
- After an incident sweep for leftovers in `DBC` such as `vectorstore_temp_%` or `ml__from_pandas_%` with `SELECT TableName FROM DBC.TablesV WHERE DatabaseName='DBC' AND TableName LIKE 'ml__%'`.

## 5. Error 3523: `The user does not have <right> access to <object>`

What the message actually means: the object exists and the session user lacks the named right on it (`SELECT`, `INSERT`, `CREATE TABLE`, ...). Contrast with `3807 Object '<db>.<table>' does not exist`, which you also get for objects you cannot see at all, and with `3524`, which is specific to creating in `DBC`.

The trap: `GRANT SELECT ON <parent> TO <user>` does not cascade to child databases. An account granted only on the parent connects fine and can read nothing in the children. Grant per database (or per object):

```sql
GRANT SELECT ON <db> TO <user>;                       -- [WRITE], repeat per child database
-- audit what the user actually holds
SELECT DatabaseName, TableName, AccessRight, GrantAuthority
FROM DBC.AllRightsV WHERE UserName = '<user>' ORDER BY 1, 2;
```

Related: a view's OWNER (the database that contains it) needs `SELECT WITH GRANT OPTION` on everything the view references when the view lives under a different parent than the tables; otherwise `CREATE VIEW` fails with `[Error 5315] An owner referenced by user does not have SELECT WITH GRANT OPTION access to <db>.<table>.<column>`. Fix: `GRANT SELECT ON <data_db> TO <view_db> WITH GRANT OPTION;` `[WRITE]`.

## 6. Quick decision table

| Symptom | Code | Actually means | Probe | Fix |
|---|---|---|---|---|
| CREATE/MODIFY DATABASE fails | 3541 | parent has no unallocated PERM | parent `DiskSpaceV` / `DatabasesV` | grow the parent first, size the child from real bytes |
| Every write fails system-wide, reads fine | 2644 (DBC) | DBC cannot grow the transient journal | DBC pct, `TableSizeV` for DBC | purge log tables only; fix retention |
| Writes to one database fail | 2644 (`<db>`) | that database is full on an AMP | `dba_databaseSpace`, skew query | `MODIFY DATABASE` or drop/archive |
| CREATE TABLE fails as dbc | 3524 | default database is DBC | check URI path / `DATABASE` | set a working database |
| Read fails on an existing table | 3523 | missing right on that object | `DBC.AllRightsV` | grant on the database, not just the parent |
| Table "does not exist" | 3807 | wrong database qualifier, or no visibility | `DBC.TablesV` | qualify `<db>.<table>` |
