#!/usr/bin/env bash
# teradata-vantage plugin — MCP server launcher.
#
# stdout is the MCP protocol channel between Claude Code and the server: this script NEVER writes to stdout.
# Everything it says goes to stderr (visible with `claude --debug`). It never prints a connection string.
#
# Resolution ladder (TERADATA_MCP_MODE=auto):
#   1. bridge  — TERADATA_MCP_URL (or the plugin option mcp_url) is set  -> npx mcp-remote@<pinned> <url>
#   2. venv    — the local server installed by scripts/install-server.sh from the vendored wheel
#   3. uvx     — only when TERADATA_MCP_MODE=uvx is set explicitly (resolves from PyPI, pinned version)
#   4. docker  — only when TERADATA_MCP_DOCKER_IMAGE is set
#   5. exit 1 with instructions. The launcher never installs anything by itself.
#
# Usage:  launch-mcp.sh            (called by .mcp.json)
#         launch-mcp.sh --dry-run  (print the resolution as key=value lines on stdout; used by doctor.sh and the
#                                   SessionStart hook; safe: names only, no secrets)
set -u

SERVER_VERSION="0.2.6"
MCP_REMOTE_VERSION="0.8.3"

# shellcheck source=_plugin_env.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_plugin_env.sh"
tv_resolve_paths "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${DATA}/teradata.env"
WHEEL="${ROOT}/servers/teradata_mcp_server-${SERVER_VERSION}-py3-none-any.whl"
VENV="${DATA}/venv"

DRY=0
case "${1:-}" in --dry-run) DRY=1 ;; esac

log() { printf '[teradata-vantage] %s\n' "$*" >&2; }
out() { # dry-run output only (stdout is protocol in normal mode)
  [ "$DRY" = 1 ] && printf '%s\n' "$*"
  return 0
}

# A value is "unset" when empty OR when Claude Code passed the placeholder through unexpanded (`${VAR}`).
val() {
  local v="${!1:-}"
  case "$v" in
    '' ) printf '' ;;
    '${'*'}' ) printf '' ;;
    * ) printf '%s' "$v" ;;
  esac
}

# --- settings file (written by scripts/teradata-vantage-connect; KEY=VALUE lines, mode 0600) --------------------
# Holds the connection string and any optional server setting, because Claude Code only passes a fixed env block to a
# plugin MCP server: explicit environment variables win, then plugin options, then this file, then defaults.
ENV_FILE_KEYS="DATABASE_URI LOGMECH TERADATA_MCP_URL TD_BASE_URL TD_PAT TD_PEM TERADATA_MCP_MODE TERADATA_MCP_PROFILE TERADATA_MCP_EXTRAS TERADATA_MCP_PYTHON TERADATA_MCP_DOCKER_IMAGE TERADATA_MCP_CONFIG_DIR TERADATA_MCP_PROGRESSIVE TERADATA_MCP_AUDIT_LOG TD_POOL_SIZE TD_MAX_OVERFLOW TD_POOL_TIMEOUT DEFAULT_ROW_LIMIT MAX_ROW_LIMIT LOGGING_LEVEL"
load_env_file() {
  [ -r "$ENV_FILE" ] || return 0
  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"; value="${line#*=}"
    case " $ENV_FILE_KEYS " in *" $key "*) ;; *) continue ;; esac
    # file values fill gaps only; explicit environment always wins
    if [ -z "$(val "$key")" ] && [ -n "$value" ]; then
      export "$key=$value"
      FROM_FILE="$FROM_FILE $key"
    fi
  done < "$ENV_FILE"
}

FROM_FILE=""
CRED_SOURCE="none"
URL_SOURCE="none"

# plugin options (userConfig) arrive through .mcp.json as TERADATA_MCP_OPTION_* (${user_config.*} substitution);
# some Claude Code versions also export CLAUDE_PLUGIN_OPTION_<KEY>. Explicit environment always wins.
opt() { # opt NAME -> first non-empty of TERADATA_MCP_OPTION_NAME / CLAUDE_PLUGIN_OPTION_NAME
  local a; a="$(val "TERADATA_MCP_OPTION_$1")"; [ -n "$a" ] && { printf '%s' "$a"; return; }
  val "CLAUDE_PLUGIN_OPTION_$1"
}
if [ -n "$(val DATABASE_URI)" ]; then
  CRED_SOURCE="environment"
elif [ -n "$(opt DATABASE_URI)" ]; then
  export DATABASE_URI="$(opt DATABASE_URI)"; CRED_SOURCE="plugin-option"
fi
if [ -n "$(val TERADATA_MCP_URL)" ]; then
  URL_SOURCE="environment"
elif [ -n "$(opt URL)" ]; then
  export TERADATA_MCP_URL="$(opt URL)"; URL_SOURCE="plugin-option"
elif [ -n "$(opt MCP_URL)" ]; then
  export TERADATA_MCP_URL="$(opt MCP_URL)"; URL_SOURCE="plugin-option"
fi
if [ -z "$(val TERADATA_MCP_EXTRAS)" ]; then
  e="$(opt EXTRAS)"; [ -n "$e" ] || e="$(opt SERVER_EXTRAS)"; [ -n "$e" ] && export TERADATA_MCP_EXTRAS="$e"
fi
load_env_file
case " $FROM_FILE " in *" DATABASE_URI "*) CRED_SOURCE="file" ;; esac
case " $FROM_FILE " in *" TERADATA_MCP_URL "*) URL_SOURCE="file" ;; esac

# --- defaults for the server (plain ${VAR} pass-through in .mcp.json: unset values arrive empty or literal) --------
MODE="$(val TERADATA_MCP_MODE)"; MODE="${MODE:-auto}"
export LOGMECH="${LOGMECH:-}"; [ -n "$(val LOGMECH)" ] || export LOGMECH="TD2"
export PROFILE="$(val TERADATA_MCP_PROFILE)"; [ -n "$PROFILE" ] || export PROFILE="tv_all"
export CONFIG_DIR="$(val TERADATA_MCP_CONFIG_DIR)"; [ -n "$CONFIG_DIR" ] || export CONFIG_DIR="${ROOT}/config"
export DEFAULT_ROW_LIMIT="$(val DEFAULT_ROW_LIMIT)"; [ -n "$DEFAULT_ROW_LIMIT" ] || export DEFAULT_ROW_LIMIT="1000"
export MAX_ROW_LIMIT="$(val MAX_ROW_LIMIT)"; [ -n "$MAX_ROW_LIMIT" ] || export MAX_ROW_LIMIT="50000"
PROG="$(val TERADATA_MCP_PROGRESSIVE)"
case "$(printf '%s' "$PROG" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) export PROGRESSIVE_DISCLOSURE="true" ;; *) export PROGRESSIVE_DISCLOSURE="false" ;; esac
export LOGGING_LEVEL="${LOGGING_LEVEL:-WARNING}"
export MCP_TRANSPORT="stdio"
# never hand the server empty strings for optional settings
for k in TD_BASE_URL TD_PAT TD_PEM TD_POOL_SIZE TD_MAX_OVERFLOW TD_POOL_TIMEOUT TERADATA_MCP_PYTHON TERADATA_MCP_DOCKER_IMAGE TERADATA_MCP_EXTRAS TERADATA_MCP_AUDIT_LOG; do
  if [ -z "$(val "$k")" ]; then unset "$k"; fi
done
AUDIT="$(val TERADATA_MCP_AUDIT_LOG)"
if [ -n "$AUDIT" ]; then
  export HOOKS_MODULE="${ROOT}/scripts/td_server_hooks.py"
  export TERADATA_MCP_AUDIT_LOG="$AUDIT"
fi

# --- discover runtimes (names/versions only) -----------------------------------------------------------------------
find_python() {
  local c
  for c in "$(val TERADATA_MCP_PYTHON)" python3.13 python3.12 python3.11 python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      printf '%s' "$c"; return 0
    fi
  done
  return 1
}
PY_CANDIDATE="$(find_python || true)"
VENV_BIN="${VENV}/bin/teradata-mcp-server"
VENV_OK=0; SHA_OK="n/a"
if [ -x "$VENV_BIN" ]; then
  VENV_OK=1
  if [ -r "${DATA}/VENDORED.sha256" ] && cmp -s "${ROOT}/servers/VENDORED.sha256" "${DATA}/VENDORED.sha256"; then SHA_OK="match"; else SHA_OK="stale"; fi
fi
# What scripts/install-server.sh put in the venv, as opposed to what was asked for. A venv built before the
# marker existed reports "unknown" — NEVER "none", which would be a claim we cannot make.
EXTRAS_REQUESTED="$(val TERADATA_MCP_EXTRAS)"
EXTRAS_INSTALLED=""
if [ -r "${DATA}/INSTALLED_EXTRAS" ]; then
  EXTRAS_INSTALLED="$(tr -d ' \r\n' < "${DATA}/INSTALLED_EXTRAS")"
elif [ "$VENV_OK" = 1 ]; then
  EXTRAS_INSTALLED="unknown"
fi
HAVE_NPX=0; command -v npx >/dev/null 2>&1 && HAVE_NPX=1
HAVE_UVX=0; command -v uvx >/dev/null 2>&1 && HAVE_UVX=1
HAVE_DOCKER=0; command -v docker >/dev/null 2>&1 && HAVE_DOCKER=1
URL="$(val TERADATA_MCP_URL)"
# Host part of the bridge URL, for the status lines. `sed -n …p` prints NOTHING when the pattern does not
# match; the earlier `sed -E s#…#\2#` form printed the pattern space UNCHANGED, so a URL without a scheme —
# `host:8001/mcp/?u=admin:pw@x` — was echoed verbatim into last-launch.txt, the dry-run, the bridge log and,
# through the SessionStart hook, the conversation transcript. Bridge mode is http/https only (that is all
# mcp-remote speaks), so anything else has no host to report and must print a placeholder, never the input.
URL_HOST=""
if [ -n "$URL" ]; then
  URL_HOST="$(printf '%s' "$URL" | sed -nE 's#^[Hh][Tt][Tt][Pp][Ss]?://([^/@]*@)?(\[[^]]+\]|[^]/:?\#]+).*#\2#p')"
  # A URL was configured but no host could be parsed out of it. Report that fact, never the input.
  [ -n "$URL_HOST" ] || URL_HOST="(unparsed)"
fi
IMAGE="$(val TERADATA_MCP_DOCKER_IMAGE)"

# --- choose the mode -------------------------------------------------------------------------------------------------
CHOSEN="none"
case "$MODE" in
  bridge) [ -n "$URL" ] && CHOSEN="bridge" ;;
  venv)   [ "$VENV_OK" = 1 ] && CHOSEN="venv" ;;
  uvx)    [ "$HAVE_UVX" = 1 ] && CHOSEN="uvx" ;;
  docker) [ -n "$IMAGE" ] && [ "$HAVE_DOCKER" = 1 ] && CHOSEN="docker" ;;
  auto|*)
    if   [ -n "$URL" ] && [ "$HAVE_NPX" = 1 ]; then CHOSEN="bridge"
    elif [ "$VENV_OK" = 1 ]; then CHOSEN="venv"
    elif [ -n "$IMAGE" ] && [ "$HAVE_DOCKER" = 1 ]; then CHOSEN="docker"
    fi ;;
esac

# --- docker argv ------------------------------------------------------------------------------------------------------
# ALWAYS configure the container through -e, NEVER by appending flags after the image: the upstream image declares a
# CMD and no ENTRYPOINT, so appended arguments REPLACE the server command. The plugin's profiles (tv_all, tv_readonly,
# tv_analyst, tv_dba) live in ${ROOT}/config, so that directory MUST be mounted and named by CONFIG_DIR — without it
# the server exits at startup with "Profile 'tv_all' not found" and the MCP connection never comes up.
DOCKER_CONFIG_MOUNT="/opt/tv-config"
if [ "$CHOSEN" = docker ]; then
  DOCKER_ARGV=(docker run -i --rm \
    -e DATABASE_URI -e LOGMECH -e PROFILE -e DEFAULT_ROW_LIMIT -e MAX_ROW_LIMIT -e LOGGING_LEVEL \
    -e PROGRESSIVE_DISCLOSURE -e MCP_TRANSPORT=stdio \
    -e TD_BASE_URL -e TD_PAT -e TD_PEM -e TD_POOL_SIZE -e TD_MAX_OVERFLOW -e TD_POOL_TIMEOUT \
    -e "CONFIG_DIR=${DOCKER_CONFIG_MOUNT}" \
    -v "${CONFIG_DIR}:${DOCKER_CONFIG_MOUNT}:ro" \
    "$IMAGE")
fi

# --- names-only launch record for doctor.sh (never contains a value, host names excepted) ------------------------------
record() {
  # The plugin data directory holds the 0600 credential file, so keep it owner-only, and write the launch
  # record owner-only too: it names the configured host and the interpreter path, which is site information
  # even though no credential value is ever written into it.
  mkdir -p "$DATA" 2>/dev/null || return 0
  chmod 700 "$DATA" 2>/dev/null || true
  umask 077
  present=""
  for k in DATABASE_URI TERADATA_MCP_URL TERADATA_MCP_MODE TERADATA_MCP_PROFILE TERADATA_MCP_OPTION_DATABASE_URI TERADATA_MCP_OPTION_URL TERADATA_MCP_OPTION_EXTRAS CLAUDE_PLUGIN_OPTION_DATABASE_URI CLAUDE_PLUGIN_OPTION_MCP_URL CLAUDE_PLUGIN_DATA CLAUDE_PLUGIN_ROOT CLAUDE_PROJECT_DIR TERADATA_MCP_PROBE; do
    if [ -n "$(val "$k")" ]; then present="$present $k"; elif [ -n "${!k:-}" ]; then present="$present $k(literal)"; fi
  done
  {
    echo "ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "mode=$CHOSEN requested_mode=$MODE credential_source=$CRED_SOURCE url_source=$URL_SOURCE url_host=${URL_HOST:-}"
    echo "profile=$PROFILE config_dir=$CONFIG_DIR python=${PY_CANDIDATE:-none} venv_installed=$VENV_OK venv_sha=$SHA_OK"
    echo "inputs_present=${present# }"
  } > "${DATA}/last-launch.txt" 2>/dev/null || true
}
[ "$DRY" = 1 ] || record

# --- dry run: report and stop --------------------------------------------------------------------------------------
if [ "$DRY" = 1 ]; then
  out "mode=$CHOSEN"
  out "requested_mode=$MODE"
  out "server_version=$SERVER_VERSION"
  out "credential_source=$CRED_SOURCE"
  out "url_source=$URL_SOURCE"
  out "url_host=${URL_HOST:-}"
  out "python=${PY_CANDIDATE:-none}"
  out "venv_installed=$VENV_OK"
  out "venv_sha=$SHA_OK"
  out "npx=$HAVE_NPX"
  out "uvx=$HAVE_UVX"
  out "docker=$HAVE_DOCKER"
  out "profile=$PROFILE"
  out "config_dir=$CONFIG_DIR"
  out "progressive=$PROGRESSIVE_DISCLOSURE"
  out "audit_log=$([ -n "$AUDIT" ] && echo on || echo off)"
  out "extras=$EXTRAS_REQUESTED"
  out "extras_installed=$EXTRAS_INSTALLED"
  out "env_file=$([ -r "$ENV_FILE" ] && echo present || echo absent)"
  # names only — which inputs were present (helps settle how options reach the server)
  present=""
  for k in DATABASE_URI TERADATA_MCP_URL TERADATA_MCP_MODE TERADATA_MCP_PROFILE TERADATA_MCP_OPTION_DATABASE_URI TERADATA_MCP_OPTION_URL TERADATA_MCP_OPTION_EXTRAS CLAUDE_PLUGIN_OPTION_DATABASE_URI CLAUDE_PLUGIN_OPTION_MCP_URL CLAUDE_PLUGIN_DATA CLAUDE_PLUGIN_ROOT CLAUDE_PROJECT_DIR TERADATA_MCP_PROBE; do
    if [ -n "$(val "$k")" ]; then present="$present $k"; elif [ -n "${!k:-}" ]; then present="$present $k(literal)"; fi
  done
  out "inputs_present=${present# }"
  [ "$CHOSEN" = docker ] && out "docker_argv=${DOCKER_ARGV[*]}"
  exit 0
fi

# --- run ------------------------------------------------------------------------------------------------------------
case "$CHOSEN" in
  bridge)
    log "bridge mode: connecting to the Teradata MCP server at ${URL_HOST:-(unparsed URL)} via mcp-remote@${MCP_REMOTE_VERSION}"
    exec npx -y "mcp-remote@${MCP_REMOTE_VERSION}" "$URL"
    ;;
  venv)
    if [ -z "$(val DATABASE_URI)" ]; then
      log "no connection string: set the plugin option database_uri, export DATABASE_URI, or run"
      log "  bash ${ROOT}/scripts/teradata-vantage-connect   (in your own terminal; writes ${ENV_FILE})"
      exit 1
    fi
    [ "$SHA_OK" = "stale" ] && log "the plugin's bundled server changed since this venv was built — run /teradata-vantage:setup install"
    MISSING_EXTRAS=""
    [ "$EXTRAS_INSTALLED" = unknown ] && EXTRAS_REQUESTED=""   # nothing to compare against; stay quiet
    # shellcheck disable=SC2046  # deliberate word split on the comma-separated extras list
    for e in $(printf '%s' "$EXTRAS_REQUESTED" | tr -d ' ' | tr ',' ' '); do
      case ",${EXTRAS_INSTALLED}," in *",$e,"*) ;; *) MISSING_EXTRAS="${MISSING_EXTRAS} $e" ;; esac
    done
    if [ -n "$MISSING_EXTRAS" ]; then
      log "requested extras are NOT in this venv:${MISSING_EXTRAS} (installed: ${EXTRAS_INSTALLED:-none})"
      log "  the plugin option server_extras reaches this launcher only — extras are installed by the installer:"
      log "  bash ${ROOT}/scripts/install-server.sh --extras ${EXTRAS_REQUESTED}"
    fi
    log "local mode: teradata-mcp-server ${SERVER_VERSION} from ${VENV} (profile ${PROFILE}, config ${CONFIG_DIR})"
    exec "$VENV_BIN" --mcp_transport stdio --profile "$PROFILE" --config_dir "$CONFIG_DIR"
    ;;
  uvx)
    [ -n "$(val DATABASE_URI)" ] || { log "no connection string (see bash ${ROOT}/scripts/teradata-vantage-connect)"; exit 1; }
    log "uvx mode (explicit): teradata-mcp-server==${SERVER_VERSION} from PyPI"
    exec uvx --from "teradata-mcp-server==${SERVER_VERSION}" teradata-mcp-server --mcp_transport stdio --profile "$PROFILE" --config_dir "$CONFIG_DIR"
    ;;
  docker)
    [ -n "$(val DATABASE_URI)" ] || { log "no connection string (see bash ${ROOT}/scripts/teradata-vantage-connect)"; exit 1; }
    log "docker mode: ${IMAGE} (profile ${PROFILE}, ${CONFIG_DIR} mounted read-only at ${DOCKER_CONFIG_MOUNT})"
    [ -n "$AUDIT" ] && log "the audit log is not available in docker mode (the hooks module is a host path) — use venv mode for TERADATA_MCP_AUDIT_LOG"
    # variables are passed by NAME so values never appear in the process list
    exec "${DOCKER_ARGV[@]}"
    ;;
  *)
    log "no Teradata MCP server runtime is available."
    log "  - to use a server you already run:   set TERADATA_MCP_URL (or the plugin option mcp_url) to its streamable-http URL"
    log "  - to run the bundled server locally:  run /teradata-vantage:setup install"
    log "    (installs teradata-mcp-server ${SERVER_VERSION} from the vendored wheel plus ~555 MB of pinned dependencies"
    log "     into ${DATA}/venv, first run only; needs Python 3.11+ — found: ${PY_CANDIDATE:-none})"
    log "  - then provide a connection string: plugin option database_uri, DATABASE_URI, or"
    log "    bash ${ROOT}/scripts/teradata-vantage-connect"
    exit 1
    ;;
esac
