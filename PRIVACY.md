# Privacy statement

`teradata-vantage` is a community-maintained Claude Code plugin. It runs on your machine, under your user account,
against a Teradata Vantage system you configure. It has no server side and no account: there is nothing for the
maintainer to collect.

This statement describes what the code in this repository actually does. Every claim below is derived from the
scripts and hooks under `plugins/teradata-vantage/`, which are short and readable; the trust model that goes with it
is in `SECURITY.md`.

## Telemetry: none

The plugin sends no usage data, no analytics, no crash reports, no prompts, no SQL and no results to the maintainer,
to Anthropic, or to any third party. No script, hook, agent or workflow in this repository contacts an endpoint
operated by the maintainer — there is none.

The plugin does not change what Claude Code itself sends to Anthropic in the course of a normal session; that is
governed by Anthropic's own terms and privacy policy, not by this plugin.

## What is read

- **SQL text and tool arguments, in-process, by the hooks.** The `PreToolUse` guards
  (`sql_read_guard.py`, `write_gate.py`, `destructive_tool_gate.py`, `host_ops_gate.py`) receive the tool payload on
  stdin, decide `pass` / `ask` / `deny`, and write that decision to stdout. The `PostToolUse` coach reads the tool
  result to add a hint. None of these persists or transmits what it read.
- **Your prompt text, when the optional prompt secret guard is on.** `prompt_secret_guard.py` (the
  `enable_prompt_secret_guard` plugin option, off by default) inspects the submitted prompt in-process to warn that
  it looks like a pasted credential. It reports the fact, never the value.
- **`.sql` files you edit, when `sqlfluff` is installed.** `sql_lint.py` runs `sqlfluff` as a local subprocess on the
  file you just wrote. Nothing is uploaded.

## What is written on your machine

Everything the plugin writes stays in the plugin's own data directory (`${CLAUDE_PLUGIN_DATA}`). Nothing outside it is
modified.

| File | Written by | Contents |
|---|---|---|
| `teradata.env` | `scripts/teradata-vantage-connect`, only when you run it yourself | The connection values you typed. Created `umask 077`, mode `0600`. You run the script in your own terminal so the password never enters the conversation transcript. |
| `last-launch.txt` | `scripts/launch-mcp.sh`, on every launch | A names-only launch record: which input variables were *present* (names, not values), the chosen mode, credential source, profile, config directory and interpreter path, and — in bridge mode — the host part of the MCP URL you configured. No credential value is written. |
| `audit/tool-calls.jsonl` | `scripts/td_server_hooks.py`, only when you set `TERADATA_MCP_AUDIT_LOG` | Off by default. One JSON object per tool call: timestamp, tool name, database user, profile, request id, success flag. **SQL text is never written** — only its SHA-256 and length. Arguments whose key looks like a credential become `<redacted>`; string literals in other values become `'<lit>'`. Directory `0700`, file `0600`. |
| `.venv/`, `VENDORED.sha256`, `INSTALLED_EXTRAS` | `scripts/install-server.sh`, only when you run the install | The local server virtual environment and a record of which extras it has. |

## What leaves your machine

- **Your Teradata system.** The connection you configure (`DATABASE_URI`, or the `database_uri` plugin option, or
  `teradata.env`). Statements go there and results come back; that is the point of the plugin.
- **Your MCP server URL, in bridge mode.** The streamable-http endpoint you set (`mcp_url` / `TERADATA_MCP_URL`).
- **PyPI, only on an explicit install.** `/teradata-vantage:setup install` or `scripts/install-server.sh` downloads a
  hash-pinned dependency closure from PyPI, after telling you the size. The MCP server itself is vendored as a wheel
  under `plugins/teradata-vantage/servers/` with a recorded SHA-256, so the server is never downloaded.
- **npm, only in bridge mode.** One pinned package, `mcp-remote@0.8.3`, fetched through `npx` when the bridge starts.
- **A container registry, only in docker mode.** The image you name is pulled by `docker`.
- **Optional server endpoints you supply yourself.** If you enable the Enterprise Vector Store tools or the backup
  tools, the bundled server calls the base URL you set (`TD_VS_BASE_URL`, `DSA_BASE_URL`). Both are your own systems.
- **Your Teradata system again, on an interval, once the health monitor is armed.** Invoking the `health` skill
  starts `td-health-watch` (`monitors/monitors.json`, `scripts/monitor_health.sh`). From then on, for the life of
  that Claude Code session, it opens a database session every `TERADATA_MONITOR_INTERVAL` seconds (default 900,
  floor 60), runs two read-only dictionary queries — DBC space headroom and a count of active sessions — and prints
  one line only when the ok / warn / critical bucket changes. It contacts nothing but the Teradata system you
  already configured, and it never prints a host, a user or SQL. It is inert unless BOTH a stored `teradata.env`
  and the local server virtualenv exist, so a bridge-mode user never triggers it. `TERADATA_MONITORS=0` disables it.

Nothing happens at session start. With no connection configured, the plugin makes no network call at all.

Maintainer tooling that contributors run — `scripts/vendor_server.sh` — fetches release metadata from `pypi.org` and
`api.github.com`. It is not part of a user session.

## Session tagging in your own query log

Before each tool call the bundled `teradata-mcp-server` 0.2.6 runs `SET QUERY_BAND = '...' FOR SESSION` on **your**
Teradata connection. The band carries `ORG=TERADATA-INTERNAL-TELEM`, `APPNAME=TeradataOSSMCP`, `APPVERSION`,
`APPFUNC`/`TOOL_NAME`, `APPUSER`, `APPLICATION`, `PROFILE`, `PROCESS_ID` and, when the server runs over HTTP,
`REQUEST_ID`, `SESSION_ID`, `TENANT`, `CLIENT_IP`, `USER_AGENT`, `AUTH_SCHEME`, a 12-character `AUTH_HASH` and
`PROXYUSER`.

Despite the `ORG` key's name, this is **not egress**. It is a string set on your own database session. It is visible
to your DBAs through DBQL (`DBC.QryLogV`) if query logging is enabled for the user, and it is never transmitted to Teradata
Corporation, to Anthropic or to the maintainer. It is useful: it is how you attribute a query in DBQL to the agent
that ran it. See `plugins/teradata-vantage/skills/setup/references/customize-server.md` for how to query it.

## Retention

The plugin retains nothing, because it stores nothing remotely. The local files listed above persist until you delete
them; removing the plugin's data directory removes all of them. There is no rotation and no expiry — if you enable the
audit log, you own its lifecycle.

## Children and special categories

The plugin is a developer tool. It is not directed at children, and it collects nothing itself. Whatever data your
queries touch stays between you and your own Teradata system.

## Questions

Open an issue, or use the private channel described in `SECURITY.md` if the question concerns a vulnerability.
