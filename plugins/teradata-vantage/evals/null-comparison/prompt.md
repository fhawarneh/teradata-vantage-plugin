---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades the SQL you write, not a result set. Do not call any Teradata tool and do not
  look the schema up: give the statement you would run, and say what you corrected and why.
---
Write a query against `analytics.customer_dim` that returns every row where the `churn_reason`
column has no value, for customers created in the last 90 days (`created_date`).
