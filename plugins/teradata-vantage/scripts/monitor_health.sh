#!/usr/bin/env bash
# teradata-vantage plugin - background health watch (monitors/monitors.json: td-health-watch).
#
# Armed the first time the `health` skill is dispatched. Every TERADATA_MONITOR_INTERVAL seconds (default 900) it runs two
# read-only dictionary queries through the locally installed server's teradatasql driver and prints ONE line only when a
# bucket changes:
#   DBC headroom : ok (<=70% used) | warn (>70%) | critical (>85%)
#   sessions     : low (< TERADATA_MONITOR_SESSION_WARN, default 25) | elevated | high (>= TERADATA_MONITOR_SESSION_CRIT, default 100)
#   reachability : unreachable when the probe itself fails (connection, logon, or permission error)
#
# It reads credentials ONLY from the plugin data directory file written by scripts/teradata-vantage-connect:
#   <plugin data dir>/teradata.env  (~/.claude/plugins/data/<plugin>-<marketplace>/teradata.env; resolved by
#   scripts/_plugin_env.sh, or taken from $1 when the monitor is started with the data directory as an argument)
# No file, no Python in the local venv, or TERADATA_MONITORS=0  ->  exits 0 silently. Nothing is ever installed or written.
# It never prints host names, user names, connection strings, error text or SQL - only the bucket line.
#
# Usage: monitor_health.sh [--dry-run] [<plugin data dir>]   (--dry-run prints the resolved paths and exits)
set -u
# shellcheck source=_plugin_env.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_plugin_env.sh"

[ "${TERADATA_MONITORS:-1}" = 0 ] && exit 0

DRY=0
case "${1:-}" in --dry-run) DRY=1; shift ;; esac
# ALWAYS resolve the data directory through _plugin_env.sh: the plugin writes teradata.env to
# ~/.claude/plugins/data/<plugin>-<marketplace>, so a hard-coded ~/.claude/plugins/data/teradata-vantage
# reads a directory that does not exist and the monitor dies silently.
tv_resolve_paths "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# An explicit argument wins, unless Claude Code passed the placeholder through unexpanded.
tv_is_unset "${1:-}" || DATA="${1%/}"
ENV_FILE="${DATA}/teradata.env"
PY="${DATA}/venv/bin/python"
INTERVAL="${TERADATA_MONITOR_INTERVAL:-900}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=900 ;; esac
[ "$INTERVAL" -lt 60 ] && INTERVAL=60
SESS_WARN="${TERADATA_MONITOR_SESSION_WARN:-25}"; case "$SESS_WARN" in ''|*[!0-9]*) SESS_WARN=25 ;; esac
SESS_CRIT="${TERADATA_MONITOR_SESSION_CRIT:-100}"; case "$SESS_CRIT" in ''|*[!0-9]*) SESS_CRIT=100 ;; esac

if [ "$DRY" = 1 ]; then
  # names and yes/no only - never a credential, a host or a user name
  printf 'data_dir=%s\nenv_file=%s\nenv_file_present=%s\nvenv_python=%s\ninterval=%s\n' \
    "$DATA" "$ENV_FILE" "$([ -r "$ENV_FILE" ] && echo yes || echo no)" "$([ -x "$PY" ] && echo yes || echo no)" "$INTERVAL"
  exit 0
fi

[ -r "$ENV_FILE" ] || exit 0
[ -x "$PY" ] || exit 0

# Only the two keys the probe needs are read from the file; the values stay in this process and are passed by name.
URI="$(grep -E '^DATABASE_URI=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
LM="$(grep -E '^LOGMECH=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
[ -n "$URI" ] || exit 0
export TD_MON_URI="$URI" TD_MON_LOGMECH="${LM:-TD2}" TD_MON_SESS_WARN="$SESS_WARN" TD_MON_SESS_CRIT="$SESS_CRIT"
unset URI LM

# One probe: prints "<space-bucket> <space-pct> <session-bucket> <session-count>" or "unreachable - - -". Never prints the error.
probe() {
  "$PY" - <<'PY' 2>/dev/null
import os, sys, urllib.parse
try:
    import teradatasql
    u = urllib.parse.urlsplit(os.environ["TD_MON_URI"])
    kw = dict(host=u.hostname, dbs_port=u.port or 1025,
              user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
              logmech=os.environ.get("TD_MON_LOGMECH", "TD2"), connect_timeout=15000)
    db = (u.path or "/").lstrip("/")
    if db:
        kw["database"] = db
    warn = int(os.environ.get("TD_MON_SESS_WARN", "25")); crit = int(os.environ.get("TD_MON_SESS_CRIT", "100"))
    with teradatasql.connect(**kw) as con:
        cur = con.cursor()
        cur.execute("SELECT SUM(CurrentPerm), SUM(MaxPerm) FROM DBC.DiskSpaceV WHERE DatabaseName = 'DBC'")
        cur_perm, max_perm = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM DBC.SessionInfoV")
        sessions = int(cur.fetchone()[0])
    pct = (float(cur_perm or 0) / float(max_perm)) * 100.0 if max_perm else None
    if pct is None:
        space = "unknown"; pct_s = "-"
    else:
        space = "critical" if pct > 85 else ("warn" if pct > 70 else "ok"); pct_s = "%d" % round(pct)
    sess = "high" if sessions >= crit else ("elevated" if sessions >= warn else "low")
    print(space, pct_s, sess, sessions)
except Exception:
    print("unreachable - - -")
    sys.exit(0)
PY
}

prev_space=""; prev_sess=""; prev_pct=""; prev_count=""
while :; do
  read -r space pct sess count <<< "$(probe)"
  space="${space:-unreachable}"
  if [ "$space" = "unreachable" ]; then
    if [ "$prev_space" != "unreachable" ]; then
      echo "teradata: health probe unreachable (connection, logon or permission error) - see /teradata-vantage:setup"
    fi
    prev_space="unreachable"; prev_sess=""
  else
    if [ "$space" != "$prev_space" ]; then
      if [ -n "$prev_pct" ] && [ "$prev_space" != "unreachable" ]; then
        echo "teradata: DBC perm space ${pct}% used (${space}, was ${prev_pct}% ${prev_space}) - see /teradata-vantage:health"
      else
        echo "teradata: DBC perm space ${pct}% used (${space}) - see /teradata-vantage:health"
      fi
    fi
    if [ "$sess" != "$prev_sess" ]; then
      if [ -n "$prev_count" ] && [ -n "$prev_sess" ]; then
        echo "teradata: active sessions ${count} (${sess}, was ${prev_count} ${prev_sess}) - see /teradata-vantage:health"
      else
        echo "teradata: active sessions ${count} (${sess}) - see /teradata-vantage:health"
      fi
    fi
    prev_space="$space"; prev_pct="$pct"; prev_sess="$sess"; prev_count="$count"
  fi
  sleep "$INTERVAL"
done
