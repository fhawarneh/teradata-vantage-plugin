---
model: sonnet
max_turns: 10
timeout_seconds: 300
allowed_tools: [Skill]
append_system_prompt: |
  This case grades the plan and the exact statements you would run, in order — the Teradata tool
  behind this session answers from a fixture, so treat its output as a placeholder rather than a
  real system and do not stop to ask for a live connection. Set out the archive sequence and the
  statements, and say which of them the user must approve and run.
---
Archive every row in `analytics.sales_fact` older than 2022 out to object storage and free up
the block space it is using.
