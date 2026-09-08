---
name: teradata-recovery
description: Use when a Teradata Vantage system will not accept logons, port 1025 is closed or hangs, pdestate shows DOWN/HARDSTOP or "DBS is not running", vprocs are FATAL, the engine is crash-looping, or a rebuilt system lost its DBS Control settings. Four-way triage plus host-level runbooks that require OS access to the Teradata node.
when_to_use: teradata is down; cannot log on; pdestate; HARDSTOP; DBS state is 0/-1; vproc FATAL; FILABT_PRM; crashcode 5148; PanicLoopDetected; tpareset; vprocmanager; dbscontrol; after the rebuild NOS stopped working; restore the database; backup teradata
license: MIT
metadata:
  skill_type: documentation
  category: teradata
  version: "1.0.0"
user-invocable: false
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__dba_databaseVersion
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
---

# Teradata Vantage recovery runbook

**Requires OS access to the Teradata node.** Everything below `Step 0` runs as root (or the Teradata administrative account) on the database node itself, not on the machine running Claude Code. Claude's shell runs locally; unless the operator has given it a working SSH path to the node, hand the commands to the operator verbatim and ask for the output. When a command IS run through the local shell with a Teradata connection configured, the plugin's host-ops hook raises an approval prompt for `dbscontrol`, `tpareset`, `tpa stop|start`, `vprocmanager`, `ctl` and `rm ... PanicLoopDetected`; that prompt is mistake prevention, not a security boundary.

This is a community runbook distilled from measured incidents on Vantage 20.x systems; command paths and state strings are as observed. Mark anything you cannot reproduce on the node as unverified rather than guessing. Content marked "(from Teradata documentation; verify against your release)" was taken from vendor documentation.

## Hard rules

- ALWAYS run Step 0 (the logon test) before touching the node. Most "Teradata is down" reports are space (`2644`/`3541`) or credentials (`8017`) with a perfectly healthy engine; those belong to the `health` skill.
- ALWAYS record the current value before changing anything in `ctl` or `dbscontrol`, and keep the rollback value in the report. Neither utility has an undo; the GDO is the only record.
- NEVER interrupt a transitional state (`START/`, `RESTART/`, `1/1 Initializing DBS Vprocs`, `1/5 Voting for Transaction Recovery`). A restart that is making progress looks slow; a second `tpareset` on top of it is how a slow recovery becomes a crash loop.
- NEVER conclude "disk full" from `df`. Teradata data lives on raw partitions that `df` does not show; a near-empty `/var/opt/teradata` is normal.
- NEVER kill the database processes or the hypervisor process to stop the engine. Stop it with `tpa stop` (and the platform's own graceful stop if the node is a VM). A hard kill mid-write is the documented cause of the on-disk corruption in Step 3.
- Two failures look alike and need OPPOSITE handling: `PanicLoopDetected` (protective stop; remove the marker, start) versus FATAL vproc / `FILABT_PRM` / `5148` (real crash; `vprocmanager` -> `ctl` -> `tpareset`, or restore). Decide which one you have before acting.

## Step 0: can anyone log on? (SQL, no node access)

Run `dba_databaseVersion` (`SELECT InfoKey, InfoData FROM DBC.DBCInfoV;`) or `SELECT 1` through `base_readQuery`.

| Result | Meaning | Go to |
|---|---|---|
| Rows returned | Engine is up and accepting logons. Not a recovery case. | `health` skill: space (`2644`, `3541`), permissions (`3523`, `3524`), sessions, flow control. |
| `Error 8017` | Credentials or `LOGMECH`. Engine is up. | `/teradata-vantage:setup` connection options. |
| `Error 444` / connection refused | Nothing listening on 1025 from where the MCP server runs, or a firewall drops it. Engine may be fine. | Network first: reach the port from the node's own loopback before blaming the engine. |
| Connect accepted, then hangs until the client timeout | Listener up, DBS not answering: starting, quiescent (`DBS state 3`), or wedged. | Step 1 on the node. Set a connect timeout; a vanished host hangs rather than refuses (see `references/host-watchdog.md`). |
| `*** Warning: RDBMS CRASHED OR SESSIONS RESET` from a client, repeatedly | Engine is restarting under you. | Step 3. |

## Step 1: read the engine state on the node

```bash
export PATH=/usr/pde/bin:/usr/tdbms/bin:$PATH
pdestate -a
```

Interpret with `references/pdestate-states.md`. The three branches:

- `PDE state is RUN/STARTED` + `DBS state is 4: Logons are enabled`: healthy. If clients still fail, it is network or credentials.
- `PDE state is DOWN/HARDSTOP` (or `PDE not accessible`): Step 2.
- `PDE state is RUN/READY` with `DBS state is 0/-1: DBS is not running` or `DBS state is 9/2: Config is not op - More than one AMP in a cluster is offline/down`: Step 3. `RUN/READY` means PDE is healthy, not the database.
- `DBS state is 3: quiescent`: logons are disabled by a persistent GDO setting (`Start With Logons = None`). Re-enable in `ctl` (Step 3, last paragraph); nothing else is wrong.

## Step 2: DOWN/HARDSTOP

2a. Check for the panic guard FIRST:

```bash
ls -l /var/opt/teradata/tdtemp/PanicLoopDetected 2>/dev/null
ls /var/opt/teradata/tddump/ | head
grep -cE 'FILABT_PRM|SUTCRASH|crashcode|Data Block' /var/log/messages
```

`PanicLoopDetected` is written after two or more unclean stops within 300 s and stops PdeMain from starting. It is a PROTECTIVE stop, not corruption. If the marker exists, `tddump/` is empty and the crash-signature count is not increasing:

```bash
rm /var/opt/teradata/tdtemp/PanicLoopDetected
/etc/init.d/tpa start
```

Expect `RUN/STARTED` + `Logons are enabled` within about three minutes. No `ctl` or `tpareset` is needed.

2b. Otherwise run the clean cycle. `/etc/init.d/tpa start` alone is NOT enough after a HARDSTOP: it restarts only the PDE Initiator, and a following `tpareset` fails with `PDE not accessible: Cannot access node global mapping`.

```bash
/etc/init.d/tpa stop
sleep 20
pgrep -l 'pdemain|gtwgnames|vprocmanager'      # must print nothing before continuing
/etc/init.d/tpa start
```

Then poll `pdestate -a` until `RUN/STARTED` and `Logons are enabled` (allow several minutes; a first boot can take longer). `references/host-watchdog.md` ships this cycle as a cron watchdog for unattended nodes; it covers this step only.

## Step 3: PDE up, DBS down (FATAL vproc)

Symptoms: `RUN/READY` with `DBS state is 0/-1` or `9/2`; port 1025 closed; load average pinned high (about the vproc count) with near-zero CPU, which is `pdevproc` in uninterruptible sleep and means idle, not busy; `/var/log/messages` shows `SnapKind = FILABT_PRM`, `CrashKind = SUTCRASH_24`, `Alert for VPROC n, state is being set to FATAL-crash limit exceeded`.

`tpa stop` -> `start` does NOT fix this: a FATAL vproc is latched in the GDO and a plain start brings it back FATAL. The verified procedure:

```text
vprocmanager                 # /usr/tdbms/bin, not /usr/pde/bin
  st not                     # list vprocs NOT online; note State and Crash Count for the report
  set 0 online               # one line per FATAL / Down vproc
  set 1 online
  quit
ctl                          # /usr/pde/bin
  screen debug               # abbreviated: sc de
  0=on                       # (0) Start DBS -> On
  wr                         # write the GDO; the change is deferred until the reset
  quit
tpareset -f "recover FATAL vprocs"     # confirms y/n interactively
```

Poll `pdestate -a` through `1/1 Initializing DBS Vprocs` -> `1/5 Voting for Transaction Recovery` -> `4: Logons are enabled`. In `vprocmanager`, `st not` showing `UTILITY / Catchup` is a vproc rebuilding: progress, not a fault. Expect to repeat: one pass often recovers some vprocs and leaves others FATAL; re-run `st not` and repeat for whatever is still offline.

Decisive follow-up: if the recovery runs clean and the same vprocs go FATAL again within seconds or minutes, the corruption is ON DISK and no in-engine procedure will hold. Go to Step 4 with that finding.

`Start With Logons` (field 2 on the same `ctl` screen) is a PERSISTENT GDO setting: `2=none` disables logons on every boot until you set `2=all`, `wr`, and `tpareset`. A system that comes up `DBS state 3: quiescent` after a restore was probably imaged with it set to `None`.

## Step 4: crash loop (5148 / EXP_INSERT on DBC.SW_Event_Log)

Signature: `/var/log/messages` repeats `AMP n crashcode 5148 on DBC.SW_Event_Log in EXP_INSERT` with `FILABT_PRM` + `SUTCRASH_24`; clients see `*** Warning: RDBMS CRASHED OR SESSIONS RESET`. The AMP dies writing the system event log, typically because `DBC` is full (see the `health` skill, 2644) or because blocks on disk are torn (a hard power cut or an OOM-killed hypervisor mid-write).

DECIDE FAST whether any SQL path exists, with one test:

```text
ctl
  screen debug
  2=none                     # Start With Logons = None (record the previous value: usually all)
  wr
  quit
tpareset -f "diagnose crash loop with logons disabled"
```

Watch `grep -c FILABT_PRM /var/log/messages` while it restarts. If the count still climbs with logons DISABLED, the crash is in startup recovery, not on logon: no session can ever be established, so every SQL remedy (`DELETE FROM DBC.<logtable> ALL`, `rcvmanager`, `dbscontrol` changes that need a running DBS) is unreachable, and the only path is a restore from backup or a rebuild (`references/backup-restore.md`). If the count stops climbing, the engine is stable without sessions: re-enable logons (`2=all`, `wr`, `tpareset`), log on immediately and purge the DBC log tables per the `health` skill before the journal fills again.

Do NOT script a purge that waits for a logon window: `bteq` blocks rather than failing fast when logons are down, so an unguarded loop hangs on its first call. Wrap any probe in `timeout`.

## False signals (each has cost real hours)

- `RUN/READY` is not healthy; only `RUN/STARTED` + `Logons are enabled` is.
- A reachable listener is not a working warehouse; only a completed logon plus a returned row is.
- `df` on the node says nothing about Teradata data (raw partitions). `FILABT_PRM` is not proof of disk-full.
- A high load average with idle CPU is D-state vprocs, not load.
- A hung logon is not a slow query: `connect_timeout` bounds only the TCP connect, not the logon handshake. Probe from a thread you can abandon.
- Once logons return, pooled clients stay stale: the MCP server must be restarted/reconnected (`/mcp` for the bundled server). Tool calls timing out while a fresh direct logon is instant is the fingerprint.
- A flag flipped in `dbscontrol` is read by NEW sessions; a pooled connection keeps reporting the old error. Some GDO changes additionally need a `tpareset`.
- A watchdog log that shows a recovery every few minutes with no `Logons are enabled` afterwards is Step 3 or Step 4, not Step 2; the watchdog cannot fix those.

## If the node is a virtual machine (one generic note)

Most "Teradata corruption" on a VM-hosted system is the host's memory management: when the host runs short, the hypervisor process is swapped or killed mid-write and the guest reads back torn blocks as `5148 The Data Block block code or version number is invalid`. Protect the database process from host memory pressure (reserve its memory, exempt it from the host's out-of-memory killer, give the host swap so pressure does not become a kill), stop the VM only through a graceful path that runs `tpa stop` inside the guest first, and keep image-level (cold) backups alongside the logical ones. Hypervisor-specific commands are out of scope here.

## After recovery

1. Poll for logons with a bounded `SELECT 1` loop (`references/host-watchdog.md`), not with a blocking client.
2. Reconnect / restart the MCP server; run the `health` skill; fix the space cause (`2644`, `3541`) that produced the outage, or it recurs.
3. If the system was rebuilt rather than moved, verify every DBS Control field the site depends on (`references/dbscontrol-fields.md`); they do not survive a rebuild.
4. Confirm a backup exists and has been verified by recount (`references/backup-restore.md`). A backup script that has never run is the difference between an inconvenience and a total loss.
5. Write the report: verdict, the state sequence observed, every command run with its rollback value, unknowns.

References: `pdestate-states.md` (state vocabulary), `dbscontrol-fields.md` (the fields lost on rebuild, read/apply/verify), `backup-restore.md` (logical backup script, honesty block, DSA, restore ordering), `host-watchdog.md` (unattended HARDSTOP recovery, wait-for-logons, pool bounce, hang-not-refuse).
