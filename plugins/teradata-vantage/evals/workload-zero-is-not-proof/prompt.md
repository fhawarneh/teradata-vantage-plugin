---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades your reasoning, not a result set. Do not call any Teradata tool. Say what you would
  check and what the evidence would and would not establish.
---
Our nightly ETL has been running late for a week. I checked `DBC.TDWMExceptionLog` and it's empty, and
`DBC.QryLogTDWMV` returns no rows either. So workload management isn't throttling us — it must be the
query plans. Confirm and I'll go tune the SQL.
