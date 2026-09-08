---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades the SQL you write, not a result set. Do not call any Teradata tool and do not
  look the schema up: give the statement you would run, and say what you corrected and why.
---
Compute the average order value per region from `analytics.sales_fact`, dividing
`total_amount` (DECIMAL(18,2)) by `order_count` (INTEGER), grouped by `region`.
It feeds a finance report that needs four decimal places on the result.
