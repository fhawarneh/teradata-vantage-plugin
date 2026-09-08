#!/usr/bin/env bash
# teradata-vantage plugin — install the bundled Teradata MCP server into the plugin's persistent data directory.
#
# This is the ONLY place the plugin installs software, and it runs only when you ask for it
# (/teradata-vantage:setup install, an explicit TERADATA_MCP_MODE=venv bootstrap, or a `claude --init` Setup hook).
#
# What it does:
#   1. finds a Python >= 3.11 (TERADATA_MCP_PYTHON, python3.13, python3.12, python3.11, python3)
#   2. creates ${CLAUDE_PLUGIN_DATA}/venv
#   3. installs the hash-pinned dependency closure servers/requirements-<ver>[-<extra>].txt (--require-hashes) and the
#      vendored wheel servers/teradata_mcp_server-<ver>-py3-none-any.whl (found via --find-links, hash-verified)
#   4. records servers/VENDORED.sha256 and the extras it installed (INSTALLED_EXTRAS) in the data dir, so the launcher
#      can tell when a plugin update changed the pin and when a requested extra is not actually in the venv
#
# Extras (tdvs, bar) are requested HERE and nowhere else: the plugin option server_extras is delivered to the MCP
# server process, never to this installer, so `--config server_extras=tdvs` alone installs nothing.
#
# Usage: install-server.sh [--quiet] [--extras tdvs,bar] [--force]
set -u

SERVER_VERSION="0.2.6"
# shellcheck source=_plugin_env.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_plugin_env.sh"
tv_resolve_paths "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${DATA}/venv"
WHEEL="${ROOT}/servers/teradata_mcp_server-${SERVER_VERSION}-py3-none-any.whl"

# NEVER read a plugin option here: CLAUDE_PLUGIN_OPTION_* / TERADATA_MCP_OPTION_* reach hook and MCP server
# processes, not this script, so reading one only pretends the option works. --extras or TERADATA_MCP_EXTRAS.
QUIET=0; FORCE=0; EXTRAS="${TERADATA_MCP_EXTRAS:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --quiet) QUIET=1 ;;
    --force) FORCE=1 ;;
    --extras) shift; EXTRAS="${1:-}" ;;
    --extras=*) EXTRAS="${1#--extras=}" ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
say() { [ "$QUIET" = 1 ] || echo "[teradata-vantage] $*"; }
die() { echo "[teradata-vantage] ERROR: $*" >&2; exit 1; }

[ -r "$WHEEL" ] || die "vendored wheel not found at $WHEEL"
[ -r "${ROOT}/servers/requirements-${SERVER_VERSION}.txt" ] || die "pinned requirements not found under ${ROOT}/servers"

find_python() {
  local c
  for c in "${TERADATA_MCP_PYTHON:-}" python3.13 python3.12 python3.11 python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      printf '%s' "$c"; return 0
    fi
  done
  return 1
}
PY="$(find_python || true)"
[ -n "$PY" ] || die "no Python >= 3.11 found (teradata-mcp-server requires it). Install one, or point TERADATA_MCP_PYTHON at an interpreter."

# extras: only tdvs and bar are supported; fs is refused (huge dependency closure + ~100 auto-registered tdml_* tools)
REQ_FILES=("${ROOT}/servers/requirements-${SERVER_VERSION}.txt")
ACCEPTED=""
# Deliberate word split on the comma-separated list. NEVER `read -a` + "${arr[@]}" here: an empty array is an
# unbound variable under `set -u` on bash 3.2, which is what macOS ships as /bin/bash.
# shellcheck disable=SC2046
for e in $(printf '%s' "$EXTRAS" | tr -d ' ' | tr ',' ' '); do
  [ -n "$e" ] || continue
  case "$e" in
    tdvs|bar)
      f="${ROOT}/servers/requirements-${SERVER_VERSION}-${e}.txt"
      [ -r "$f" ] || die "no pinned requirements for extra '$e' (expected $f)"
      # The tdvs closure pins numpy 2.5.2, which requires Python >= 3.12 and publishes no cp311 wheel
      # (verified on PyPI 2026-09-08: requires_python ">=3.12", wheel tags cp312/cp313/cp314/cp315).
      # The core server needs only 3.11, so refuse HERE with the real reason instead of letting pip
      # fail deep inside a hash-pinned resolve with "no matching distribution".
      if [ "$e" = tdvs ] && ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        die "extra 'tdvs' needs Python >= 3.12 (its pinned numpy publishes no 3.11 wheel), but $("$PY" -c 'import sys; print(sys.version.split()[0])') was selected. Point TERADATA_MCP_PYTHON at a 3.12+ interpreter, or install without the tdvs extra — the core server and every non-vector tool work on 3.11."
      fi
      REQ_FILES+=("$f"); ACCEPTED="${ACCEPTED},${e}" ;;
    fs)
      [ "${TERADATA_MCP_ALLOW_FS:-0}" = 1 ] || die "extra 'fs' is not supported by default (it pulls a very large dependency set and registers ~100 extra tools). Set TERADATA_MCP_ALLOW_FS=1 to override at your own risk."
      REQ_FILES+=("${ROOT}/servers/requirements-${SERVER_VERSION}-fs.txt"); ACCEPTED="${ACCEPTED},fs" ;;
    *) die "unknown extra '$e' (supported: tdvs, bar)" ;;
  esac
done

if [ -x "${VENV}/bin/teradata-mcp-server" ] && [ "$FORCE" = 0 ] && [ -r "${DATA}/VENDORED.sha256" ] \
   && cmp -s "${ROOT}/servers/VENDORED.sha256" "${DATA}/VENDORED.sha256" && [ -z "$EXTRAS" ]; then
  say "already installed: teradata-mcp-server ${SERVER_VERSION} in ${VENV} (use --force to reinstall)"
  exit 0
fi

say "installing teradata-mcp-server ${SERVER_VERSION} into ${VENV}"
say "  interpreter: $("$PY" -c 'import sys; print(sys.executable, sys.version.split()[0])')"
say "  this downloads the pinned dependency closure from PyPI (~555 MB on disk for the core server, more with extras);"
say "  first run only. Nothing is modified outside ${DATA}."
mkdir -p "$DATA"
[ -d "$VENV" ] || "$PY" -m venv "$VENV" || die "could not create the virtual environment"
PIP=("${VENV}/bin/python" -m pip)
# NEVER install an unpinned package here. Every requirement below is hash-pinned and every artifact in the closure
# is a wheel, so pip never runs a build backend and the venv's own pip needs no upgrade.
for f in "${REQ_FILES[@]}"; do
  say "  pip install --require-hashes -r $(basename "$f")"
  "${PIP[@]}" install $( [ "$QUIET" = 1 ] && echo --quiet ) --require-hashes --find-links "${ROOT}/servers" -r "$f" \
    || die "dependency install failed (see pip output above). Nothing else was changed."
done
[ -x "${VENV}/bin/teradata-mcp-server" ] || die "install finished but ${VENV}/bin/teradata-mcp-server is missing"
cp "${ROOT}/servers/VENDORED.sha256" "${DATA}/VENDORED.sha256"
# Record the extras this venv has (union with any earlier run), so scripts/launch-mcp.sh can say out loud when a
# requested extra is missing instead of letting the doctor report an extra that was never installed.
PREV_EXTRAS=""; [ -r "${DATA}/INSTALLED_EXTRAS" ] && PREV_EXTRAS="$(tr -d ' \r' < "${DATA}/INSTALLED_EXTRAS" | tr '\n' ',')"
ALL_EXTRAS="$(printf '%s,%s' "$PREV_EXTRAS" "$ACCEPTED" | tr ',' '\n' | grep -v '^$' | sort -u | tr '\n' ',' | sed 's/,$//')"
printf '%s\n' "$ALL_EXTRAS" > "${DATA}/INSTALLED_EXTRAS"
say "installed: $("${VENV}/bin/teradata-mcp-server" --version 2>/dev/null || echo "teradata-mcp-server ${SERVER_VERSION}")"
say "extras in this venv: ${ALL_EXTRAS:-none}  (add one with: bash ${ROOT}/scripts/install-server.sh --extras tdvs)"
say "next: provide a connection string (plugin option database_uri, DATABASE_URI, or bash ${ROOT}/scripts/teradata-vantage-connect), then start a new Claude Code session."
