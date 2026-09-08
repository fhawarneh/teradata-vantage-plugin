# Physical design — table shape, distribution, partitioning and scratch space

The rules that decide how a Teradata table is stored and spread across AMPs. Read this when writing or
reviewing DDL, or when a query is slow for a reason the SQL text does not explain.

## SET vs MULTISET

`SET` tables reject duplicate rows, which costs a full-row duplicate check on every insert. `MULTISET`
does not. Prefer `MULTISET` and enforce uniqueness where you actually need it, with a
`UNIQUE PRIMARY INDEX` or a `UNIQUE INDEX`.

A bare `CREATE TABLE` is not the same everywhere: in Teradata session mode it defaults to `SET`, in ANSI
mode to `MULTISET`. Never rely on the default — write the keyword.

```sql
CREATE MULTISET TABLE <db>.<table> (
  id        INTEGER NOT NULL,
  txn_date  DATE FORMAT 'YYYY-MM-DD',
  amount    DECIMAL(18,2)
) PRIMARY INDEX (id);
```

## Primary index — the distribution decision

`PRIMARY INDEX (col)` decides how rows hash across AMPs, and therefore whether work is spread evenly.

- Pick a HIGH-cardinality column that is frequently joined on. An even hash is the whole point.
- A low-cardinality primary index (status, country, a flag) puts most rows on a few AMPs. The table is
  then skewed: one AMP does most of the work and the query is slow no matter how the SQL is written.
- `NO PRIMARY INDEX` is for staging tables that are written once and read in full.
- Two tables joined on their common primary index are co-located, so the join needs no redistribution.
  This is the single biggest lever on join cost.

Check skew before blaming the query:

```sql
SELECT TableName,
       CAST(MAX(CurrentPerm) - AVG(CurrentPerm) AS DECIMAL(18,2)) AS skew_bytes
FROM   DBC.TableSizeV
WHERE  DatabaseName = '<db>' AND TableName = '<table>'
GROUP BY TableName;
```

## Partitioning (PPI)

```sql
CREATE MULTISET TABLE <db>.<table> ( ... )
PRIMARY INDEX (id)
PARTITION BY RANGE_N(txn_date BETWEEN DATE '2020-01-01' AND DATE '2030-12-31' EACH INTERVAL '1' MONTH);
```

Partition elimination only happens when the predicate uses the partitioning column DIRECTLY. Wrapping it
in a function or comparing it to an expression the optimizer cannot resolve at parse time reads every
partition, which is the opposite of what the partitioning was for.

## Scratch space

```sql
CREATE VOLATILE TABLE vt AS (SELECT ...) WITH DATA ON COMMIT PRESERVE ROWS;
```

Volatile tables are session-scoped and disappear at logoff. Without `ON COMMIT PRESERVE ROWS` the table
is emptied at the end of the transaction, which looks exactly like a query that returned nothing.
`CREATE GLOBAL TEMPORARY TABLE` gives a persistent definition with session-scoped contents.

Copy a structure with `CREATE TABLE <db>.<new> AS <db>.<old> WITH NO DATA;`, and the data too with
`WITH DATA`.

## How these reach the tools

Volatile tables and `CREATE TABLE ... AS` go through `base_writeQuery`. They create nothing permanent and
are not classified destructive, so they do not raise an approval prompt. `CREATE DATABASE`/`USER`, `DROP`,
`DELETE`, `UPDATE`, `INSERT`, `MERGE`, `ALTER`, `GRANT`, `REVOKE` and `ABORT SESSION` do.

After loading or substantially changing a table, collect statistics — an `EXPLAIN` that says "no
confidence" is the optimizer telling you they are missing:

```sql
COLLECT STATISTICS COLUMN (pi_col), COLUMN (join_col) ON <db>.<table>;
```

## Temporal tables and the PERIOD type

These are two different things and conflating them wastes an afternoon.

**The `PERIOD` data type always works.** It is an ordinary type with its own operators, available on any
Vantage system. All of these were verified on Vantage 20.00:

```sql
SELECT PERIOD(DATE '2020-01-01', DATE '2021-01-01');           -- a period value
SELECT BEGIN(p), END(p) ...                                     -- endpoints
SELECT 1 WHERE p1 OVERLAPS p2;                                  -- do two periods overlap
SELECT p1 P_INTERSECT p2;                                       -- the overlapping span
```

`P_INTERSECT`, `P_NORMALIZE`, `LDIFF` and `RDIFF` are the period algebra. A single `PERIOD` column plus
`OVERLAPS` replaces the usual `start_date`/`end_date` pair and the off-by-one bugs that come with it, so
it is worth reaching for even on a system with no temporal feature at all.

Note the half-open convention: a period includes its start and **excludes** its end. Adjacent periods
therefore share a boundary value without overlapping, which is what makes `OVERLAPS` behave.

**Temporal TABLES are a separate feature and may not be installed.** The `VALIDTIME` and
`TRANSACTIONTIME` query qualifiers — the ones that make Teradata automatically filter and maintain
history — fail unambiguously when the feature is absent:

```
CURRENT VALIDTIME SELECT ...
Error 3706: Syntax error: Temporal operations are not supported on this system.
```

That message is explicit, which is a mercy. Check for it before designing around temporal tables:
`SEQUENCED VALIDTIME`, `NONSEQUENCED VALIDTIME` and `CURRENT VALIDTIME` all return the same error on a
system without the feature.

Two consequences:

- **A `PERIOD` column does not make a table temporal.** Counting `DBC.ColumnsV` rows with `ColumnType IN
  ('PD','PM','PS','PT','PZ')` will find plenty of hits on any system — on the one measured, all 165 were
  internal UDT method signatures in `SYSUDTLIB`, not user tables. That count proves nothing about
  temporal support.
- **dbt's `valid_history` incremental strategy needs the temporal feature.** On a system that returns the
  error above, that strategy cannot work; use `merge` and maintain the validity window yourself with
  `PERIOD` columns. See the `pipelines` skill.

Without temporal tables, model history explicitly: a `PERIOD` column for validity, a surrogate key, and a
`merge` that closes the old row and opens a new one. More work than `SEQUENCED VALIDTIME`, and it runs
everywhere.
