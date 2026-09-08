# The Graph Edge Contract — building and populating an edge repository

Every `graph_*` analysis tool reads one table or view conforming to this contract. Teradata has no
dependency catalog the tools could default to, so on a stock system you build it. This page carries the
contract, the direction rule, the two extraction recipes and the one workaround the bundled server needs.

The contract itself is also served as an MCP resource: reference `graph://edge-contract` with an `@`
mention to read the canonical text.

## The six required columns

| Column | Type | Meaning |
|---|---|---|
| `Src_Container_Name` | `VARCHAR(128) NOT NULL` | Source (upstream) container — a database, an ETL folder, a dbt project |
| `Src_Object_Name` | `VARCHAR(128) NOT NULL` | Source object name |
| `Src_Kind` | `VARCHAR(30) NOT NULL` | `Table`, `View`, `Job`, … (single-letter legacy codes also accepted) |
| `Tgt_Container_Name` | `VARCHAR(128) NOT NULL` | Target (downstream) container |
| `Tgt_Object_Name` | `VARCHAR(128) NOT NULL` | Target object name |
| `Tgt_Kind` | `VARCHAR(30) NOT NULL` | as `Src_Kind` |

Two optional enrichment columns, ignored by the analysis tools and used by visualisation clients:
`Edge_Relationship` (`DIRECT`, `ETL_INPUT`, `ETL_OUTPUT`, `JOIN`, `TRANSFORM`, `FILTER`) and
`Transformation_Type` (`ETL`, `FEATURE_ENG`, `AGGREGATION`, `EMBEDDING_GEN`, …).

Generate the DDL with `graph_edgeContractDDL` rather than writing it by hand — it emits the `CREATE TABLE`
with the right multi-value compression, sample `INSERT`s and a null-check validation query, and it needs
no database connection.

## The direction rule

**Src is always upstream, Tgt is always downstream. Src is the prerequisite; Tgt is the consumer.** One
direction, read three ways:

| Edge type | Read it as | Example |
|---|---|---|
| Object dependency | Src *is referenced by* Tgt | `customer` → `v_customer_active` |
| ETL input | Src *is read by* Tgt | `customer` → `load_job` |
| ETL output | Src *writes to* Tgt | `load_job` → `customer_features` |

Get this backwards and every impact analysis is inverted while still returning plausible-looking rows.
Validate a new repository by tracing one object whose dependents you already know.

## Recipe 1 — structural edges from view DDL

Every view carries its own text in `DBC.TablesV.RequestText`. Parse the objects it reads, resolve each
against the dictionary so only real objects become edges, and emit one row per reference.

Start from the raw material:

```sql
SELECT DatabaseName, TableName, RequestText
FROM   DBC.TablesV
WHERE  TableKind = 'V'
  AND  RequestText IS NOT NULL
  AND  DatabaseName NOT IN ('DBC','TD_SYSFNLIB','SYSLIB','SYSUDTLIB','TD_SYSGPL','TD_SYSXML',
                            'SYSSPATIAL','TD_SERVER_DB','SystemFe','Sys_Calendar','TD_SYSAI');
```

and the resolution catalogue:

```sql
SELECT DatabaseName, TableName, TableKind
FROM   DBC.TablesV
WHERE  TableKind IN ('T','V','O');
```

Rules that keep the result honest:

- Take the object after each `FROM` and `JOIN`, after stripping `/* */` and `--` comments.
- If the reference is qualified, use that database. If it is bare, resolve it against the catalogue and
  **skip it when the name is ambiguous or unknown** — an invented edge is worse than a missing one.
- Drop self-references and system databases.
- `Src_Kind` comes from the resolved object's `TableKind`; the target is always `View`.

Measured on a system with 853 views: **530 edges derived, 95 references skipped as unresolvable.** Report
the skipped count — it is the honest measure of how complete the graph is.

Structural lineage is complete for views and **blind to ETL**: a table loaded by an external job has no
inbound edge and will look like a root object.

## Recipe 2 — observed edges from the DBQL object log

```sql
SELECT DISTINCT
       u.ObjectDatabaseName AS Src_Container_Name,
       u.ObjectTableName    AS Src_Object_Name,
       'Table'              AS Src_Kind,
       d.ObjectDatabaseName AS Tgt_Container_Name,
       d.ObjectTableName    AS Tgt_Object_Name,
       'Table'              AS Tgt_Kind
FROM   DBC.QryLogObjectsV u
JOIN   DBC.QryLogObjectsV d
  ON   u.QueryID = d.QueryID
 AND   u.ObjectType = 'Tab' AND d.ObjectType = 'Tab'
 AND   (u.ObjectDatabaseName, u.ObjectTableName) <> (d.ObjectDatabaseName, d.ObjectTableName)
WHERE  u.CollectTimeStamp >= CURRENT_DATE - 30;
```

Two caveats that must reach the reader:

- Co-occurrence in one query is **not** a direction. Without the statement text you know the objects were
  used together, not which fed which. Treat it as an undirected hint, or recover direction from the
  statement's target table.
- Object-level DBQL logging must be on, and the grant for `DBC.QryLogObjectsV` does not carry the base
  `DBC.DBQLObjTbl`. **An empty result means "nothing logged in the window", never "nothing depends on it".**

## The `_FQ` workaround for `graph_bfsLevels`

Standalone `graph_bfsLevels` reads two columns the contract does not define and
`graph_edgeContractDDL` does not generate — `Src_Object_Name_FQ` and `Tgt_Object_Name_FQ`. Against a
conforming table it fails with:

```
[Error 3810] Column/Parameter '<db>.r.Src_Object_Name_FQ' does not exist.
```

It is the only one of the seven tools that does this; the other six, including `graph_analyseDatabase`'s
internal BFS, work against the contract as written. Point `graph_bfsLevels` at a view instead:

```sql
CREATE VIEW <db>.EdgeRepositoryFQ AS
SELECT Src_Container_Name, Src_Object_Name, Src_Kind,
       Tgt_Container_Name, Tgt_Object_Name, Tgt_Kind,
       Edge_Relationship, Transformation_Type,
       TRIM(Src_Container_Name) || '.' || TRIM(Src_Object_Name) AS Src_Object_Name_FQ,
       TRIM(Tgt_Container_Name) || '.' || TRIM(Tgt_Object_Name) AS Tgt_Object_Name_FQ
FROM   <db>.EdgeRepository;
```

Verified: with the view, waves return with `nearest_root` populated. Reported upstream against
`teradata-mcp-server` 0.2.6.

## Keeping it current

An edge repository is a snapshot. A view created after you built it has no edges and looks like a root.
Rebuild it on the cadence the answers need — before a migration wave, before a drop review — and state the
build date whenever you report from it.
