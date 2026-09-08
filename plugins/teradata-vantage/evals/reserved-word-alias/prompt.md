---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades the SQL you write, not a result set. Do not call any Teradata tool and do not
  look the schema up: give the statement you would run, and say what you corrected and why.
---
This Teradata query fails. Fix it and explain why it failed.

    SELECT customer_id, SUM(amount) AS sum, COUNT(*) AS count
    FROM analytics.sales_fact
    GROUP BY customer_id;
