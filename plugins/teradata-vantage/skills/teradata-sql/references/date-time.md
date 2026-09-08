# Teradata date and time — literals, functions, INTERVAL literals, relative-time filters

Purpose: the complete date/time companion to `teradata-sql`; read it before writing any predicate,
bucket or offset that involves a DATE, TIME, TIMESTAMP, INTERVAL or a date stored as a string.

Items marked (doc) are authored from Teradata documentation; verify against your release. Everything
else was observed on live Vantage systems.

## 1. Literals and current values

| Form | Example |
|---|---|
| DATE literal | `DATE '2026-06-15'` |
| TIME literal | `TIME '14:21:13'` |
| TIMESTAMP literal | `TIMESTAMP '2026-06-15 14:30:00'`, `TIMESTAMP '2026-06-15 14:30:00.123456'` |
| TIMESTAMP with zone | `TIMESTAMP '2026-06-15 14:30:00+00:00'` |
| INTERVAL literals | `INTERVAL '5' YEAR`, `INTERVAL '6' MONTH`, `INTERVAL '30' DAY`, `INTERVAL '3 12:30:00' DAY TO SECOND` |
| PERIOD literal (doc) | `PERIOD(DATE '2026-01-01', DATE '2026-12-31')` |
| Now | `CURRENT_DATE`, `CURRENT_TIME`, `CURRENT_TIMESTAMP` (`CURRENT_TIMESTAMP(0)` for whole seconds) |

There is no `NOW()`, `GETDATE()`, `SYSDATE`, `DATEADD`, `DATEDIFF`, `MONTH()`, `YEAR()`, `DAY()`,
`WEEK()` or `DATE_TRUNC()`. Each of those fails with `Error 3707 Syntax error, expected something like
...`. The equivalents are below.

## 2. Arithmetic

| Need | Write | Notes |
|---|---|---|
| n days ago | `CURRENT_DATE - 90` | DATE ± INTEGER is days. Safest form for any day offset. |
| days between | `d2 - d1` | DATE − DATE is an INTEGER. |
| n months ago | `ADD_MONTHS(CURRENT_DATE, -6)` | Clamps to month end. Never `INTERVAL` for month math on the 29th–31st. |
| months between | `MONTHS_BETWEEN(d2, d1)` (doc) | Fractional result. |
| first of month | `TRUNC(d, 'MM')` | Also `'Q'` (quarter), `'YYYY'` (year). `TRUNC(d, 'IW')` is not the weekly bucket to use here. |
| week start | `TD_WEEK_BEGIN(d)` | Sunday-anchored DATE. The only verified weekly bucket. |
| year / month / day | `EXTRACT(YEAR FROM d)`, `EXTRACT(MONTH FROM d)`, `EXTRACT(DAY FROM d)` | Returns INTEGER. Also `HOUR`/`MINUTE`/`SECOND` from TIME/TIMESTAMP. |
| day of week (doc) | `TD_DAY_OF_WEEK(d)` (1 = Sunday) | Also `TD_DAY_OF_MONTH`, `TD_DAY_OF_YEAR`, `TD_MONTH_OF_YEAR`, `TD_WEEK_OF_YEAR`. |
| last day of month (doc) | `LAST_DAY(d)` | |
| timestamp difference | `(ts2 - ts1) DAY(4) TO SECOND` | The result is an INTERVAL; the leading-field precision (`DAY(4)`) is mandatory when the difference can exceed 99 days. |
| interval field out of a difference | `EXTRACT(HOUR FROM ((ts2 - ts1) HOUR(2) TO SECOND(6)))` | Observed in production DBQL delay SQL. |
| format for display | `TO_CHAR(d, 'YYYY-MM-DD')`, `d (FORMAT 'YYYY-MM-DD')` | The FORMAT phrase is Teradata-specific and applies at output. |
| string → date | `CAST('2026-06-15' AS DATE)`, `CAST(s AS DATE FORMAT 'YYYY-MM-DD')`, `TO_DATE(s, 'YYYY-MM-DD')` (doc) | See §4 for strings that carry a time. |

`CAST(<date> AS VARCHAR(n))` renders in the session DATEFORM, which defaults to `IntegerDate`
(`YY/MM/DD`): `CAST(CURRENT_DATE AS VARCHAR(10))` returns `'26/09/05'`, never ISO. This is the silent
one — no error code, the statement succeeds, the key is wrong and the predicate matches nothing.
ALWAYS turn a date into text with `TO_CHAR(d, 'YYYY-MM-DD')` or `CAST(d AS DATE FORMAT 'YYYY-MM-DD')`,
and NEVER by `SUBSTRING` over a plain `CAST(... AS VARCHAR)`. Read the session's setting from
`HELP SESSION` (`Current DateForm`).

## 3. INTERVAL literals — how wide they can be

An INTERVAL literal is sized from its own digits, up to the maximum leading precision of 4.
`INTERVAL '365' DAY`, `INTERVAL '3650' DAY` and `INTERVAL '9999' DAY` all work;
`INTERVAL '10000' DAY` fails with `Error 3706 Syntax error: Invalid INTERVAL Literal`. A wide literal
does not silently overflow — it either computes correctly or raises 3706.

- NEVER attach a precision to a literal: `INTERVAL '365' DAY(4)` fails with `Error 3706 Syntax error:
  expected something between '(' and the integer '4'`. `DAY(4)` is a TYPE qualifier — legal in
  `CAST(x AS INTERVAL DAY(4))` and on a datetime difference (`(ts2 - ts1) DAY(4) TO SECOND`), never on
  a literal.
- The 2-digit default belongs to a DECLARED `INTERVAL DAY` column, parameter or CAST target, not to a
  literal: `CAST(INTERVAL '365' DAY AS INTERVAL DAY)` raises `Error 7453 Interval field overflow`.
  Widen the target — `CAST(INTERVAL '365' DAY AS INTERVAL DAY(4))` returns `365`.
- NEVER add a time INTERVAL to a DATE: `CURRENT_DATE + INTERVAL '120' MINUTE` fails with `Error 5407
  Invalid operation for DateTime or Interval`, because a DATE carries no time fields. Use a TIMESTAMP:
  `CURRENT_TIMESTAMP(0) + INTERVAL '120' MINUTE` works.
- Style, not a repair: prefer integer-day arithmetic (`d - 365`) for day offsets because it is
  portable, and `ADD_MONTHS()` for month offsets because it is month-exact and clamps to month end.

```sql
-- day offset (portable)
WHERE order_date >= CURRENT_DATE - 365
-- month-exact:
WHERE order_date >= ADD_MONTHS(CURRENT_DATE, -12)
```

## 4. The type-adaptive date expression

Pick the wrong form and you get either a loud error (`2666` on a string, `5407` on a TIMESTAMP) or —
on a real DATE — no error at all and a silently wrong bucket. So the column TYPE decides the form.
Check it first (`base_tableDDL`, or `SELECT ColumnType FROM DBC.ColumnsV WHERE DatabaseName = '<db>'
AND TableName = '<t>' AND ColumnName = '<c>'`; DA = DATE, TS = TIMESTAMP, SZ = TIMESTAMP WITH TIME ZONE,
CV/CF = VARCHAR/CHAR).

| Column type | date_expr | The error if you pick the other form |
|---|---|---|
| DATE | `CAST(col AS DATE)`; bucket with `TRUNC(col, 'MM')` or `TO_CHAR(col, 'YYYY-MM')` | `SUBSTRING(col ...)` does NOT error — it returns the DATEFORM rendering (`'26/09/05'`), so the key is wrong and one month splits across buckets |
| TIMESTAMP, TIMESTAMP WITH TIME ZONE | `CAST(col AS DATE)` | `SUBSTRING(col ...)` → `Error 5407 Invalid operation for DateTime or Interval` |
| VARCHAR/CHAR holding `'YYYY-MM-DD HH:MI:SS'` | `TRYCAST(SUBSTRING(col FROM 1 FOR 10) AS DATE)` | `CAST(col AS DATE)` → `Error 2666 Invalid date supplied` (the time part does not parse) |
| VARCHAR/CHAR holding a pure `'YYYY-MM-DD'` | `CAST(col AS DATE FORMAT 'YYYY-MM-DD')` | works without SUBSTRING |
| VARCHAR `'YYYY-MM'` period key | compare as strings: `col >= '2026-01'` | no cast needed; lexicographic order is chronological. Derive the current key with `TO_CHAR(CURRENT_DATE, 'YYYY-MM')`, never by casting a DATE to VARCHAR |

- `TRYCAST` returns NULL where `CAST` would abort the statement on the first unparsable value. Count
  the NULLs (`SUM(CASE WHEN date_expr IS NULL THEN 1 ELSE 0 END)`) and report them; when three
  buckets sum to less than `COUNT(*)`, the cast is producing NULLs and the classification is wrong.
- A string-date filter needs no cast at all when both sides are strings of the same shape:
  `WHERE order_date >= '2026-03-01'` is correct on a `'YYYY-MM-DD HH:MI:SS'` column. To compare a
  string-date column to a DATE-typed expression, cast the STRING side:
  `WHERE CAST(SUBSTRING(str_col FROM 1 FOR 10) AS DATE FORMAT 'YYYY-MM-DD') >= date_col`. NEVER
  `SUBSTRING` the DATE side — it renders per DATEFORM and the predicate silently matches nothing.
- `DBC.ColumnsV` reports no `ColumnType` for a VIEW's columns, and every value arrives as a string over
  the tool. When the type is unknown, PROVE it: `SELECT MAX(CAST(col AS DATE)) FROM <db>.<view>` —
  success means the column is date-like; a column named like a date can hold `1, 2, 3, 4`.
- Never pick the date column by name alone. A substring hint such as `trans` matched `transaction_id`
  in production, the `TRYCAST` came back all-NULL, and the report showed 0/0/0 with no error.

## 5. Anchor relative time to the data

"Last 90 days", "last 6 months", "latest year" are relative to the DATA unless the user says "today".
Historical datasets do not extend to the calendar date; anchoring on `CURRENT_DATE` returns zero rows
or an empty trailing period and the model then invents an explanation.

```sql
-- AS_OF = most recent value in the fact table
WHERE order_date >= (SELECT MAX(order_date) FROM <db>.sales_fact) - 90
-- latest period on an integer-keyed view
WHERE time_year = (SELECT MAX(time_year) FROM <db>.<view>)
-- year-over-year: compare against MAX(time_year) - 1
```

- Issue a `SELECT MAX(<date_expr>)` probe first when you do not know the window; then run the anchored
  query. State the anchor in the answer ("relative to the latest record, 2026-05-10").
- Use `CURRENT_DATE` when the user explicitly means the calendar ("as of today", "this week").
- Bands are half-open so rows are never double-counted: hot `>= as_of - 90`, warm `>= as_of - 365 AND
  < as_of - 90`, cold `< as_of - 365`. Thresholds are the user's policy, not constants.

## 6. Relative phrase → filter (DATE/TIMESTAMP columns, calendar-anchored)

| Phrase | Filter |
|---|---|
| this week | `d >= TD_WEEK_BEGIN(CURRENT_DATE)` |
| last week | `d BETWEEN TD_WEEK_BEGIN(CURRENT_DATE - 7) AND TD_WEEK_BEGIN(CURRENT_DATE) - 1` |
| last 7 / 30 / 90 days | `d >= CURRENT_DATE - 7` / `- 30` / `- 90` |
| this month, MTD | `d >= TRUNC(CURRENT_DATE, 'MM')` |
| last month | `d >= TRUNC(ADD_MONTHS(CURRENT_DATE, -1), 'MM') AND d < TRUNC(CURRENT_DATE, 'MM')` |
| this quarter, QTD | `d >= TRUNC(CURRENT_DATE, 'Q')` |
| last quarter | `d >= TRUNC(ADD_MONTHS(CURRENT_DATE, -3), 'Q') AND d < TRUNC(CURRENT_DATE, 'Q')` |
| this year, YTD | `d >= TRUNC(CURRENT_DATE, 'YYYY')` |
| last year | `EXTRACT(YEAR FROM d) = EXTRACT(YEAR FROM CURRENT_DATE) - 1` |
| year over year | same period, `EXTRACT(YEAR FROM d) - 1` on the comparison side, or `LAG(agg, 12) OVER (ORDER BY month)` |

For `'YYYY-MM'` string period columns: this month `= TO_CHAR(CURRENT_DATE, 'YYYY-MM')`; last month
`= TO_CHAR(ADD_MONTHS(CURRENT_DATE, -1), 'YYYY-MM')`; this year
`>= TO_CHAR(CURRENT_DATE, 'YYYY') || '-01'`. For `'YYYY-Qn'`
columns: `CAST(EXTRACT(YEAR FROM CURRENT_DATE) AS VARCHAR(4)) || '-Q' || CAST(((EXTRACT(MONTH FROM
CURRENT_DATE) + 2) / 3) AS VARCHAR(1))`.

## 7. Bucketing

```sql
-- weekly (typed column)
SELECT TD_WEEK_BEGIN(order_date) AS week_start, COUNT(*) AS cnt
FROM <db>.sales_fact GROUP BY 1 ORDER BY 1;
-- weekly (string timestamp column)
SELECT TD_WEEK_BEGIN(CAST(SUBSTRING(order_ts FROM 1 FOR 10) AS DATE FORMAT 'YYYY-MM-DD')) AS week_start, ...
-- monthly
SELECT TRUNC(order_date, 'MM') AS month_start, ...           -- typed
SELECT SUBSTRING(order_ts FROM 1 FOR 7) AS order_month, ...   -- string, yields 'YYYY-MM'
-- quarterly / yearly
SELECT TRUNC(order_date, 'Q') AS quarter_start, ...
SELECT EXTRACT(YEAR FROM order_date) AS yr, ...
```

- NEVER improvise a week: `EXTRACT(WEEK FROM d)` and `WEEK(d)` fail; `SUBSTRING(d FROM 1 FOR 7)` is a
  month, not a week; `TRUNC(d, 'IW')` is not verified on the systems this plugin was built against.
- Label week buckets "week starting <Sunday>", not "ISO week n".
- A monthly YoY: `LAG(SUM(x), 12) OVER (ORDER BY month_start)`; quarterly `LAG(..., 4)`; yearly `LAG(..., 1)`.
  Use `PARTITION BY EXTRACT(YEAR FROM month_start)` when the comparison must stay inside a year.

## 8. Precision and overflow

- `TIMESTAMP(0)` stores whole seconds. Inserting a value with microseconds fails with `Error 5404
  Datetime field overflow` — the message points at a field, so people hunt for a bad year. Truncate at
  the single insert path, or declare `TIMESTAMP(6)`.
- Reading a microsecond TIMESTAMP from an external (Parquet/Iceberg) source into a `TIMESTAMP(0)`
  target with a direct `CAST(ts AS TIMESTAMP(0))` overflows (`Error 7454`). Bridge it through a
  string: `CAST(CAST(ts AS VARCHAR(19)) AS TIMESTAMP(0))`.
- `TIMESTAMP WITH TIME ZONE` compares in UTC; `CURRENT_TIMESTAMP` carries the session zone. When a
  report must be day-exact across zones, cast both sides to DATE in the same zone (doc).
- A `TIME` or `TIMESTAMP` literal without a zone is interpreted in the session time zone (doc).

## 9. Errors in this area — what the message actually means

| Code | Means | Fix |
|---|---|---|
| 2666 `Invalid date supplied` | The string you cast to DATE is not `YYYY-MM-DD` (it carries a time, or a non-date). | `TRYCAST(SUBSTRING(col FROM 1 FOR 10) AS DATE)`; check for junk values. |
| 5407 `Invalid operation for DateTime or Interval` | A string operation (`SUBSTRING`, `LIKE`, `||`) hit a TIME, TIMESTAMP or INTERVAL, or a time INTERVAL was added to a DATE. On a DATE a string operation does NOT raise this — it succeeds and returns DATEFORM text. | `CAST(col AS DATE)`, `TRUNC`, `TO_CHAR`; add time intervals to a TIMESTAMP, not a DATE. |
| 5404 `Datetime field overflow` | A component does not fit the target: microseconds into `TIMESTAMP(0)`, or out of range. | Truncate; widen the type. |
| 3707 `Syntax error, expected something like ...` | `MONTH()`/`YEAR()`/`DATEDIFF`, comma-form `SUBSTRING`, or a reserved alias (`date`, `month`, `year`, `time`, `zone`, `hour`). | Use `EXTRACT`; `SUBSTRING(... FROM ... FOR ...)`; rename the alias. |
| 7453 `Interval field overflow` | An INTERVAL value did not fit a DECLARED INTERVAL type — a 2-digit-default `INTERVAL DAY` column or CAST target. Not caused by a wide literal. | Widen the target type: `CAST(x AS INTERVAL DAY(4))`. |
| 3706 `Invalid INTERVAL Literal` | The literal has more than 4 leading digits (`INTERVAL '10000' DAY`). A second INTERVAL form of 3706, `expected something between '(' and the integer 'n'`, means a precision was attached to a literal. | Use integer-day math for offsets that large; drop the precision from the literal. |
| 7454 | A datetime value overflowed the target during CAST (external TIMESTAMP(6) → `TIMESTAMP(0)`). | Bridge through `VARCHAR(19)`. |
