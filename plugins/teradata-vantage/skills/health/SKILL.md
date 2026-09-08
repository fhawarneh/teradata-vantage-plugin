---
name: health
description: Use when asked whether a Teradata Vantage system or database is healthy, how much space is left, why writes fail with 2644 or CREATE DATABASE fails with 3541, who is logged on, whether the system is in flow control, or who holds which rights and roles and why a user cannot see a table. Runs read-only DBA probes and returns a verdict-first report; a probe that fails is UNKNOWN, never healthy.
when_to_use: is teradata healthy; check the database; space left in <db>; DBC is full; no more room in database; 3541 permanent space; who is logged on; sessions; flow control; system health report; audit all databases; why are writes failing; who can read <db>; what roles does <user> have; why can't I see this table; 3523 no access; grant a service account read-only; access rights audit
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[system | dbc | <database> | space | sessions | all-databases]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__dba_databaseVersion
  - mcp__plugin_teradata-vantage_teradata__dba_systemSpace
  - mcp__plugin_teradata-vantage_teradata__dba_databaseSpace
  - mcp__plugin_teradata-vantage_teradata__dba_tableSpace
  - mcp__plugin_teradata-vantage_teradata__dba_sessionInfo
  - mcp__plugin_teradata-vantage_teradata__dba_flowControl
  - mcp__plugin_teradata-vantage_teradata__dba_resusageSummary
  - mcp__plugin_teradata-vantage_teradata__dba_featureUsage
  - mcp__plugin_teradata-vantage_teradata__dba_userDelay
  - mcp__plugin_teradata-vantage_teradata__dba_userSqlList
  - mcp__plugin_teradata-vantage_teradata__dba_tableSqlList
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_databaseList
  - mcp__plugin_teradata-vantage_teradata__sec_userDbPermissions
  - mcp__plugin_teradata-vantage_teradata__sec_rolePermissions
  - mcp__plugin_teradata-vantage_teradata__sec_userRoles
  - Workflow
  - Workflow(teradata-vantage:health-audit)
---

# Teradata Vantage health check

Read-only probes over the bundled `teradata` MCP server (tool names `mcp__plugin_teradata-vantage_teradata__<tool>`), rolled up into a verdict. The `allowed-tools` list above is a per-turn pre-approval for the read-only probes so the report does not stall on permission prompts; it is not what makes the probes safe. Safety comes from the tools being SELECT-only and from the plugin's read guard on `base_readQuery`.

## Hard rules

- ALWAYS lead with one verdict line: `HEALTHY`, `DEGRADED`, `AT-RISK`, `BLOCKED` or `UNKNOWN`, then evidence, then the exact SQL or tool calls you ran, then next actions with `[WRITE]` markers, then unknowns.
- NEVER report `HEALTHY` for a dimension whose probe failed, timed out, or returned NO ROWS — whatever the reason. An empty result is `UNKNOWN`, never zero and never healthy. Two ways it happens without an error: logging is off, and the session lacks rights. Several DBA tools read the rights-filtered `VX` dictionary views (`dba_databaseSpace` reads `DBC.DiskSpaceVX`; `dba_tableSpace` reads the unrestricted `DBC.AllSpaceV` but LEFT JOINs `DBC.TablesVX`, so with `exclude_system='Y'` its `TableKind='T'` predicate silently drops every table the session cannot see). A short list is a rights answer, not a small system. Report it as `UNKNOWN` with the error text verbatim (`Error 3523`, `Error 444`, timeout). A reachable listener is not a working warehouse; only a completed logon plus a returned row set counts.
- NEVER trust the system-wide total to answer a space question about one database. `DBC.DiskSpace` totals can show tens of GB free while the parent a new database is carved from has none (Teradata 3541). Probe the parent. See `references/space-management.md`.
- NEVER run a write from this skill. Everything it proposes that mutates state - the DBC log purge in `references/space-management.md`, `MODIFY DATABASE`, `CREATE DATABASE`, `GRANT` - is a `[WRITE]` next action for a human, not a call you make. Those statements need `base_writeQuery`, which the bundled upstream 0.2.6 server does not register at all; it exists only when you are bridged to a server that adds one, where the plugin's write gate prompts before each. On the bundled server there is NO write path: show the exact statement and hand it to the DBA to run in their own SQL client. `base_readQuery` is held read-only by a hook, so a `DELETE` through it is denied rather than executed - and NEVER switch `TERADATA_SQL_GUARD_MODE` to `audit` to force one through.
- ALWAYS surface Teradata error codes verbatim (`[Error 2644] No more room in database DBC`) and say what the message actually means before proposing a fix.
- Keep numbers exact as returned; do not round GB figures below two decimals; state the unit.

## Probe ladder

Run the probes that the question needs; for "is it healthy" run all of the first group. Prefer the MCP tool when one exists; the SQL and the parameter names shown are the bundled upstream 0.2.6 server's DBA object definitions, so you can fall back to `base_readQuery` if a tool is filtered out by the active profile. A bridged server may define the same tool with different parameters - read its schema before passing one you have not seen there.

### 1. Liveness and version (always first)

`dba_databaseVersion` runs `SELECT InfoKey, InfoData FROM DBC.DBCInfoV;`. Record `VERSION` and `RELEASE`. If this probe fails, every other dimension is `UNKNOWN`; classify the failure:

| Symptom | Actually means | Next |
|---|---|---|
| `Error 444` or "connection refused" | Nothing is listening on the database port from where the server runs, or a firewall drops it | Network / firewall / wrong host. Not a database fault yet. |
| Connect hangs past the client timeout | The listener accepted and never answered; the engine is starting, quiescent, or wedged | If you have OS access to the node, `teradata-recovery`. Otherwise wait and retry; do not stack retries. |
| `Error 8017` | The UserId, Password or Account is invalid (also raised when `LOGMECH` is wrong for the site) | Credentials / `LOGMECH` (TD2 vs LDAP/KRB5/JWT). |
| `Error 3523` on a DBC view | The connected user lacks SELECT on that view | Ask for the right; report the dimension as `UNKNOWN`. |

### 2. Space (system, DBC, databases, tables, skew)

`dba_systemSpace`:

```sql
Select
SUM(CurrentPerm)/1024/1024/1024 AS SpaceUsed_GB
,SUM(MaxPerm)/1024/1024/1024 AS SpaceAllocated_GB
,SUM(CurrentPerm)/ NULLIFZERO (SUM(MaxPerm)) *100 (FORMAT 'zz9.99%') AS Percentage_Used
,SpaceAllocated_GB- SpaceUsed_GB AS FreeSpace_GB
FROM DBC.DiskSpace;
```

⚠️ **Do NOT read the verdict off that `Percentage_Used` column.** It divides two INTEGER-typed sums BEFORE
applying the FORMAT, so the result is integer 0 on any system below 100% full and the FORMAT only decorates the
zero. Measured on Vantage 20.0: the tool returned `Percentage_Used = 0` on a system that was **3.80%** used.
Derive the figure yourself from `SpaceUsed_GB / SpaceAllocated_GB`, or run
`SELECT CAST(100.0*SUM(CurrentPerm)/NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(6,2)) FROM DBC.DiskSpace` through
`base_readQuery`. This applies to `dba_systemSpace` ONLY — `dba_databaseSpace` casts correctly and its
`PercentUsed` is sound.

DBC headroom (the number that predicts a system-wide write outage):

```sql
SELECT CAST(100.0*SUM(CurrentPerm)/NULLIFZERO(SUM(MaxPerm)) AS DECIMAL(6,2)) AS DBC_Pct_Used
FROM DBC.DiskSpaceV WHERE DatabaseName = 'DBC';
```

Thresholds: over 70 percent is `AT-RISK` (act now: the transient journal lives in DBC and every write on the system stops with `[Error 2644]` when it cannot grow); over 85 percent is `DEGRADED` even if writes still succeed. Below 70 is healthy for this dimension.

`dba_databaseSpace(database_name='<db>')` reports ONE database's SpaceAllocated_GB, SpaceUsed_GB, FreeSpace_GB and PercentUsed; `database_name` is REQUIRED and the bundled server has no all-databases mode. Flag over 85 percent red, over 70 percent yellow. To sweep several databases, enumerate with `base_databaseList(scope='user')` and call `dba_databaseSpace` once per database - the `health-audit` workflow does exactly this - or run the `DBC.DiskSpaceV` SQL above.

`dba_tableSpace(database_name='<db>', top_n=10, exclude_system='Y')` lists that one database's largest tables; `database_name` is required here too. `exclude_system` is the STRING `'Y'`/`'N'` (default `'N'`), NOT a boolean: any value other than `'Y'` leaves the whole filter off, which also drops the tool's `TableKind = 'T'` and `TableName <> 'All'` conditions, so the `All` pseudo-row (the database roll-up) ranks first and non-table objects are mixed into the list.

Parent headroom for a specific parent (needed before any `CREATE DATABASE ... FROM <parent>`):

```sql
SELECT CAST((SUM(MaxPerm) - SUM(CurrentPerm))/1e9 AS DECIMAL(10,3)) AS Unallocated_GB
FROM DBC.DiskSpaceV WHERE DatabaseName = '<parent>';
```

Skew (a table can only use the space of its fullest AMP; skew above 20 percent on a large table is a finding):

```sql
SELECT TOP 20 DatabaseName, TableName,
       CAST(SUM(CurrentPerm)/1e9 AS DECIMAL(10,3)) AS Total_GB,
       CAST(100*(1 - AVG(CurrentPerm)/NULLIFZERO(MAX(CurrentPerm))) AS DECIMAL(5,2)) AS Skew_Pct
FROM DBC.AllSpaceV
WHERE TableName <> 'All' AND DatabaseName NOT IN ('DBC','SYSLIB','SYSUDTLIB','TD_SYSFNLIB','SystemFe')
GROUP BY 1, 2
HAVING SUM(CurrentPerm) > 1000000000
ORDER BY Skew_Pct DESC;
```

`DBC.DiskSpaceV`, `DBC.AllSpaceV` and `DBC.TableSizeV` hold one row per AMP; ALWAYS aggregate with `SUM`/`MAX`, never read a single row as the total.

### 3. Sessions

`dba_sessionInfo` runs:

```sql
SELECT UserName, AccountName, SessionNo, DefaultDataBase, LogonDate, LogonTime, LogonSource,
       LogonAcct, CurrentRole, QueryBand, ClientIpAddress, ClientProgramName,
       ClientSystemUserId, ClientInterfaceVersion
FROM DBC.SessionInfoVX
WHERE UserName = :user_name (NOT CASESPECIFIC) or :user_name='*';
```

`DBC.SessionInfoVX` is defined `... FROM DBC.SessionTbl WHERE UserId = TD_AUTHID`, so it returns ONLY the connected user's own sessions, on every account including `DBC`. The `user_name` default of `*` drops the UserName filter INSIDE that self-scoped set; it does not mean everyone, and naming another user returns zero rows. Measured: `DBC.SessionInfoV` showed 17 sessions across two users while `DBC.SessionInfoVX` returned the connected user's 9 and none of the other user's 8.

Whole-system session count for a trend line: `SELECT COUNT(*) FROM DBC.SessionInfoV;` through `base_readQuery` - the unrestricted view is the only one that sees every session, and `dba_sessionInfo` cannot produce this number. Both views carry `R` to `PUBLIC` on a stock system, so this needs no extra right. A count pinned at the site's session limit means logons will queue. A drop to only your own sessions is evidence of a client reset ONLY when it comes from `DBC.SessionInfoV`; through `dba_sessionInfo` that is the normal, always result and is not an incident.

### 4. Flow control, resource usage, delays, features

- `dba_flowControl(start_date, end_date)`: ONE call covering the window (compute the dates yourself, `YYYY-MM-DD`; last 24 hours means yesterday to today). It joins `DBC.RESUSAGESAWT`, `DBC.RESUSAGESVPR`, `DBC.RESUSAGESPMA` and `SYS_CALENDAR.CALENDAR` and returns `FlowControl%`, `CPUBusy`, `CPUEXEC%`, `WAITIO%`, `IDLE%` per interval. Sustained `FlowControl%` above 0 is `DEGRADED`; empty result means ResUsage logging is not enabled for those tables, which is `UNKNOWN`, not healthy.
- `dba_resusageSummary(no_days=7)` for the resource trend. The grouping is FIXED in the tool's SQL (`GROUP BY 1,2,3,4,5,6,7`) - one row per LogDate x hourOfDay x dayOfWeek x workloadType x workloadComplexity x UserName x AppId - and there is no dimension selector; aggregate the returned rows yourself for a daily trend or an hour-of-day heatmap. Every other parameter (`LogDate`, `hourOfDay`, `dayOfWeek`, `workloadType`, `workloadComplexity`, `AppID`, `user_name`) is a FILTER, empty meaning all, so passing one NARROWS the result instead of adding a breakdown. Do not pass a date to set the window; the range is `CURRENT_DATE - no_days` through `CURRENT_DATE`, so `no_days=7` spans 8 calendar dates. Two traps, both silent: `dayOfWeek` filters on the day NAME the tool's SQL projects (`'Monday'`), not the 1-7 code its own parameter description advertises, so `'2'` returns zero rows; and `LogDate` is compared as `CAST(LogDate AS VARCHAR(10)) = :LogDate`, which renders in the session's DATEFORM — the default `IntegerDate` gives `26/09/08`, not the `YYYY-MM-DD` the parameter description advertises — so an ISO date also returns zero rows with no error. Measured on Vantage 20.0. Leave both empty and filter the returned rows yourself.
- `dba_userDelay(start_date, end_date)`: ONE call; rows are queries with `DelayTime > 0` from `DBC.DBQLogTbl` (needs DBQL enabled). Any workload with repeated delay is a TASM/throttle finding.
- `dba_featureUsage(start_date, end_date)`: feature bits from `DBC.DBQLOGTBL` joined to `DBC.QRYLOGFEATURELISTV`; useful to prove whether a feature (for example NOS or columnar) is actually exercised before a change.
- `dba_userSqlList` / `dba_tableSqlList` when a hot user or table needs explaining.

### 5. Blocked-writes triage

If the user reports failing INSERT/UPDATE/CREATE:

1. `[Error 2644] No more room in database DBC` or `... in database <db>`: space. Run the DBC headroom SQL and `dba_databaseSpace` for `<db>`; follow `references/space-management.md`.
2. `Error 3541`: parent space, not syntax. Probe the parent, not the system.
3. `Error 3524` on `CREATE TABLE`: the session's default database is `DBC`; nothing may create there.
4. Logon fails or hangs: not a space problem. Hand off to `teradata-recovery` (needs OS access to the node).

## Access and roles

Three read-only tools answer the access questions, and each answers a different one. Say which question you are
answering before you call one.

| Question | Tool |
|---|---|
| Which rights does user X hold on database Y? | `sec_userDbPermissions` |
| Which roles does user X hold? | `sec_userRoles` |
| What does role R carry? | `sec_rolePermissions` |

- **A right can arrive through a role.** Finding nothing on the direct grants is not proof of no access — check the
  user's roles, then what those roles carry, before telling anyone they cannot see something.
- **Profile gating.** `config/profiles.yml` exposes only `sec_userDbPermissions` under `tv_readonly` and `tv_analyst`;
  all three exist under `tv_all` (the default) and `tv_dba`. If a `sec_` tool is missing, the profile is the reason.
- **`GRANT` and `REVOKE` are writes.** They are `[WRITE]` actions for the human unless you are connected to a server
  that exposes `base_writeQuery`; the bundled 0.2.6 server does not. Where one is available the write gate prompts.
- Catalog SQL for the same questions — `DBC.AllRightsV`, `DBC.RoleMembersV`, `DBC.AllRoleRightsV` — is in
  `../teradata-sql/references/dbc-dictionary.md`. Use `AllRoleRightsV` + `RoleMembersV` for role-derived rights;
  `DBC.UserRightsV` reports only the requesting session's own rights and will mislead you.
- `Error 3523` ("user does not have <right> access to <object>") is an access answer, not a health one: report which
  right is missing on which object, and whether a role would supply it.

## Escalation and scale-out

- Multi-database or system-wide asks ("audit every database", "health report for the whole system", more than a handful of databases): launch the `health-audit` workflow, `Workflow({name: "teradata-vantage:health-audit", args: {...}})`. It discovers databases with `base_databaseList` unless `args.databases` is given, probes in parallel with read-only auditor agents (cap 20 databases, drops logged) and synthesizes one verdict-first report. If the `Workflow` tool is absent in this session, say so and point at the Dynamic workflows setting rather than probing 50 databases inline.
- Invoking this skill ARMS the plugin's `td-health-watch` monitor for the rest of the session, when a credential file and the local server virtualenv both exist in the plugin data directory. It then opens a database session every `TERADATA_MONITOR_INTERVAL` seconds (default 900, floor 60), reads DBC headroom and the active session count, and prints one line only when the ok/warn/critical bucket changes. It is inert in bridge mode and without credentials, never prints hosts, users or SQL, and `TERADATA_MONITORS=0` disables it. Say so if the user should know a recurring connection has started.
- After any Teradata outage or restart, tell the user to reconnect the MCP server (`/mcp` in Claude Code for the bundled server; restart the remote server in bridge mode). Pooled connections stay stale after recovery; the fingerprint is tool calls timing out while a fresh direct logon is instant.

## Report shape

```
VERDICT: AT-RISK  (DBC 74.20% used; sales_db 91.3% used; 2 tables skewed > 40%)

| Dimension | Value | Threshold | Status |
|---|---|---|---|
| Version | 20.00.xx.xx | - | ok |
| DBC perm used | 74.20% | 70 / 85 | at-risk |
| sales_db perm used | 91.3% | 70 / 85 | degraded |
| Flow control (24h) | no rows | - | UNKNOWN: ResUsage logging off |
| Sessions | 41 | - | ok |

SQL run: <exact statements / tool calls>
Next actions:
  1. [WRITE] Purge DBC log tables per references/space-management.md (needs base_writeQuery; absent on the bundled server - hand the statements to the DBA).
  2. MODIFY DATABASE sales_db AS PERM = <bytes>  [WRITE]  after sizing against the parent.
Unknowns: flow control, user delay (DBQL not enabled).
```

Deeper material: `references/space-management.md` (parent/child PERM model, 3541, 2644 purge list, 3524, 3523).
