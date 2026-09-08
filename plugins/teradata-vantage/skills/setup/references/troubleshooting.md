# Troubleshooting - symptom -> what it actually means -> what to do, for the teradata-vantage plugin's server, hooks and connection.

Read the doctor report first (`/teradata-vantage:setup doctor`). Every entry below starts with what the symptom
actually means, because most of these are misread as "the model is misbehaving" or "the query is slow".

## The agent narrates SQL instead of calling a tool

What it actually means: the agent has no usable Teradata tool for that call. Either the tool is missing from its
tool list (transport/profile problem) or every attempt is being denied (guard over-block). Both are silent from the
model's side: it writes the SQL out as text and moves on. This is a configuration problem, not a model problem.

Diagnose in this order:

1. `/mcp` - is `plugin:teradata-vantage:teradata` connected? If it shows failed, the problem is transport (below).
2. Is the tool exposed by the profile? `tv_readonly` and `tv_analyst` hide `base_writeQuery`; `tdvs_*` need the
   `tdvs` extra plus `TD_BASE_URL`; `bar_*` need the `bar` extra plus DSA variables. Check `references/tool-inventory.md`.
3. Is the read guard denying? Run Claude Code with `--debug` and look for `permissionDecision: deny` with a reason
   such as `read-only: verb 'delete' is not allowed` or `multi_statement`. Common legitimate causes: the model sent
   two statements with a `;`, or a non-SELECT (`HELP`, `SHOW`, `EXPLAIN` ARE allowed; `CREATE VOLATILE TABLE` is not -
   it belongs to `base_writeQuery`). A leading `EXPLAIN` suspends the blocked-verb scan, so `EXPLAIN DELETE ...`,
   `EXPLAIN UPDATE ...`, `EXPLAIN INSERT ... SELECT` and `EXPLAIN MERGE ...` pass and return a plan without executing
   anything; a deny on one of those is a stale copy of the hook, not policy. `EXPLAIN SELECT 1; DROP TABLE t` still
   denies as `multi_statement` - the single-statement rule runs first.
4. To rule the guard out without disabling it: `export TERADATA_SQL_GUARD_MODE=audit` in the launching shell, new
   session, repeat the request. In audit mode every call passes and each would-be block is logged to stderr as a line
   containing `WOULD_BLOCK` with the reason. Zero `WOULD_BLOCK` lines means the guard was not the cause.
5. If the hook itself crashed you will see a permission prompt saying `read guard crashed: <ExceptionClass>` - the
   hook fails to ASK, never to silent allow. The reason carries the exception CLASS only, never its message or a
   traceback (nothing from the statement leaks into the prompt); report the class name and the statement you sent.

## "I cannot run that UPDATE/DELETE/CREATE" - the agent refuses every write

What it actually means: the bundled upstream 0.2.6 server registers no write tool. `base_readQuery` is its only SQL
executor, and the plugin's read guard denies anything that is not a single `SELECT`/`WITH`/`EXPLAIN`/`SHOW`/`HELP` -
so there is no path for a statement the agent composes, by design. `base_writeQuery` (which the write gate protects)
exists only on servers that add it; check `/mcp` -> tool list. Two core tools do write on their own account and are
not affected by this: `sql_Execute_Full_Pipeline` drops and re-creates its clustering tables in the feature database,
and `rag_Execute_Workflow` creates and inserts into its query table. Both prompt every time (`destructive_tool_gate`).

Do this: let the agent prepare and preview the statement, then run it yourself in your SQL client. For scratch
tables, `base_readQuery` with `persist: true` creates a volatile table server-side without a write tool. Do NOT set
`TERADATA_SQL_GUARD_MODE=audit` to force writes through `base_readQuery`: in audit mode nothing prompts and DML
commits immediately.

## `/mcp` shows the server failed, or "421 Misdirected Request" in the debug log

What it actually means (bridge mode): the remote teradata-mcp-server's DNS-rebinding protection rejected the `Host`
header. FastMCP-based servers accept only the host names they were configured with (typically `localhost`), and the
MCP client surfaces the 421 as an opaque `ExceptionGroup: unhandled errors in a TaskGroup`. The agent then has no
tools at all.

Do this: use the host name the server allows in `TERADATA_MCP_URL` (usually `http://localhost:8001/mcp/` or
`http://127.0.0.1:8001/mcp/`), or configure the server's allowed hosts. A plain `GET` on `/mcp/` returning
**HTTP 406** is proof the server is UP (it requires POST + `Accept: text/event-stream`); it is not a failure.

## Logon hangs for ~3 minutes, then a timeout - not "connection refused"

What it actually means: a firewalled, tunnelled or half-started Teradata does not refuse; it completes the TCP
handshake and never answers the logon. The 180 s per-server timeout in `.mcp.json` is the only thing that ends the
wait, so this looks like a very slow query.

- A client outside a firewall allowlist typically sees `Error 444` or `connection reset by peer`. A system that is
  still starting shows the same until its "Logons are enabled" state - wait and retry.
- Check reachability from the machine that runs the server: the port is 1025 unless the DBA changed it. Over a VPN
  or SSH tunnel, the port is often remapped - use the tunnel's local port in the URI.
- Verify the credential file independently in your own terminal:
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/teradata-vantage-connect" --test` (needs the local server installed).

## Error 8017 "The UserId, Password or Account is invalid" with correct credentials

What it actually means: the logon mechanism does not match the system. A directory-authenticated (LDAP) warehouse
rejects a TD2 logon even with a correct password.

Do this: set `LOGMECH=LDAP` (or `KRB5`, `JWT`) - via the environment, the connect helper's LOGMECH prompt, or a
`?LOGMECH=LDAP` query parameter on the URI. Default is `TD2`. See `references/connection-options.md`.

## Error 3524 "The user does not have CREATE TABLE access to database DBC"

What it actually means: the session's default database is DBC, and nothing - not even `dbc` - may create objects
there. Three places produce it: a `DATABASE_URI` whose path ends in `/dbc` (or has no path and the user's default
database is DBC), `tdvs_create` without `target_database`, and any Python client that materialises scratch tables.

Do this: end the URI with a working database (`.../1025/<working_db>`), re-run the connect helper if you used the
file, new session. It is not a permissions bug to fix with GRANT.

## Two Teradata toolsets (duplicate `base_*` tools)

What it actually means: the plugin server AND another Teradata MCP server (a project `.mcp.json` entry, or one added
with `claude mcp add`) are both enabled. Tools appear twice under different prefixes (`mcp__plugin_teradata-vantage_teradata__*`
and `mcp__<name>__*`). The hooks match both (they anchor on the tool-name suffix), the plugin's agents only see the
plugin's copy, and the model may pick either.

Do this: keep one. Disable the other in `/mcp`, remove it (`claude mcp remove <name>`), or, for a project
`.mcp.json`, leave it out of `enabledMcpjsonServers`. If you prefer the existing server, bridge to it instead:
set `mcp_url`/`TERADATA_MCP_URL` and disable the standalone entry.

## "no Teradata MCP server runtime is available" (from the launcher, in `claude --debug` or the doctor)

What it actually means: no bridge URL, no installed venv, no explicit uvx/docker mode. The launcher never installs on
its own. Do ONE of: `/teradata-vantage:setup install` (bundled 0.2.6 wheel + ~555 MB pinned dependencies into
`${CLAUDE_PLUGIN_DATA}/venv`, once, Python 3.11+ required), or set `TERADATA_MCP_URL`/`mcp_url`, or set
`TERADATA_MCP_DOCKER_IMAGE`. Then a new session.

## "the plugin's bundled server changed since this venv was built"

What it actually means: a plugin update bumped the vendored wheel (`servers/VENDORED.sha256` differs from the copy
in the data dir). Re-run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-server.sh"` (it detects the drift and reinstalls).

## "no Python >= 3.11 found"

What it actually means: teradata-mcp-server declares `requires-python >= 3.11`; the system `python3` is older. Install
a newer interpreter or point `TERADATA_MCP_PYTHON=/path/to/python3.12` at one, then install again. Bridge mode does
not need Python at all.

## `/plugin manage` - where the options live

Options (`database_uri`, `mcp_url`, `server_extras`, `enable_prompt_secret_guard`) are edited in `/plugin manage`
-> teradata-vantage. `database_uri` is marked sensitive. Changing an option does not restart a running server: start a
new session. If a value shows as the literal text `${DATABASE_URI}`, Claude Code passed an unset variable through
unexpanded; the launcher treats that as unset - it is harmless.

## `.sql` edits produce no lint findings

What it actually means: the lint hook is silent unless `sqlfluff` is on PATH (or `TERADATA_SQL_LINT=off` was set).
Install it in your own environment: `pip install sqlfluff` (or `pipx install sqlfluff`), confirm `sqlfluff --version`,
new session. The hook uses `config/sqlfluff-teradata.cfg` with `--dialect teradata` unless your project has its own
sqlfluff config with a `dialect` key.

## Tool calls time out at 180 s but a direct logon is instant

What it actually means: the server's connection pool holds connections that died with a Teradata restart or a dropped
network path; `pool_pre_ping` does not reliably clear them, and 0.2.6 has no `pool_recycle`. Restart the server:
new Claude Code session for stdio modes; restart the remote server for bridge mode.

If a single statement is truly running away on Teradata, there is no server-side query timeout in 0.2.6. Find the
session with `mcp__plugin_teradata-vantage_teradata__dba_sessionInfo` (or `DBC.SessionInfoV`) and end it with
`ABORT SESSION` through `base_writeQuery` - the write gate will ask first. See `references/customize-server.md`.

## Vector-store (`tdvs_*`) or backup (`bar_*`) tools are missing

What it actually means: these groups are gated by BOTH an install extra and an environment variable. `tdvs_*`: install
with `--extras tdvs` and set `TD_BASE_URL` (plus `TD_PAT`+`TD_PEM` or the URI credentials). `bar_*`: `--extras bar` and
`DSA_BASE_URL` or `DSA_HOST`+`DSA_PORT`. A failed vector-store connection disables the group with only a log line.

## Progressive disclosure turned most tools into `search_tool` / `execute_tool`

What it actually means: `TERADATA_MCP_PROGRESSIVE=true` is set. The server exposes `search_tool`, `execute_tool` and
`base_readQuery` only; other tools are called through `execute_tool(tool_name=...)`. The read guard and write gate
still apply - they read the inner `tool_name`. Unset the variable for the full tool list.

## A "[RESULT TRUNCATED ...]" note appeared after a query

What it actually means: the result exceeded `TERADATA_MAX_RESULT_CHARS` (default 120000) and the `PostToolUse`
coaching hook attached a note asking for a smaller, aggregated query. The hook ATTACHES context; it does not edit the
result, and no rows were dropped by the plugin. This is working as intended: GROUP BY, WHERE, or `SELECT TOP n`.
Raise the cap only for a deliberate export.

## A hook shows a permission prompt with "crashed" in the reason

What it actually means: the read guard, the write gate or the destructive-tool gate hit an unexpected exception and
failed to ASK rather than allow. The call is safe to approve if you have read the SQL yourself. The reason names the
exception CLASS only ("write gate crashed: KeyError") - there is no message and no traceback, so report the class name
and the call that produced it. It is not a Teradata error.

## Still stuck

Collect (names only, no values): the doctor report, `/mcp` status, the exact Teradata error code and message, the
profile, and whether the failure reproduces with `TERADATA_SQL_GUARD_MODE=audit`. Upstream server issues belong at
https://github.com/Teradata/teradata-mcp-server/issues; plugin issues at
https://github.com/fhawarneh/teradata-vantage-plugin/issues (the `repository` field in `plugin.json`).
