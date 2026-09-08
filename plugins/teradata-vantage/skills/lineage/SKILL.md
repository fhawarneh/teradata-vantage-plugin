---
name: lineage
description: Use when the user asks what depends on a Teradata object, what would break if it changed or were dropped, what feeds a table or view, whether there are circular dependencies, which objects have no upstream source, or how to group objects into migration waves. Drives the seven graph_* dependency tools over an edge repository, and covers building that repository on a system that has none.
when_to_use: what breaks if I drop this; impact analysis; what depends on <db>.<table>; what feeds this view; upstream and downstream dependencies; data lineage; dependency graph; circular dependency; which tables are root objects; migration waves; what order do I migrate these in; blast radius; graph_traceLineage; edge repository; Error 3810 Src_Object_Name_FQ.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[<db>.<object> | roots <db-pattern> | cycles <db-pattern> | waves <root,root> | build-repository]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__graph_traceLineage
  - mcp__plugin_teradata-vantage_teradata__graph_findRootObjects
  - mcp__plugin_teradata-vantage_teradata__graph_connectedComponents
  - mcp__plugin_teradata-vantage_teradata__graph_detectCycles
  - mcp__plugin_teradata-vantage_teradata__graph_bfsLevels
  - mcp__plugin_teradata-vantage_teradata__graph_analyseDatabase
  - mcp__plugin_teradata-vantage_teradata__graph_edgeContractDDL
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_tableList
  - Workflow
  - Workflow(teradata-vantage:drop-impact)
disallowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_writeQuery
---

# Teradata object lineage and dependency analysis

Answers "what depends on this", "what feeds this", "what breaks if I change it", "is anything circular"
and "what order do I migrate these in" — from a dependency graph, not from guesswork.

## Read this first: the prerequisite that stops everything

**All six analysis tools require an `edge_repository` argument, and Teradata has no dependency catalog to
default to.** Called without it they return, verbatim:

```
edge_repository is required. Call graph_edgeContractDDL to generate one.
```

So the first question is always: *does this system already have an edge repository?* Ask, or look for a
table with the contract's six columns. If there is none, you are in the build path below — say so plainly
rather than running a tool that will refuse.

`DBC.DatasetSchemaDependenciesV` is not a substitute: it covers datasets, not tables and views.

## Step 0 — Is there a repository?

```sql
SELECT DatabaseName, TableName
FROM DBC.ColumnsV
WHERE ColumnName = 'Src_Container_Name'
GROUP BY 1,2;
```

A hit is a candidate repository. Confirm it carries all six required columns before using it. If the
system runs the AI-Native Data Products observability module, `<Product>_Semantic.lineage_graph` already
conforms — use it directly.

## Step 1 — Build one, if needed

`graph_edgeContractDDL(target_database=..., object_name='EdgeRepository', output_type='TABLE')` emits the
`CREATE TABLE`, sample `INSERT`s and a null-check validation query. It needs no database connection and
executes nothing. The `CREATE` is a `[WRITE]` for the user to run.

Then populate it. Two sources, and they answer different questions — say which one you used:

| Source | Gives | Needs |
|---|---|---|
| View DDL — `DBC.TablesV.RequestText` where `TableKind='V'` | **Structural** edges: which tables and views each view reads | nothing; every view carries its text |
| DBQL object log — `DBC.DBQLObjTbl` / `DBC.QryLogObjectsV` | **Observed** edges: which objects real queries touched together | object-level DBQL logging enabled, and the grant for the base table |

Structural lineage is complete for views and blind to ETL. Observed lineage catches ETL and jobs but only
what ran inside the DBQL window, and an empty result means "nothing logged", never "nothing depends on it".
`references/edge-repository.md` carries the contract, the direction rule and the extraction recipe.

Measured on a system with 853 views: 530 structural edges, 95 references skipped as unresolvable. Skipping
an ambiguous reference is correct — never invent an edge to reach a rounder number.

## Step 2 — The ladder

| Question | Tool |
|---|---|
| Give me the whole picture in one call | `graph_analyseDatabase` — runs roots, components, cycles and BFS on ONE shared edge fetch |
| What has no upstream source? | `graph_findRootObjects` |
| What depends on this / what feeds it? | `graph_traceLineage` |
| Are there circular dependencies? | `graph_detectCycles` |
| What are the independent clusters? | `graph_connectedComponents` |
| What order do I migrate in? | `graph_bfsLevels` — read the caveat below first |

Prefer `graph_analyseDatabase` for anything that starts "tell me about this database": it is one call and
one edge fetch instead of four, and its output carries all four sections.

## Two traps, both measured on Vantage 20.0

**1. `graph_traceLineage` needs a QUALIFIED object name.** A bare name returns zero nodes with
`"status": "success"` — no error, nothing to notice.

```
object_name="<db>.<table>"    ->  12 nodes      CORRECT
object_name="<table>"         ->   0 nodes      silent, looks like "nothing depends on it"
object_name="%.<table>"       ->  37 nodes      wildcard on the container, every database
```

Always qualify. If the user names a bare object, resolve the database first with `base_tableList` or the
`%.` wildcard, and say which you did.

**2. Standalone `graph_bfsLevels` fails on a contract-conforming repository.** It is the only tool that
reads `Src_Object_Name_FQ` and `Tgt_Object_Name_FQ`, and `graph_edgeContractDDL` does not generate them:

```
[Error 3810] Column/Parameter '<db>.r.Src_Object_Name_FQ' does not exist.
```

This is an upstream defect in the bundled server, not a mistake in your repository. Two ways past it:

- Point the tool at a view that adds the two columns — the fix, and `references/edge-repository.md` has the
  `CREATE VIEW`. Verified: waves then return with `nearest_root` populated.
- Or use `graph_analyseDatabase`, whose internal BFS does not have the defect and returns the same wave
  data. Verified on both a plain table and the view: identical node counts.

## Reporting

- Lead with the answer, then the evidence. "17 objects depend on it, across 3 databases, deepest chain 4
  hops" before any table.
- **Say which lineage you used.** Structural, observed, or both — they answer different questions and a
  reader cannot tell from the numbers.
- **An empty result is not "safe to drop".** It means no edge exists in the repository you queried. State
  the repository, its source, and for observed lineage the DBQL window. The `drop-impact` workflow applies
  the same rule and is the right escalation before any `DROP`.
- Report cycles as an ordered node list, and name the edge to break.
- Give migration waves as wave 0, wave 1, … with the node count in each. Wave N cannot start until N-1 is
  complete; that is the whole value of the grouping.

## Escalation

- Before any `DROP` or destructive change: launch `drop-impact`,
  `Workflow({name: "teradata-vantage:drop-impact", args: {objects: ["<db>.<table>"]}})`. It combines this
  lineage with DBQL usage and table affinity and never emits a `DROP`.
- The tools read only the edge repository — they never see the objects themselves. Confirm an object still
  exists with `base_tableList` before reporting on it.

## References

- `references/edge-repository.md` — the Graph Edge Contract's six required and two optional columns, the
  Src→Tgt direction rule, the extraction recipes for both lineage sources, and the `_FQ` view.
