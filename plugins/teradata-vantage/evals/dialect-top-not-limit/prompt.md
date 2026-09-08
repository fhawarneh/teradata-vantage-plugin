---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades the SQL you write, not a result set. Do not call any Teradata tool and do not
  look the schema up: give the statement you would run, and say what you corrected and why.
---
Show me the top 10 rows of `sales_fact` in the `analytics` database, ordered by `sale_amount`
descending. Use `LIMIT 10`.
