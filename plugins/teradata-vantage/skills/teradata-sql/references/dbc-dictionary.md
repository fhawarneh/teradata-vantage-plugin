# DBC data dictionary — catalog SQL that runs on every Teradata system

Purpose: ready-to-run `SELECT`s over the `DBC` views for versions, databases, tables, columns,
indexes, space, statistics, sessions, query log and rights, plus the code decodes you need to read
them. Every statement here passes the read-only guard on `base_readQuery`.

Conventions:

- Query the `V` views (`DBC.TablesV`), not the base tables (`DBC.TVM`). The `VX` views
  (`DBC.ColumnsVX`) return only objects the session user has rights on — use them for
  least-privilege discovery. The unsuffixed names (`DBC.Tables`) are compatibility views with 30-char
  names; avoid them in new SQL.
- Dictionary names are stored in the case they were created with. Compare with `UPPER(...)` or
  `(NOT CASESPECIFIC)`; never rely on a bare `=`.
- Never write to `DBC` except the documented log-table purge in the `health` skill. Nobody can
  `CREATE TABLE` in `DBC` (`Error 3524`).
- Items marked (doc) are from Teradata documentation; verify against your release.

## 1. Version and liveness

```sql
SELECT InfoKey, InfoData FROM DBC.DBCInfoV;
-- InfoKey: VERSION, RELEASE, LANGUAGE SUPPORT MODE
```

The same probe is what `dba_databaseVersion` runs; it is the cheapest "can I reach the database" test.

## 2. Databases and users

```sql
SELECT DatabaseName,
       CASE DBKind WHEN 'U' THEN 'User' WHEN 'D' THEN 'Database' END AS db_kind,
       OwnerName, PermSpace, SpoolSpace, TempSpace, CommentString
FROM DBC.DatabasesV
WHERE OwnerName <> 'PDCRADM'
ORDER BY DatabaseName;
```

- `PermSpace` on a PARENT is the number to check before `CREATE DATABASE ... FROM <parent> AS PERM = n`;
  a child is carved from the parent's unallocated space (`Error 3541` otherwise).
- System databases you usually exclude from discovery: `DBC SYS_CALENDAR SYSLIB SYSUDTLIB SYSUIF
  SYSSPATIAL SYSBAR SYSJDBC SystemFe TDStats TDQCD TDMaps TD_SYSFNLIB TD_SYSGPL TD_SYSXML TD_SERVER_DB
  TD_ANALYTICS_DB TD_SYSAI TDBCMgmt External_AP PDCRAdmin PDCRSTG PDCRDATA PDCRINFO PDCRTPCD PDCRADM
  TD_DATASHARING_REPO TD_METRIC_SVC console tdwm val LockLogShredder SQLJ Crashdumps`, plus names
  starting `TDaaS`.

## 3. Tables, views and other objects

```sql
SELECT TableName, TableKind, CreatorName, CreateTimeStamp, LastAlterTimeStamp, CommentString
FROM DBC.TablesV
WHERE UPPER(DatabaseName) = UPPER('<db>')
  AND TableKind IN ('T', 'O', 'V', 'Q')
ORDER BY TableName;
```

`TableKind` decode (doc; the letters most often met first):

| Kind | Object | Kind | Object |
|---|---|---|---|
| `T` | table (with primary index) | `O` | table with no primary index (also NOS/foreign tables on some releases) |
| `V` | view | `Q` | queue table |
| `M` | macro | `P` | stored procedure |
| `E` | external stored procedure | `F` | standard function (UDF) |
| `A` | aggregate function | `R` | table function |
| `S` | ordered analytical function | `B` | combined aggregate/analytical function |
| `I` | join index | `N` | hash index |
| `G` | trigger | `J` | journal |
| `U` | user-defined type | `X` | authorization |
| `K` | foreign server | `L` | user-defined table operator / datalake objects on some releases |
| `D` | JAR | `H` | instance/constructor method |
| `Y` | GLOP set | `Z` | UIF |
| `1` | dataset schema | `2` | function alias |

The letters for foreign tables and datalake objects vary by release. To inventory foreign tables
reliably, read the DDL text instead of the kind:

```sql
SELECT DatabaseName, TableName
FROM DBC.TablesV
WHERE UPPER(DatabaseName) = UPPER('<db>')
  AND UPPER(RequestText) LIKE 'CREATE FOREIGN TABLE%';
```

`RequestText` holds the creating statement (truncated on very long DDL; use `SHOW TABLE` for the full
text). `SHOW TABLE <db>.<t>` on a VIEW fails with `Error 3853 '<name>' is not a table` — use `SHOW VIEW`.
`HELP TABLE <db>.<t>` lists columns and types; `HELP COLUMN <db>.<t>.*` gives per-column detail.

## 4. Columns

```sql
SELECT ColumnName, ColumnType, ColumnLength, DecimalTotalDigits, DecimalFractionalDigits,
       Nullable, DefaultValue, CharType, CommentString
FROM DBC.ColumnsV
WHERE UPPER(DatabaseName) = UPPER('<db>') AND UPPER(TableName) = UPPER('<t>')
ORDER BY ColumnId;
```

- `ColumnType` is NULL for a VIEW's columns. Prove a view column's type with a cast
  (`SELECT MAX(CAST(col AS DATE)) FROM <db>.<view>`), or use `HELP COLUMN <db>.<view>.*`.
- `CharType`: 1 = LATIN, 2 = UNICODE (doc). A LATIN column rejects non-Latin text with `Error 6706`.
- Alias `ColumnType` as `coltype`, never `ct` (reserved).

`ColumnType` decode — the strings `base_columnDescription` ACTUALLY RETURNS (its SQL, not the Teradata type names; the two differ, see the note under the table):

| Code | Type | Code | Type | Code | Type |
|---|---|---|---|---|---|
| `I1` | BYTEINT | `I2` | SMALLINT | `I` | INTEGER |
| `I8` | BIGINT | `D` | DECIMAL | `N` | NUMBER |
| `F` | FLOAT | `CF` | CHAR | `CV` | VARCHAR |
| `CO` | CLOB | `BF` | BYTE | `BV` | VARBYTE |
| `BO` | BLOB | `DA` | DATE | `AT` | TIME |
| `TZ` | TIME WITH TIME ZONE | `TS` | TIMESTAMP | `SZ` | TIMESTAMP WITH TIME ZONE |
| `YR` | INTERVAL YEAR | `YM` | INTERVAL YEAR TO MONTH | `MO` | INTERVAL MONTH |
| `DY` | INTERVAL DAY | `DH` | INTERVAL DAY TO HOUR | `DM` | INTERVAL DAY TO MINUTE |
| `DS` | INTERVAL DAY TO SECOND | `HR` | INTERVAL HOUR | `HM` | INTERVAL HOUR TO MINUTE |
| `HS` | INTERVAL HOUR TO SECOND | `MI` | INTERVAL MINUTE | `MS` | INTERVAL MINUTE TO SECOND |
| `SC` | INTERVAL SECOND | `PD` | PERIOD(DATE) | `PT` | PERIOD(TIME) |
| `PZ` | PERIOD(TIME WITH TIME ZONE) | `PS` | PERIOD(TIMESTAMP) | `PM` | PERIOD(TIMESTAMP WITH TIME ZONE) |
| `JN` | JSON | `XM` | XML | `DT` | DATASET |
| `UT` | UDT | `A1` / `AN` | UDT *(these are the ARRAY codes)* | `??` | `STGEOMETRY'ANY_TYPE` |
| `++` | TD_ANYTYPE | | | | |

Two places where the returned string is NOT the Teradata type name, both verified in the vendored 0.2.6 wheel:
`A1` and `AN` are the ARRAY codes — the tool's own Python map decodes them as `ARRAY`, but the SQL that
`base_columnDescription` runs flattens them to `UDT` along with `UT`, so all three come back indistinguishable.
And `??` returns the single string `STGEOMETRY'ANY_TYPE`, with an embedded apostrophe, because the SQL escapes
a quote inside one literal. Read the column type from `SHOW TABLE` when the distinction matters.

Type-adaptive date rule: `DA`/`TS`/`SZ` → `CAST(col AS DATE)`; `CV`/`CF` → `TRYCAST(SUBSTRING(col FROM 1
FOR 10) AS DATE)` (see `date-time.md`).

## 5. Indexes and partitioning

```sql
SELECT IndexNumber, IndexType, UniqueFlag, IndexName, ColumnName, ColumnPosition
FROM DBC.IndicesV
WHERE UPPER(DatabaseName) = UPPER('<db>') AND UPPER(TableName) = UPPER('<t>')
ORDER BY IndexNumber, ColumnPosition;
```

`IndexType` (doc): `P` primary, `Q` partitioned primary, `S` secondary, `K` primary key, `U` unique
constraint, `J` join index, `N` hash index, `V` value-ordered secondary, `H` hash-ordered ALL covering
secondary, `O` value-ordered ALL covering secondary, `I` ordering column of a composite secondary,
`M` multi-column statistics, `D` derived column partition statistics, `1`/`2` field1/field2 of a join
index, `A` primary AMP index. `IndexNumber = 1` is the primary index; `UniqueFlag = 'Y'` marks UPI/USI.

Partitioning expression: `SELECT ConstraintText FROM DBC.PartitioningConstraintsV WHERE UPPER(DatabaseName)
= UPPER('<db>') AND UPPER(TableName) = UPPER('<t>');` (doc). Statistics collected:
`HELP STATISTICS <db>.<t>;` or `SELECT ColumnName, LastCollectTimeStamp, RowCount FROM DBC.StatsV WHERE
UPPER(DatabaseName) = UPPER('<db>') AND UPPER(TableName) = UPPER('<t>');`.

## 6. Space

> **Never divide two DECIMALs and format the result.** `SUM(a)/NULLIFZERO(SUM(b)) * 100 (FORMAT 'zz9.99')`
> reports **0** on any system below 100% full: Teradata truncates the scale of the division before the
> multiply, and the driver discards `FORMAT` entirely. Measured 2026-09-05 against a live system that was
> 4.0% used — the expression returned `0`. Cast first, as every query below does:
> `CAST(100.0*SUM(a)/NULLIFZERO(SUM(b)) AS DECIMAL(6,2))`.


```sql
-- system-wide
SELECT SUM(CurrentPerm)/1024/1024/1024 AS used_gb,
       SUM(MaxPerm)/1024/1024/1024 AS allocated_gb,
       CAST(100.0*SUM(CurrentPerm)/NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(6,2)) AS pct_used
FROM DBC.DiskSpaceV;

-- per database, with skew (max AMP vs average)
SELECT DatabaseName,
       SUM(CurrentPerm)/1048576 AS used_mb,
       SUM(MaxPerm)/1048576 AS max_mb,
       CAST(100.0*SUM(CurrentPerm)/NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(6,2)) AS pct_used,
       CAST(100.0*(1 - CAST(AVG(CurrentPerm) AS FLOAT)/NULLIFZERO(MAX(CurrentPerm))) AS DECIMAL(6,2)) AS skew_pct
FROM DBC.DiskSpaceV
GROUP BY DatabaseName
ORDER BY used_mb DESC;

-- DBC headroom (2644 watch): every write on the system fails when this nears 100
SELECT CAST(100.0*SUM(CurrentPerm)/NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(6,2)) AS dbc_pct_used
FROM DBC.DiskSpaceV WHERE DatabaseName = 'DBC';

-- per table (all AMPs)
SELECT TableName, SUM(CurrentPerm)/1048576 AS used_mb,
       CAST(100.0*(1 - CAST(AVG(CurrentPerm) AS FLOAT)/NULLIFZERO(MAX(CurrentPerm))) AS DECIMAL(6,2)) AS skew_pct
FROM DBC.AllSpaceV
WHERE UPPER(DatabaseName) = UPPER('<db>') AND TableName <> 'All'
GROUP BY TableName ORDER BY used_mb DESC;

-- parent headroom before CREATE DATABASE (3541 diagnosis)
SELECT DatabaseName, PermSpace FROM DBC.DatabasesV WHERE DatabaseName = '<parent>';
```

`DiskSpaceV` totals can show tens of GB free while the parent you are creating under has none; the
parent's `PermSpace` minus its children's allocations is what a `CREATE DATABASE` draws on.

## 7. Sessions and workload

```sql
SELECT UserName, AccountName, SessionNo, DefaultDataBase, LogonDate, LogonTime, LogonSource,
       CurrentRole, QueryBand, ClientIpAddress, ClientProgramName
FROM DBC.SessionInfoV
WHERE UserName = '<user>' (NOT CASESPECIFIC) OR '<user>' = '*';
```

`(NOT CASESPECIFIC)` is the Teradata idiom for a case-insensitive comparison against a CASESPECIFIC
column. Ending a wedged query is `ABORT SESSION <hostid>.<sessionno>;` via `base_writeQuery` (always
prompts). Flow control and CPU per node come from `DBC.ResUsageSAWT` / `ResUsageSVPR` / `ResUsageSPMA`
joined to `SYS_CALENDAR.CALENDAR` on `TheDate = calendar_date` — the `dba_flowControl` tool runs that
query; ResUsage logging must be enabled for rows to exist.

## 8. Query log (DBQL — requires DBQL logging to be enabled)

```sql
-- heaviest statements in a window (statement text lives in DBQLSqlTbl)
SELECT TOP 20 q.QueryID, q.UserName, q.StartTime, q.AMPCPUTime, q.TotalIOCount, q.SpoolUsage,
       q.NumResultRows, q.MaxAMPCPUTime, q.MinAmpCPUTime, q.ErrorCode
FROM DBC.DBQLogTbl q
WHERE CAST(q.StartTime AS DATE) BETWEEN DATE '<yyyy-mm-dd>' AND DATE '<yyyy-mm-dd>'
ORDER BY q.AMPCPUTime DESC;

-- statements that touched one object
SELECT TOP 50 o.QueryID, o.ObjectDatabaseName, o.ObjectTableName, o.ObjectType, o.FreqOfUse,
       s.SqlTextInfo
FROM DBC.DBQLObjTbl o
JOIN DBC.DBQLSqlTbl s ON s.QueryID = o.QueryID
WHERE UPPER(o.ObjectDatabaseName) = UPPER('<db>') AND UPPER(o.ObjectTableName) = UPPER('<t>')
ORDER BY o.CollectTimeStamp DESC;

-- delay (queued time) per user
SELECT UserName, COUNT(*) AS cnt, AVG(ZEROIFNULL(DelayTime)) AS avg_delay_s
FROM DBC.DBQLogTbl
WHERE DelayTime > 0 AND CAST(StartTime AS DATE) >= CURRENT_DATE - 7
GROUP BY UserName ORDER BY avg_delay_s DESC;
```

Skew: `MaxAMPCPUTime * NumOfActiveAMPs / NULLIFZERO(AMPCPUTime)` (CPU skew), likewise for IO; `PJI =
AMPCPUTime * 1000 / NULLIFZERO(TotalIOCount)`. Feature usage per statement decodes the
`FeatureUsage` bitmap: `SUM(GETBIT(q.FeatureUsage, 2047 - f.FeatureBitPos))` joined to
`DBC.QryLogFeatureListV f`. Absence of a table in DBQL for the logged window is not proof the table is
unused — check the window and whether logging covers all users.

## 9. Rights and roles

```sql
-- what a user can do, object by object
SELECT DatabaseName, TableName, ColumnName, AccessRight, GrantAuthority, GrantorName
FROM DBC.AllRightsV
WHERE UserName = '<user>' (NOT CASESPECIFIC)
ORDER BY DatabaseName, TableName, AccessRight;

-- roles a user holds
SELECT r.RoleName, r.CreatorName, rm.Grantee, rm.WhenGranted, rm.DefaultRole, rm.WithAdmin
FROM DBC.RoleInfoV r
JOIN DBC.RoleMembersV rm ON r.RoleName = rm.RoleName
WHERE rm.Grantee = '<user>' (NOT CASESPECIFIC);

-- rights a role carries
SELECT DatabaseName, TableName, ColumnName, AccessRight, GrantorName
FROM DBC.AllRoleRightsV
WHERE RoleName = '<role>' (NOT CASESPECIFIC)
ORDER BY DatabaseName, TableName;
```

`AccessRight` codes (doc, common ones): `R` SELECT, `I` INSERT, `U` UPDATE, `D` DELETE, `E` EXECUTE,
`CT` CREATE TABLE, `DT` DROP TABLE, `CV` CREATE VIEW, `DV` DROP VIEW, `CD` CREATE DATABASE, `DD` DROP
DATABASE, `CU` CREATE USER, `DU` DROP USER, `CM` CREATE MACRO, `DM` DROP MACRO, `PC` CREATE PROCEDURE,
`PD` DROP PROCEDURE, `PE` EXECUTE PROCEDURE, `CF` CREATE FUNCTION, `DF` DROP FUNCTION, `EF` EXECUTE
FUNCTION, `CG` CREATE TRIGGER, `DG` DROP TRIGGER, `CP` CHECKPOINT, `IX` INDEX, `RF` REFERENCES, `ST` STATISTICS, `SH` SHOW, `DP` DUMP,
`RS` RESTORE, `CA` CREATE AUTHORIZATION, `DA` DROP AUTHORIZATION, `CO` CREATE PROFILE, `DO` DROP PROFILE,
`CR` CREATE ROLE, `DR` DROP ROLE, `UM` UDT METHOD, `UT` UDT TYPE, `UU` UDT USAGE, `NT` NONTEMPORAL,
`OP` CREATE OWNER PROCEDURE, `TH` CTCONTROL, `SA`/`SD` security constraint assignment/definition,
`SR`/`SS` set resource/session rate.

- A `GRANT SELECT ON <parent>` does not cascade to a child database created later; reading a child's
  table then fails with `Error 3523 The user does not have SELECT access to <db>.<table>`. Grant on the
  object or database you actually read.
- A view's OWNER (the containing database, not the connected user) must hold `SELECT WITH GRANT
  OPTION` on everything the view references, or `CREATE VIEW` fails with `Error 5315 An owner
  referenced by user does not have SELECT WITH GRANT OPTION access to <db>.<table>.<column>`.

## 10. Data-quality operators (native, not hand-written SQL)

```sql
SELECT * FROM TD_ColumnSummary (ON <db>.<t> AS InputTable USING TargetColumns ('[:]')) AS dt;
SELECT * FROM TD_UnivariateStatistics (ON <db>.<t> AS InputTable USING TargetColumns ('amount') Stats ('ALL')) AS dt;
SELECT * FROM TD_CategoricalSummary (ON <db>.<t> AS InputTable USING TargetColumns ('region')) AS dt;
SELECT * FROM TD_GetRowsWithMissingValues (ON <db>.<t> AS InputTable USING TargetColumns ('[email]')) AS dt;
```

`TargetColumns ('[:]')` means all columns. These are what the `qlty_*` tools run; some operators
reject unusual column types with `Error 7810 Unsupported data type` — narrow `TargetColumns` then.
