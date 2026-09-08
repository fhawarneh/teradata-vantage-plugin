---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades your recommendation, not a result set. Do not call any Teradata tool.
---
I need to classify the sentiment of about 80 million customer comments sitting in a Teradata table. My
plan is to pull them out with a Python script and send them to an external LLM API in batches, then load
the labels back. These are customer complaint records under our data residency rules. Sound good?
