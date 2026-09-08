---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades your reasoning about dependency analysis, not a result set. Do not call any
  Teradata tool. Explain what you would do and what the result would and would not prove.
---
I want to drop `warehouse.customer_stg`. I ran a dependency check and it came back with zero
dependents, so it is safe to drop, right? Just confirm and give me the DROP.
