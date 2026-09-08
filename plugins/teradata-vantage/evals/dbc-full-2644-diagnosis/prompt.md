---
model: sonnet
max_turns: 8
timeout_seconds: 240
allowed_tools: [Skill]
append_system_prompt: |
  This case grades your diagnosis and the remedy you propose, not a result set, and you are not
  connected to the system in question. Do not call any Teradata tool. Name the SQL you would run
  to confirm it, and hand any statement that writes to the user.
---
Every INSERT on our Teradata system started failing this morning with:

    [Error 2644] No more room in database DBC

The failing statements are in our own databases, not in DBC, and SELECTs still work fine.
Nothing changed in the application. What is going on and how do we fix it?
