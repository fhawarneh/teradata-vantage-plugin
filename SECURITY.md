# Security policy

`teradata-vantage` is a community-maintained Claude Code plugin. It runs on your machine, under your user account,
against a Teradata Vantage system you configure. This document states the trust model precisely, so you can decide
what to point it at, and tells you how to report a vulnerability.

## Reporting a vulnerability

Report privately to the maintainer address in `.claude-plugin/marketplace.json` (`owner.email`). Do not open a public
issue for an unfixed vulnerability.

Once this project is published on GitHub, the preferred channel is GitHub's private vulnerability reporting — the
**Report a vulnerability** button on the repository's *Security* tab — because it keeps the report, the fix and the
advisory in one place. The maintainer address stays available as a fallback.

Include: the plugin version (`plugins/teradata-vantage/.claude-plugin/plugin.json` → `version`), your Claude Code
version, the transport mode (`bridge` or a local server), and the smallest reproduction you have. A first response is
sent within 7 days. If the issue is in the upstream `teradata-mcp-server` rather than in this plugin, it is forwarded
to https://github.com/Teradata/teradata-mcp-server and you are told so.

Please do not include a real `DATABASE_URI`, password, PAT, or production table or column names in a report.

## Trust model — read this before pointing the plugin at anything that matters

**The hooks are mistake-prevention for an agent, not a security boundary.** They reduce the chance that a language
model destroys data by accident. They do not stop a determined caller, and they are not access control. The database's
own GRANTs, roles and row-level security remain the only real control. Connect with a least-privilege account.

Specifically:

- **The read guard is mechanical.** `scripts/hooks/sql_read_guard.py` checks that a `base_readQuery` statement is a
  single statement whose first keyword is `SELECT`, `WITH`, `EXPLAIN`, `SHOW` or `HELP`, and that no blocked verb
  appears outside quotes and comments. It does not reason about which tables are sensitive, how many rows are
  returned, or who is asking.
- **It cannot see every tool.** Tools sourced from the server's `registry` group take their names from database
  views, and custom YAML tools run SQL you declared. Neither is matched by the guards. If you enable those groups,
  the database's permissions are the only thing standing behind them.
- **Hooks run unsandboxed, with your permissions.** They are local Python 3 and bash scripts under
  `plugins/teradata-vantage/scripts/`, run by Claude Code on your machine. Read them; they are short.
- **In bridge mode the guards protect only your session.** Other clients of the same MCP server are unaffected.
- **The upstream audit log is observe-only.** `HOOKS_MODULE` / `TERADATA_MCP_AUDIT_LOG` cannot deny or modify a call,
  and an exception inside it is swallowed by the server. Treat it as a log, never as a control.

For the full per-hook table, the fail directions, and the switches that tighten or relax each guard, see
`plugins/teradata-vantage/skills/setup/references/safety-model.md`.

## Network and data behaviour

- **Telemetry: none.** The plugin sends no usage data, no prompts, no SQL and no results anywhere.
- **Session tagging, not telemetry.** Before each tool call the bundled server runs `SET QUERY_BAND = '...' FOR
  SESSION` on your own Teradata connection, carrying `ORG=TERADATA-INTERNAL-TELEM`, `APPNAME=TeradataOSSMCP`,
  `APPVERSION`, `APPFUNC`/`TOOL_NAME`, `APPUSER`, `APPLICATION`, `PROFILE`, `PROCESS_ID` and, over HTTP,
  `REQUEST_ID`, `SESSION_ID`, `TENANT`, `CLIENT_IP`, `USER_AGENT`, `AUTH_SCHEME`, a 12-character `AUTH_HASH` and
  `PROXYUSER`. Despite the `ORG` key's name this is a string set on your session, not egress: it is recorded only in
  your own DBQL, if query logging is enabled, and is never transmitted to Teradata Corporation, to Anthropic or to
  the maintainer. See `plugins/teradata-vantage/skills/setup/references/customize-server.md` (Auditing what the agent
  ran) for how to report on it.
- **Outbound connections are only the ones you configure:** your Teradata system, and the MCP server URL you set in
  bridge mode. In addition, and only when you explicitly run the install path
  (`/teradata-vantage:setup install`, or `scripts/install-server.sh`), the plugin downloads pinned packages from PyPI;
  bridge mode fetches one pinned npm package (`mcp-remote`) through `npx`. Nothing is installed at session start.
- **Every package specification is pinned**, and the bundled MCP server ships as a vendored wheel under
  `plugins/teradata-vantage/servers/` with a recorded SHA-256 (`servers/VENDORED.sha256`), so the server itself is not
  downloaded at all.
- **SQL text is read in-process by the hooks and never persisted or transmitted.** The files the plugin writes are
  local, confined to its own data directory, and listed in `PRIVACY.md`: the optional credentials file you create
  yourself with `scripts/teradata-vantage-connect`, a names-only launch record, and the audit log if you enable it.

For the full statement — what is read, what is written locally, what leaves the machine, and retention — see
[PRIVACY.md](PRIVACY.md).

## Credentials

- Supply credentials as the `database_uri` plugin option (marked `sensitive`), as the `DATABASE_URI` environment
  variable, or in `${CLAUDE_PLUGIN_DATA}/teradata.env` written by `scripts/teradata-vantage-connect`, which you run in
  your own terminal so the password never enters the conversation transcript.
- The file is created mode `0600`. The plugin never echoes a credential; `doctor.sh` prints the *name* of the source
  it found, never its value.
- **Never paste a `teradata://user:password@host` URI into a Claude prompt.** The optional
  `enable_prompt_secret_guard` plugin option (off by default) warns when you do.
- Prefer a dedicated, least-privilege service account over a DBA account, and prefer `LOGMECH=LDAP` or another
  centrally managed mechanism over stored passwords where your site supports it.

## Supported versions

The most recent release is supported. This is a pre-1.0 plugin; there is no long-term support branch.
