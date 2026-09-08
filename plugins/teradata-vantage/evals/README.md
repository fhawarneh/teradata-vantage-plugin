# Eval cases

Behaviour tests for the `teradata-vantage` plugin, in the shape `claude plugin eval` reads: each
case is a directory holding `prompt.md` — YAML frontmatter plus the literal user turn — and one
file per grader under `graders/`. Thirteen cases.

## Running them

```bash
cd plugins/teradata-vantage

# PR-time smoke: one run per case, no baseline arm
claude plugin eval . --runs 1 --ablation none --no-publish

# full run: three runs per case against a no-plugin baseline arm
claude plugin eval . --judge-model sonnet

claude plugin eval . --case 'read-guard*'      # one family
claude plugin eval . --threshold 0.8           # non-zero exit below 0.8 (default is 1.0)
```

`claude plugin eval` is in early access. If it is not enabled for your account the command
reports that `plugin eval` is currently in early access and exits without running anything. The
CLI names `CLAUDE_CODE_WALNUT_SPIRE=1` — set in the shell, in `~/.claude/settings.json` under
`env`, or in managed settings — as the enablement variable for machines that cannot receive the
per-organization rollout. Everything measured below was measured with it set, on Claude Code
2.1.263.

Each `prompt.md` body is a literal user turn, so a case also works as a manual smoke check:

```bash
claude -p --plugin-dir . \
  "$(awk 'f{print} /^---$/{n++; if(n==2) f=1}' evals/dialect-top-not-limit/prompt.md)"
```

The `awk` drops the frontmatter. Nine cases also set `append_system_prompt`, which a manual run
like this does not apply — see "Why some cases steer the model" below.

## Cost and time

Every case pins `model: sonnet`. Without that pin a run inherits whatever model the
contributor's account defaults to, which makes scores incomparable between contributors and, on
a larger default model, roughly doubles the bill. `--model <name>` overrides the pin for one
invocation; `--max-cost-usd <n>` aborts with exit 2 and partial results if a budget is hit.

Measured on this machine, all thirteen cases:

| Invocation | Agent runs | Time | Cost |
|---|---|---|---|
| `--runs 1 --ablation none` (default haiku judge) | 13 | 8 min | $1.86 |
| `--runs 1 --ablation none --judge-model sonnet` | 13 | 8 min | $1.88 |
| `--runs 2 --ablation none --judge-model sonnet` | 26 | RUN11TIME | RUN11COST |

`claude plugin eval .` with nothing else is three runs per case against a with/without baseline
arm — 78 agent runs, so budget for roughly six times the first row.

The LLM graders were calibrated against `--judge-model sonnet`, which is stricter than the
default haiku judge: rubric wording that haiku waves through can fail under sonnet. If you add a
case, calibrate it the same way, and prefer a `regex` or `tool_used` grader wherever the
behaviour is mechanically observable — an LLM grader pointed at the whole `trace` is noisy
enough to fail a correct answer, which is why every LLM grader here reads only the final
message.

## The mocked Teradata server

`mocks/teradata/` is a stand-in for the plugin's MCP server, so cases that need a tool run
without credentials and without a live warehouse. `teradata` is the server's key in `.mcp.json`;
the stand-in is registered under the same name, so tools arrive as
`mcp__plugin_teradata-vantage_teradata__<tool>` and the plugin's `PreToolUse` matchers fire on
them exactly as they would against the real server.

- `_tools.json` is the real `tools/list` response of the bundled teradata-mcp-server 0.2.6 for
  the two mocked tools, so the model sees their genuine descriptions and schemas.
- `base_readQuery.md` and `base_columnDescription.md` are `type: fixed` responders. The body is
  the canned result, in the `{"status": "success", "results": [...]}` shape `create_response` in
  the upstream server produces.

The directory is suite-wide: every case runs against it. `--mocks off` spawns the real server
instead, which then needs a working `DATABASE_URI`.

Two consequences of a fixed responder, both learned the hard way:

- It answers every call with the same body regardless of arguments. The four SQL-authoring cases
  therefore tell the model not to call a tool at all; left free, it looks the schema up, finds
  the fixture does not carry the columns the prompt names, and answers about the mismatch
  instead of writing the SQL.
- Do not put a "this is a mock" note in the canned body. An earlier draft did, and the model
  correctly refused to present fixture rows as a result — which failed four cases for a reason
  that had nothing to do with the plugin.

## Why some cases steer the model

Nine cases carry an `append_system_prompt`. It never tells the model what to answer; it fixes
what the case is measuring:

- The three `read-guard-*` cases need the guard, not the model's own caution, to be the thing
  that decides — so they instruct the model to send the statement to the tool once, verbatim.
  Without that, the model usually refuses `SELECT 1; DROP TABLE ...` on its own, the hook never
  runs, and the case scores the model rather than the plugin. The model refusing a stacked DROP
  by itself is good behaviour and is covered separately by `drop-requires-impact-first`.
- The four SQL-authoring cases grade the statement the model writes, so they tell it not to call
  tools or look the schema up.
- `archive-verifies-before-deleting` grades an ordered plan, so it tells the model to set the
  plan out against the fixture rather than stop and ask for a live connection.
- `explore-uses-tools-not-memory` needs the lookup to happen, so it says the tools answer from a
  fixture. The plugin's own SessionStart line reports `server mode=none` in the sandbox, and
  without this the model sometimes declines to call a tool that is in fact available.

## What these cases are for

| Case | Pins |
|---|---|
| `dialect-top-not-limit` | `LIMIT` is a syntax error on Teradata; the skill must correct it and say why |
| `null-comparison` | `= NULL` silently returns zero rows; date math stays Teradata-valid |
| `reserved-word-alias` | error 3707 diagnosed as a reserved-word alias, not a GROUP BY problem |
| `decimal-division-precision` | DECIMAL division truncates scale; money needs an explicit cast |
| `qmark-not-percent-s-parameters` | `teradatasql` is `qmark`: `?`, not `%s`, and never an f-string |
| `space-3541-diagnosis` | error 3541 is PARENT space, not system free space — the misdiagnosis that costs hours |
| `dbc-full-2644-diagnosis` | error 2644 on DBC stops every write system-wide; purge logs, not the dictionary |
| `explore-uses-tools-not-memory` | schema comes from a tool result, never from invention |
| `drop-requires-impact-first` | blast radius before a `DROP`; "no DBQL usage" is not "unused" |
| `archive-verifies-before-deleting` | archive, verify the count, then delete by membership — in that order |
| `read-guard-blocks-write-through-read-tool` | the read guard refuses a stacked statement, and the refusal is described honestly |
| `read-guard-allows-explain` | `EXPLAIN DELETE` is a read and must NOT be refused |
| `read-guard-allows-case-end` | `CASE ... END` must NOT be refused — the blocklist mistake two other servers shipped |

Nothing here needs production data: every object named is a generic placeholder
(`analytics.sales_fact`, `analytics.customer_dim`).

## Grading

Each case has one `llm` grader over the final message. Where the behaviour is mechanically
observable there are `regex` and `tool_used` graders as well, and those are what make the
read-guard family a real gate rather than a description of one:

- `tool_used` on `mcp__plugin_teradata-vantage_teradata__base_readQuery` proves the call was
  attempted (a hook-denied call still counts as attempted), with `max: 1` where the case also
  forbids a retry.
- `regex` over the `trace` for `read-only: multiple statements are not allowed` proves the hook
  itself, not the model, produced the refusal.
- `regex` for a value that appears only in the mock's canned body proves whether the call
  actually reached the server: absent in the blocking case, present in the two allowing cases.
  A pattern over the JSON envelope does not work — the trace is JSONL, so quotes inside a tool
  result are escaped and `"status": "success"` never matches literally. That grader silently
  passed in every arm until it was replaced.

These were checked against deliberately regressed copies of the plugin, not just against a
working one:

| Regression injected in a scratch copy | Result |
|---|---|
| `sql_read_guard` removed from `hooks.json` | `read-guard-blocks-…` 1.00 → 0.25 |
| `end` added to `BLOCKED_VERBS`, EXPLAIN exemption removed | `read-guard-allows-explain` and `read-guard-allows-case-end` 1.00 → 0.25 each |

## Adding a case

Create `evals/<case-name>/prompt.md` with frontmatter and the user turn, and one file per grader
under `evals/<case-name>/graders/`. Frontmatter keys the prose loader accepts include `model`,
`max_turns`, `timeout_seconds`, `allowed_tools`, `append_system_prompt`, `env`, `runs`, `name`,
`description`, `tags` and `plugins`; a grader file's frontmatter needs `type:`
(`regex` | `tool_used` | `tool_order` | `file_exists` | `llm` | `baseline`) and its body is the
pattern or the rubric. A `tool_used` grader on a tool needs that tool in `allowed_tools` — except
mocked tools, which are allowed automatically.

Write the criteria as checkable conditions, always say what makes it FAIL, and say explicitly
what does NOT count against the answer: a rubric that only describes success passes weak
answers, and one that leaves a true-but-unlisted addition ambiguous fails good ones.

Then prove the case can fail for the right reason. Run it, and run it again against a copy of
the plugin with the behaviour broken; a case that has never been seen to fail is not yet a test.

## Manifest note

`claude plugin eval` takes its case directory from `experimental.evals` in `plugin.json` and
falls back to `evals/`. This plugin uses the fallback and declares nothing, deliberately:
`claude plugin validate --strict` accepts an `experimental.evals` key (string or array), but
`scripts/validate_plugin.py` allows a fixed list of top-level keys that does not include
`experimental`, and rejects it as "unknown top-level key `experimental` (fails claude plugin
validate --strict)" — which is not true of this key. Adding the declaration today would break
the repository's own validator, so moving the cases out of `evals/` needs that allowlist widened
first.
