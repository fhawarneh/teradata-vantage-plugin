# pdestate state vocabulary and what each state requires

Purpose: decode `pdestate -a` output (run on the Teradata node, `/usr/pde/bin`) into one of four actions: leave it alone, HARDSTOP cycle, FATAL-vproc recovery, or crash-loop test. States are as observed on Vantage 20.x nodes; strings from Teradata documentation are marked "(from Teradata documentation; verify against your release)".

## Reading the output

`pdestate -a` prints two lines that matter:

```text
PDE state is RUN/STARTED.
DBS state is 4: Logons are enabled - The system is quiescent
```

PDE (Parallel Database Extensions) is the platform layer; DBS is the database. PDE can be healthy while DBS is dead. Only the pair `RUN/STARTED` + `4: Logons are enabled` is a working system.

## State table

| Output | Layer | Actually means | Action |
|---|---|---|---|
| `PDE state is RUN/STARTED` + `DBS state is 4: Logons are enabled` | both | Fully up. Client failures are network, firewall (Error 444) or credentials (8017). | None. Restart pooled clients if this follows an outage. |
| `PDE state is RUN/READY` | PDE | PDE is up; says nothing about DBS. Read the DBS line. | Decide on the DBS line. |
| `DBS state is 0/-1: DBS is not running` | DBS | Database is down with PDE healthy. On a system that was running, this is FATAL vprocs latched in the GDO. | FATAL-vproc recovery (`vprocmanager` -> `ctl` -> `tpareset`). `tpa stop/start` will not clear it. |
| `DBS state is 9/2: Config is not op - More than one AMP in a cluster is offline/down` | DBS | Enough vprocs are down that the configuration cannot operate. | Same as above; expect more than one pass. |
| `DBS state is 1/1: Initializing DBS Vprocs` | DBS | Startup in progress. | Wait. Do not reset. |
| `DBS state is 1/5: Voting for Transaction Recovery` | DBS | Startup recovery in progress (rollback/rollforward of in-flight work). Can take minutes to much longer on a big journal. | Wait. Disk-write activity on the node is the progress signal, not CPU. |
| `DBS state is 3: quiescent` (logons disabled) | DBS | Up but refusing logons because the GDO says `Start With Logons = None`. Persistent across boots. | `ctl` -> `screen debug` -> `2=all` -> `wr` -> `tpareset`. |
| `PDE state is START/...` or `RESTART/...` | PDE | PDE is booting or resetting. Transitional. | Wait. The watchdog treats these as healthy-in-progress. |
| `PDE state is DOWN/HARDSTOP` | PDE | PDE Initiator is down. Either the panic guard (`PanicLoopDetected`) or an unclean stop. | Check the marker first; else the clean `tpa stop` -> verify -> `tpa start` cycle. A plain `tpa start` leaves it in HARDSTOP. |
| `PDE not accessible: Cannot access node global mapping` | PDE | Utilities cannot reach PDE because it was never (re)started properly, typically after `tpa start` on top of a HARDSTOP, or `tpareset` with PDE down. | Clean HARDSTOP cycle, then retry the utility. |
| Empty output / command not found | tooling | `pdestate` is not on `PATH` (cron), or PDE is so far down the utility cannot report. | `export PATH=/usr/pde/bin:/usr/tdbms/bin:$PATH`; treat as unhealthy for a watchdog. |

## Companion signals on the node

```bash
# crash signatures: compare against a baseline count; ANY increase is a new crash
grep -cE 'FILABT_PRM|SUTCRASH|crashcode|PanicLoopDetected|Data Block' /var/log/messages

# which vprocs are not online, with state and crash count
printf 'st not\nquit\n' | /usr/tdbms/bin/vprocmanager

# processes that must be GONE before 'tpa start' after a stop
pgrep -l 'pdemain|gtwgnames|vprocmanager'

# the panic guard marker and the dump directory (empty dumps + marker = protective stop, not a crash)
ls -l /var/opt/teradata/tdtemp/PanicLoopDetected /var/opt/teradata/tddump/
```

Message fragments and what they actually mean:

| Fragment (`/var/log/messages`) | Actually means |
|---|---|
| `SnapKind = FILABT_PRM` | A file-system-layer abort snapshot was taken. Not, by itself, a disk-full condition; the data partitions are raw and `df` cannot see them. |
| `CrashKind = SUTCRASH_24` | A vproc crashed with a software abort; count increases per crash. |
| `Alert for VPROC n, state is being set to FATAL-crash limit exceeded` | Vproc `n` crashed too many times and is now FATAL in the GDO; it will stay FATAL through `tpa stop/start`. |
| `AMP n crashcode 5148 on DBC.SW_Event_Log in EXP_INSERT` | The AMP died writing the system event log: DBC full, or torn blocks on disk. Run the logons-disabled test before anything else. |
| `5148: The Data Block block code or version number is invalid` | On-disk corruption read back. If recovery holds for seconds and then re-crashes, restore. |
| `PanicLoopDetected` | Two or more unclean stops within 300 s; the daemon refuses to start PdeMain until the marker is removed. Protective. |

## vprocmanager states (`st not`)

| State | Actually means | Action |
|---|---|---|
| `ONLINE` | Working. | none |
| `FATAL` | Crash limit exceeded; latched. | `set <n> online`, then `ctl` `0=on`, `wr`, `tpareset`. |
| `DOWN` / `OFFLINE` | Administratively or after a reset not brought up. | `set <n> online` as above. |
| `UTILITY` / `Catchup` | Rebuilding after being brought online. | Wait; this is progress. |

`vprocmanager` lives in `/usr/tdbms/bin`; `ctl`, `pdestate` and `tpareset` live in `/usr/pde/bin`. A "command not found" is a PATH problem, not a missing feature.

## Sequence of a good restart

`tpareset -f "<reason>"` -> `PDE START/...` -> `RUN/READY` -> `DBS 1/1 Initializing DBS Vprocs` -> `1/5 Voting for Transaction Recovery` -> `RUN/STARTED` + `4: Logons are enabled`. Time it once on your system when healthy so a slow recovery can be told apart from a wedged one; on a small single-node system it is a few minutes, and a first boot after a rebuild is longer.
