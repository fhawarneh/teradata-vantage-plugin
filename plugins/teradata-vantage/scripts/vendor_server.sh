#!/usr/bin/env bash
# teradata-vantage plugin — vendor (or verify) the upstream teradata-mcp-server release wheel.
#
#   vendor_server.sh <version>   fetch the exact PyPI wheel for teradata-mcp-server==<version> (no deps, binary only),
#                                verify its sha256 against the PyPI JSON digest, move it into servers/, refresh
#                                servers/VENDORED.sha256, compile hash-pinned requirements (core, tdvs, bar) with
#                                pip-tools in a temporary venv, and rewrite servers/VENDORED.md.
#   vendor_server.sh --check     verify the wheel in servers/ against VENDORED.sha256 and — when the network is
#                                reachable — against the PyPI digest. Exit 1 on any drift. Offline, the sha file
#                                is the only check (reported as such). Used by CI and validate_plugin.py.
#
# Requirements: bash, curl, python3 (a Python >= 3.11 for the compile step: TERADATA_MCP_PYTHON, python3.13,
# python3.12, python3.11 are tried in that order), sha256sum (or shasum -a 256). Network only for a real vendor run.
# Nothing here touches the user's Claude Code data directory; everything happens under the repository and mktemp.
set -euo pipefail

PACKAGE="teradata-mcp-server"
DIST_NAME="teradata_mcp_server"
PIP_TOOLS_SPEC="pip-tools==7.5.2"        # pinned: the compile step must be reproducible
EXTRAS=("tdvs" "bar")                    # opt-in extras that get their own hash-pinned closure; fs is refused

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd "${HERE}/.." && pwd)"
SERVERS="${PLUGIN}/servers"
SHA_FILE="${SERVERS}/VENDORED.sha256"

log() { printf '[vendor_server] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}

find_python() {
  local c
  for c in "${TERADATA_MCP_PYTHON:-}" python3.13 python3.12 python3.11 python3; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

# PyPI JSON digest for the wheel of <version>; prints the sha256 or nothing when offline / not found.
pypi_wheel_digest() {
  local version="$1" json
  json="$(curl -fsSL --max-time 20 "https://pypi.org/pypi/${PACKAGE}/${version}/json" 2>/dev/null)" || return 1
  printf '%s' "$json" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for u in d.get("urls",[]):
    if u.get("packagetype")=="bdist_wheel":
        print(u["digests"]["sha256"]); break
'
}

# --------------------------------------------------------------------------- --check
do_check() {
  [ -f "$SHA_FILE" ] || die "missing ${SHA_FILE}"
  local rc=0 line digest fname actual version pypi
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    digest="${line%% *}"; fname="${line##* }"
    [ -f "${SERVERS}/${fname}" ] || { log "DRIFT: ${fname} listed in VENDORED.sha256 but missing from servers/"; rc=1; continue; }
    actual="$(sha256_of "${SERVERS}/${fname}")"
    if [ "$actual" != "$digest" ]; then
      log "DRIFT: ${fname} sha256 ${actual} != recorded ${digest}"; rc=1
    else
      log "ok: ${fname} matches VENDORED.sha256"
    fi
    case "$fname" in
      "${DIST_NAME}"-*.whl)
        version="${fname#${DIST_NAME}-}"; version="${version%%-*}"
        if pypi="$(pypi_wheel_digest "$version")" && [ -n "$pypi" ]; then
          if [ "$pypi" = "$actual" ]; then log "ok: ${fname} matches the PyPI digest for ${PACKAGE} ${version}"
          else log "DRIFT: ${fname} sha256 ${actual} != PyPI digest ${pypi}"; rc=1; fi
        else
          log "note: PyPI not reachable — verified against VENDORED.sha256 only"
        fi
        ;;
    esac
  done < "$SHA_FILE"
  # every wheel present must be listed
  local w
  for w in "${SERVERS}"/*.whl; do
    [ -e "$w" ] || continue
    grep -q " $(basename "$w")\$" "$SHA_FILE" || { log "DRIFT: $(basename "$w") is not listed in VENDORED.sha256"; rc=1; }
  done
  [ "$rc" = 0 ] && log "check passed" || log "check FAILED"
  return "$rc"
}

# --------------------------------------------------------------------------- vendor <version>
do_vendor() {
  local version="$1"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([a-z0-9.+-]*)?$ ]] || die "version must look like 0.2.6 (got '${version}')"
  local py; py="$(find_python)" || die "no Python >= 3.11 found (set TERADATA_MCP_PYTHON)"
  command -v curl >/dev/null 2>&1 || die "curl is required"
  log "python: ${py} ($("$py" --version 2>&1))"

  local tmp; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  log "downloading ${PACKAGE}==${version} (wheel only, no dependencies) into ${tmp}"
  "$py" -m pip download "${PACKAGE}==${version}" --no-deps --only-binary=:all: -d "$tmp/dl" --quiet \
    || die "pip download failed"
  local wheel; wheel="$(ls "$tmp"/dl/${DIST_NAME}-*.whl 2>/dev/null | head -1)"
  [ -n "$wheel" ] || die "no wheel downloaded"
  local actual; actual="$(sha256_of "$wheel")"
  local pypi; pypi="$(pypi_wheel_digest "$version")" || die "could not read the PyPI JSON digest for ${version}"
  [ -n "$pypi" ] || die "PyPI JSON has no wheel digest for ${version}"
  [ "$actual" = "$pypi" ] || die "sha256 mismatch: downloaded ${actual}, PyPI says ${pypi}"
  log "wheel $(basename "$wheel") sha256 ${actual} — matches PyPI"

  # compile hash-pinned requirement closures in a throw-away venv
  log "creating temporary venv for ${PIP_TOOLS_SPEC}"
  "$py" -m venv "$tmp/venv" || die "venv creation failed"
  "$tmp/venv/bin/python" -m pip install --quiet --upgrade pip "$PIP_TOOLS_SPEC" || die "pip-tools install failed"
  local req_core="${tmp}/requirements-${version}.txt"
  printf '%s==%s\n' "$PACKAGE" "$version" > "$tmp/core.in"
  log "compiling requirements-${version}.txt (core)"
  "$tmp/venv/bin/pip-compile" --quiet --allow-unsafe --generate-hashes --strip-extras \
      --output-file "$req_core" "$tmp/core.in" || die "pip-compile (core) failed"
  local extra
  for extra in "${EXTRAS[@]}"; do
    printf '%s[%s]==%s\n' "$PACKAGE" "$extra" "$version" > "$tmp/${extra}.in"
    log "compiling requirements-${version}-${extra}.txt"
    "$tmp/venv/bin/pip-compile" --quiet --allow-unsafe --generate-hashes --strip-extras \
        --output-file "${tmp}/requirements-${version}-${extra}.txt" "$tmp/${extra}.in" || die "pip-compile (${extra}) failed"
  done

  # install into servers/
  mkdir -p "$SERVERS"
  local old
  for old in "${SERVERS}"/${DIST_NAME}-*.whl "${SERVERS}"/requirements-*.txt; do
    [ -e "$old" ] && { log "removing $(basename "$old")"; rm -f "$old"; }
  done
  cp "$wheel" "${SERVERS}/"
  cp "$req_core" "${SERVERS}/"
  for extra in "${EXTRAS[@]}"; do cp "${tmp}/requirements-${version}-${extra}.txt" "${SERVERS}/"; done
  printf '%s  %s\n' "$actual" "$(basename "$wheel")" > "$SHA_FILE"
  log "wrote ${SHA_FILE}"

  # upstream tag commit (optional, network)
  local commit
  commit="$(curl -fsSL --max-time 20 "https://api.github.com/repos/Teradata/${PACKAGE}/git/ref/tags/v${version}" 2>/dev/null \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["object"]["sha"])' 2>/dev/null || true)"
  write_vendored_md "$version" "$(basename "$wheel")" "$actual" "${commit:-unknown}"
  log "done: ${PACKAGE} ${version} vendored. Now regenerate scripts/data/mcp_tools.yaml (render_references.py --capture),"
  log "      bump SERVER_VERSION in scripts/launch-mcp.sh and scripts/install-server.sh, and run validate_plugin.py."
}

write_vendored_md() {
  local version="$1" wheel="$2" sha="$3" commit="$4" today; today="$(date -u +%Y-%m-%d)"
  cat > "${SERVERS}/VENDORED.md" <<EOF
# Vendored server: teradata-mcp-server ${version}

One-line purpose: provenance record for the upstream MCP server wheel bundled in this directory (unmodified).

| Field | Value |
|---|---|
| Package | \`${PACKAGE}\` (MIT) — https://github.com/Teradata/${PACKAGE} |
| Version | ${version} (upstream tag \`v${version}\`, commit \`${commit}\`) |
| Wheel | \`${wheel}\` |
| sha256 | \`${sha}\` (identical to the PyPI digest at https://pypi.org/pypi/${PACKAGE}/${version}/json) |
| Fetched | ${today}, \`pip download ${PACKAGE}==${version} --no-deps --only-binary=:all:\` |
| Requirements | \`requirements-${version}.txt\` (core), \`requirements-${version}-tdvs.txt\`, \`requirements-${version}-bar.txt\` — \`pip-compile --allow-unsafe --generate-hashes --strip-extras\` (${PIP_TOOLS_SPEC}) |
| Python | upstream requires >= 3.11 |

## Extras policy

- **core** — installed by default (\`/teradata-vantage:setup install\`).
- **tdvs** — Enterprise Vector Store tools; opt-in (\`server_extras\` option / \`TERADATA_MCP_EXTRAS=tdvs\`). Large closure.
- **bar** — DSA backup tools; opt-in. Needs \`DSA_BASE_URL\` or \`DSA_HOST\`/\`DSA_PORT\`.
- **fs** — refused by default: very large dependency closure and ~100 dynamically registered \`tdml_*\` tools that
  are not covered by \`scripts/data/mcp_tools.yaml\` or by the plugin's hook matchers.

## Re-vendoring

\`\`\`bash
bash scripts/vendor_server.sh <new version>   # fetch, verify against PyPI, compile requirements, rewrite this file
bash scripts/vendor_server.sh --check         # CI drift check (sha file; PyPI when reachable)
\`\`\`

Then: regenerate \`scripts/data/mcp_tools.yaml\` from a live \`tools/list\`, bump \`SERVER_VERSION\` in
\`scripts/launch-mcp.sh\` and \`scripts/install-server.sh\`, run \`scripts/validate_plugin.py\`, note the change in CHANGELOG.md.
EOF
  log "wrote ${SERVERS}/VENDORED.md (edit the upstream-PR section by hand if it was customised)"
}

# --------------------------------------------------------------------------- main
case "${1:-}" in
  --check) do_check ;;
  -h|--help|'') sed -n '2,15p' "${BASH_SOURCE[0]}"; [ -n "${1:-}" ] || exit 2 ;;
  *) do_vendor "$1" ;;
esac
