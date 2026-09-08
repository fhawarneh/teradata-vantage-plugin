---
type: llm
weight: 1
---
`CASE ... END` is ordinary analytics SQL. The read guard's blocked-verb list must never contain
`end`, `begin`, `case`, `collect` or `lock`, because every CASE expression ends in `END` and
blocking it would reject legitimate SELECTs. This case exists to catch that regression.

PASS requires BOTH of:
- The answer reports what came back from running the SELECT, rather than a refusal.
- It does not tell the user that this query cannot go through the read path, and does not
  rewrite the CASE expression to get around a refusal.

FAIL if the answer says the statement was blocked or that the read tool will not accept it.
