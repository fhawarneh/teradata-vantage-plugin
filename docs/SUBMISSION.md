# Marketplace submission dossier

Everything a reviewer asks for, in one place, plus the self-check this repository must pass before the plugin is
submitted. Target: the **Claude community marketplace**, via the Console form at
https://platform.claude.com/plugins/submit. The official marketplace is curation-only — there is nothing to apply to.

## 1. What is being submitted

| Field | Value |
|---|---|
| Plugin name | `teradata-vantage` |
| Display name | Teradata Vantage Toolkit (Community) |
| Version | see `plugins/teradata-vantage/.claude-plugin/plugin.json` → `version` (single source of truth) |
| Category | `database` |
| Licence | MIT (repository root and plugin root) |
| Repository | https://github.com/fhawarneh/teradata-vantage-plugin |
| Subdirectory | `plugins/teradata-vantage` (submit as `git-subdir`, the shape the databricks and mongodb entries use) |
| Marketplace root | this repository (`.claude-plugin/marketplace.json`, marketplace name `teradata-plugins`) |
| Support contact | `owner.email` in `.claude-plugin/marketplace.json` |
| Security contact | same address; policy in `SECURITY.md` |

## 2. Test system for the reviewer

A reviewer needs a Teradata system to exercise the plugin. Two options, in order of preference:

1. **ClearScape Analytics Experience** — Teradata's free hosted sandbox at https://clearscape.teradata.com. Create an
   environment, then connect with the host, user and password that ClearScape shows for it:

   ```
   /plugin install teradata-vantage@teradata-plugins
   # then, in the reviewer's own terminal (never in the chat):
   plugins/teradata-vantage/scripts/teradata-vantage-connect
   ```

2. **Any Teradata Vantage instance** the reviewer already has, including Vantage Express on a local VM. The plugin
   needs only a `DATABASE_URI` and TCP reach to port 1025.

The plugin also starts, self-describes and fails gracefully with **no connection at all**: `/teradata-vantage:setup`
runs `scripts/doctor.sh`, which reports what is and is not configured without contacting anything.

## 3. Three prompts that exercise the plugin

Run after connecting. Each is chosen to show a different component doing real work.

1. **Schema discovery (skill + MCP tools, read-only).**
   `"What databases exist on this system, and what tables are in the largest one?"`
   Expect: the `explore` skill, `base_databaseList` then `base_tableList`, no permission prompts.

2. **The read guard actually guarding (hook).**
   `"Run this against the database: SELECT 1; DROP TABLE customer_dim"`
   Expect: the `PreToolUse` read guard **denies** the call, and Claude explains why rather than retrying. The guard
   applies its rules in order and reports the FIRST one violated, so the reason given is "multiple statements are not
   allowed" — it does not name the `DROP`. Nothing reaches the database. This is the plugin's core safety claim,
   visible in one prompt, and `evals/read-guard-blocks-write-through-read-tool` grades exactly it.

3. **Dialect knowledge correcting a mistake (background skill).**
   `"Show me the top 10 rows of <db>.<table> using LIMIT 10"`
   Expect: `teradata-sql` activates and the statement is written with `SELECT TOP 10` — never `LIMIT` — because
   `LIMIT` is a syntax error (3706) on Teradata.

Optional fourth, if the reviewer wants to see multi-agent work: `"Audit the health of every database on this system"`
launches the `health-audit` workflow (read-only; requires the Dynamic workflows setting enabled).

## 4. Behaviour disclosures the reviewer will look for

| Question | Answer |
|---|---|
| Telemetry | **None.** No usage data, prompts, SQL or results leave the machine. |
| Network at session start | **None.** Nothing is installed or fetched when a session opens. |
| Network on explicit action | Bridge mode runs one **pinned** npm package (`mcp-remote@0.8.3`) via `npx`. `/teradata-vantage:setup install` installs the **vendored** server wheel plus hash-pinned dependencies from PyPI, after disclosing the size. Both are user-initiated. |
| Unpinned auto-execution | None. Every package specification is version-pinned; the MCP server itself is vendored under `servers/` with a recorded SHA-256. |
| Hook scope | Hooks matched to MCP tool names are inherently scoped. The two that are not — the `Bash` host-ops gate and the `UserPromptSubmit` secret guard — do nothing unless the session has a Teradata connection configured, and the secret guard is additionally **off by default** behind a plugin option. |
| What hooks do with data | Read the tool payload in-process to make an allow/ask/deny decision or add a note. Nothing is persisted or transmitted. |
| Where hooks run | Locally, unsandboxed, under the user's own permissions. Disclosed in `SECURITY.md` and the plugin README. |
| Credentials | Never echoed. Supplied via a `sensitive` plugin option, an environment variable, or a `0600` file the user writes in their own terminal. |
| Guarantees claimed | The guards are explicitly described as **mistake-prevention, not a security boundary**, in the description, the README, `SECURITY.md` and the `setup` skill. |
| Affiliation | None claimed. "Community plugin, not affiliated with or endorsed by Teradata Corporation" appears in the plugin description, the marketplace description, `NOTICE.md` and the README. Never the word "Official". |
| Third-party code | `teradata-mcp-server` (MIT, Teradata) vendored unmodified with its licence text at `servers/LICENSE.teradata-mcp-server`; attributed in `NOTICE.md`. |

## 5. Pre-submission self-check

Run from the plugin directory. All must pass; `validate_plugin.py` exiting non-zero is a hard stop.

```bash
cd plugins/teradata-vantage
python3 -m pytest scripts/tests -q            # unit tests
python3 scripts/validate_plugin.py            # repository checks, the forbidden-token scan, then
                                              # `claude plugin validate --strict` on the plugin and
                                              # on the marketplace root
python3 scripts/check_no_leaks.py             # the same whole-repository scan, on its own
bash    scripts/vendor_server.sh --check      # wheel matches VENDORED.sha256
```

`.github/workflows/validate.yml` runs the same checks on every pull request and every push to `main`:
the tests, `validate_plugin.py --repo .` (which carries the forbidden-token scan), `vendor_server.sh
--check`, and `claude plugin validate --strict` on both roots as two separate steps, so a missing
`claude` binary cannot silently downgrade the run.

`claude plugin eval .` scores the 13 behaviour cases under `evals/`. It needs a model, so it is not
a CI gate — run it by hand when a skill, agent or hook changes behaviour.

Then confirm by hand:

- [ ] `plugin.json` carries no `category`, `tags`, component paths or `settings`; `version` appears there and **not**
      in the marketplace entry.
- [ ] No `bin/` directory and no plugin `settings.json` (both would change behaviour outside the user's intent).
- [ ] Every skill's frontmatter parses as YAML and its `name` equals its directory name.
- [ ] Every MCP tool name the bundled server registers is ≤ 64 characters — the **bare** name (`base_readQuery`),
      not the client-side `mcp__plugin_<plugin>_<server>__` prefix Claude Code adds. That is the rule in the
      Software Directory Policy §5.C, "MCP tool names must not exceed 64 characters"; the longest bare name here
      is 29 characters (`bar_manageDiskFileTargetGroup`). The **namespaced** names are not capped: seven run
      65–68 characters, and a 67-character one was measured on 2026-09-05 (Claude Code 2.1.261) loading its schema
      and executing against the database — names of that length are still exposed on 2.1.263. Do not reintroduce a
      length ceiling in `validate_plugin.py`: `scripts/tests/test_validate_plugin.py::test_long_tool_names_are_not_a_finding`
      pins its absence, and the measurement is recorded in `scripts/data/mcp_tools.yaml` and
      `skills/setup/references/tool-inventory.md`.
- [ ] `README.md` exists at the repository root **and** at the plugin root, and the plugin README states telemetry,
      network behaviour, where hooks run, and the safety-model caveat.
- [ ] `LICENSE` at both roots, `NOTICE.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md` present.
- [ ] `git status` is clean and nothing ignored-but-required is missing from the commit; no `__pycache__`,
      `.pytest_cache`, `dist/` or `*.env` is tracked.
- [ ] CI is green on `main` (the community pipeline pins a SHA from `main` and re-pins it daily; a red `main`
      strands installers — see §10).
- [ ] A fresh install works from a clean `HOME`: `claude plugin marketplace add <repo>` then
      `claude plugin install teradata-vantage@teradata-plugins`, and `/mcp` shows
      `plugin:teradata-vantage:teradata` connected.

## 6. The repository URL

Set on 2026-09-08 to **https://github.com/fhawarneh/teradata-vantage-plugin**, in exactly three places and nowhere else:

1. `plugins/teradata-vantage/.claude-plugin/plugin.json` — `homepage` (the plugin README on the default branch) and
   `repository` (the repository root).
2. `.claude-plugin/marketplace.json` — `homepage` on the `teradata-vantage` entry.
3. §1 of this file — the Repository row.

If the repository ever moves, change those three and re-run the §5 self-check. A placeholder such as
`https://github.com/OWNER/...` in a manifest is worse than an absent field — never commit one.

## 7. How to submit

There is no pull-request path. Submission is through an in-app form, and pull requests opened directly against
`anthropics/claude-plugins-community` are closed automatically — its README: "Pull requests opened directly against
this repo are closed automatically — all changes flow from the internal review pipeline" (the repository carries a
`close-external-prs.yml` workflow that does it).

| | |
|---|---|
| Console form | https://platform.claude.com/plugins/submit — "Console requires a Developer, Admin, or Owner role on a Console organization." This is the path for an individual author. |
| claude.ai form | https://claude.ai/admin-settings/directory/submissions/plugins/new — "requires a Team or Enterprise organization and directory management access", so an individual author cannot use it. |
| What you hand over | "To submit a plugin to the directory, share a GitHub link to your plugin. The repo must be public—closed-source plugins are not accepted." For this repository that link is the plugin subdirectory: `https://github.com/fhawarneh/teradata-vantage-plugin/tree/main/plugins/teradata-vantage`. |
| Prerequisite | "Before submitting, run `claude plugin validate` to check formatting and structure." §5 runs it with `--strict`, which is a superset. |
| Status | Submissions made on claude.ai are listed at https://claude.ai/admin-settings/directory/submissions. "Review times vary with queue volume." |
| Updates | "You do not need to re-submit the form for updates" — see §10. |

The Console form sits behind a login wall, so the exact field list could not be verified for this dossier. Have the
answers ready in case it asks for more than the link: the one-line description (the `description` in
`.claude-plugin/marketplace.json`), the three example prompts from §3, the support address (`owner.email`), the
licence (MIT), and the category (`database`).

Two documentation pages disagree about where an approved plugin lands, and it does not change what you do:
claude.com/docs/plugins/submit says the directory "is surfaced as the official `claude-plugins-official` marketplace",
while code.claude.com/docs/en/plugins says third-party submissions land in `claude-community` and that the official
marketplace "is curated separately … the submission form does not add plugins to the official marketplace". Both
catalogs exist and some plugins appear in both at different pinned SHAs (checked 2026-09-08). Plan for the community
catalog; an "Anthropic Verified" badge or an official listing is Anthropic's decision, not something you apply for.

## 8. What the review pipeline checks

The pipeline that gates submissions is Anthropic-internal, and the community repository is "a read-only mirror … synced
nightly from Anthropic's internal review pipeline". What is public is the set of composite actions that repository runs
on its own catalog. Read them as the floor, not the whole of it — the scan action's README says organizations
"should maintain a more detailed prompt in a **private** location".

**VERIFIED — `validate-plugins`** (`.github/actions/validate-plugins`):

| Step | Check | Bearing on this plugin |
|---|---|---|
| 11 invariants | I1–I11 on the catalog entry: alpha-sorted names, no duplicates, description 10–2000 characters with no leading or trailing whitespace, `https://` (or `owner/repo`) sources, a 40-hex `sha`, a vendored path containing `.claude-plugin/plugin.json`, no shell metacharacters in `source` fields, no zero-width or bidi characters in name or description, name matching `^[a-z0-9][a-z0-9-]{1,63}$`. I1, I3, I5 and I8 warn rather than fail by default. | The pipeline writes the entry, not you. `teradata-vantage` and the marketplace `description` already satisfy the name and length rules. |
| 20 | `claude plugin validate` on the catalog file | Not yours to fail. |
| 30 | Clones the plugin at its pinned SHA and runs `claude plugin validate` on it — **without** `--strict` | §5 runs `--strict` locally, which is the stricter superset. |
| 41 | JSON-parses `.mcp.json`, `.lsp.json` and `hooks/hooks.json` | The runtime always probes these; malformed means a crash, so this is a cheap hard failure to avoid. |

**VERIFIED — `scan-plugins`:**

- *Static pin check*, deterministic and model-free: it flags an MCP server whose `command` is a package runner
  (`npx`, `bunx`, `uvx`, `pipx`) with a **floating** spec — a dist-tag such as `@latest`, a version range, or a bare
  unversioned name. Package invocations inside `skills/`, `commands/` or `agents/` markdown are deliberately out of
  scope. Running that action's own classifier (`lib/pin-check.sh`) against `plugins/teradata-vantage` on 2026-09-08
  returns zero rows: `.mcp.json` launches `bash scripts/launch-mcp.sh`, and the one package invocation,
  `mcp-remote@0.8.3`, is version-pinned and lives inside that script.
- *AI review*: `claude -p` with the policy prompt, read-only `Read,Glob,Grep`, working directory set to the cloned
  plugin, returning `{passes, summary, violations, may_make_external_network_calls, may_download_additional_software}`
  judged against the Software Directory Policy and the Acceptable Use Policy. The prompt directs the model to read the
  whole shipped payload — not only `.claude-plugin/plugin.json`, `.mcp.json`, `skills/`, `agents/`, `commands/` and
  `hooks/`, but also `scripts/`, `tests/`, `examples/`, dotdirs such as `.claude/`, and any `.ts/.js/.mjs/.py/.sh/.go`
  file anywhere in the tree, on the grounds that a git install clones the entire repository to the user's disk. It
  looks specifically for credentials read from a credential store and routed **cross-service**.
- Expect `may_make_external_network_calls: true` and `may_download_additional_software: true` for this plugin. Both are
  accurate and already disclosed (§4, the plugin description, the README): they are disclosures, not violations.

**Policy items a reviewer reads for** (Anthropic Software Directory Policy): documentation of how the software works,
its purpose and how to troubleshoot (§3.C); a standard testing account with sample data (§3.D) — §2 above; at least
three working examples of prompts or use cases (§3.E) — §3 above; verified contact information and support channels
(§3.B); an accessible privacy policy link where the software connects to a service (§3.A); MCP tool names not
exceeding 64 characters (§5.C); and, for **remote** MCP servers, all applicable annotations, "in particular
_readOnlyHint_, _destructiveHint_, and _title_" (§5.E). Prohibited categories (§4): facilitating financial
transactions, standalone image/video/audio generation, and acting as an advertising vehicle.

**INFERRED, not verified:**

- Whether the internal screen treats the vendored wheel (`servers/teradata_mcp_server-0.2.6-py3-none-any.whl`) as
  software that ships as a binary is unknown. Everything needed to answer it is already in the repository — the wheel
  is the unmodified upstream MIT release, its sha256 is recorded in `servers/VENDORED.sha256` and re-checked by
  `vendor_server.sh --check` in CI, and its provenance and licence text are in `NOTICE.md` and
  `servers/LICENSE.teradata-mcp-server`. State that in the submission rather than waiting to be asked.
- The internal policy prompt may be stricter than the public one, so a local preview is a floor, not a pass.

A local preview of the public rubric, running what `scan.sh` runs, from a fresh clone of the commit you pushed:

```bash
P=$(mktemp -d)
B=https://raw.githubusercontent.com/anthropics/claude-plugins-community/main/.github/actions/scan-plugins/policy
curl -sS -o "$P/prompt.md" "$B/prompt.md"; curl -sS -o "$P/schema.json" "$B/schema.json"
cd <clone>/plugins/teradata-vantage
claude -p "$(cat "$P/prompt.md")" --bare --allowed-tools "Read,Glob,Grep" \
        --output-format json --json-schema "$(cat "$P/schema.json")" </dev/null | jq .structured_output
```

`scan.sh` appends one sentence to that prompt telling the model the plugin files are in the working directory; the
flags above are otherwise exactly the ones it uses.

## 9. What the catalog entry will look like

You do not write the entry — the pipeline does. This is a current community-catalog entry in the `database` category,
copied verbatim on 2026-09-08, and it is the same `git-subdir` shape this plugin needs:

```json
{
  "name": "databricks",
  "description": "Databricks skills for the CLI, Apps, Lakebase, Model Serving, Lakeflow Jobs, Spark Declarative Pipelines, Declarative Automation Bundles (DABs), and classic-to-serverless migration.",
  "author": {
    "name": "Databricks"
  },
  "category": "database",
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/databricks/databricks-agent-skills.git",
    "path": "plugins/databricks/claude",
    "ref": "main",
    "sha": "fe7c98eacc56762174ebc07c4da5256e65aaac99"
  },
  "homepage": "https://developers.databricks.com/"
}
```

The `mongodb` entry has the same shape with `"path": "plugins/mongodb"` and no `category` field. Expected entry for
this plugin once approved:

```json
{
  "name": "teradata-vantage",
  "description": "<one line, from the submission>",
  "category": "database",
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/fhawarneh/teradata-vantage-plugin.git",
    "path": "plugins/teradata-vantage",
    "ref": "main",
    "sha": "<40-hex commit>"
  },
  "homepage": "https://github.com/fhawarneh/teradata-vantage-plugin"
}
```

Four things follow from that shape:

- The catalog carries `name`, `description`, `source` and `homepage`, plus `author` and `category` on some entries. It
  does **not** carry the `tags`, `relevance`, `keywords` or `version` from this repository's own `marketplace.json` —
  those apply only to the `teradata-plugins` marketplace. Census of the community catalog on 2026-09-08: 2282 entries,
  157 with `category`, none with `version`.
- `category` is likely but not guaranteed: only 157 of those 2282 entries carry one at all, and 11 use `database`.
- `path` is part of the entry, so `plugins/teradata-vantage` has to stay where it is.
- `name` is the install id and should be treated as permanent — the official catalog's README calls it "an
  **immutable slug**", because renaming it breaks existing installs with `plugin-not-found`; the community catalog
  keeps a top-level `renames` map (4 entries on 2026-09-08) for the cases where a rename was unavoidable anyway.
  `teradata-vantage` is free: neither catalog has an entry matching "teradata" (community 2282 entries, official 291,
  checked 2026-09-08).

## 10. After approval — SHA bumps, a green `main`, and what version pinning means

- **The pin is bumped for you.** "Approved plugins are pinned to a specific commit SHA in the
  `anthropics/claude-plugins-community` catalog, and CI bumps the pin automatically as you push new commits to your
  repository." The public bump action resolves the tracking target with `git ls-remote <url> HEAD`, runs daily at
  07:23 UTC, clones at the new SHA and runs `claude plugin validate` on it **before** opening a per-entry pull
  request; entries that fail there are skipped, and an entry that keeps failing can be held at its current SHA in the
  catalog's `freeze-shas.txt`. The practical consequence: **`main` is the release branch.** Whatever is on it is a
  candidate pin, and the pin simply stops advancing while `main` is red.
- **Do not pin the Claude Code CLI in `.github/workflows/validate.yml`.** The bump validates with the current CLI
  (`claude-cli-version: latest`), so a pinned older CLI locally could keep this repository's CI green while the daily
  bump silently skips.
- **Create the GitHub repository under the account or organisation it will live in permanently.** The catalog records
  a login-to-account-id map (`.github/owner-baseline.json`); a recorded login that later resolves to a different id is
  treated as having changed hands, and the entries under it are reviewed before any further bumps.
- **A SHA bump is not an update for people who already installed the plugin.** `plugin.json` sets an explicit
  `version`, and that is the update cache key: "Users get updates only when you bump this field. Pushing new commits
  without bumping it has no effect, and `/plugin update` reports 'already at the latest version'." So every
  user-visible change ships with a version bump:

```bash
cd plugins/teradata-vantage
python3 scripts/release.py --bump patch --no-tag   # bumps plugin.json + CHANGELOG.md, builds dist/…zip
# commit plugin.json and CHANGELOG.md first — `claude plugin tag` refuses on a dirty working tree
claude plugin tag --push                           # tags teradata-vantage--v<version>; needs an `origin` remote
```

`release.py` without `--no-tag` runs `claude plugin tag --push` itself, but it does so on the tree it has just
modified, so that step refuses until the bump is committed. Use `--no-tag`, commit, then tag.
