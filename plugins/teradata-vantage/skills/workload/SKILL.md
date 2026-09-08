---
name: workload
description: Use when a query is queued rather than slow, when the warehouse appears to be throttling a job, or when the question is about workload management - which TASM/TDWM rules apply, which workload a query landed in, whether service-level goals were met, and how to tag work with a query band so it is attributable later. Diagnosis is read-only; rule changes are handed to the user.
when_to_use: why is my query queued; is the warehouse throttling me; workload management; TASM; TDWM; which workload did my query run in; service level goal; SLG; DelayTime; query band; SET QUERY_BAND; GETQUERYBAND; tag my job so I can find it in DBQL; throttle; concurrency limit; my ETL slows down at the same time every night; rejected query; delayed count.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[why is this queued | rules | workloads | slg <workload> | queryband <app>]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__dba_userDelay
  - mcp__plugin_teradata-vantage_teradata__dba_sessionInfo
  - mcp__plugin_teradata-vantage_teradata__dba_flowControl
  - mcp__plugin_teradata-vantage_teradata__dba_resusageSummary
  - mcp__plugin_teradata-vantage_teradata__base_tableList
---

# Workload management on Teradata

**Slow and queued are different problems with different fixes.** A slow query has a bad plan — that is
the `tune` skill. A queued query has a perfectly good plan and is waiting for a workload-management rule
to let it run. Tuning a queued query changes nothing, and it is the most common wasted afternoon in this
area. Establish which one you have before doing anything else.

The tell is `DelayTime`. If it is significant, the query spent that time waiting, not working.

## Step 0 — is anything actually being delayed?

```sql
SELECT s.WDID,
       TRIM(w.WorkloadName), TRIM(w.WorkloadType),
       SUM(s.Arrivals), SUM(s.Completions), SUM(s.DelayedCount),
       AVG(s.AvgDelayTime), MAX(s.MaxRespTime),
       SUM(s.RejectedCount), SUM(s.AbortCount)
FROM   DBC.TDWMSummaryLog s
LEFT JOIN TDWM.WorkloadInfoV w ON s.WDID = w.WorkloadId
GROUP BY s.WDID, TRIM(w.WorkloadName), TRIM(w.WorkloadType)
ORDER BY 4 DESC;
```

⚠️ **Spell the GROUP BY expressions out.** `GROUP BY 1,2,3` over those `TRIM(...)` columns fails with
`Error 3504 Selected non-aggregate values must be part of the associated group` — measured. The
`teradata-sql` skill covers 3504 generally; this query is where it bites in practice.

⚠️ **The join is mandatory to make the answer readable.** `TDWMSummaryLog` records only `WDID`. A bare
number means nothing to the reader, and the name lives in **`TDWM.WorkloadInfoV`** — the `TDWM` database,
not `DBC`. `DBC.WorkloadInfoV` does not exist (measured: `Error 3807`).

Measured on a healthy system: one workload, `WD-Default` (`TSMed`), 40,227 arrivals, 40,099 completions,
**0 delayed, 0 rejected, 0 aborted**, worst response 22.14s. That is what "not throttled" looks like —
say so plainly rather than hunting for a problem that is not there.

## Step 1 — what rules exist, and what am I subject to

```sql
SELECT TRIM(RulesetName), TRIM(RuleName), TRIM(RuleType), TRIM(RuleStatus)
FROM   TDWM.RuleInfoV
WHERE  TRIM(RuleStatus) = 'E'          -- E = enabled
ORDER BY 3, 2;
```

Measured: 19 enabled rules in ruleset `FirstConfig` — 11 `Utility Session`, 7 `Workload`, 1
`System Throttle`. The three types answer different questions:

| RuleType | Governs | Reads as |
|---|---|---|
| `Workload` | classification into a workload definition and its priority | which WD your query lands in |
| `Throttle` / `System Throttle` | concurrency ceilings | why the Nth concurrent query waits |
| `Utility Session` | how many load/export utilities may run at once | why a FastLoad queued behind another |

The active workload definitions, with their service-level goals:

```sql
SELECT * FROM TABLE(TDWM.TDWMActiveWDs()) AS t;   -- WDId, WDName, Status, SLG, PGID
```

⚠️ **`TDWMActiveWDs` and `TDWMListWDs` are table FUNCTIONS, not views** (`TableKind = 'R'`). Selecting
from one directly fails with `Reference to function ... is not allowed in this context`; wrap it in
`TABLE(...)`. `TDWMListWDs()` additionally requires parameters — `TDWMActiveWDs()` takes none and is the
one to reach for.

`references/tdwm.md` carries the rest of the `TDWM` surface and the exception/event logs.

## Step 2 — query banding, so the next question is answerable

A query band tags a session or transaction with your own key/value pairs, which then travel into DBQL.
It is the difference between "something was slow last night" and "the `nightly` job in `etl` was slow
last night". Fully verified round trip:

```sql
SET QUERY_BAND = 'app=etl;job=nightly;' FOR SESSION;   -- note the trailing semicolon INSIDE the string
SELECT GETQUERYBAND();                                  -- '=S> app=etl;job=nightly;'
SELECT GETQUERYBANDVALUE(0, 'app');                     -- 'etl'
SET QUERY_BAND = NONE FOR SESSION;                      -- clears it
```

Three measured details worth carrying:

- **`GETQUERYBANDVALUE(0, 'key')` returns the value; `GETQUERYBANDVALUE(1, 'key')` returns an empty
  string.** Not an error — nothing. The first argument selects the scope, and `1` is the natural guess.
- **The `=S>` prefix marks a session-level band.** It is part of what `GETQUERYBAND()` returns, so do not
  string-match the raw output expecting only your pairs.
- **A missing key returns `''`, not NULL and not an error.** Test with `= ''`, and never read an empty
  result as "the band was not set".

`FOR TRANSACTION` scopes it to the current transaction instead. `SET QUERY_BAND` is a session setting,
not a write to data — but it is not a `SELECT`, so `base_readQuery` will refuse it. Hand it to the user
or run it through `base_writeQuery` where that tool exists.

## Step 3 — the per-query evidence

- `dba_userDelay(start_date, end_date)` — queries with `DelayTime > 0`. One call.
- `DBC.QryLogV.DelayTime` and `.WDID` — per query, joined back to `TDWM.WorkloadInfoV` for the name.
- `DBC.TDWMExceptionLog` — queries that tripped an exception rule (demoted, aborted, moved).
- `DBC.TDWMEventLog` — state and event changes; measured 55 rows here.
- `dba_flowControl` — flow control is a different, lower-level throttle: the AMPs pushing back on message
  volume, not TASM queuing work. Do not report one as the other.

## The caveat that decides whether any of this means anything

**`DBC.QryLogTDWMV` and `DBC.TDWMExceptionLog` both read 0 on the system measured, because query logging
is off — not because nothing was delayed.** Every DBQL-backed probe here returns zero for every object on
such a system. Confirm logging is enabled before drawing a conclusion, and if it is not, say that the
question cannot be answered from this system yet rather than reporting a reassuring zero.

`DBC.TDWMSummaryLog` is different — it is populated by workload management itself, not by DBQL, and had
2,278 rows on the same system. When DBQL is off, it is the one source that still answers.

## Reporting

- Lead with slow or queued, and the number that decided it.
- **Always name the workload, never just the `WDID`.** Join `TDWM.WorkloadInfoV`.
- Give delayed and rejected counts alongside arrivals — 3 delayed out of 40,000 is not a throttling
  problem, and the ratio is what makes that obvious.
- **Changing a rule is a DBA action, not yours.** Ruleset changes are made through Viewpoint / Workload
  Designer or the `TDWM` stored procedures, they take effect for everyone, and they are `[WRITE]`. State
  the finding and the proposed change; hand over the action.
- If the finding is "nothing is being throttled", say that. It is a real and useful answer.

## References

- `references/tdwm.md` — the `TDWM` database surface, rule and workload views, exception and event logs,
  and the query-band details in full.
