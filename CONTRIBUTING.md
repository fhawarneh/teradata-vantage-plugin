# Contributing

This repository is a Claude Code plugin marketplace (`teradata-plugins`) containing one plugin,
`plugins/teradata-vantage`. Contributions are welcome — especially corrections to Teradata behaviour, which is the
one thing this plugin cannot afford to get wrong.

## The rule that matters most

**A wrong fact is worse than a missing one.** Everything in `skills/` is loaded into a model's context and acted on.
A dialect rule that is subtly false makes Claude write broken SQL confidently, which costs a user more than having no
skill at all.

So: state only what you can support. Mark anything taken from Teradata documentation rather than observed as
`(from Teradata documentation; verify against your release)`. If an error code's meaning is not confirmed, leave it
out of `scripts/data/error_codes.yaml`. If a behaviour is release-dependent, say which release you saw it on.

## Before you open a pull request

Run the full gate from the plugin directory:

```bash
cd plugins/teradata-vantage
python3 scripts/validate_plugin.py          # frontmatter, tool names, grants, manifests, leak scan, claude plugin validate --strict
python3 -m pytest scripts/tests -q          # hook and tooling unit tests
python3 scripts/check_no_leaks.py           # forbidden-token scan over the whole repository
bash    scripts/vendor_server.sh --check    # vendored wheel still matches VENDORED.sha256
```

CI (`.github/workflows/validate.yml`) runs the same checks on every pull request and on pushes to `main`. All four must
pass. `validate_plugin.py` exiting non-zero is a hard stop, not advice.

## What the validator enforces, and why

Most rules exist because breaking them fails **silently** at runtime:

- **Skill frontmatter must be valid YAML.** A parse error does not raise — Claude Code loads the skill with *empty*
  metadata, so the skill silently never triggers. Multi-value fields must be a single scalar (`when_to_use: a; b; c`),
  never a bare comma-separated list of quoted strings.
- **The 64-character limit applies to the BARE tool name, not the namespaced one.** The Software Directory Policy
  caps the name the MCP server registers (`base_readQuery`); the longest here is 29 characters. The *namespaced*
  form Claude Code builds, `mcp__plugin_<plugin>_<server>__<tool>`, is NOT capped: seven of this plugin's names run
  65–68 characters, and a 67-character one was measured on 2026-09-05 (Claude Code 2.1.261) loading its schema and
  executing against the database. An earlier version of this file asserted the opposite; the rule was withdrawn
  after that measurement. Do not reintroduce a length ceiling in `validate_plugin.py` — its absence is pinned by
  `scripts/tests/test_validate_plugin.py::test_long_tool_names_are_not_a_finding`, and the measurement is recorded
  in `scripts/data/mcp_tools.yaml`.
- **Every tool named anywhere must exist in `scripts/data/mcp_tools.yaml`**, which is generated from a live
  `tools/list` against the vendored server. Do not hand-add tools to it.
- **Every `Workflow(...)` grant must resolve** to a real `meta.name` in `workflows/`.
- **`allowed-tools` is a per-turn pre-approval, not a restriction.** Never describe it as a guard in prose; the
  validator does not catch that, reviewers do. `disallowed-tools` is the field that restricts.
- **No forbidden tokens anywhere in the repository.** A marketplace reviewer reads the whole clone, not just the
  surfaces Claude loads. `scripts/data/forbidden_tokens.txt` bans private-LAN address literals, credential-shaped
  connection URIs, non-upstream environment variables, a claim of Teradata affiliation, and hidden Unicode. Use
  `<db>.<table>` and generic names such as `sales_fact`, `customer_dim`. Fixtures must be synthetic. See *Keeping
  site-specific content out* below for where site names go instead.

- **No hidden Unicode, anywhere.** `validate_plugin.py` scans every `.md`, `.json`, `.yaml`, `.yml`, `.js`, `.py`,
  `.sh` and `.cfg` file for bidirectional-override and zero-width characters and fails the build on any hit. These
  are the characters that let displayed text differ from what a tool actually executes, so a reviewer reading a
  diff cannot see the difference. The one exemption is the forbidden-token list itself, by basename, because its
  whole job is to name them.

  This rule bit the repository once: `scripts/data/forbidden_tokens.txt` held the characters as **literals**, so
  the file that bans them contained them. Write them as escape sequences or by code point, never as themselves. The
  same applies to any right-to-left script in a test fixture — put the code point in, not the glyph.

## Keeping site-specific content out

The token list is deliberately split in two, and the split is the contract:

- **`scripts/data/forbidden_tokens.txt` is public.** It ships in the repository and is read by every contributor and
  every marketplace reviewer. It therefore holds only **generic, class-based patterns** — the shape of a private-LAN
  address, the shape of a `scheme://user:password@host` URI, environment variables that exist only in a private fork
  of the server, a claim of affiliation, hidden Unicode, and a handful of generic plumbing names that have no place
  in a portable plugin. A published deny-list is also a published index of what it hides, so a *site-specific*
  literal added here leaks the very thing it was meant to suppress. Never add one.
- **Site-specific names belong in a private overlay** that stays outside this repository: your own customer,
  demonstration and internal project names, your database, schema and host names, your internal hostnames and
  identifiers. Point the scanner at it and it is applied on top of the public list:

  ```bash
  python3 scripts/check_no_leaks.py --tokens-extra /path/to/private_tokens.txt
  TERADATA_PLUGIN_TOKENS_EXTRA=/path/to/private_tokens.txt python3 scripts/check_no_leaks.py
  ```

  The overlay file uses the identical format to the public list — one Python regular expression per line, `#`
  comments, and the same `raw:` / `cs:` flag prefixes.

If you work against a real system, keep an overlay and run the scan with it before every pull request. The public
list alone will not catch your site's names, and it is not supposed to. If a *class* of leak is missing — a shape,
not a name — add the pattern to the public list and a test in `scripts/tests/test_check_no_leaks.py`.

## Editing skills

- `SKILL.md` bodies stay under 20,000 bytes; depth goes in `references/*.md`, each opening with a one-line purpose
  header. Add the reference to the skill's reference list at the end of the body.
- Frontmatter: bare `name` equal to the directory name, `description` starting `Use when …`, `when_to_use` carrying
  trigger phrases, `license: MIT`, and `metadata` with `skill_type`, `category` and `version`. Never put `paths`
  inside `metadata`; `paths` at the top level *limits* activation and belongs only on file-scoped skills.
- Some references are generated (`render_references.py` from `scripts/data/*.yaml`). Edit the data file, then
  regenerate — never hand-edit a generated reference.
- Write imperatively: NEVER / ALWAYS. Open every error section with what the message actually means before what to do
  about it. Show exact SQL in fenced blocks. Surface Teradata error codes verbatim.

## Editing hooks

Hooks are Python 3 standard library only, must run on Python 3.10, read the hook payload from stdin and write the
documented JSON to stdout. Two rules are absolute:

1. **Fail in the safe direction.** The guards that stand between the agent and a write (`sql_read_guard.py`,
   `write_gate.py`, `destructive_tool_gate.py`) must return `ask` if they themselves crash or receive malformed
   input. The coaching and lint hooks must fail silent. There is a test for this; keep it passing.
2. **Stay gated.** Hooks that are not scoped by a tool-name matcher (the `Bash` gate and the `UserPromptSubmit`
   guard) must do nothing unless the session actually has a Teradata connection configured. Marketplace screening
   fails plugins whose hooks run ungated on every session.

Add a test in `scripts/tests/` for every guard change, and never introduce a floating package specification
(`npx <pkg>`, `uvx <pkg>`, bare `pip install <pkg>`) anywhere — screening reads those as unpinned auto-execution.

## Upgrading the bundled MCP server

Run `scripts/vendor_server.sh <version>`. It fetches the release wheel, records the SHA-256 in `VENDORED.sha256`,
regenerates hash-pinned requirements and updates `VENDORED.md`. Then regenerate `scripts/data/mcp_tools.yaml` from a
live `tools/list` against the new wheel, re-run the validator, and note the change in `CHANGELOG.md`. Never edit the
vendored wheel or the upstream licence text.

## Releasing

`python3 scripts/release.py --bump patch|minor|major` bumps the version in
`plugins/teradata-vantage/.claude-plugin/plugin.json` only (never in the marketplace entry), prepends a
`CHANGELOG.md` section, builds the distribution zip, and runs `claude plugin tag --push`. Use `--dry-run` first; it
prints every action without writing.

`--bump` is required and always raises the version, so `release.py` cannot cut the **first** release at the version
already in `plugin.json`. For that one, write the `CHANGELOG.md` section by hand and tag directly from the plugin
directory:

```bash
cd plugins/teradata-vantage
claude plugin tag --dry-run          # confirms plugin.json and the marketplace entry agree
claude plugin tag --push
```

Every release after that goes through `release.py`.

## Scope

This plugin covers generic Teradata Vantage work. It deliberately does not carry site-specific content: no customer or
demonstration database names, no host addresses, no credentials, and no claim of affiliation with Teradata
Corporation. Anything you contribute must be usable by a stranger against their own system.
