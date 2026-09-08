---
name: setup
description: Use when connecting Claude Code to Teradata Vantage through this plugin - diagnose the MCP server launch (doctor), install the bundled teradata-mcp-server, store credentials safely, choose a tool profile, customize the server with your own tools/cubes, or fix "no Teradata tools", /mcp connection failures, 3524, 8017 and 444 errors.
when_to_use: set up teradata; connect to teradata; teradata doctor; install the teradata mcp server; /mcp shows the teradata server failed; no teradata tools; DATABASE_URI; LOGMECH; switch to tv_readonly; customize the mcp server; add a cube or glossary; audit log for tool calls; why is base_readQuery blocked
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[doctor|install|connect|profile <name>|customize]"
allowed-tools:
  - Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.sh":*)
  - Read
  - Grep
  - mcp__plugin_teradata-vantage_teradata__dba_databaseVersion
  - mcp__plugin_teradata-vantage_teradata__base_databaseList
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
---

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.sh" || true`

# Teradata Vantage plugin - setup

The block above is the live doctor report for this machine (names, versions and yes/no facts only - it never
prints a connection string). Read it before anything else; re-run it at any time with
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.sh"`. Everything below is a community plugin for the
open-source `teradata-mcp-server` (0.2.6, MIT); it is not affiliated with or endorsed by Teradata Corporation.

`$ARGUMENTS` selects a mode: `doctor`, `install`, `connect`, `profile <name>`, `customize`. With no argument,
run the guided path (section 1 through 5, stopping at the first step that is not satisfied).

## 0. How the server gets started (read the doctor's "mode that will run")

`.mcp.json` launches `scripts/launch-mcp.sh`, which resolves ONE of these, in order, at session start:

| mode | when | what runs |
|---|---|---|
| `bridge` | `TERADATA_MCP_URL` or plugin option `mcp_url` is set and `npx` exists | `npx mcp-remote@0.8.3 <url>` to a teradata-mcp-server you already run over streamable-http |
| `venv` | `/teradata-vantage:setup install` has built `${CLAUDE_PLUGIN_DATA}/venv` | the bundled 0.2.6 wheel, stdio |
| `uvx` | only when `TERADATA_MCP_MODE=uvx` is set explicitly | `uvx --from teradata-mcp-server==0.2.6` (PyPI) |
| `docker` | only when `TERADATA_MCP_DOCKER_IMAGE` is set | `docker run -i --rm <image>` |
| `none` | nothing above applies | exit with instructions; NOTHING is installed silently |

ALWAYS re-check with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/launch-mcp.sh" --dry-run` after changing an
environment variable. Environment reaches the server only at process start: after any change, tell the user to
start a new Claude Code session (or `/mcp` -> reconnect) before testing.

## 1. `doctor` - interpret the report

- `mode that will run: none` -> go to section 2 (install) or set a bridge URL (section 3, "bridge").
- `credential source: none` with mode `venv` -> go to section 3. Bridge mode needs no credential here (the
  remote server holds its own).
- `local venv: installed=1 pin=stale` -> the plugin was updated and the bundled server changed; re-run install.
- `python >= 3.11: none` -> the local server cannot be installed; point `TERADATA_MCP_PYTHON` at an interpreter
  (3.11+), or use bridge mode.
- `sqlfluff: not installed` -> optional; the `.sql` lint hook stays silent. `pip install sqlfluff` enables it.
- Safety switches line -> explain per `references/safety-model.md`. Default is `enforce` + writes `ask-gated`.

## 2. `install` - the bundled server (explicit, once)

Disclose BEFORE running: `install-server.sh` creates `${CLAUDE_PLUGIN_DATA}/venv` with a Python 3.11+ and
downloads the hash-pinned dependency closure from PyPI - about **555 MB on disk, first run only** (more with extras).
Nothing outside the plugin data directory is modified. It is the only place this plugin installs software.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-server.sh"                 # core server
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-server.sh" --extras tdvs    # + Enterprise Vector Store tools (~170 MB more)
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-server.sh" --extras tdvs,bar
```

The `tdvs` extra needs **Python 3.12 or newer** — its pinned `numpy` publishes no 3.11 wheel — while the core
server and every non-vector tool run on 3.11. On 3.11 the installer refuses that extra by name and says so; point
`TERADATA_MCP_PYTHON` at a 3.12+ interpreter, or install without it.

- Extras: `tdvs` (vector store; also needs `TD_BASE_URL`), `bar` (DSA backup; needs `DSA_BASE_URL` or
  `DSA_HOST`+`DSA_PORT`). `fs` is refused by default (very large dependency set, ~100 extra tools).
- Ask the user before running it (it downloads). Run it in the foreground; it is idempotent (`--force` rebuilds).
- If the doctor shows `python >= 3.11: none`, do not run it - fix the interpreter first.

## 3. `connect` - credentials (three paths, never through chat)

NEVER ask for a password in chat and NEVER paste a `teradata://user:password@...` URI into the conversation -
it lands in the transcript. Choose one:

1. **Plugin option** `database_uri` (stored by Claude Code, marked sensitive): `/plugin manage` -> teradata-vantage
   -> options. Or at install time: `claude plugin install teradata-vantage@teradata-plugins --config database_uri=...`
   (this puts the URI in shell history - prefer the UI or path 3).
2. **Environment**: export `DATABASE_URI` (and `LOGMECH` if not TD2) in the shell that launches Claude Code.
3. **Credential file, written by the helper in the USER'S OWN terminal.** Claude's Bash tool is non-interactive, so
   the helper refuses to run from a tool call by design. Print this exact command with the plugin root from the
   doctor's `plugin root` line and let the user run it themselves:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/teradata-vantage-connect"          # prompts host/port/user/password (echo off)/database/LOGMECH
bash "${CLAUDE_PLUGIN_ROOT}/scripts/teradata-vantage-connect" --show   # print the file with the password redacted
bash "${CLAUDE_PLUGIN_ROOT}/scripts/teradata-vantage-connect" --test   # logon test with the local server's driver
bash "${CLAUDE_PLUGIN_ROOT}/scripts/teradata-vantage-connect" --url http://127.0.0.1:8001/mcp/   # bridge instead
```

It writes `${CLAUDE_PLUGIN_DATA}/teradata.env` (mode 0600) and URL-encodes special characters for you.
Precedence when several are present: environment > plugin option > file (the file only fills gaps).

**Bridge instead of local**: if a teradata-mcp-server already runs over streamable-http (its URL usually ends in
`/mcp/`), set the plugin option `mcp_url` or `TERADATA_MCP_URL`, or use `--url` above. Requires `npx`.

**Working database must not be DBC.** The URI path (`.../1025/<database>`) is the session default database.
Nothing - not even the `dbc` user - can `CREATE TABLE` in DBC, so a URI ending in `/dbc` makes every CTAS,
volatile-to-permanent copy, vector-store create and analytic-function scratch table fail with
`Error 3524 The user does not have CREATE TABLE access to database DBC`. Point it at a working database.

## 4. Verify (after a NEW session)

1. `/mcp` must list `plugin:teradata-vantage:teradata` as connected.
2. Call `mcp__plugin_teradata-vantage_teradata__dba_databaseVersion` (profiles `tv_all`/`tv_dba`), or run this
   through `mcp__plugin_teradata-vantage_teradata__base_readQuery` (any profile):

```sql
SELECT InfoKey, InfoData FROM DBC.DBCInfoV;
```

3. `mcp__plugin_teradata-vantage_teradata__base_databaseList` should return user databases. An empty list on a
   fresh system is normal; a tool error is not - go to `references/troubleshooting.md`.
4. Report: server mode, credential source (name only), Teradata VERSION/RELEASE rows, profile, guard mode. Never
   echo the URI, host or user beyond what the doctor already printed.

## 5. `profile <name>` - which tools are exposed

The launcher passes `--profile ${TERADATA_MCP_PROFILE:-tv_all}` and `--config_dir ${CLAUDE_PLUGIN_ROOT}/config`
(the plugin's `config/profiles.yml`). Curated profiles:

| profile | exposes | use it for |
|---|---|---|
| `tv_all` (default) | every tool, prompt and resource (test prompts excluded) | day-to-day; the hooks still guard writes |
| `tv_readonly` | `base_*` minus `base_writeQuery`/`base_dynamicQuery`, `qlty_*`, `sec_userDbPermissions`, `graph_*`, `plot_*` | shared or production systems where the agent must not be able to write at all |
| `tv_analyst` | read-only base + `qlty_*` + `plot_*` + `sql_*` + vector-store reads + `graph_*` | analysts, data-quality and tuning work |
| `tv_dba` | `dba_*`, all `base_*` (incl. `base_writeQuery` when the server has one), `sec_*`, `graph_*` | administration; writes remain ask-gated |

The packaged upstream profiles (`all`, `dba`, `dataScientist`, `eda`, `bar`, `llmUser`, `graph`) stay available by
name. To switch: `export TERADATA_MCP_PROFILE=tv_readonly` in the launching shell (the plugin data env file is NOT
the place - it carries credentials only), then a new session. A profile that hides `base_writeQuery` turns every
write request into "tool not found" - that is the intended outcome, say so instead of working around it.

Note on writes: the bundled upstream 0.2.6 server registers **no write tool** - `base_readQuery` is its only SQL
executor and the plugin holds it read-only. `base_writeQuery` appears only when you bridge to a server that adds one;
the write gate and the profile distinction above apply to that case. Scratch tables on the bundled server come from
`base_readQuery(persist: true)` (server-side `CREATE VOLATILE TABLE`).

## 6. `customize` - your own tools, cubes, prompts, glossary

Read `references/customize-server.md`. Summary: copy `config/example_domain_objects.yml.example` to a directory
of your own as `<domain>_objects.yml`, add a `profiles.yml` exposing it (start from the plugin's copy - the server
loads ONE config directory), set `TERADATA_MCP_CONFIG_DIR` and `TERADATA_MCP_PROFILE`, new session. Use
`SELECT TOP %(limit)s`, never `LIMIT`. Glossary terms become MCP resources (`glossary://all`).

## 7. Other switches worth knowing

- `TERADATA_MCP_AUDIT_LOG=<path>` (opt-in): the launcher loads the upstream `HOOKS_MODULE` audit logger
  (`scripts/td_server_hooks.py`) which appends one JSON line per tool call/error under `${CLAUDE_PLUGIN_DATA}/audit/`,
  SQL literals redacted. It is observability only - it cannot deny or change a call; every gate lives in the Claude
  Code hooks (`references/safety-model.md`).
- `TERADATA_MCP_PROGRESSIVE=true`: the server exposes only `search_tool`, `execute_tool` and `base_readQuery`; the
  hooks still gate `execute_tool` by its inner `tool_name`.
- `DEFAULT_ROW_LIMIT` (1000) / `MAX_ROW_LIMIT` (50000), `TD_POOL_SIZE` (5) / `TD_MAX_OVERFLOW` (10) /
  `TD_POOL_TIMEOUT` (30): upstream server settings, passed through. There is **no server-side query timeout in
  0.2.6**; the 180 s per-server timeout in `.mcp.json` is the only client bound. A wedged statement is ended
  with `ABORT SESSION` - through `base_writeQuery` (write-gated) where the server has one, otherwise by the DBA in
  their own client - see `references/customize-server.md`.
- Native alternative without the launcher: `claude mcp add --transport http teradata http://<host>:8001/mcp/`.
  Tools then appear as `mcp__teradata__*`; the hooks still match (they anchor on the tool suffix) but the plugin's
  agents and profiles do not. Running BOTH gives two Teradata toolsets - disable one (`references/troubleshooting.md`).

## 8. What the server exposes besides tools

- **MCP prompts** surface as slash commands, e.g. `/mcp__plugin_teradata-vantage_teradata__base_query`,
  `..._dba_databaseHealthAssessment`, `..._qlty_databaseQuality`, `..._dba_tableDropImpact` (exact rendering as
  exposed by `/mcp`).
- **MCP resources**: `glossary://all`, `glossary://definitions`, `glossary://term/{term_name}` (when a glossary is
  loaded) and `graph://edge-contract`; reference them with `@` mentions in the form `/mcp` shows.
  `graph://edge-contract` is the schema the seven `graph_*` dependency tools read from: they take an
  `edge_repository` argument and Teradata has no dependency catalog to default to, so the table must exist
  before any of them work. The `lineage` skill covers building and populating one.
- The generated tool inventory is in `references/tool-inventory.md`.

## Rules for this skill

- NEVER run `teradata-vantage-connect` from Bash without `--show`/`--test`/`--url`; it must be run by the user.
- NEVER print, log or repeat a `DATABASE_URI`, password, PAT or token; refer to them by name.
- NEVER install anything without stating size and location first, and never at session start.
- ALWAYS finish with the verify step and a one-paragraph status: mode, credential source, profile, guard mode.
- Deeper material: `references/connection-options.md` (URI forms, LOGMECH, encoding, sandbox), `references/troubleshooting.md`,
  `references/customize-server.md`, `references/safety-model.md`, `references/tool-inventory.md`.
