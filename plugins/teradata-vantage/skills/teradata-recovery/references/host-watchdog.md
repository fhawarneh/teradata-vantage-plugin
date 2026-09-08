# Host watchdog, wait-for-logons, and the client-side rules after an outage

Purpose: unattended recovery of a HARDSTOP with `scripts/ops/td_hardstop_watchdog.sh`, the correct post-recovery readiness poll, why the MCP server must be restarted after any outage, and why a vanished host hangs rather than refuses.

## `scripts/ops/td_hardstop_watchdog.sh`

Runs ON the Teradata node as root from cron. It polls `pdestate -a` and does nothing when the engine is healthy (`RUN/READY` or `RUN/STARTED`) or transitional (`START/`, `RESTART/`), so it never interrupts a good startup. When the state is `DOWN/HARDSTOP`, `PDE not accessible`, or empty:

1. If `/var/opt/teradata/tdtemp/PanicLoopDetected` exists, the dump directory is empty and the crash-signature count in `/var/log/messages` has not increased since the last run, it removes the marker and runs `/etc/init.d/tpa start` (protective stop; no full cycle needed).
2. Otherwise it runs the clean cycle: `/etc/init.d/tpa stop`, waits until no `pdemain`/`gtwgnames`/`vprocmanager` process remains, `/etc/init.d/tpa start`.
3. It then waits for `RUN/STARTED` plus `Logons are enabled` (bounded), logs the outcome, and rate-limits itself to one recovery per `RATE_LIMIT_SECS` (default 300) so a real crash loop is not hammered.

Scope caveat: the watchdog covers recovery skill Step 2 only. It cannot clear a FATAL vproc (Step 3) or a startup-recovery crash loop (Step 4); those are latched in the GDO or on disk and need the manual `vprocmanager`/`ctl`/`tpareset` procedure or a restore. A log that shows a recovery attempt every five minutes without a `Logons are enabled` line afterwards is the signal to escalate, and the script writes an explicit `ESCALATE` line when the post-recovery state still reports `DBS state is 0/-1` or `9/2`.

Install on the node:

```bash
install -m 0755 td_hardstop_watchdog.sh /usr/local/bin/td_hardstop_watchdog.sh
printf '%s\n' '*/3 * * * * root /usr/local/bin/td_hardstop_watchdog.sh' > /etc/cron.d/td-watchdog
/usr/local/bin/td_hardstop_watchdog.sh; tail /var/log/td_watchdog.log
```

Knobs (environment, all optional): `TD_WATCHDOG_LOG` (`/var/log/td_watchdog.log`), `RATE_LIMIT_SECS` (300), `STOP_WAIT_SECS` (120, wait for processes to exit), `LOGON_WAIT_SECS` (600, wait for logons), `LOCKFILE`, `MARKER`, `DRY_RUN=1` (log what it would do). Requires `flock` and the standard Teradata paths `/usr/pde/bin` and `/usr/tdbms/bin`, which cron does not have on `PATH`; the script exports them.

Maintenance windows: disable the cron entry (or set `TD_WATCHDOG_DISABLE_FILE` to a path that exists) before a planned `tpa stop`, and re-enable afterwards; a watchdog that restarts the engine three minutes into your maintenance is worse than none, and one left disabled after the window is a silent gap.

## Wait for logons (the correct readiness poll)

After any restart, poll with a fresh connection and a bounded budget; do not use a blocking client such as `bteq`, which hangs on its first call when logons are down. A first boot on a fresh system can take several minutes; a restarted system usually enables logons within a few minutes, during which clients see resets or `Error 444`.

```python
import os, time, teradatasql

deadline = time.time() + int(os.environ.get("LOGON_WAIT_SECS", "1800"))
while time.time() < deadline:
    try:
        with teradatasql.connect(host=os.environ["TD_HOST"], dbs_port=os.environ.get("TD_PORT", "1025"),
                                 user=os.environ["TD_USER"], password=os.environ["TD_PASSWORD"],
                                 logmech=os.environ.get("TD_LOGMECH", "TD2"), connect_timeout=10000) as con:
            con.cursor().execute("SELECT 1")
        print("logons available"); break
    except Exception:          # the driver fails with a long trace, not a clean 'not yet'
        time.sleep(15)
else:
    print("timed out waiting for logons"); raise SystemExit(2)
```

`connect_timeout` is milliseconds. Never hardcode `logmech='TD2'` in a probe that must work on LDAP/Kerberos/JWT sites; a wrong mechanism fails with `Error 8017` on every attempt and looks like a permanent outage.

Wait-or-intervene rule during a long restart: disk-write activity on the node is the progress signal (`1/5 Voting for Transaction Recovery` writes heavily; a quiescent engine writes almost nothing). CPU is misleading (an idle console can show a few percent). If there is no write activity and no logons for more than five minutes with `DBS state 3: quiescent`, it will not self-recover; re-enable logons in `ctl`.

## Restart the MCP server after ANY outage

Pooled connections survive the outage as dead sockets. `pool_pre_ping` does not reliably clear them, and the upstream server has no `pool_recycle`. The fingerprint: MCP tool calls hang or time out at the client's 180 s bound while a fresh direct `teradatasql.connect` is instant. After the engine reports `Logons are enabled`:

- Bundled server (stdio): reconnect it from `/mcp` in Claude Code, or start a new session; the launcher starts a fresh process with a fresh pool.
- Bridge mode: restart the remote `teradata-mcp-server` process you bridged to, then reconnect from `/mcp`.
- A wedged query that survived the outage cannot be cancelled from the server (no server-side statement timeout in this version); end it with `ABORT SESSION` through `base_writeQuery`, which raises the approval prompt.

## A vanished host HANGS rather than refuses

Two network failure modes look like a slow query and are not:

- A listener that completes the TCP handshake and never answers the logon (engine starting, quiescent, or a NAT/port-forward that mishandles the client). The driver's `connect_timeout` bounds only the TCP connect; nothing in `teradatasql` bounds the logon handshake, so the call is uncancellable from Python.
- A firewall that DROPs (rather than rejects) the database port: packets vanish, the client waits for its own timeout, and the only symptom is silence. A REJECT or a closed port shows as `Error 444` immediately; that is the good failure.

Rules for anything that probes a Teradata system:

- ALWAYS set `connect_timeout` (milliseconds) and run the logon in a thread you can abandon (`daemon=True`, `join(timeout)`), never a `ThreadPoolExecutor` (its exit hook joins abandoned workers and wedges the process on shutdown). Cap how many abandoned probes may be in flight.
- Treat "probe failed" as UNKNOWN, not as a proven fault, and distinguish it from a proven fault in the report; but never as healthy either.
- A successful logon is the primary health signal and travels a different path than any host telemetry (SSH, ping). Report the database down only when the logon fails; report the host unreachable only when both fail.
- Emit one line per state change plus a periodic heartbeat so silence is never mistaken for health; prefix lines (`OK`, `ALERT`, `HEARTBEAT`) so a monitor can filter.
- Claude Code's per-server MCP timeout (180 s) is the only client-side bound on a hung tool call; a hung logon inside the server is what it protects against.
