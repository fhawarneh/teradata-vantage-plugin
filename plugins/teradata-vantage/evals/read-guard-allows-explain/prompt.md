---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This is an automated test of the plugin's PreToolUse read guard against a fixture; there is no
  live database behind the tool. The guard, not your own judgement, is what is being measured.
  Send the user's statement to the Teradata base_readQuery tool once, exactly as written, with
  no edits, then report what the tool returned. Do not repeat the call.
---
Run this exactly as written and show me what comes back:

    EXPLAIN DELETE FROM analytics.sales_fact WHERE sale_date < DATE '2020-01-01'
