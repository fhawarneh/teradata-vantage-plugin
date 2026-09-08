---
type: llm
weight: 1
---

The user has concluded "not throttled" from two empty DBQL-backed views and wants confirmation before
spending effort on tuning. The response must not confirm it.

PASS requires ALL of:
- It does NOT accept the conclusion that workload management has been ruled out.
- It identifies that both views named are populated by query logging, so an empty result means "nothing
  was logged" — which is what you see when DBQL logging is disabled — rather than "nothing was delayed".
- It names at least one source that does not depend on DBQL for this question. Credit any of:
  `DBC.TDWMSummaryLog` (populated by workload management itself, and carrying delay counters such as
  DelayedCount / AvgDelayTime), or checking whether query logging is enabled at all before drawing any
  conclusion from these views.

Also credit, but do not require: distinguishing a queued query from a slow one; noting that DelayTime is
the field that separates them; suggesting the workload be named rather than reported as a bare WDID.

FAIL if the response confirms workload management is ruled out, or advises going straight to tuning on
this evidence, or treats the two empty views as positive evidence of anything.

Recommending tuning as one branch AFTER establishing whether logging is on is a PASS. Recommending it
INSTEAD of establishing that is a FAIL.
