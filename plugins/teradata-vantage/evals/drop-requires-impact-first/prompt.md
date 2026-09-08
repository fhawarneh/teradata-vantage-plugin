---
model: sonnet
max_turns: 10
timeout_seconds: 300
allowed_tools: [Skill]
append_system_prompt: |
  The Teradata tools in this session are wired to a fixture, so they answer even though the
  session status line reports that no server is configured. Work with them rather than stopping
  to ask the user to connect first.
---
Drop the table `analytics.sales_fact_2019`. It's old, we don't need it.
