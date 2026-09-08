# Porting SQL to Teradata

Every construct below was executed against a live Vantage 20.00 — reads run directly, writes and DDL
parsed with `EXPLAIN`. Where something fails, the error code is the one Teradata actually returns.

`Error 3706` is a syntax error and `Error 3707` is the parser rejecting a token; both mean "this dialect
does not have that". A different code means something more interesting, and those are the entries worth
reading twice.

## Row limiting

| Written as | On Teradata |
|---|---|
| `LIMIT 10` | **`Error 3706`** |
| `FETCH FIRST 10 ROWS ONLY` | **`Error 3706`** |
| `OFFSET 10 ROWS FETCH NEXT 10 ROWS ONLY` | **`Error 3706`** |
| `SELECT TOP 10 …` | works |
| `… SAMPLE 10` | works — a random sample, not the first 10 |

`LIMIT` is the single most common porting failure, and `TOP` is the replacement.

`SAMPLE` also takes a fraction (`SAMPLE 0.10`) and a stratified form (`SAMPLE 0.05, 0.10`), which is
for profiling — never for a "top N" question. Note the two are not
interchangeable with `SAMPLE`: `TOP` with an `ORDER BY` is deterministic, `SAMPLE` is not.

There is no `OFFSET`. Paginate with `QUALIFY ROW_NUMBER() OVER (ORDER BY …) BETWEEN 11 AND 20`.

## Null handling and conditionals

| Written as | On Teradata |
|---|---|
| `COALESCE(x, y)` | works — **prefer this** |
| `NVL(x, y)` | works |
| `IFNULL(x, y)` | `Error 3706` |
| `ISNULL(x, y)` | `Error 3706` |

`COALESCE` is standard and portable in both directions; use it rather than `NVL` even though `NVL` works.

## Dates and times

| Written as | On Teradata |
|---|---|
| `CURRENT_DATE` | works |
| `NOW()` | **works** — genuinely present, contrary to expectation |
| `GETDATE()` | `Error 3706` |
| `SYSDATE` | **`Error 3822`** — parsed as a column reference, not a function |
| `DATEADD(day, 1, d)` | `Error 3706` |
| `d + 1` | works — integer arithmetic on a DATE adds days |
| `d + INTERVAL '1' DAY` | works |
| `DATEDIFF(day, a, b)` | `Error 3706` |
| `b - a` | works — DATE minus DATE yields days |
| `EXTRACT(YEAR FROM d)` | works |

`SYSDATE` failing as `3822` rather than `3706` is the tell that Teradata parsed it as an identifier. The
error text will talk about a column, which sends people looking in the wrong place entirely.

Date arithmetic is the pleasant surprise: `d + 1` and `b - a` both work and are shorter than the
`DATEADD` / `DATEDIFF` they replace. For months and years use `ADD_MONTHS`, because adding 30 days is not
adding a month. The `date-time.md` reference in this skill covers the interval traps.

## Strings

| Written as | On Teradata |
|---|---|
| `'a' \|\| 'b'` | works |
| `CONCAT('a','b')` | works |
| `SUBSTRING(s FROM 2 FOR 3)` | works |
| `SUBSTR(s, 2, 3)` | works |
| `s ILIKE 'a'` | `Error 3707` |
| `x LIKE ANY ('a%','b%')` | works |
| `STRING_AGG(x, ',')` | `Error 3706` |
| `LISTAGG(x, ',')` | `Error 3706` |

No `ILIKE`. Case-insensitive matching is a `CASESPECIFIC` question — `UPPER(x) LIKE UPPER(p)` is the
portable form, at the cost of the index.

**String aggregation has no modern spelling.** Neither `STRING_AGG` nor `LISTAGG` exists. The idiom is
`XMLAGG`, and it works:

```sql
SELECT TRIM(TRAILING ',' FROM
       XMLAGG(TRIM(col) || ',' ORDER BY col)(VARCHAR(1000)))
FROM <db>.<table>;
```

The `(VARCHAR(1000))` cast is required and its size bounds the result — a longer list is silently
truncated to it, so size it deliberately.

## Types and casting

| Written as | On Teradata |
|---|---|
| `CAST(x AS VARCHAR(10))` | works |
| `x::VARCHAR(10)` | `Error 3707` |
| `CAST('t' AS BOOLEAN)` | `Error 3706` |
| `WHERE TRUE` | `Error 3707` |

**There is no `BOOLEAN` type and no `TRUE` / `FALSE` literal.** This is the porting problem that touches
the most lines: a Postgres schema with a `BOOLEAN` column becomes `BYTEINT` with 0 and 1, and every
`WHERE flag` becomes `WHERE flag = 1`. `WHERE TRUE` as a query-builder placeholder becomes `WHERE 1=1`.

The `::` cast shorthand does not exist. `CAST` everywhere.

## Set operations, joins and windows

All of these work: `EXCEPT`, `MINUS` (both, and they are synonyms), `INTERSECT`, `FULL OUTER JOIN`,
common table expressions, `GROUP BY` by ordinal, window frames with `ROWS BETWEEN`.

These do not:

| Written as | On Teradata |
|---|---|
| `COUNT(*) FILTER (WHERE …)` | `Error 3707` — use `SUM(CASE WHEN … THEN 1 ELSE 0 END)` |
| `LATERAL (…)` | `Error 3707` — rewrite as a join or a correlated scalar subquery |

**`QUALIFY` works, and it is Teradata's own.** Filtering on a window function without a wrapping subquery
is genuinely nicer than the standard form, so port *into* it rather than around it:

```sql
SELECT customer_id, txn_date
FROM   <db>.<table>
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY txn_date DESC) = 1;
```

## DDL

| Written as | On Teradata |
|---|---|
| `CREATE TABLE IF NOT EXISTS` | **`Error 3707`** |
| `DROP TABLE IF EXISTS` | **`Error 3707`** |
| `CREATE TEMPORARY TABLE` | `Error 3707` |
| `CREATE VOLATILE TABLE … ON COMMIT PRESERVE ROWS` | works |
| `CREATE TABLE … AS ( … ) WITH DATA` | works |
| `… GENERATED ALWAYS AS IDENTITY` | works |
| `ALTER TABLE … ADD <col> <type>` | works |
| `TRUNCATE TABLE` | **`Error 3706`** |
| `DELETE FROM … ALL` | works |

**No `IF EXISTS` / `IF NOT EXISTS` anywhere.** This breaks nearly every idempotent migration script ever
written. The Teradata pattern is to attempt the statement and tolerate the specific error — `3807` for a
missing object, `5612`/`3803` for one that already exists. Tolerating *all* errors instead is how a
failed `CREATE` gets silently swallowed and the script continues against a table that does not exist.

**No `TRUNCATE`.** `DELETE FROM <t> ALL` is the equivalent and is fast — the `ALL` keyword matters.

**`CREATE VOLATILE TABLE` replaces temp tables**, and it is session-scoped: it disappears when the session
ends, and it is invisible to any other session. Add `ON COMMIT PRESERVE ROWS` or the contents vanish at
the first commit, which is the single most common surprise for anyone new to them.

## MERGE — it exists, and it has one hard rule

`MERGE` ports syntactically from every other dialect and then fails semantically:

```
Error 5758: The search condition must fully specify the Target table primary index and
            partition column(s) and expression must match INSERT specification primary
            index and partition column(s).
```

**The `ON` clause must fully specify the target's primary index**, and the `INSERT` branch must supply
those same columns. Measured: the identical `MERGE` keyed on a single non-PI column fails with 5758, and
keyed on the complete primary index it parses.

Two consequences worth stating to the user:

- Choose the primary index and the merge key together. They are the same decision, not two.
- **In dbt, a `merge` incremental model's `unique_key` must be the primary index.** A `unique_key` that
  is merely unique is not enough. This is the usual cause of a dbt merge model that fails with 5758 while
  the same model runs fine on Snowflake. See the `pipelines` skill.

## How to port well

1. **Run the statement rather than reasoning about it.** `EXPLAIN` parses without executing, so it is a
   free syntax check for DDL and DML — that is how every entry in this page was verified.
2. **Read the error code, not just the message.** `3706` and `3707` mean the construct is absent. Anything
   else means Teradata understood you and objected — a far more informative situation.
3. **Port into Teradata's strengths, not just away from errors.** `QUALIFY`, `SAMPLE` and date arithmetic
   are all shorter than what they replace.
4. **Never bulk-rewrite with a regex.** The failures above are not textual substitutions: `BOOLEAN` is a
   schema change, `MERGE` is a physical-design decision, and `IF EXISTS` is an error-handling strategy.
