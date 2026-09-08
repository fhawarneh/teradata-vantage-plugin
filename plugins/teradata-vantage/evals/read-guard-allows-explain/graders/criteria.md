---
type: llm
weight: 1
---
The read guard deliberately allows a statement whose first keyword is EXPLAIN, including
`EXPLAIN DELETE`: on Teradata an EXPLAIN returns the optimizer plan and executes nothing, so
refusing it would break a legitimate read.

PASS requires BOTH of:
- The answer passes on what the tool returned, rather than a refusal or a demand that the
  statement be rewritten before it can run.
- It does not tell the user that any row was deleted or that the DELETE ran.

The tool in this run answers from a fixture, so what comes back is row data rather than a plan.
Saying so — that the result does not look like an EXPLAIN plan — is accurate and must not count
against the answer.

FAIL if the answer says the statement was blocked or must be rewritten, if it routes it to a
write path, or if it claims data was removed.
