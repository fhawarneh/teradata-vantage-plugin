# Changelog

## 0.1.0 (2026-09-08)

First release of the `teradata-vantage` plugin — a community toolkit for working with Teradata Vantage from
Claude Code. Not affiliated with, endorsed by, or sponsored by Teradata Corporation.

### Added

- **Skills (12).** `setup`, `explore`, `query`, `health`, `tune`, `profile`, `archive`, `vector-store`,
  `client-development`, plus three background skills that load when they apply: `teradata-sql` (dialect rules
  and error-code repair), `teradata-recovery` (host-level runbooks for a system that will not accept logons),
  `sql-files` (`.sql` / `.bteq` scripts on disk).
- **Agents (6).** `explorer` and `tuner` — read-only. `dba` — administrative work, each write stated with its
  blast radius and approved before it runs. `archivist` — archives cold data to Native Object Store or Iceberg
  and verifies the copy round-trips before anything is deleted. `vector` — Enterprise Vector Store lifecycle.
  `auditor` — restricted read-only probe dispatched by the workflows.
- **Workflows (4), all read-only.** `health-audit`, `profile-database`, `sql-review`, and `drop-impact`, a
  blast-radius report that never emits a `DROP`.
- **Hooks — nine registrations across five events.** Read guard: `base_readQuery` must be one statement
  starting `SELECT`, `WITH`, `EXPLAIN`, `SHOW` or `HELP`, and must not invoke `WRITE_NOS(`, which is
  SELECT-shaped but writes Parquet objects to your store. Write gate: an approval prompt before destructive
  `base_writeQuery` statements. Destructive tool gate: always prompts before the tools that create or drop
  objects — `tdvs_destroy`, `tdvs_update`, the vector-store permission tools, `bar_manageJob`,
  `sql_Execute_Full_Pipeline`, `rag_Execute_Workflow`, and `base_saveDDL`, which upstream annotates read-only
  but which writes a `.sql` file to a caller-supplied directory. Host-ops gate: prompts before Teradata node-admin shell
  commands, and only in sessions with a Teradata connection configured. Result coach: on both tool results and
  tool failures, explains a Teradata `Error NNNN` and asks for an aggregated re-run when a result is huge.
  SQL lint: runs `sqlfluff` on edited SQL files only when it is already installed. Session status: one line at
  session start. Prompt secret guard: off unless enabled; warns when a prompt carries a
  `scheme://user:password@` URI.
- **Bundled MCP server.** teradata-mcp-server 0.2.6 (MIT, Teradata) vendored under `servers/` as a wheel with a
  recorded SHA-256 and hash-pinned requirement sets (core, `bar`, `tdvs`). `/teradata-vantage:setup install`
  builds it into the plugin's data directory; the launcher can instead bridge to a server you already run.
- **Curated tool profiles (4).** `tv_all`, `tv_readonly`, `tv_analyst` and `tv_dba` in `config/profiles.yml`,
  added to the server's packaged profiles and selected with `TERADATA_MCP_PROFILE` (default `tv_all`).
- **Monitor.** `td-health-watch`, armed the first time the `health` skill runs and only when a stored credential
  file and the local server venv are present: it opens a database session on an interval
  (`TERADATA_MONITOR_INTERVAL`, default 900 s), runs two read-only dictionary queries, and prints one line only
  when the ok / warn / critical bucket changes. It is inert in bridge mode and `TERADATA_MONITORS=0` disables it.
- **Output style and theme.** `Teradata DBA report` (verdict first, evidence table, the SQL that was run, next
  actions with `[WRITE]` markers) and the `Warehouse Dark` theme.
- **Eval suite.** Thirteen cases under `evals/` in the shape `claude plugin eval` reads, with a mock MCP layer so
  they run without a database. They cover the dialect rules (TOP not LIMIT, `IS NULL`, reserved-word aliases,
  `DECIMAL` division precision), the qmark parameter style, tool-first exploration, the read guard (denies a
  multi-statement write, allows `EXPLAIN` and a `CASE ... END`), drop-impact discipline, archive verify-before-delete,
  and the `2644` / `3541` space diagnoses.

### Security

- The guards are mistake-prevention for an agent, not a security boundary. They exist so a model does not
  destroy data by accident; the database's own GRANTs, roles and row-level security remain the only real
  control. Connect with a least-privilege account.
- The vendored server 0.2.6 exposes no `base_writeQuery`: `base_readQuery` is its only SQL executor, and the
  read guard holds it to a single read statement. The write gate matters when you bridge to a server that does
  expose one. Three documented exceptions keep this from being an absolute: `base_readQuery(persist: true)`
  makes the SERVER wrap your SELECT in `CREATE VOLATILE TABLE … WITH DATA` (session-scoped, dropped at logoff,
  and invisible to a guard that inspects only the SQL text you passed); `WRITE_NOS(` is SELECT-shaped and
  writes Parquet objects to your store, so the read guard denies it by name; and `base_saveDDL` writes a `.sql`
  file to a directory on the machine running the server, so it is gated and excluded from the read-only
  profiles.
- No telemetry. The plugin connects only to the Teradata system and MCP server you configure, and to PyPI or npm
  only when you explicitly install or bridge.
