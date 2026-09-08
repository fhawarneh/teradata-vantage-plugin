#!/bin/bash
# td_hardstop_watchdog.sh - unattended recovery of a Teradata Vantage node from DOWN/HARDSTOP.
# Part of the teradata-vantage community plugin for Claude Code (skills/teradata-recovery).
#
# WHERE IT RUNS: on the Teradata node itself, as root, from cron (every 3 minutes is the tested cadence).
#
# WHAT IT DOES: polls `pdestate -a`.
#   - healthy (RUN/READY | RUN/STARTED) or transitional (START/ | RESTART/): does nothing, never interrupts a startup.
#   - DOWN/HARDSTOP, "PDE not accessible", or empty output:
#       1. if the PanicLoopDetected marker exists, the dump directory is empty and the crash-signature count in
#          /var/log/messages has not increased since the last run -> protective stop: remove the marker, `tpa start`.
#       2. otherwise the clean cycle: `tpa stop` -> wait until no pdemain/gtwgnames/vprocmanager remain -> `tpa start`.
#       3. waits (bounded) for RUN/STARTED + "Logons are enabled" and logs the outcome.
#   Rate-limited to one recovery per RATE_LIMIT_SECS so a real crash loop is not hammered.
#
# WHAT IT CANNOT FIX: a FATAL vproc (PDE RUN/READY with "DBS state is 0/-1" or "9/2") or a startup-recovery crash
# loop. Those are latched in the GDO or on disk; see the teradata-recovery skill (vprocmanager -> ctl -> tpareset,
# or restore). The script writes an ESCALATE line when it sees that state after a recovery attempt.
#
# INSTALL (on the node, as root):
#   install -m 0755 td_hardstop_watchdog.sh /usr/local/bin/td_hardstop_watchdog.sh
#   printf '%s\n' '*/3 * * * * root /usr/local/bin/td_hardstop_watchdog.sh' > /etc/cron.d/td-watchdog
#   /usr/local/bin/td_hardstop_watchdog.sh; tail /var/log/td_watchdog.log
#
# KNOBS (environment, all optional):
#   TD_WATCHDOG_LOG           /var/log/td_watchdog.log
#   RATE_LIMIT_SECS           300   at most one recovery per this many seconds
#   STOP_WAIT_SECS            120   how long to wait for PDE processes to exit after `tpa stop`
#   LOGON_WAIT_SECS           600   how long to wait for "Logons are enabled" after `tpa start`
#   LOCKFILE / MARKER / CRASHCOUNT_FILE   state files under /run
#   TD_WATCHDOG_DISABLE_FILE  /etc/td-watchdog-maintenance   if this path exists the watchdog exits without acting
#   DRY_RUN=1                 log the decision, run nothing

export PATH=/usr/pde/bin:/usr/tdbms/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH

LOG="${TD_WATCHDOG_LOG:-/var/log/td_watchdog.log}"
LOCKFILE="${LOCKFILE:-/run/td_hardstop_watchdog.lock}"
MARKER="${MARKER:-/run/td_hardstop_watchdog.last}"
CRASHCOUNT_FILE="${CRASHCOUNT_FILE:-/run/td_hardstop_watchdog.crashcount}"
DISABLE_FILE="${TD_WATCHDOG_DISABLE_FILE:-/etc/td-watchdog-maintenance}"
RATE_LIMIT_SECS="${RATE_LIMIT_SECS:-300}"
STOP_WAIT_SECS="${STOP_WAIT_SECS:-120}"
LOGON_WAIT_SECS="${LOGON_WAIT_SECS:-600}"
PANIC_MARKER=/var/opt/teradata/tdtemp/PanicLoopDetected
DUMP_DIR=/var/opt/teradata/tddump
CRASH_RE='FILABT_PRM|SUTCRASH|crashcode|Data Block'

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { echo "$(ts) $*" >> "$LOG"; }
run() { if [ "${DRY_RUN:-0}" = 1 ]; then log "  DRY_RUN: $*"; else log "  $*"; "$@" >> "$LOG" 2>&1; fi; }
oneline() { printf '%s' "$1" | tr '\n' ' ' | cut -c1-240; }
crash_count() { grep -cE "$CRASH_RE" /var/log/messages 2>/dev/null || echo 0; }

# Single instance: never stack recoveries.
exec 9>"$LOCKFILE" 2>/dev/null || exit 0
flock -n 9 || exit 0

# Maintenance window: a watchdog that restarts the engine mid-maintenance is worse than none.
if [ -e "$DISABLE_FILE" ]; then
    exit 0
fi

STATE=$(pdestate -a 2>&1)

# Healthy -> nothing to do. Keep the crash baseline fresh so a later increase is measured from a healthy point.
if printf '%s' "$STATE" | grep -qE 'RUN/(READY|STARTED)'; then
    crash_count > "$CRASHCOUNT_FILE" 2>/dev/null
    exit 0
fi
# Mid-boot / transitional -> leave it alone.
if printf '%s' "$STATE" | grep -qE 'START/|RESTART/'; then
    exit 0
fi

# Unhealthy. Rate-limit so a true crash loop is not hammered every cycle.
if [ -f "$MARKER" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$MARKER" 2>/dev/null || echo 0) ))
    if [ "$AGE" -lt "$RATE_LIMIT_SECS" ]; then
        log "UNHEALTHY but recovered ${AGE}s ago (<${RATE_LIMIT_SECS}s) - skip. state: $(oneline "$STATE")"
        exit 0
    fi
fi
touch "$MARKER"
log "UNHEALTHY -> recovering. state: $(oneline "$STATE")"

NOW_CRASH=$(crash_count)
BASE_CRASH=$(cat "$CRASHCOUNT_FILE" 2>/dev/null || echo "$NOW_CRASH")
DUMPS=$(ls -A "$DUMP_DIR" 2>/dev/null | wc -l)
log "  crash signatures: baseline=${BASE_CRASH} now=${NOW_CRASH}; dumps in ${DUMP_DIR}: ${DUMPS}"

# Branch 1: protective stop (PanicLoopDetected with no evidence of a real crash) -> remove marker, start.
if [ -e "$PANIC_MARKER" ] && [ "$DUMPS" -eq 0 ] && [ "$NOW_CRASH" -le "$BASE_CRASH" ]; then
    log "  PanicLoopDetected present, no dumps, no new crash signatures -> protective stop; clearing the marker"
    run rm -f "$PANIC_MARKER"
    run /etc/init.d/tpa start
else
    # Branch 2: clean cycle. A plain `tpa start` on top of a HARDSTOP restarts only the PDE Initiator.
    if [ -e "$PANIC_MARKER" ]; then
        log "  PanicLoopDetected present WITH dumps or new crash signatures -> treating as a real crash, not clearing it yet"
    fi
    run /etc/init.d/tpa stop
    WAITED=0
    while pgrep -x pdemain >/dev/null 2>&1 || pgrep -x gtwgnames >/dev/null 2>&1 || pgrep -x vprocmanager >/dev/null 2>&1; do
        if [ "$WAITED" -ge "$STOP_WAIT_SECS" ]; then
            log "  ESCALATE: PDE processes still present ${STOP_WAIT_SECS}s after tpa stop: $(pgrep -l 'pdemain|gtwgnames|vprocmanager' | tr '\n' ' ')"
            exit 0
        fi
        sleep 5; WAITED=$((WAITED + 5))
    done
    log "  PDE processes gone after ${WAITED}s"
    if [ -e "$PANIC_MARKER" ]; then
        # The clean stop counts as a clean stop; the guard would otherwise refuse to start PdeMain.
        run rm -f "$PANIC_MARKER"
    fi
    run /etc/init.d/tpa start
fi

# Wait (bounded) for logons.
WAITED=0
while [ "$WAITED" -lt "$LOGON_WAIT_SECS" ]; do
    POST=$(pdestate -a 2>&1)
    if printf '%s' "$POST" | grep -q 'RUN/STARTED' && printf '%s' "$POST" | grep -q 'Logons are enabled'; then
        log "  RECOVERED after ${WAITED}s: $(oneline "$POST")"
        crash_count > "$CRASHCOUNT_FILE" 2>/dev/null
        exit 0
    fi
    if printf '%s' "$POST" | grep -qE 'DBS state is (0/-1|9/2)'; then
        log "  ESCALATE: PDE is up but DBS is not (FATAL vproc?) - the tpa cycle cannot fix this; run the vprocmanager -> ctl -> tpareset procedure. state: $(oneline "$POST")"
        exit 0
    fi
    sleep 15; WAITED=$((WAITED + 15))
done
log "  TIMEOUT after ${LOGON_WAIT_SECS}s without 'Logons are enabled'. state: $(oneline "$(pdestate -a 2>&1)")"
if [ "$(crash_count)" -gt "$NOW_CRASH" ]; then
    log "  ESCALATE: crash signatures increased during the restart - possible crash loop; see the recovery skill Step 4"
fi
exit 0
