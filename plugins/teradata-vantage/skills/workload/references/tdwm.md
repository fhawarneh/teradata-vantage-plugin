# The TDWM surface — rules, workloads, telemetry and query bands

Everything here was executed against a live Vantage 20.00. Counts are from that one system and will
differ on yours; the shapes and the traps will not.

## Where things live

Two databases, and confusing them costs a `3807`:

| Database | Holds | Measured |
|---|---|---|
| `TDWM` | the rule and workload **definitions** — what the rules ARE | 108 objects |
| `DBC` | the **logs** — what actually happened | 9 TDWM-related views |

`DBC.WorkloadInfoV` **does not exist**. The view is `TDWM.WorkloadInfoV`. This is the single easiest
mistake to make here because every other log in this area is under `DBC`.

## Definitions — the `TDWM` database

```sql
-- every rule, with its state
SELECT TRIM(RulesetName), TRIM(RuleName), TRIM(RuleType), TRIM(RuleStatus)
FROM   TDWM.RuleInfoV ORDER BY 3, 2;

-- the workload definitions a query can be classified into
SELECT RulesetName, WorkloadId, WorkloadName, WorkloadType,
       WorkloadStatus, WorkloadEvalOrder, VirtualPartition, WorkloadGroup
FROM   TDWM.WorkloadInfoV ORDER BY WorkloadEvalOrder;

-- the currently active workloads, WITH their service-level goals
SELECT * FROM TABLE(TDWM.TDWMActiveWDs()) AS t;   -- WDId, WDName, Status, SLG, PGID
```

Useful objects in `TDWM`: `RuleInfoV`, `RuleDefs`, `RuleQualifyCriteria`, `RuleStateValues`,
`StateDefs`, `Exceptions`, `Events`, `EventCombos`, `Actions`, `AllocGroups`, `OpEnvs`,
`ClassificationForRuleV`, `WorkloadInfoV`, `WorkloadPlanEnvV`.

**`TDWMActiveWDs` and `TDWMListWDs` are table functions** (`DBC.TablesV.TableKind = 'R'`), not views.
`SELECT * FROM TDWM.TDWMActiveWDs` fails with `Reference to function 'TDWMActiveWDs' is not allowed in
this context`; wrap it: `SELECT * FROM TABLE(TDWM.TDWMActiveWDs()) AS t`. `TDWMListWDs()` needs
parameters and returns `invalid number or type of parameters` when called bare — prefer `TDWMActiveWDs`.

`WorkloadEvalOrder` is load-bearing: workloads are evaluated in that order and **the first match wins**.
A query landing in an unexpected workload is almost always an earlier rule matching first, not the
expected rule failing.

Measured rule mix: 19 enabled in ruleset `FirstConfig` — 11 `Utility Session`, 7 `Workload`,
1 `System Throttle`.

## Telemetry — the `DBC` logs

| View | Populated by | Reads 0 when |
|---|---|---|
| `DBC.TDWMSummaryLog` | **workload management itself** | genuinely nothing ran |
| `DBC.TDWMEventLog` | state / event changes | no state changes in the window |
| `DBC.TDWMExceptionLog` | exception rules firing | DBQL logging is off, **or** nothing tripped |
| `DBC.QryLogTDWMV` | DBQL | **DBQL logging is off**, or nothing ran |

**That distinction is the most useful thing on this page.** On the measured system DBQL was off, so
`QryLogTDWMV` and `TDWMExceptionLog` both read 0 while `TDWMSummaryLog` had 2,278 rows and
`TDWMEventLog` 55. When DBQL is off, `TDWMSummaryLog` is the only source that still answers — and a zero
from the DBQL-backed views is not evidence of anything.

`TDWMSummaryLog` columns worth knowing: `WDID`, `OpEnvID`, `SysConID`, `StartColTime`, `Arrivals`,
`ActiveCount`, `Completions`, `MinRespTime`, `MaxRespTime`, `AvgRespTime`, `MinCPUTime`, `MaxCPUTime`,
`AvgCPUTime`, **`DelayedCount`**, **`AvgDelayTime`**, `ExceptionAbCount`, `ExceptionMvCount`,
`ExceptionCoCount`, **`MetSLGCount`**, `AbortCount`, `ErrorCount`, **`RejectedCount`**.

The three exception counters distinguish what happened to a query that tripped a rule: `Ab` aborted,
`Mv` moved to another workload, `Co` continued with a changed priority. Reporting "exceptions fired"
without saying which is not an answer.

### SLG compliance

`MetSLGCount` against `Completions` per workload is the compliance ratio. The goal itself comes from
`TDWMActiveWDs().SLG`. Report both — a 90% met rate means nothing without the goal it was measured
against.

## Query bands, in full

```sql
SET QUERY_BAND = 'app=etl;job=nightly;step=load;' FOR SESSION;
SET QUERY_BAND = 'txn=abc;'                       FOR TRANSACTION;
SET QUERY_BAND = NONE                             FOR SESSION;
```

The value is one string of `key=value;` pairs, **each terminated by a semicolon inside the quotes**.
Omitting the final semicolon is the usual syntax error.

Reading it back — all measured:

| Call | Returns |
|---|---|
| `GETQUERYBAND()` | `'=S> app=etl;job=nightly;'` — the `=S>` marks session scope |
| `GETQUERYBANDVALUE(0, 'app')` | `'etl'` |
| `GETQUERYBANDVALUE(1, 'app')` | `''` — **empty, not an error** |
| `GETQUERYBANDVALUE(0, 'absent')` | `''` — empty, not NULL |
| `GETQUERYBAND()` after `NONE` | `''` |

Other functions present: `GETQUERYBANDPAIRS`, `GETQUERYBANDVALUESF`, `MONITORQUERYBAND`,
`QUERYBANDRESERVEDNAMES_TBF`.

**Why it matters beyond tagging:** `DBC.QryLogV.QueryBand` carries the band, so banding is what makes a
pipeline's queries findable afterwards. Set it in the orchestrator — the `pipelines` skill's Airflow and
dbt sections are the natural place — and a whole class of "which job did this" questions becomes a
`WHERE` clause instead of guesswork.

⚠️ `SET QUERY_BAND` does not start with `SELECT`, so the read guard denies it through `base_readQuery`.
It changes session state rather than data, so it is not destructive — but it is still the user's to run,
or `base_writeQuery`'s where that tool is available.

## What this skill will not do

Rule changes go through Viewpoint / Workload Designer, or the `TDWM` stored procedures
(`TDWMCreateWorkload`, `TDWMSetWorkloadAttr`, `TDWMAddWorkloadException`, `TDWMAssignWD`). They are
`[WRITE]`, they change behaviour **for every user on the system**, and a throttle loosened to speed up
one job can destabilise the platform. State the finding, propose the change, and hand it over.
