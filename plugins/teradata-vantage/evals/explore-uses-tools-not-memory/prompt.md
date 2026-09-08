---
model: sonnet
max_turns: 8
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  The Teradata tools in this session are wired to a fixture, so they answer even though the
  session status line reports that no server is configured. Use them rather than asking the user
  to connect first.
---
What columns does the table `analytics.sales_fact` have, and what are their data types?
