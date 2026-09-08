# Changelog

## 0.4.0 (2026-09-08)

Closes the skill-breadth gap found by auditing this plugin against every database plugin in Anthropic's
marketplaces, and the last four MCP tools it shipped but taught nowhere. **19 skills, 7 agents.**

Context for the audit: 291 plugins in the official directory and 2,282 in the community one, with no
Teradata/Vantage plugin in either, while Oracle, Databricks, Snowflake, MongoDB, CockroachDB, BigQuery
and DuckDB are all listed. Against those, this plugin led on agents and was the only one with multi-agent
workflows, but trailed on skills — 15 against CockroachDB's 34 and Databricks' 31. Most of that surplus
is product surface Teradata does not have; five of their skills were genuinely portable and this release
builds the Teradata equivalent of each.

Everything below was executed against a live Vantage 20.00 before being written.

### Added

- **`workload` skill** + `references/tdwm.md`. Why a query is queued rather than slow — TASM/TDWM rules,
  which workload it landed in, service-level goals, delay and rejection counters, and query banding.
  Bound to the `dba` agent. Four traps, each hit while writing it:
  - `DBC.WorkloadInfoV` does not exist; the view is `TDWM.WorkloadInfoV`, and joining to it is mandatory
    because `TDWMSummaryLog` records only a `WDID`.
  - `GROUP BY 1,2,3` over that join's `TRIM()` columns fails with `3504`.
  - `TDWMActiveWDs` / `TDWMListWDs` are table **functions**, not views — and `TDWMActiveWDs()` carries
    the SLG, which is what makes `MetSLGCount` interpretable.
  - `GETQUERYBANDVALUE(0,'key')` returns the value; `(1,'key')` returns an empty string, not an error.
- **In-database AI** — `analytics/references/in-database-ai.md` plus a section in the skill. Closes the
  `databricks-ai-functions` gap and two untaught tools.
- **`loading` skill.** Choosing between BTEQ, FastLoad, MultiLoad, TPump, TPT and NOS; checking the
  target will accept the utility; verifying what landed. Answers a question the existing `tpt.md` left
  open — the load-slot limits are TDWM Utility Session rules, readable per system.
- **`migration` skill.** The project around the dialect: inventory, sizing, dependency order via the
  `lineage` waves, the four validation checks, cutover.
- **`docs` skill.** Release identity, whether a feature is installed, and `COMMENT` — 16,410 column and
  866 table comments existed on the measured system, and **`SHOW TABLE` does not include them**, so
  `base_tableDDL` can never show them either.
- **`profile/references/test-data.md`.** Generating realistic volume in-database with
  `Sys_Calendar.CALENDAR` (73,414 rows, 1900–2100) and `RANDOM`.
- **Two eval cases** — an empty DBQL-backed view is not proof nothing was throttled, and 80 million
  governed records should not be shipped to an external API.

### Fixed

- **All four untaught MCP tools are now taught.** `plot_polar_chart` and `plot_radar_chart` were missing
  from the `query` skill (the rule that matters was there; the tools were not named, so the coverage
  check could not see them), and `chat_completeChat` / `chat_aggregatedCompleteChat` had no home at all.
  Coverage re-run: every tool the plugin ships is taught by a non-generated skill.

### Measured, and deliberately not written

- **`AI_AskLLM` is present** and takes exactly two input tables, but its input aliases are not
  discoverable from the database — `DBC.FunctionParametersV` does not exist and `HELP FUNCTION` returns
  zero rows for a table operator. Rather than guess, the reference states what it requires and points at
  the release documentation. A wrong alias produces the same error as a wrong table count, so guessing
  looks like progress without being progress.
- **CompleteChat is not installed** on the measured system, so the `chat_*` tools register only where it
  is *and* `CHAT_API_KEY` is set. Taught as an availability check, not as a given.
- **No time-series and no nPath/attribution families exist** on this release. Writing skills for them
  would have been inventing coverage.
- **`TD_API_VertexAI` requires an `AccessToken` as a `USING` argument**, which puts the credential in the
  statement text and therefore in DBQL. Worth knowing before recommending it.

### Context cost

**~4,139 always-on tokens** for 19 skills and 7 agents, measured with `claude plugin details` against a
clean-HOME install of this release from GitHub — up from ~3,389 at 0.3.0, so four skills cost about 190
tokens each. Reference material still loads only when a skill needs it. The local-directory install
reads roughly 1.46x higher; that discrepancy is documented at 0.2.0 and the clean-install figure is the
one that reflects what a user gets.

## 0.3.0 (2026-09-08)

The remaining backlog from the 0.2.0 plan, implemented rather than deferred. Released as 0.3.0
because 0.2.0 was already tagged and published; moving a published tag is worse than a version number.

### Added

- **A grounding cache and verified-query repository** (`scripts/grounding.py`), persisting derived
  schema and human-confirmed SQL between sessions, wired into the `query` skill ahead of its repair
  loop. Three properties make it safe rather than merely fast:
  - **Every recall reports `age_seconds` and `stale`**, and an entry with no timestamp is treated as
    stale rather than fresh. A cached column list that survived an `ALTER TABLE` is worse than no
    cache, because it is confidently wrong — so DDL invalidates, deliberately bluntly.
  - **The promotion gate needs a human.** Clean execution alone is refused: a wrong join returning
    plausible numbers runs perfectly, and promoting on execution would fill the repository with
    confident mistakes for the next session to trust.
  - **Lookup is exact-normalised only** — case and whitespace, nothing more. Deciding that two
    differently-worded questions mean the same thing is a judgement about meaning, and it belongs to
    the model reading `list`, never to keyword logic in a script. A test pins that absence.

  Only a digest of the connection target is stored, the files are `0600`, and a corrupt store
  degrades to empty rather than failing the session.
- **Static checks over `workflows/*.js`** (`scripts/tests/test_workflow_static.py`). Three mistakes
  that were previously invisible until a fan-out had already been paid for: an `agentType` naming an
  agent that does not exist, a schema whose `required` names a key its `properties` omits, and a
  phase entered but not declared in `meta.phases` (or the reverse). Each check was mutation-tested —
  broken deliberately, confirmed to fail, restored.
- **`argument-hint` on every user-invocable skill**, plus a validator rule that keeps it that way in
  both directions: an invocable skill without one, and a background skill carrying one that is never
  shown.

### Changed

- **CI now tests on a Python matrix, 3.10 and 3.12.** The hooks run under whatever bare `python3` a
  user has, which is not the version the vendored server needs. Verified locally before asserting it
  in CI: the full suite passes on 3.10.
- **The `validate-latest` canary moved to a weekly schedule** instead of every push. Its job is to
  notice a new CLI release breaking strict validation, which has nothing to do with whichever commit
  happened to trigger CI.

### Not done, and why

- **`compatibility:`, `color:` and `effort:` frontmatter were not added.** The plan called for them,
  and the validator's allowlist already permits them, but I could not demonstrate that any of the
  three is actually read — `claude plugin validate` checks only `plugin.json`, and unknown YAML keys
  in skill and agent frontmatter are silently ignored. Shipping fields that may do nothing is exactly
  the kind of unverified claim this plugin is built to avoid, and `effort:` in particular could
  silently change reasoning effort. They remain allowed for a contributor who has a reason.

### Test count

617, up from 572, passing on both 3.10 and 3.12.

## 0.2.0 (2026-09-08)

Two capability areas the plugin shipped tools for but taught nowhere, a pipelines skill, and the
carried-forward commitments from 0.1.0 either closed or explicitly dropped.

### Added

- **`lineage` skill and the `cartographer` agent.** The bundled server exposes seven `graph_*`
  dependency tools and a `graph://edge-contract` resource that appeared in no skill, agent or
  workflow — and that cannot run at all until an edge repository exists, because `edge_repository`
  is a required argument and Teradata has no dependency catalog to default to. The skill leads with
  that prerequisite and covers both ways to populate one: structural edges parsed from view
  `RequestText`, and observed edges from the DBQL object log. Proved end to end before shipping —
  853 views yielded 530 edges with 95 references skipped, and a real blast-radius question returned
  12 dependent views.
- **`analytics` skill**, with references for the ClearScape `TD_*` functions and BYOM scoring.
  In-database analytics and scoring an ONNX, PMML, H2O, Dataiku or DataRobot model where the data
  already is.
- **`pipelines` skill**, with references for dbt-teradata and the Airflow Teradata provider.
- **`teradata-sql/references/porting-sql.md`.** 54 constructs from other dialects, each executed
  against a live system, with the error code Teradata actually returns.
- **`vector-store/references/rag-workflow.md`.** What `rag_Execute_Workflow` is, and what it is not.
- **Temporal tables and the `PERIOD` type** folded into `teradata-sql/references/physical-design.md`.
- **Two eval cases** — an empty dependency result is not proof of safety, and 400 million rows
  should be scored where they live.
- **`experimental.evals` declared in the manifest**, with `PLUGIN_JSON_ALLOWED` widened to match.
- **Optional surfaces are now documented with activation steps** — the output style, the theme and
  the health monitor each had been named in a single clause with no way to turn them on.

### Fixed

- **`agents/vector.md` could not run the permission workflow its own skill documents** —
  `tdvs_grant_user_permission` and `tdvs_revoke_user_permission` were missing from its tools.
- **`workflows/drop-impact.js` hand-wrote weaker DBQL lineage SQL** while seven purpose-built
  dependency tools sat unused. It now prefers structural lineage when a repository is available and
  says so honestly when it is not.
- **`vector-store/references/in-database-vectors.md` named `mldb`**, which does not exist. The BYOM
  functions live in `TD_MLDB`. The same error appears in the upstream server's own documentation.
- **Theme contrast.** `promptBorder` measured 3.36 against a dark background — the dimmest token in
  the theme, on the element that asks you to approve a destructive write. Now 4.89, with every token
  above WCAG AA 4.5, and `error` moved so the two are 46% further apart.
- **The release zip had two implementations.** CI built it with its own inline `zipfile` code while
  `release.py` had `build_zip`, so the two could ship different archives. CI now calls
  `release.py --zip-only`.

### Changed

- **The hidden-Unicode rule is now documented in `CONTRIBUTING.md`.** The validator has always
  enforced it; the rule that bit this repository — the forbidden-token list held the characters as
  literals, so the file banning them contained them — is now written down.
- **A repo-root `CLAUDE.md`** imports `CONTRIBUTING.md`, so the contributor rules load for anyone
  working on the plugin itself.

### Not done, and why

- **The `teradata-sqlfluff-lsp` companion plugin is dropped, not deferred again.** 0.1.0 excluded a
  `.lsp.json` from this plugin for a good reason — a `.sql` LSP entry is exclusive, so it would
  hijack the extension for every project the user opens — and recorded a companion plugin for
  0.2.0. On review the companion would largely duplicate what the `sql_lint.py` hook already does
  for Claude Code, while adding a second plugin to maintain and a real risk of surprising people who
  install it. Saying so is better than carrying it forward a third time.
- **The four upstream changes proposed in `servers/VENDORED.md` have not been filed.** They are
  written up in proposal shape, and filing against someone else's repository is a decision for a
  maintainer rather than an automated run. See *Filing status* in that file.
- **The new eval cases have not been executed.** `claude plugin eval` is gated behind early access
  and was unavailable at release time. The cases validate structurally; their scores are unmeasured.

### Context cost

**~3,389 always-on tokens** for 15 skills and 7 agents, measured with `claude plugin details` against
a clean-HOME install of this release from GitHub — the path an actual user takes.

Worth knowing if you measure it yourself and get a different answer: the same commit installed from a
local directory reports **~4,965**, and the gap is systematic rather than random. Every component
scales by roughly the same factor — `setup` is 160 against 240, `health` 200 against 290 — so it is
the estimate that differs, not the plugin. The clean-install figure is the one quoted here because it
reflects what someone installing from the marketplace gets. Treat either number as an estimate.

Per-skill frontmatter is uniformly disciplined: 570 to 775 characters, every field inside the
validator's 400-character limit, with no outlier to trim. Reference material still loads only when a
skill needs it.

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
