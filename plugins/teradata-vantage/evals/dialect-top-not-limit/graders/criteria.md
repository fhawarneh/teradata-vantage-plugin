---
type: llm
weight: 1
---

The response must produce Teradata-valid SQL despite the user asking for `LIMIT`.

PASS requires ALL of:
- The SQL uses `SELECT TOP 10` (TOP immediately after SELECT, before the column list), or
  `QUALIFY ROW_NUMBER() OVER (ORDER BY ...) <= 10`.
- The SQL does NOT contain `LIMIT 10` or `FETCH FIRST 10 ROWS ONLY` as the row-limiting clause.
- The table is qualified as `analytics.sales_fact`, not bare `sales_fact`.
- The response says, at least briefly, that `LIMIT` is not valid Teradata syntax (a syntax error,
  Teradata error 3706) — it does not silently swap the clause without telling the user.

FAIL if the answer emits `LIMIT`, invents a different table, or claims `LIMIT` works on Teradata.
