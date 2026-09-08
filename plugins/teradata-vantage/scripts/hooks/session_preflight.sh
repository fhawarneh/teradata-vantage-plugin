#!/usr/bin/env bash
# teradata-vantage plugin — SessionStart hook (matcher: startup|resume).
#
# Runs `scripts/doctor.sh --short` and hands its one-or-two status lines to the session as
# additionalContext: server mode, credential SOURCE (names only, never a value), guard mode,
# whether writes are enabled, pinned server version, sqlfluff presence. It never fails: every
# path exits 0, and when doctor.sh itself is unavailable the hook simply says nothing.
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

cat >/dev/null 2>&1 || true   # drain the hook JSON on stdin; nothing in it is needed here

if [ ! -r "${ROOT}/scripts/doctor.sh" ]; then
  exit 0
fi

STATUS="$(bash "${ROOT}/scripts/doctor.sh" --short 2>/dev/null || true)"
[ -n "${STATUS}" ] || exit 0

command -v python3 >/dev/null 2>&1 || exit 0
printf '%s' "${STATUS}" | python3 -c '
import json, sys
text = sys.stdin.read().strip()
if text:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
' 2>/dev/null || true
exit 0
