---
type: llm
weight: 1
---

PASS requires ALL of:
- The null test is `churn_reason IS NULL`. The SQL must NOT contain `churn_reason = NULL`,
  `churn_reason != NULL` or `churn_reason <> NULL` anywhere.
- The 90-day window is expressed with Teradata-valid date arithmetic — for example
  `created_date >= CURRENT_DATE - 90` or `CURRENT_DATE - INTERVAL '90' DAY` — not with a
  MySQL/Postgres-only function such as `DATE_SUB`, `DATEADD` or `NOW() - INTERVAL '90 days'`.
- The table is qualified as `analytics.customer_dim`.

Credit (not required) if the answer notes that `= NULL` is never true and would silently return
zero rows rather than erroring.
