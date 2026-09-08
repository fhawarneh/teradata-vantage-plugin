# Generating test and synthetic data on Teradata

Teradata has no synthetic-data feature. It does have primitives that generate realistic volume entirely
in-database, which beats loading a fixture file: no client, no load slot, no transfer.

## The row multiplier

The trick is a table that already has enough rows. `Sys_Calendar.CALENDAR` ships with every system and
had **73,414 rows spanning 1900-01-01 to 2100-12-31** on the system measured — both a row source and a
ready-made date spine.

```sql
SELECT COUNT(*), MIN(calendar_date), MAX(calendar_date) FROM Sys_Calendar.CALENDAR;
```

Its columns, which save you all the date arithmetic: `calendar_date`, `day_of_week`, `day_of_month`,
`day_of_year`, `day_of_calendar`, `weekday_of_month`, `week_of_month`, `week_of_year`,
`week_of_calendar`, `month_of_quarter`, `month_of_year`, `month_of_calendar`, `quarter_of_year`,
`quarter_of_calendar`, `year_of_calendar`.

Cross-join it for volume. **Bound both sides explicitly** — an unbounded self cross join is 5.4 billion
rows and fills spool rather than failing politely. This exact statement was run and returns 18,300 rows
(366 days x 50):

```sql
CREATE TABLE <db>.txn_test AS (
  SELECT
    ROW_NUMBER() OVER (ORDER BY c1.calendar_date, c2.day_of_month)  AS txn_id,
    c1.calendar_date                                                AS txn_date,
    CAST(RANDOM(1, 50000) AS DECIMAL(11,2)) / 100                   AS amount,
    'CUST' || TRIM(CAST(RANDOM(1, 5000) AS INTEGER))                AS customer_id,
    CASE RANDOM(1, 4) WHEN 1 THEN 'CARD' WHEN 2 THEN 'CASH'
                      WHEN 3 THEN 'TRANSFER' ELSE 'CHEQUE' END      AS channel
  FROM      Sys_Calendar.CALENDAR c1
  CROSS JOIN (SELECT day_of_month FROM Sys_Calendar.CALENDAR SAMPLE 50) c2
  WHERE     c1.calendar_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
) WITH DATA PRIMARY INDEX (txn_id);
```

Scale by widening the date range or raising the `SAMPLE` on the inner copy: a full year against
`SAMPLE 1000` is 366,000 rows, and two unrestricted years reach the tens of millions.

`RANDOM(lower, upper)` returns an integer in range, inclusive, evaluated per row. Money is best built as
an integer of minor units divided down — dividing two `DECIMAL`s truncates the scale, which is a real
source of drift (see `teradata-sql`).

## Other primitives

- **`TD_BasketGenerator`** — builds item baskets from transaction rows for association-rules work.
  Confirm it exists first with `DBC.FunctionsV`; it is part of the analytics package, which is not
  installed everywhere. `analytics` Rule 0 has the check.
- **`SAMPLE n` / `SAMPLE 0.10`** — shrink a real table into a test one. Almost always more realistic than
  generating from nothing, and it preserves the distribution that matters.

## Make it look real, or do not bother

Uniformly random data hides exactly the problems test data exists to surface:

- **Skew the keys.** Real customer distributions are long-tailed. `RANDOM(1, 5000)` gives every customer
  identical volume and will never reproduce a primary-index skew problem — the class of bug most worth
  catching before production. Weight it: a small `CASE` that sends 80% of rows to 20% of the range.
- **Include nulls deliberately** — `CASE WHEN RANDOM(1,20) = 1 THEN NULL ELSE … END` — because the code
  path that mishandles a null is the one you want exercised.
- **Vary by date.** Month-end and weekday effects catch aggregation bugs flat data never will;
  `day_of_week` and `day_of_month` are right there.

## Three traps, all hit by this project

- **The default character set is LATIN.** A non-ASCII byte in generated *data* fails with
  `Error 6706 The string contains an untranslatable character`, usually surfaced as a batch failure with
  the real cause hidden behind an escape function. An em-dash in a display name is enough. Comments may
  use typography; data may not.
- **`TIMESTAMP(0)` rejects microseconds** with `Error 5404 Datetime field overflow` — which reads like an
  out-of-range year and sends you hunting in the wrong place. Truncate once at the insert path, not at
  each construction site.
- **A child database is carved from its parent's unallocated `PERM`**, so `CREATE DATABASE … AS PERM = n`
  fails with `Error 3541` when the parent has less than `n` free — a message that reads like syntax and
  means "there is no room". Size the parent first; the `health` skill covers it.

## Reporting

- State the row count AND the shape — key distribution, null rate, date range. "10 million rows" alone
  does not tell anyone whether the test was meaningful.
- Generation is a `[WRITE]`. Hand over the `CREATE TABLE … AS`; the read guard denies it.
- Say plainly when generated data cannot answer the question. Performance work on synthetic data with the
  wrong distribution produces a confident number that does not transfer to production.
