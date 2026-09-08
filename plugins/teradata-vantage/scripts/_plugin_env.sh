# teradata-vantage plugin — shared path resolution, sourced by the bash scripts.
#
# Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} in .mcp.json `args`, but an `env` value may reach the process as the
# literal text "${CLAUDE_PLUGIN_DATA}" (observed on Claude Code 2.1.261). Treat empty AND literal placeholders as unset
# and derive sane defaults:
#   ROOT — the plugin directory (parent of scripts/)
#   DATA — ~/.claude/plugins/data/<plugin>-<marketplace> when ROOT is a marketplace cache path
#          (…/plugins/cache/<marketplace>/<plugin>/<version>/); otherwise the plugin was loaded with
#          --plugin-dir, where Claude Code uses <plugin>-inline, so prefer that directory when it
#          exists and fall back to ~/.claude/plugins/data/teradata-vantage.
#
# WHY THE -inline PROBE (measured 2026-09-08): Claude Code exports CLAUDE_PLUGIN_DATA to hooks and to
# the MCP server process, but NOT to a Bash tool call — so doctor.sh invoked from the setup skill hits
# this fallback. Under --plugin-dir the hooks were writing to …/teradata-vantage-inline while doctor.sh
# read …/teradata-vantage, and reported "credentials=none, venv installed=0" for a session whose
# SessionStart line said "mode=venv, credentials=file". Same plugin, same moment, opposite answers.
# Usage:  source "$(dirname "${BASH_SOURCE[0]}")/_plugin_env.sh"; tv_resolve_paths "$(dirname "${BASH_SOURCE[0]}")"

tv_is_unset() { case "${1:-}" in '' | '${'*'}') return 0 ;; *) return 1 ;; esac; }

tv_resolve_paths() {
  local here="$1"
  if tv_is_unset "${CLAUDE_PLUGIN_ROOT:-}"; then
    ROOT="$(cd "$here/.." && pwd)"
  else
    ROOT="${CLAUDE_PLUGIN_ROOT%/}"
  fi
  if tv_is_unset "${CLAUDE_PLUGIN_DATA:-}"; then
    local base="${HOME}/.claude/plugins/data" id="teradata-vantage" rest mkt plg
    case "$ROOT" in
      */plugins/cache/*/*/*)
        rest="${ROOT#*/plugins/cache/}"; mkt="${rest%%/*}"; rest="${rest#*/}"; plg="${rest%%/*}"
        [ -n "$mkt" ] && [ -n "$plg" ] && id="${plg}-${mkt}" ;;
      *)
        # loaded with --plugin-dir: Claude Code names the data dir <plugin>-inline
        [ -d "${base}/${id}-inline" ] && id="${id}-inline" ;;
    esac
    DATA="${base}/${id}"
  else
    DATA="${CLAUDE_PLUGIN_DATA%/}"
  fi
  export CLAUDE_PLUGIN_ROOT="$ROOT" CLAUDE_PLUGIN_DATA="$DATA"
}
