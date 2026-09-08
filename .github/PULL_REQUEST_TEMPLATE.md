## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Gates

Run from `plugins/teradata-vantage`; CI runs the same four. `validate_plugin.py` exiting non-zero is a hard
stop, not advice.

- [ ] `python3 scripts/validate_plugin.py`
- [ ] `python3 -m pytest scripts/tests -q`
- [ ] `python3 scripts/check_no_leaks.py`
- [ ] `bash scripts/vendor_server.sh --check`

## Claims

- [ ] Every statement about Teradata behaviour is something I observed or can cite. Anything taken from
      Teradata documentation rather than observed is marked
      `(from Teradata documentation; verify against your release)`, and release-dependent behaviour says which
      release I saw it on.
- [ ] No site-specific content: examples use `<db>.<table>` and generic names such as `sales_fact` and
      `customer_dim`; fixtures are synthetic.
- [ ] No real `DATABASE_URI`, password, PAT, host address, or production table or column name — including in
      tests and fixtures.
- [ ] Nothing claims affiliation with, endorsement by, or sponsorship from Teradata Corporation.

## If this touches

**Skills** — `SKILL.md` stays under 20,000 bytes, depth goes in `references/*.md` with a one-line purpose
header, and the new reference is listed at the end of the skill body. Generated references were regenerated
from `scripts/data/*.yaml`, not hand-edited.

**Hooks** — standard library only, runs on Python 3.10, reads the payload from stdin and writes the documented
JSON to stdout. The guards that stand between the agent and a write return `ask` when they crash or get
malformed input; the coaching and lint hooks fail silent. Hooks without a tool-name matcher still do nothing
unless the session has a Teradata connection configured. A test was added in `scripts/tests/`.

**Package specifications** — nothing floating (`npx <pkg>`, `uvx <pkg>`, bare `pip install <pkg>`); screening
reads those as unpinned auto-execution.

**The bundled MCP server** — vendored with `scripts/vendor_server.sh <version>`, `scripts/data/mcp_tools.yaml`
regenerated from a live `tools/list` against the new wheel, and the change noted in `CHANGELOG.md`.

## Safety framing

If this changes a guard, the description still says what it is: mistake-prevention for an agent, not a
security boundary. The database's own GRANTs, roles and row-level security remain the only real control.
