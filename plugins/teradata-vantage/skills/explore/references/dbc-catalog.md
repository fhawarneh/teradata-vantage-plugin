# DBC catalog reference - exact SQL for DatabasesV / TablesV / ColumnsV / ColumnsVX, the ColumnType decode table, TableKind codes, the system-database exclusion list, and DBQL usage queries.

All statements are single `SELECT`s and pass the read guard on `base_readQuery`. Replace `<db>`, `<table>`, `<col>`.
Column names below are the Teradata `DBC.*V` views; casing is not significant in Teradata identifiers. The `*VX`
twins (`DatabasesVX`, `TablesVX`, `ColumnsVX`, `IndicesVX`) return only the objects the connected user has rights on
- the bundled tools use those, so "not listed" can mean "no access" rather than "does not exist".

## Databases

```sql
-- user databases and users (the same query base_databaseList runs with scope='user')
SELECT DatabaseName,
       CASE DBKind WHEN 'U' THEN 'User' WHEN 'D' THEN 'Database' END AS DBType,
       OwnerName, CommentString
FROM DBC.DatabasesV
WHERE OwnerName <> 'PDCRADM'
  AND DatabaseName NOT LIKE 'TDaaS%'
  AND DatabaseName NOT IN ( /* system list below */ 'DBC','SYSLIB','SystemFe','SYSUDTLIB','SYSJDBC','SYSSPATIAL',
    'TD_SYSFNLIB','TDQCD','TDStats','TDPUSER','dbcmngr','Crashdumps','LockLogShredder','SYSBAR','SysAdmin',
    'Sys_Calendar','EXTUSER','DEFAULT','All','PUBLIC','SQLJ','SYSUIF','TD_ANALYTICS_DB','TD_SERVER_DB','TD_SYSGPL',
    'TDSYSFLOW','TDMaps','SAS_SYSFNLIB','TDBCMgmt','External_AP','PDCRAdmin','PDCRSTG','PDCRDATA','PDCRINFO',
    'PDCRTPCD','PDCRADM','TD_DATASHARING_REPO','TD_METRIC_SVC','console','tdwm','val',
    'TD_SYSXML','TDaaS_DB','TD_SYSAI','TD_MLDB','TD_VAL','TD_OTFDB','mldb')
ORDER BY DatabaseName;

-- parent/child tree and PERM allocation (space questions: diagnose 3541 on the PARENT's PermSpace)
SELECT DatabaseName, OwnerName, PermSpace, SpoolSpace, TempSpace, DBKind, CreateTimeStamp
FROM DBC.DatabasesV
WHERE OwnerName = '<parent_db>'
ORDER BY DatabaseName;
```

The exclusion list is the union of the names `base_databaseList` hides and the ones the plugin's system-database
data file carries. `DBKind`: `D` database, `U` user. `TDaaS%` names belong to the managed-cloud control plane.

## Tables, views and other objects

```sql
-- what a database holds, by kind
SELECT TableName, TableKind, CreatorName, CreateTimeStamp, LastAlterTimeStamp, CommentString
FROM DBC.TablesV
WHERE DatabaseName = '<db>'
ORDER BY TableKind, TableName;

-- counts per kind (quick overview of a large database)
SELECT TableKind, COUNT(*) AS object_cnt
FROM DBC.TablesV
WHERE DatabaseName = '<db>'
GROUP BY TableKind
ORDER BY 2 DESC;

-- find a table by name fragment across all user databases
SELECT DatabaseName, TableName, TableKind
FROM DBC.TablesV
WHERE TableName LIKE '%customer%' (NOT CASESPECIFIC)
  AND TableKind IN ('T','O','V','Q')
ORDER BY 1, 2;
```

`base_tableList` uses `TableKind IN ('T','V','O','Q')`; widen the list when you need macros, procedures, join indexes
or foreign tables.

### TableKind codes (from Teradata documentation; verify against your release)

| code | object | code | object |
|---|---|---|---|
| `T` | table with a primary index / partitioned table | `O` | table with no primary index (NoPI) |
| `V` | view | `Q` | queue table |
| `M` | macro | `P` | SQL stored procedure |
| `E` | external stored procedure | `F` | standard function |
| `A` | aggregate function | `B` | combined aggregate/ordered analytic function |
| `R` | table function | `S` | ordered analytic function |
| `L` | table operator | `C` | table operator parser contract function |
| `I` | join index | `N` | hash index |
| `J` | journal | `G` | trigger |
| `U` | user-defined type | `D` | JAR |
| `X` | authorization | `K` | foreign server object |
| `Z` | UIF | `H` | instance or constructor method |
| `Y` | GLOP set | `1` | DATASET schema |
| `2` | function alias | | |

Foreign tables (NOS) and datalake/OTF objects also appear in `DBC.TablesV`; the kind letters used for them differ
across releases - confirm with `SHOW TABLE` / `HELP TABLE` and the `RequestText` column rather than relying on a letter.

## Columns

```sql
-- columns of a table OR view, in definition order, with the type code and lengths
SELECT ColumnName, ColumnType, ColumnLength, DecimalTotalDigits, DecimalFractionalDigits,
       CharType, Nullable, DefaultValue, ColumnId, CommentString
FROM DBC.ColumnsV
WHERE DatabaseName = '<db>' AND TableName = '<table>'
ORDER BY ColumnId;

-- the same, decoded (this is what base_columnDescription runs; ColumnsVX applies the caller's access rights)
SELECT TableName, ColumnName,
  CASE ColumnType
    WHEN '++' THEN 'TD_ANYTYPE'  WHEN 'A1' THEN 'UDT'       WHEN 'AT' THEN 'TIME'
    WHEN 'BF' THEN 'BYTE'        WHEN 'BO' THEN 'BLOB'      WHEN 'BV' THEN 'VARBYTE'
    WHEN 'CF' THEN 'CHAR'        WHEN 'CO' THEN 'CLOB'      WHEN 'CV' THEN 'VARCHAR'
    WHEN 'D'  THEN 'DECIMAL'     WHEN 'DA' THEN 'DATE'
    WHEN 'DH' THEN 'INTERVAL DAY TO HOUR'     WHEN 'DM' THEN 'INTERVAL DAY TO MINUTE'
    WHEN 'DS' THEN 'INTERVAL DAY TO SECOND'   WHEN 'DY' THEN 'INTERVAL DAY'
    WHEN 'F'  THEN 'FLOAT'
    WHEN 'HM' THEN 'INTERVAL HOUR TO MINUTE'  WHEN 'HR' THEN 'INTERVAL HOUR'
    WHEN 'HS' THEN 'INTERVAL HOUR TO SECOND'
    WHEN 'I1' THEN 'BYTEINT'     WHEN 'I2' THEN 'SMALLINT'  WHEN 'I8' THEN 'BIGINT'   WHEN 'I' THEN 'INTEGER'
    WHEN 'MI' THEN 'INTERVAL MINUTE'          WHEN 'MO' THEN 'INTERVAL MONTH'
    WHEN 'MS' THEN 'INTERVAL MINUTE TO SECOND'
    WHEN 'N'  THEN 'NUMBER'
    WHEN 'PD' THEN 'PERIOD(DATE)'             WHEN 'PM' THEN 'PERIOD(TIMESTAMP WITH TIME ZONE)'
    WHEN 'PS' THEN 'PERIOD(TIMESTAMP)'        WHEN 'PT' THEN 'PERIOD(TIME)'
    WHEN 'PZ' THEN 'PERIOD(TIME WITH TIME ZONE)'
    WHEN 'SC' THEN 'INTERVAL SECOND'          WHEN 'SZ' THEN 'TIMESTAMP WITH TIME ZONE'
    WHEN 'TS' THEN 'TIMESTAMP'                WHEN 'TZ' THEN 'TIME WITH TIME ZONE'
    WHEN 'UT' THEN 'UDT'         WHEN 'AN' THEN 'UDT'
    WHEN 'YM' THEN 'INTERVAL YEAR TO MONTH'   WHEN 'YR' THEN 'INTERVAL YEAR'
    WHEN 'XM' THEN 'XML'         WHEN 'JN' THEN 'JSON'      WHEN 'DT' THEN 'DATASET'
    WHEN '??' THEN 'ST_GEOMETRY / ANY_TYPE'
  END AS CType
FROM DBC.ColumnsVX
WHERE UPPER(TableName) LIKE UPPER('<table_or_%>') AND UPPER(DatabaseName) LIKE UPPER('<db>');

-- find a column by name across databases (where is customer_id used?)
SELECT DatabaseName, TableName, ColumnName, ColumnType
FROM DBC.ColumnsV
WHERE ColumnName LIKE '%customer_id%' (NOT CASESPECIFIC)
  AND DatabaseName NOT IN ('DBC','SYSLIB','SystemFe','TD_SYSFNLIB','Sys_Calendar','TDStats','SysAdmin')
ORDER BY 1, 2;
```

Reading the type columns:

- `CV`/`CF`: `ColumnLength` is the declared length; `CharType` 1 = LATIN, 2 = UNICODE (inserting non-ASCII into
  LATIN raises `Error 6706`).
- `D`: `DecimalTotalDigits`/`DecimalFractionalDigits` give `DECIMAL(p,s)`. DECIMAL / DECIMAL keeps the numerator's
  scale - cast to FLOAT before dividing.
- `DA` is a real DATE; `SUBSTRING` on it fails (`5407`). A date stored in `CV` is text - `SUBSTRING(col FROM 1 FOR 7)`
  for year-month and `CAST(... AS DATE FORMAT 'YYYY-MM-DD')` to compare.
- `TS`: `ColumnLength`/`DecimalFractionalDigits` encode the fractional-seconds precision; `TIMESTAMP(0)` rejects
  microseconds (`5404`).
- For **views**, `DBC.ColumnsV` may report blank or unresolved types on some releases; when it matters, prove the type
  by casting a sample (`SELECT TOP 1 CAST(<col> AS DATE) FROM <db>.<view>`), or read `SHOW VIEW`.

## DDL and statistics

```sql
SHOW TABLE <db>.<table>;      -- exact DDL (what base_tableDDL runs); fails with 3853 on a view
SHOW VIEW  <db>.<view>;       -- view definition
SHOW MACRO <db>.<macro>;
HELP TABLE <db>.<table>;      -- one row per column with type/nullability, works for tables and views
HELP COLUMN <db>.<table>.*;   -- detailed per-column attributes (lengths, format, charset)
HELP STATISTICS <db>.<table>; -- collected statistics and their age
```

All of these pass the read guard (`SHOW`/`HELP` are allowed first keywords).

## Row counts and domains

```sql
SELECT COUNT(*) AS row_cnt FROM <db>.<table>;

-- the domain of a column: DISTINCT, never a TOP 5 preview
SELECT <col>, COUNT(*) AS row_cnt
FROM <db>.<table>
GROUP BY <col>
ORDER BY 2 DESC;

-- date window of a fact table (anchor "last N days" to MAX, not CURRENT_DATE)
SELECT MIN(<date_col>) AS first_dt, MAX(<date_col>) AS last_dt, COUNT(*) AS row_cnt FROM <db>.<table>;

-- size per table (bytes summed across AMPs) and skew
SELECT TableName,
       SUM(CurrentPerm) AS current_perm_bytes,
       CAST(100 - (AVG(CurrentPerm) / NULLIFZERO(MAX(CurrentPerm)) * 100) AS DECIMAL(5,2)) AS skew_pct
FROM DBC.AllSpaceV
WHERE DatabaseName = '<db>' AND TableName <> 'All'
GROUP BY TableName
ORDER BY 2 DESC;
```

## Usage and relationships from DBQL (precondition: query logging with OBJECTS enabled)

```sql
-- is DBQL object logging available to me at all?
SELECT COUNT(*) AS n FROM DBC.DBQLObjTbl SAMPLE 1;

-- most-queried tables in a database (what base_tableUsage ranks; 'High' >= 10% of logged queries, 'Medium' >= 5%)
SELECT o.DatabaseName, o.ObjectTableName AS TableName,
       COUNT(DISTINCT o.QueryID) AS query_cnt,
       (CURRENT_TIMESTAMP - MIN(o.CollectTimeStamp)) DAY(4) AS first_query_days_ago,
       (CURRENT_TIMESTAMP - MAX(o.CollectTimeStamp)) DAY(4) AS last_query_days_ago
FROM DBC.DBQLObjTbl o
JOIN DBC.DBQLogTbl q ON q.QueryID = o.QueryID
WHERE o.ObjectType IN ('Tab','Viw') AND o.ObjectColumnName IS NULL
  AND o.DatabaseName = '<db>'
  AND (q.AMPCPUTime + q.ParserCPUTime) > 0
GROUP BY 1, 2
ORDER BY 3 DESC;

-- tables queried TOGETHER with <table> (what base_tableAffinity infers relationships from)
SELECT b.DatabaseName, b.ObjectTableName AS TableName, COUNT(DISTINCT a.QueryID) AS query_cnt
FROM DBC.DBQLObjTbl a
JOIN DBC.DBQLObjTbl b ON a.QueryID = b.QueryID
WHERE a.ObjectType IN ('Tab','Viw') AND b.ObjectType IN ('Tab','Viw')
  AND a.ObjectColumnName IS NULL AND b.ObjectColumnName IS NULL
  AND a.DatabaseName = '<db>' AND a.ObjectTableName = '<table>'
  AND NOT (b.DatabaseName = a.DatabaseName AND b.ObjectTableName = a.ObjectTableName)
GROUP BY 1, 2
ORDER BY 3 DESC;
```

Prefix long DBQL scans with `LOCKING ROW FOR ACCESS` when the site allows it. On systems with PDCR, use the
`PDCRINFO.DBQLObjTbl_Hst` history tables with a `LogDate` window instead. An empty result means no logged usage in the
retained window - not an unused object.

## Privileges (why an object is invisible)

```sql
-- what a user may do on a database/table (sec_userDbPermissions equivalent)
SELECT DatabaseName, TableName, AccessRight, GrantAuthority, GrantorName
FROM DBC.AllRightsV
WHERE UserName = '<user>' AND DatabaseName = '<db>'
ORDER BY 1, 2, 3;
```

`Error 3523 The user does not have SELECT access to <db>.<table>` is a rights problem, and a right granted on a parent
database does not cascade to a child database created afterwards.
