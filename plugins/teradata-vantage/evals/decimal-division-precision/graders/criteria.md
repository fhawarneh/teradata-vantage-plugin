---
type: llm
weight: 1
---
A Teradata division takes the WIDER input's scale and rounds the quotient to it — measured on
Vantage 20.0: `DECIMAL(9,1) 2/3` returns `0.7`, and `DECIMAL(18,2) / INTEGER` therefore returns
only two decimal places no matter how wide the operands are. Widening the operands for overflow
headroom does NOT change that; only casting to FLOAT, or to a DECIMAL whose SCALE the answer
chooses, keeps more digits. The user has asked for four.

If the answer offers more than one statement, judge the one it recommends.

PASS requires ALL of:
- The recommended statement makes the QUOTIENT carry at least four decimals: `CAST(... AS FLOAT)`,
  or a `CAST` to a DECIMAL whose SCALE is stated (`DECIMAL(18,4)` or wider). Widening only the
  PRECISION for overflow headroom — `DECIMAL(38,2)`, `BIGINT` — does not satisfy this, because the
  quotient still comes back at scale 2.
- The divisor is protected against zero: `NULLIF`, `NULLIFZERO` or an equivalent CASE guard.
- `GROUP BY region` is present and the select list agrees with it.
- The explanation attributes the cast to Teradata's division SCALE rule, not to overflow headroom
  and not to style. Mentioning overflow as well is fine; offering it as the only reason is not.

Rewriting `AVG(a/b)` as `SUM(a)/SUM(b)`, or the reverse, is a modelling choice rather than a
defect; do not fail the answer for it.

FAIL if the quotient can only carry two decimals, if the divide-by-zero guard is missing, or if
the answer tells the user the cast is optional.
