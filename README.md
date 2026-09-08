# teradata-plugins

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A Claude Code plugin marketplace holding one plugin: **[`teradata-vantage`](plugins/teradata-vantage/README.md)** —
a toolkit for working with Teradata Vantage from Claude Code.

> Community project. Not affiliated with, endorsed by, or sponsored by Teradata Corporation. Teradata, Vantage and
> ClearScape Analytics are trademarks of Teradata Corporation.

## Install

`claude plugin marketplace add` takes a path or a GitHub repository.

**From GitHub:**

```bash
claude plugin marketplace add fhawarneh/teradata-vantage-plugin
claude plugin install teradata-vantage@teradata-plugins
```

**From a local clone**, which is also how you install a branch you are working on:

```bash
claude plugin marketplace add /path/to/your/clone
claude plugin install teradata-vantage@teradata-plugins
```

Already running a Teradata MCP server? Bridge straight to it and skip the local install:

```bash
claude plugin install teradata-vantage@teradata-plugins --config mcp_url=http://127.0.0.1:8001/mcp/
```

Then run `/teradata-vantage:setup` — it works with no connection configured and tells you exactly what is missing.

Full install, connection and usage instructions: **[plugins/teradata-vantage/README.md](plugins/teradata-vantage/README.md)**.

## What the plugin gives you

- **The Teradata MCP server, bundled.** The upstream open-source server ships as a vendored, hash-verified wheel;
  nothing is downloaded at session start.
- **Dialect knowledge that stops wrong SQL before it runs.** `LIMIT` is a syntax error on Teradata, `col = NULL`
  silently returns nothing, `sum` cannot be an alias, `DECIMAL/DECIMAL` division truncates money. The plugin carries
  the corrections and the error-code translations.
- **A read-only guard on the query tool.** `base_readQuery` accepts one statement starting `SELECT`, `WITH`,
  `EXPLAIN`, `SHOW` or `HELP`; anything else is denied with a reason. Destructive writes and destructive tools raise
  an explicit approval prompt. The guards are mistake-prevention, not a security boundary — see [SECURITY.md](SECURITY.md). Telemetry: none; [PRIVACY.md](PRIVACY.md) states exactly what the plugin reads, writes locally, and sends.
- **Operational runbooks** for space and health (`2644`, `3541`), performance (EXPLAIN, statistics, skew, DBQL),
  data quality, NOS/Iceberg archiving, the Enterprise Vector Store, and node recovery when the system will not
  accept logons.
- **Agents and read-only workflows** that take on a whole job: audit every database, profile a whole database,
  review a directory of SQL, or assess the blast radius before a `DROP`.

## Repository layout

```
.claude-plugin/marketplace.json     this marketplace
plugins/teradata-vantage/           the plugin (manifest, skills, agents, workflows, hooks, vendored server)
docs/SUBMISSION.md                  marketplace submission dossier and pre-submission self-check
SECURITY.md                         trust model, network behaviour, vulnerability reporting
PRIVACY.md                          what is read, what is written locally, what leaves the machine
CONTRIBUTING.md                     how to change it without breaking it
```

## Developing

There are two test tiers. The **pytest unit suite** (`scripts/tests`) is deterministic and needs no model or
database — it covers the hooks, the guards and the tooling scripts. The **eval cases** (`evals/`, 8 of them) score
model behaviour and need a model.

```bash
cd plugins/teradata-vantage
python3 scripts/validate_plugin.py          # manifests, frontmatter, tool names, grants, leak scan, --strict
python3 -m pytest scripts/tests -q          # unit tests over the hooks and the tooling scripts
python3 scripts/check_no_leaks.py           # forbidden-token scan over the whole repository
bash    scripts/vendor_server.sh --check    # vendored wheel matches its recorded hash
claude plugin eval .                        # the 8 behaviour cases, scored against a no-plugin baseline arm
```

CI runs the deterministic tier on every pull request and on pushes to `main`: the unit tests,
`validate_plugin.py`, the vendored-wheel drift check, and `claude plugin validate --strict` on both the plugin and
this marketplace. The eval cases are run by hand — what each one pins, and how to run them as manual smoke checks,
is in [plugins/teradata-vantage/evals/README.md](plugins/teradata-vantage/evals/README.md). See
[CONTRIBUTING.md](CONTRIBUTING.md) — starting with the rule at the top: **a wrong fact is worse than a missing one.**

## Licence

MIT. The bundled `teradata-mcp-server` is MIT, Copyright Teradata; see
[plugins/teradata-vantage/NOTICE.md](plugins/teradata-vantage/NOTICE.md).
