# Safety model - what the plugin's hooks do, how each one fails, how to enable/disable writes, audit mode, and what the guards are NOT.

## The one-paragraph version

`base_readQuery` is held read-only by a Claude Code `PreToolUse` hook; destructive `base_writeQuery` statements, the
destructive vector-store/backup tools and the two core tools that write on their own (`sql_Execute_Full_Pipeline`,
`rag_Execute_Workflow`) raise an approval prompt; a `Bash` gate asks before Teradata host-admin
commands; a `PostToolUse` coach explains Teradata error codes and oversize results; an optional lint runs on `.sql`
edits. These are **mistake-prevention guards for an agent, not a security boundary**: the database's own GRANTs, roles
and row-level security remain the only real access control. Use `tv_readonly` (a server profile) when the agent must
not be able to write at all.

## Hooks (all `hooks/hooks.json`, command hooks, Python 3 standard library)

| # | event / matcher | script | what it decides | if the script crashes |
|---|---|---|---|---|
| 1 | PreToolUse `mcp__.*__(base_readQuery\|execute_tool)$` | `sql_read_guard.py` | statement must be ONE statement starting `SELECT`, `WITH`, `EXPLAIN`, `SHOW` or `HELP`; blocked verbs anywhere outside quotes/comments: `insert update delete drop create replace merge grant revoke alter rename call exec abort truncate` -> `deny`, remedy "rewrite as a single SELECT (use base_writeQuery for writes)" - EXCEPT after a leading `EXPLAIN`, which returns a plan and runs nothing, so `EXPLAIN DELETE/INSERT/UPDATE/MERGE/DROP` pass; a second statement still denies as `multi_statement`, remedy "send one statement per call"; a non-dict `tool_input` -> `ask` | `ask` ("read guard crashed: ...") |
| 2 | PreToolUse `mcp__.*__(base_writeQuery\|execute_tool)$` | `write_gate.py` | `DELETE DROP TRUNCATE INSERT UPDATE MERGE ALTER MODIFY RENAME REPLACE GIVE CREATE DATABASE/USER GRANT REVOKE ABORT SESSION` -> `ask`, naming the target object and warning on WHERE-less DELETE/UPDATE; a statement that is only `DROP FOREIGN TABLE` -> allow (metadata only, no data deleted) - the exemption is per STATEMENT, so a compound that opens with it still asks for every later destructive statement; other DDL (`CREATE TABLE/VIEW/AUTHORIZATION`, `CREATE FOREIGN TABLE`, `COLLECT STATISTICS`, `WRITE_NOS`) -> allow; a non-dict `tool_input` -> `ask`; `TERADATA_ALLOW_WRITES=0` -> deny everything | `ask` |
| 3 | PreToolUse `mcp__.*__(tdvs_destroy\|tdvs_update\|tdvs_revoke_user_permission\|tdvs_grant_user_permission\|bar_manageJob\|sql_Execute_Full_Pipeline\|rag_Execute_Workflow)$` | `destructive_tool_gate.py` | the tool NAME is the classification -> unconditional `ask` naming the object | `ask` |
| 4 | PreToolUse `Bash` | `host_ops_gate.py` | only in sessions where a Teradata connection is configured; `ask` on `dbscontrol`, `tpareset`, `tpa stop/start`, `vprocmanager` anywhere in the command (absolute paths and quoted `ssh` payloads included), on `ctl` only where it is the command being invoked (so `ctl.log` and a commit message mentioning ctl do NOT prompt), and on `rm ... PanicLoopDetected`; the reason says "record the current value before modify; keep the rollback value" | allow |
| 5 | PostToolUse `mcp__.*__(base_readQuery\|base_writeQuery\|base_tablePreview\|qlty_.*\|dba_.*\|tdvs_.*\|execute_tool)$` | `result_coach.py` | result > `TERADATA_MAX_RESULT_CHARS` (120000) -> adds a note asking for GROUP BY / WHERE / TOP n; a Teradata `Error NNNN` in the result -> "what it actually means + do this" from `scripts/data/error_codes.yaml` | silent |
| 6 | PostToolUseFailure, same matcher | `result_coach.py` | same coaching when the server returned an error (PostToolUse fires only on success) | silent |
| 7 | SessionStart `startup\|resume` | `session_preflight.sh` | up to 6 lines of context: mode, credential source (name only), guard mode, writes on/off, pinned version, sqlfluff present | never fails |
| 8 | UserPromptSubmit | `prompt_secret_guard.py` | OFF unless plugin option `enable_prompt_secret_guard` is on, and only in Teradata-configured sessions; warns (or blocks, per `TERADATA_PROMPT_SECRET_GUARD`) when a prompt contains `scheme://user:password@` | allow |
| 9 | PostToolUse `Write\|Edit\|MultiEdit` | `sql_lint.py` | `.sql .bteq .btq .ddl .dml` only; runs `sqlfluff` if installed (`config/sqlfluff-teradata.cfg`, dialect teradata) and reports up to 15 findings; `TERADATA_SQL_LINT=off` disables | silent |

Fail directions are deliberate: the two gates that stand between the agent and a write fail to ASK; the coaching and
lint hooks fail silent; the Bash gate fails open because it guards host commands, not data.

"Fail to ask" covers an in-script crash AND input the guard cannot read: stdin that arrived but is not a JSON object
(malformed text, or valid JSON that is not a mapping) makes hooks 1 and 2 ask, because a guard that cannot see the
call cannot certify it. Genuinely EMPTY stdin passes - there is nothing to check. One direction it does NOT cover: a
hook that never STARTS. If `python3` is not on `PATH`, or the hook exceeds its 10-second timeout, Claude Code has no
decision to apply and the call proceeds. Keep `python3` available; see the README's platform-support note.

Hook 3 classifies by tool NAME on purpose: upstream 0.2.6 annotates its `tdvs_`, `sql_` and `rag_` tools
`readOnlyHint=True`, as it does `base_saveDDL`, which writes a `.sql` file to disk - see "Known gaps in 0.2.6" in
`servers/VENDORED.md`.

Payload-shape drift: hooks 1 and 2 read the statement out of `tool_input`. A `tool_input` that is NOT a dict - or an
`execute_tool` call whose `arguments` is not a dict - is not a shape the documented contract produces, so both hooks
`ask` rather than pass. A guard that passed silently there would have gone inert without saying so. An empty dict, and
a dict with no `sql` key, are legitimate (a registry tool reached through `base_readQuery` carries no SQL) and pass.

Relevance gate: hooks 4 and 8 run their logic only when the session has a Teradata connection configured
(`DATABASE_URI`, `TERADATA_MCP_URL`, the plugin options, or `${CLAUDE_PLUGIN_DATA}/teradata.env`). Hooks 1-3 and 5-6
are already scoped by their tool-name matchers.

## The bundled server has no `base_writeQuery` - read this before reasoning about writes

Upstream teradata-mcp-server 0.2.6 (the wheel this plugin vendors) registers `base_readQuery` as its ONLY SQL
executor; there is no `base_writeQuery` (the live tool inventory in `references/tool-inventory.md` shows the `base`
group without it). Consequences:

- Read guard (hook 1) is the whole story for SQL the agent writes itself: in `enforce` mode no statement it composes
  can change anything. It is NOT the whole write story. Two CORE tools issue their own DDL and DML:
  `sql_Execute_Full_Pipeline` drops and re-creates its clustering tables in the configured feature database, and
  `rag_Execute_Workflow` creates and inserts into its query table. Neither passes through hook 1; hook 3 prompts
  before each of them, and the database's GRANTs decide the rest.
- Write gate (hook 2) and the `tv_readonly`/`tv_dba` distinction only matter when you bridge to a server that DOES
  expose `base_writeQuery` (some deployments add one). Then destructive statements prompt as described.
- `TERADATA_SQL_GUARD_MODE=audit` on the bundled server means writes sent to `base_readQuery` are executed with NO
  prompt (DDL auto-commits; DML commits through the driver). Use audit mode for a soak, never as a way to write.
- `base_readQuery(persist: true)` is the supported scratch path: the server wraps your SELECT in
  `CREATE VOLATILE TABLE ... WITH DATA`; the guard sees only your SELECT.

## Switches (set in the shell that launches Claude Code, or in a settings `env` block; new session to apply)

| variable | values | effect |
|---|---|---|
| `TERADATA_SQL_GUARD_MODE` | `enforce` (default) / `audit` | `audit` lets every `base_readQuery` call through and writes a `WOULD_BLOCK <reason>: <statement>` line to stderr for each one the guard would have denied. The statement is masked first - quoted literals and comments are blanked, so values do not reach the log - but bare identifiers (database, table and column names) are preserved, because the reason is usually only legible with them. Unknown values behave as `enforce`. |
| `TERADATA_ALLOW_WRITES` | `1` (default) / `0` | `0` makes the write gate DENY every destructive `base_writeQuery` statement instead of asking. Non-destructive DDL still passes; use profile `tv_readonly` to remove the write tool entirely. |
| `TERADATA_MAX_RESULT_CHARS` | integer, default 120000 | size above which the coach asks for an aggregated re-run |
| `TERADATA_SQL_LINT` | `on` (default) / `off` | the `.sql` lint hook |
| `TERADATA_PROMPT_SECRET_GUARD` | `warn` / `block` | behaviour of hook 8 when the plugin option enables it |

How to enable writes: connect to a server that exposes `base_writeQuery` (the bundled 0.2.6 server does not); under
`tv_all`/`tv_dba` it is then visible and each destructive statement raises Claude Code's native permission prompt
naming the object. That prompt is intentional; never try to route a write through `base_readQuery` to avoid it (the
read guard denies it anyway).

How to disable writes: `TERADATA_ALLOW_WRITES=0` (gate denies), or `TERADATA_MCP_PROFILE=tv_readonly` (the profile
never exposes a write tool even on a server that has one - the stronger option), or both. On the bundled server the
read guard leaves no SQL write path, with three documented exceptions worth knowing: `base_readQuery(persist: true)`
has the SERVER run `CREATE VOLATILE TABLE ... WITH DATA` (session-scoped, dropped at logoff, invisible to a guard
that inspects only the SQL text); `WRITE_NOS(` is SELECT-shaped and writes objects to your store, so the read guard
denies it by name; and `base_saveDDL` writes a file to disk, so it is in the destructive-tool gate and excluded from
`tv_readonly` / `tv_analyst`.

## Audit mode is the rollout tool

An over-broad guard fails silently: the data tool becomes an error tool and the agent starts narrating SQL as text,
which reads as a model problem. Before tightening anything, run a normal session in `audit` mode and grep stderr for
`WOULD_BLOCK`. Zero lines -> switch to `enforce` with confidence. Lines present -> each carries the reason
(`not_a_select`, `multi_statement`, `blocked_verb:<verb>`) and the first 500 characters of the statement.

## `allowed-tools` in a skill is a pre-approval, not a guard

Several skills list read-only tools under `allowed-tools`. That only spares the user a permission prompt for those
tools while the skill is active. It does not restrict what the agent may call, and it does not bypass the hooks: a
`base_readQuery` call is still read-guarded, a `base_writeQuery` call still asks. `disallowed-tools` (set on
`explore`, `profile` and `tune`) is the field that restricts. Never describe `allowed-tools` as a safety control.

## What the guards do not cover

- **Registry-sourced tools.** With the `registry` group, tool names come from database views and are site-specific;
  the matchers above anchor on known suffixes (`base_readQuery`, `base_writeQuery`, `execute_tool`, `tdvs_*`,
  `bar_manageJob`) and cannot know those names. A registry tool that writes is gated only by the database's GRANTs.
- **Custom YAML tools** (`<domain>_objects.yml`) run whatever SQL you declared; the read guard does not see them. Keep
  custom tool SQL read-only unless you intend otherwise.
- **The bridge server's own users.** In bridge mode the hooks run on your side; other clients of that server are not
  affected.
- **The upstream `HOOKS_MODULE` audit log** (`TERADATA_MCP_AUDIT_LOG`) is observe-only by contract: it cannot deny or
  modify a call, and an exception inside it is swallowed by the server. It is a log, never a control.
- **Statement semantics.** The read guard is a mechanical check (single statement, allowed first keyword, no blocked
  verbs outside strings/comments). It does not reason about table sensitivity, row counts or who the user is; that
  belongs to the database's permission layer.

## What "safe" looks like in practice

1. Read path: any number of `SELECT`/`WITH`/`EXPLAIN`/`SHOW`/`HELP` calls, no prompts. `EXPLAIN` covers DML as well -
   `EXPLAIN DELETE FROM <db>.<table> WHERE ...` returns the plan and executes nothing - which is how a write statement
   gets reviewed and tuned on a server that exposes no write tool.
2. Write path: a `COUNT(*)` preview through `base_readQuery`, then ONE `base_writeQuery` statement, which prompts with
   the object name and a WHERE-less warning if applicable, then a verification `SELECT`.
3. Destructive tool: prompt every time, no exceptions, even if the user asked twice.
4. Host commands: prompt with the reminder to record the rollback value.
