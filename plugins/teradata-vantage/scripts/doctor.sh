#!/usr/bin/env bash
# teradata-vantage plugin — read-only diagnostic report. Prints names, versions and yes/no facts; never a secret.
# Used by /teradata-vantage:setup (as injected context) and by the SessionStart hook (short form: --short).
set -u
# shellcheck source=_plugin_env.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_plugin_env.sh"
tv_resolve_paths "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHORT=0; [ "${1:-}" = "--short" ] && SHORT=1

# Plain scalars, NEVER an associative array: `declare -A` needs bash 4, and macOS ships bash 3.2 as /bin/bash.
D_MODE=""; D_REQ=""; D_SV=""; D_CRED=""; D_URLSRC=""; D_URLHOST=""; D_PY=""; D_VENV=""; D_VSHA=""
D_PROFILE=""; D_CFGDIR=""; D_PROG=""; D_EXTRAS=""; D_EXTRAS_INST=""; D_AUDIT=""; D_ENVFILE=""; D_INPUTS=""
while IFS='=' read -r k v; do
  case "$k" in
    mode)              D_MODE="$v" ;;
    requested_mode)    D_REQ="$v" ;;
    server_version)    D_SV="$v" ;;
    credential_source) D_CRED="$v" ;;
    url_source)        D_URLSRC="$v" ;;
    url_host)          D_URLHOST="$v" ;;
    python)            D_PY="$v" ;;
    venv_installed)    D_VENV="$v" ;;
    venv_sha)          D_VSHA="$v" ;;
    profile)           D_PROFILE="$v" ;;
    config_dir)        D_CFGDIR="$v" ;;
    progressive)       D_PROG="$v" ;;
    extras)            D_EXTRAS="$v" ;;
    extras_installed)  D_EXTRAS_INST="$v" ;;
    audit_log)         D_AUDIT="$v" ;;
    env_file)          D_ENVFILE="$v" ;;
    inputs_present)    D_INPUTS="$v" ;;
  esac
done < <(bash "${ROOT}/scripts/launch-mcp.sh" --dry-run 2>/dev/null)

ver() { command -v "$1" >/dev/null 2>&1 && "$1" "${2:---version}" 2>&1 | head -1 || echo "not installed"; }
PLUGIN_VERSION="$(python3 - "$ROOT/.claude-plugin/plugin.json" <<'PY' 2>/dev/null
import json,sys; print(json.load(open(sys.argv[1])).get("version","?"))
PY
)"

if [ "$SHORT" = 1 ]; then
  echo "teradata-vantage ${PLUGIN_VERSION}: server mode=${D_MODE:-none} (server ${D_SV:-?}), credentials=${D_CRED:-none}, bridge url=${D_URLSRC:-none}${D_URLHOST:+ (${D_URLHOST})}, profile=${D_PROFILE:-?}, writes=$([ "${TERADATA_ALLOW_WRITES:-1}" = 0 ] && echo disabled || echo 'ask-gated'), read-guard=${TERADATA_SQL_GUARD_MODE:-enforce}, sqlfluff=$(command -v sqlfluff >/dev/null 2>&1 && echo yes || echo no)."
  [ "${D_MODE:-none}" = none ] && echo "No server runtime yet: run /teradata-vantage:setup to connect (bridge to an existing server or install the bundled one)."
  exit 0
fi

cat <<EOF
# teradata-vantage doctor

plugin version      : ${PLUGIN_VERSION}
claude code         : $(command -v claude >/dev/null 2>&1 && claude --version 2>/dev/null | head -1 || echo 'not on PATH')
plugin root         : ${ROOT}
plugin data dir     : ${DATA}
bundled server      : teradata-mcp-server ${D_SV:-?} (wheel sha256 in servers/VENDORED.sha256)

## Server launch resolution
mode that will run  : ${D_MODE:-none}   (requested: ${D_REQ:-auto})
credential source   : ${D_CRED:-none}   (environment | plugin-option | file | none)
bridge URL source   : ${D_URLSRC:-none}${D_URLHOST:+   host=${D_URLHOST}}
local venv          : installed=${D_VENV:-0}  pin=${D_VSHA:-n/a}
profile / config dir: ${D_PROFILE:-?} / ${D_CFGDIR:-?}
progressive disclos.: ${D_PROG:-false}   extras: requested=${D_EXTRAS:-none} in-venv=${D_EXTRAS_INST:-none}   audit log: ${D_AUDIT:-off}
credential file     : ${D_ENVFILE:-absent} (${DATA}/teradata.env)
inputs present      : ${D_INPUTS:-none}

## Runtimes on this machine
python >= 3.11      : ${D_PY:-none}$( [ -n "${D_PY:-}" ] && [ "${D_PY:-}" != none ] && printf ' (%s)' "$("${D_PY}" -c 'import sys;print(sys.version.split()[0])' 2>/dev/null)")
node / npx          : $(ver node) / $(command -v npx >/dev/null 2>&1 && echo yes || echo no)   (npx is needed for bridge mode)
uv / uvx            : $(command -v uvx >/dev/null 2>&1 && echo yes || echo no)   (optional, explicit TERADATA_MCP_MODE=uvx only)
docker              : $(command -v docker >/dev/null 2>&1 && echo yes || echo no)   (optional, TERADATA_MCP_DOCKER_IMAGE)
sqlfluff            : $(ver sqlfluff)   (optional; enables .sql linting: pip install sqlfluff)

## Safety switches (this session)
TERADATA_SQL_GUARD_MODE=${TERADATA_SQL_GUARD_MODE:-enforce}   TERADATA_ALLOW_WRITES=${TERADATA_ALLOW_WRITES:-1}   TERADATA_SQL_LINT=${TERADATA_SQL_LINT:-on}

## Next steps
- bridge to a server you already run : set the plugin option mcp_url (or TERADATA_MCP_URL) to its streamable-http URL
- install the bundled server locally : bash "${ROOT}/scripts/install-server.sh"   (~555 MB of pinned dependencies, once)
- add the vector-store tools         : bash "${ROOT}/scripts/install-server.sh" --extras tdvs
                                       (the server_extras option reaches the server, never the installer)
- provide a connection string        : plugin option database_uri, DATABASE_URI, or in YOUR terminal:
                                       bash "${ROOT}/scripts/teradata-vantage-connect"
- then start a new Claude Code session and run /mcp to confirm plugin:teradata-vantage:teradata is connected
EOF
