# Teradata Vantage Toolkit (Community)

A Claude Code plugin for working with **Teradata Vantage**: it bundles the open-source Teradata MCP server, teaches
Claude the Teradata SQL dialect and its error codes, ships DBA health and recovery runbooks, and puts a read-only
guard in front of the query tool so an agent cannot write to your warehouse by accident.

> Community plugin. Not affiliated with, endorsed by, or sponsored by Teradata Corporation. Teradata, Vantage and
> ClearScape Analytics are trademarks of Teradata Corporation.

## Why this exists

A general-purpose model writes Postgres or MySQL SQL by default, and most of it fails on Teradata — `LIMIT 10` is a
syntax error, `col = NULL` silently returns nothing, `sum` cannot be an alias, and `DECIMAL/DECIMAL` division quietly
truncates money. Meanwhile the errors Teradata does raise (`3541`, `2644`, `3707`, `5628`) name a cause that is
usually not the real one. This plugin carries the corrections, the error-code translations and the operational
runbooks, and wires up the tools to act on them.

## What makes it different

- **The MCP server is vendored, not fetched.** `servers/` holds the upstream wheel with a recorded SHA-256, and the
  launcher builds from that file — there is no floating version range resolved at launch.
- **The credential is a `sensitive` plugin option**, so Claude Code keeps it in secure storage rather than in
  `settings.json`; and the connect script keeps the password out of the transcript altogether.
- **The read guard and the write gate fail to *ask*, not to allow**, when the script itself crashes or gets a payload
  shape it does not recognise — a direction pinned by the test suite. The case where a hook cannot run at all is
  different and is disclosed under [Platform support](#platform-support).
- **Four multi-agent workflows.** `health-audit`, `profile-database`, `sql-review` and `drop-impact` fan out
  read-only probes in parallel and report a verdict with its evidence. `drop-impact` never emits a `DROP`.
- **A grounding cache that never becomes an authority.** Derived schema and human-verified queries
  persist between sessions, but every recall reports its age, DDL invalidates it, and nothing enters
  the verified repository without a person confirming the answer was right — running without an error
  is explicitly not enough.
- **The guards are tested, not asserted.** 572 tests cover the hooks, the launcher and the inventory, including the
  direction each one fails in.
- **It is useful before it is connected.** `/teradata-vantage:setup` runs the doctor and reports what is missing
  without contacting anything.
- **The context cost is measurable.** `claude plugin details` reports what the plugin adds to every session; the
  ~340 KB of reference material loads only when a skill needs it.

## Install

```bash
claude plugin marketplace add fhawarneh/teradata-vantage-plugin
claude plugin install teradata-vantage@teradata-plugins
```

To install a local clone instead — a branch you are working on — point `marketplace add` at its path.

Installed from the shell, the plugin loads the next time Claude Code starts, or when you run `/reload-plugins` in an
open session. Until you connect it, `/mcp` lists `plugin:teradata-vantage:teradata` as failed and the session-start
line reads `server mode=none` — that is expected; connect next. Nothing is downloaded or installed when a session
starts — see [Network behaviour](#network-behaviour).

## Updating

```bash
claude plugin marketplace update teradata-plugins
claude plugin update teradata-vantage@teradata-plugins
```

`claude plugin update` applies on restart, or on `/reload-plugins` in an open session. If the doctor then reports
`local venv: installed=1 pin=stale`, the bundled server changed with the update — re-run `/teradata-vantage:setup
install`.

## Connect

`.mcp.json` starts `scripts/launch-mcp.sh`, which resolves one of these modes at session start:

| Mode | When | What runs |
|---|---|---|
| `bridge` | the `mcp_url` option or `TERADATA_MCP_URL` is set, and `npx` exists | `npx mcp-remote@0.8.3 <url>` against a Teradata MCP server you already run |
| `venv` | `/teradata-vantage:setup install` has built the local virtualenv | the bundled 0.2.6 wheel over stdio |
| `docker` | `TERADATA_MCP_DOCKER_IMAGE` is set — picked only when neither of the above applies | `docker run -i --rm <image>`; the opt-in audit log is not available in this mode |
| `uvx` | only when `TERADATA_MCP_MODE=uvx` is set explicitly; never chosen automatically | `uvx --from teradata-mcp-server==0.2.6`, resolved from PyPI |
| `none` | nothing above applies | the launcher exits with instructions; nothing is installed silently |

`bash "${CLAUDE_PLUGIN_ROOT}/scripts/launch-mcp.sh" --dry-run` prints which mode will run. The two you are likely to
use are below: **bridge** is the fastest way in if a Teradata MCP server already runs somewhere, **local** runs the
bundled server yourself.

### Bridge to a server you already run

```bash
claude plugin install teradata-vantage@teradata-plugins --config mcp_url=http://127.0.0.1:8001/mcp/
```

The launcher connects through a pinned `mcp-remote`. No Python install, no credentials stored by the plugin. This
mode needs Node: `npx` is not part of Claude Code's native, Homebrew or WinGet installs, so check `node --version`
first.

No Node? Register the server natively instead of bridging:
`claude mcp add --transport http teradata http://<host>:8001/mcp/`. Tools then appear as `mcp__teradata__*`; the
hooks still guard them (they anchor on the tool-name suffix) but the plugin's agents and profiles do not see them,
and the plugin's own entry in `/mcp` stays failed until bridge or local mode is configured.

### Run the bundled server locally

Supply credentials by any one of these, in the order the launcher checks them:

1. **Plugin option** (stored by Claude Code, marked `sensitive`):
   ```bash
   claude plugin install teradata-vantage@teradata-plugins \
     --config database_uri='teradata://user:password@host:1025/your_default_db'
   ```
2. **Environment variable** `DATABASE_URI`, exported in the shell that launches Claude Code.
3. **A credentials file you write yourself** — the option that keeps the password out of the transcript entirely:
   ```bash
   # run this in YOUR OWN terminal, not in the Claude conversation
   /path/to/plugins/teradata-vantage/scripts/teradata-vantage-connect
   ```
   It prompts for the host, user and password without echoing, and writes `${CLAUDE_PLUGIN_DATA}/teradata.env`
   mode `0600`.

Then install the server once:

```
/teradata-vantage:setup install
```

That builds a virtualenv in the plugin's data directory from the **vendored** wheel plus a hash-pinned dependency
closure. It needs Python ≥ 3.11 (set `TERADATA_MCP_PYTHON=/path/to/python3.12` if the right interpreter is not the
default `python3`) and about 555 MB on disk on first install, measured. The `tdvs` extra additionally needs
**Python ≥ 3.12** — its pinned numpy publishes no 3.11 wheel — and the installer says so by name rather than
letting pip fail obscurely.

**Never paste a `teradata://user:password@host` URI into a Claude prompt.** The optional `enable_prompt_secret_guard`
option warns when you do.

### Check it worked

```
/teradata-vantage:setup            # runs doctor.sh and reports mode, credential source, guard state
/mcp                               # lists plugin:teradata-vantage:teradata as connected once a connection is configured
```

`/teradata-vantage:setup` works with no connection at all and tells you exactly what is missing.

**A failed MCP connection is cached for about 15 minutes.** After you fix the cause — install the server, supply the
credential, set the bridge URL — even a brand-new session can still report `Skipping connection (recent failure
cached; retries automatically in 15 min, or edit the plugin config to retry now)`. That is the cache talking, not a
second failure: editing the plugin's configuration retries immediately, otherwise wait it out.

## Try it

Four prompts that each put a different part of the plugin to work. Run them after connecting.

1. **Schema discovery** — `"What databases exist on this system, and what tables are in the largest one?"`
   Expect: the `explore` skill, `base_databaseList` then `base_tableList`, no permission prompts.
2. **The read guard actually guarding** — `"Run this against the database: SELECT 1; DROP TABLE customer_dim"`
   Expect: the `PreToolUse` read guard denies the call with a reason naming the multi-statement and the blocked verb,
   and Claude explains why rather than retrying.
3. **Dialect knowledge correcting a mistake** — `"Show me the top 10 rows of <db>.<table> using LIMIT 10"`
   Expect: `teradata-sql` activates and the statement comes back as `SELECT TOP 10` — `LIMIT` is a syntax error
   (`3706`) on Teradata.
4. **Multi-agent work** — `"Audit the health of every database on this system"`
   Expect: the read-only `health-audit` workflow. Needs the **Dynamic workflows** setting enabled.

## What's inside

### Skills — the knowledge, loaded when it is relevant

| Skill | Use it for |
|---|---|
| `setup` | Connecting, the doctor, installing the local server, tool profiles, customising the server |
| `explore` | What databases, tables, views and columns exist; DDL, sample rows, relationships |
| `query` | Answering a question with SQL, the write contract, error-driven repair |
| `health` | Is the system healthy; space, skew, sessions, flow control; who holds which rights and roles; why `2644` / `3541` happen |
| `tune` | Why a query is slow: EXPLAIN plans, statistics, primary index and skew, DBQL metrics |
| `profile` | Data quality: nulls, blanks, negatives, distributions, completeness |
| `archive` | Moving cold data to object storage as Parquet (NOS) or Apache Iceberg, and back |
| `vector-store` | Teradata Enterprise Vector Store, and in-database vectors (`VECTOR32`, `TD_VECTORDISTANCE`) |
| `pipelines` | dbt models through the dbt-teradata adapter and Airflow DAGs through the Teradata provider: the profile, incremental strategies, primary-index config, and which operator to use |
| `client-development` | Application code that talks to Vantage: `teradatasql`, `teradataml`, SQLAlchemy, JDBC; the qmark parameter style, session mode, pooling |
| `lineage` | What depends on an object, what feeds it, what breaks if it changes; cycles, root objects and migration waves, over an edge repository |
| `analytics` | Running analytics in the database instead of extracting to a notebook: the ClearScape `TD_*` fit/transform functions, and BYOM scoring of ONNX, PMML, H2O, Dataiku and DataRobot models |
| `teradata-sql` | *(background)* The dialect rules and error-code repair, applied whenever SQL is written |
| `teradata-recovery` | *(background)* The system will not accept logons: `pdestate`, FATAL vprocs, crash loops. Requires OS access to the Teradata node |
| `sql-files` | *(background)* Reading and writing `.sql` / `.bteq` / `.btq` / `.ddl` / `.dml` scripts on disk: BTEQ structure, return codes, and what the sqlfluff hook does |

Depth lives in each skill's `references/` — roughly 340 KB of curated Teradata material in total, loaded only when
the skill needs it.

### Agents — delegate a whole job

| Agent | Does |
|---|---|
| `explorer` | Read-only schema and data-quality exploration. Cannot write or edit files |
| `dba` | System health, permissions, DBQL, and administrative changes — each write stated with its blast radius and approved before it runs |
| `tuner` | Read-only performance work: plans, statistics, skew, DBQL history, SQL review |
| `archivist` | Cold-data archiving to Native Object Store or Iceberg, and back. Verifies the copy round-trips before anything is deleted |
| `vector` | The Enterprise Vector Store lifecycle: create, inspect, search, repair a `CREATE FAILED` store, destroy. Every destructive step prompts |
| `cartographer` | Object lineage and dependency analysis. Owns the edge repository the `graph_*` tools require. Read-only, never emits a `DROP` |
| `auditor` | Restricted read-only probe, dispatched by the workflows; not for direct use |

```bash
claude --agent teradata-vantage:dba      # start a session as the DBA agent
```

### Workflows — multi-agent jobs, all read-only

| Workflow | Does |
|---|---|
| `health-audit` | One system probe plus one space/skew probe per database in parallel, then a verdict-first report |
| `profile-database` | Every base table in a database profiled and graded through a pipeline |
| `sql-review` | Review a set of `.sql` files, then adversarially verify each finding through three lenses |
| `drop-impact` | Blast-radius report before dropping objects. Never emits a `DROP` |

They launch from the relevant skill, or directly: `Workflow({name: "teradata-vantage:health-audit", args: {...}})`.
Workflows require the **Dynamic workflows** setting to be enabled.

### Hooks — the guards

| Hook | What it does |
|---|---|
| Read guard | `base_readQuery` must be ONE statement starting `SELECT`, `WITH`, `EXPLAIN`, `SHOW` or `HELP`, and must not invoke `WRITE_NOS(` — SELECT-shaped, but it writes objects to your store. Anything else is denied with a reason |
| Write gate | Destructive `base_writeQuery` statements raise an approval prompt naming the object, and warn on a WHERE-less `DELETE`/`UPDATE` |
| Destructive tool gate | `tdvs_destroy`, `tdvs_update`, the vector-store permission tools, `bar_manageJob`, the clustering and RAG pipelines, and `base_saveDDL` (which writes a file to disk) always prompt |
| Host-ops gate | Prompts before `dbscontrol`, `tpareset`, `vprocmanager` and `ctl` — matched as the command being invoked, so `ssh node 'ctl'` prompts and `ctl.log` or `kubectl` do not. Only in sessions that have a Teradata connection configured |
| Result coach | Explains a Teradata `Error NNNN` in the result, and asks for an aggregated re-run when a result is huge |
| SQL lint | Runs `sqlfluff` on edited `.sql`/`.bteq` files **if you already have it installed**. Never installs it, never nags |
| Session status | One line at session start: mode, credential source (name only), guard state, version |

Switches: `TERADATA_SQL_GUARD_MODE=enforce|audit`, `TERADATA_ALLOW_WRITES=0`, `TERADATA_MAX_RESULT_CHARS`,
`TERADATA_SQL_LINT=off`. Full detail: `skills/setup/references/safety-model.md`.

### Also included

Four curated server tool profiles — `tv_all` (default), `tv_readonly`, `tv_analyst`, `tv_dba`. Select one with
`TERADATA_MCP_PROFILE`. There is also an eval suite under `evals/`, run with `claude plugin eval .`.

### Optional surfaces, and how to turn them on

Three surfaces ship switched off or unselected, because none of them should change your session without you
asking. None is required, and each is one step:

| Surface | Turn it on | What it does |
|---|---|---|
| **Teradata DBA report** output style | `/config` → Output style → *Teradata DBA report* | Verdict first, then evidence, then the next action. Suits health and audit work; leave it off for ordinary coding |
| **Warehouse Dark** theme | `/theme` → *Warehouse Dark* | A dark theme tuned so the guards' prompt colour is distinguishable from the error colour |
| **Health monitor** | Armed by the `health` skill once a credential file is stored | Periodic local health check. `TERADATA_MONITORS=0` disables it |

The output style is deliberately not forced. A plugin that silently rewrites how every answer in your session is
formatted is doing something you did not ask for, so this one waits to be chosen.

## Safety model — read this once

**The guards are mistake-prevention for an agent, not a security boundary.** They exist so a model does not destroy
data by accident. They are not access control: the database's own GRANTs, roles and row-level security remain the
only real control. Connect with a least-privilege account.

The two gates that stand between the agent and a write fail **to ask** if they themselves crash or are handed a
payload shape they do not recognise; the coaching and lint hooks fail silent; the host-ops gate fails open because it
guards commands, not data. A hook that cannot *start*, or that exceeds its timeout, is a different case that Claude
Code treats as non-blocking — see [Platform support](#platform-support). Guards cannot see tools
whose names come from the database (`registry` group) or custom YAML tools — those are governed only by your GRANTs.

Note also that upstream `teradata-mcp-server` 0.2.6, the version this plugin vendors, exposes **no write tool at
all** — `base_readQuery` is its only SQL executor. With the read guard in `enforce` mode the bundled server has no
write path. The write gate matters when you bridge to a server that does expose one.

`allowed-tools` in a skill is a per-turn *pre-approval*, not a restriction. `disallowed-tools` is the field that
restricts.

## Platform support

Developed and tested on Linux and macOS. On Windows the plugin needs a POSIX shell: `.mcp.json` and the session-start
hook both run `bash`, so without Git Bash or WSL the server does not start at all. The guard hooks are launched as
`python3`, which a python.org install does not provide — it creates `python.exe` and the `py` launcher — so a
Microsoft Store Python or an alias on `PATH` is needed for them. The bundled local server additionally wants Python
≥ 3.11, or `TERADATA_MCP_PYTHON` pointing at one.

That distinction matters. A hook that raises inside its own script still returns a decision, which is why the read
guard, write gate and destructive-tool gate fail to *ask*. But per Claude Code's hook semantics a hook that cannot
**start** — no `python3` executable on `PATH` — or that exceeds its 10 s timeout is non-blocking: Claude Code reports
a hook error and the tool call proceeds through the normal permission flow. On a host where the guards cannot run
they are not a gate at all, so connect with a least-privilege account, which is the right posture anyway.

## Troubleshooting

Start with `/teradata-vantage:setup`. It runs `scripts/doctor.sh`, which reports the mode that will run, the
credential source, the runtimes present and the guard state, and it works with nothing configured.
`skills/setup/references/troubleshooting.md` is the symptom-keyed list — server failed in `/mcp`, error `8017` with
correct credentials, error `3524`, a hung logon, duplicate toolsets, a stale venv pin, silent linting. The launcher's
own diagnostics go to stderr, which `claude --debug` shows.

One timing note. `.mcp.json` sets a per-server `timeout` of 180 000 ms: a hard wall clock on each tool call, which
also acts as a floor on the idle timeout, so an idle call is never aborted sooner. On Claude Code 2.1.212 or later a
main-conversation tool call still running after two minutes moves to a background task, so a hung logon shows up in
`/tasks` and fails there at 180 s rather than freezing the session. Calls made by this plugin's agents and workflows,
and calls in non-interactive `claude -p` runs, are not backgrounded — they block for the full 180 s.

## Testing

Two tiers.

**Unit** — `python3 -m pytest scripts/tests -q` from the plugin directory (needs `pytest` and `pyyaml`; no network,
no Teradata). Over 400 tests covering the guards' allow / ask / deny decisions and each Python hook's fail direction —
the read guard, write gate and destructive-tool gate fail to ask on an internal crash, the coach, lint and secret
guard fail silent, the host-ops gate fails open — plus the validator, the leak scan and the launcher scripts. The one
gap: the bash session-start hook has no test.

**Behaviour** — thirteen cases under `evals/` in the layout `claude plugin eval` reads (`prompt.md` plus
`graders/`), with a mock MCP layer so they run without a database. They pin the dialect corrections, the qmark
parameter style, the `2644` / `3541` space diagnoses, schema from tools rather than memory, blast radius before a
`DROP`, archive verify-before-delete, and the read-guard behaviour in both directions — it must refuse a
multi-statement write and must NOT refuse `EXPLAIN` or a `CASE ... END`. `claude plugin eval .` scores them against a
no-plugin baseline arm where that command is enabled for your account — it is in early access — and otherwise each
`prompt.md` is a literal user turn you can run by hand. See [evals/README.md](evals/README.md).

CI runs the unit tier, `validate_plugin.py`, the vendored-wheel hash check and `claude plugin validate --strict` on
every pull request and on pushes to `main`.

## Uninstall

```bash
claude plugin uninstall teradata-vantage@teradata-plugins
```

When that was the last scope it was installed in, Claude Code also deletes the plugin's data directory under
`~/.claude/plugins/data/` — the local server virtualenv, `teradata.env` and any audit log go with it. Pass
`--keep-data` to preserve them; the interactive `/plugin` interface shows the size and asks first. The `database_uri`
and `mcp_url` options are stored by Claude Code itself (secure storage for the sensitive one), so see the Claude Code
plugins reference for how it handles them. Two leftovers uninstall does not touch: bridge mode leaves `mcp-remote` in
your npm cache, and if you ran `scripts/teradata-vantage-connect` from a checkout rather than from the installed
plugin, the `teradata.env` it wrote sits under a different data directory — delete that one yourself.

## Network behaviour

- **Telemetry: none.** No usage data, prompts, SQL or results leave your machine.
- **At session start: no network at all.** Nothing is installed or fetched.
- **Only on your explicit action:** bridge mode runs one pinned npm package (`mcp-remote@0.8.3`) through `npx`;
  `/teradata-vantage:setup install` fetches hash-pinned dependencies from PyPI after telling you the size. The MCP
  server itself is vendored in `servers/` with a recorded SHA-256, so it is never downloaded.
- **Hooks run locally, unsandboxed, with your permissions.** They are short Python 3 and bash scripts under
  `scripts/`; read them.
- **SQL text is read in-process by the hooks and never persisted or transmitted.**

See [SECURITY.md](https://github.com/fhawarneh/teradata-vantage-plugin/blob/main/SECURITY.md) for the full trust model and how to report a vulnerability, and [PRIVACY.md](https://github.com/fhawarneh/teradata-vantage-plugin/blob/main/PRIVACY.md) for exactly what the plugin reads, writes and sends. Both live at the repository root, so they are NOT in the installed plugin directory — follow the links.

## Requirements

| For | You need |
|---|---|
| Claude Code | Tested on 2.1.263. No lower bound has been measured — older releases may lack features used here, such as dynamic workflows |
| Bridge mode | Node.js with `npx` — **not** bundled with Claude Code's native, Homebrew or WinGet installs; check `node --version` — and a reachable Teradata MCP server |
| Local server | Python ≥ 3.11, ~555 MB on disk on first install, network reach to your Teradata system on port 1025 |
| The guard hooks | a `python3` executable on `PATH`; `bash` for the server launcher and the session-start hook — see [Platform support](#platform-support) |
| Optional `.sql` linting | `pip install sqlfluff` — the hook is silent without it |
| Optional vector store | The `tdvs` extra (`--config server_extras=tdvs`), ~170 MB more, and **Python ≥ 3.12** |
| Workflows | The **Dynamic workflows** setting enabled in Claude Code |

## MCP prompts and resources

The bundled server also exposes prompts (surfaced as `/mcp`-namespaced commands) and resources such as
`graph://edge-contract`; glossary resources appear once a glossary is defined. `skills/setup/references/tool-inventory.md`
lists the live inventory captured from the vendored server.

**If you already have a Teradata MCP server configured in your project's own `.mcp.json`, you will see two sets of
tools.** The plugin's are namespaced `mcp__plugin_teradata-vantage_teradata__…`; the hooks match on the tool-name
suffix, so they guard both. Disable one of the two if the duplication is confusing.

## Attribution

Bundles [`teradata-mcp-server`](https://github.com/Teradata/teradata-mcp-server) (MIT, Copyright Teradata),
vendored unmodified under `servers/` with its licence text. See `NOTICE.md`.

Licensed MIT. Contributions welcome — see [CONTRIBUTING.md](https://github.com/fhawarneh/teradata-vantage-plugin/blob/main/CONTRIBUTING.md), and read the rule at the top of
it first: a wrong fact is worse than a missing one.
