# Vendored server: teradata-mcp-server 0.2.6

One-line purpose: provenance record for the upstream MCP server wheel bundled in this directory, unmodified, and the
procedure that reproduces it.

| Field | Value |
|---|---|
| Package | `teradata-mcp-server` — MIT — https://github.com/Teradata/teradata-mcp-server |
| Version | 0.2.6 (upstream tag `v0.2.6`, commit `0de3368b60c1de7152a8a08dd3e67b32ebf62578`) |
| Wheel | `teradata_mcp_server-0.2.6-py3-none-any.whl` (270,681 bytes) |
| sha256 | `8e93870a76231bd736f0369d3a085f1d78814c38466fd36534ba183eac63422c` — identical to the `bdist_wheel` digest at https://pypi.org/pypi/teradata-mcp-server/0.2.6/json |
| Recorded in | `VENDORED.sha256` (checked by `scripts/vendor_server.sh --check` and `scripts/validate_plugin.py`) |
| Fetched with | `pip download teradata-mcp-server==0.2.6 --no-deps --only-binary=:all:` — exactly the PyPI bytes, no build step |
| Requirements | `requirements-0.2.6.txt` (core closure), `requirements-0.2.6-tdvs.txt`, `requirements-0.2.6-bar.txt`; each header records `pip-compile --allow-unsafe --generate-hashes --no-index` under Python 3.12. Every dependency is hash-pinned, so `pip install --require-hashes -r …` refuses anything else. `scripts/vendor_server.sh` does not compile with that exact flag set — see the re-vendor checklist |
| Python | upstream declares `Requires-Python: >=3.11`; the plugin's installer looks for python3.13 / 3.12 / 3.11 |
| Licence | `LICENSE.teradata-mcp-server` (upstream MIT text); attribution in `NOTICE.md` |

Nothing in the wheel is modified or patched. Behaviour is configured only through the documented environment
variables (`DATABASE_URI LOGMECH PROFILE CONFIG_DIR DEFAULT_ROW_LIMIT MAX_ROW_LIMIT PROGRESSIVE_DISCLOSURE HOOKS_MODULE
TD_POOL_SIZE TD_MAX_OVERFLOW TD_POOL_TIMEOUT TD_BASE_URL TD_PAT TD_PEM`) and the plugin's `config/` directory
(`CONFIG_DIR`, merged by top-level key).

## What the plugin installs

`/teradata-vantage:setup install` (scripts/install-server.sh) creates a virtual environment under the plugin data
directory and installs this wheel plus its hash-pinned requirements from PyPI. It runs only when you ask; the
launcher never installs anything at session start. A copy of `VENDORED.sha256` is kept next to the venv so a plugin
update that changes the pin triggers a reinstall.

Measured 2026-09-08 on Linux x86_64 with Python 3.12, core only: the pinned core closure is roughly 160 MB of wheels
to download and **555 MB on disk** once installed. `teradatasql` is 352 MB of that, because its 130 MB
`py3-none-any` wheel carries the driver binary for every platform it supports; nothing else in the closure reaches
25 MB.

## Extras policy

| Extra | Default | Why |
|---|---|---|
| (core) | installed | base, dba, sec, qlty, plot, rag, sql_opt, graph, chat, registry tool groups; prompts and `glossary://` resources |
| `tdvs` | opt-in (`server_extras` plugin option or `TERADATA_MCP_EXTRAS=tdvs`) | Enterprise Vector Store tools; adds a large analytics closure — measured 2026-09-08 at ~43 MB of wheels and ~224 MB on disk (pandas, numpy, teradataml). Needs `TD_BASE_URL` (+ `TD_PAT`/`TD_PEM`) at runtime |
| `bar` | opt-in (`TERADATA_MCP_EXTRAS=tdvs,bar` or `bar`) | DSA backup-and-restore tools; needs `DSA_BASE_URL` or `DSA_HOST`/`DSA_PORT` |
| `fs` | refused | very large dependency closure and ~100 dynamically registered `tdml_*` tools that neither `scripts/data/mcp_tools.yaml` nor the plugin's hook matchers cover. Install it outside the plugin if you need it |

Hash-pinned closures for the two opt-in extras (`requirements-0.2.6-tdvs.txt`, `requirements-0.2.6-bar.txt`) are
produced by `scripts/vendor_server.sh <version>`; when they are absent the installer falls back to
`pip install "teradata-mcp-server[<extra>]==0.2.6"` for the extra's additional packages and says so.

## Re-vendoring a new upstream release

```bash
bash scripts/vendor_server.sh 0.2.7        # download, verify against the PyPI digest, compile requirements, rewrite this file
bash scripts/vendor_server.sh --check      # what CI runs: sha file always, PyPI digest when reachable
```

What the script covers: it downloads the wheel with `pip download --no-deps --only-binary=:all:`, refuses to
continue unless the sha256 equals the PyPI `bdist_wheel` digest, deletes the old wheel and every
`servers/requirements-*.txt`, compiles fresh core / `tdvs` / `bar` closures with `pip-compile` in a throw-away venv,
rewrites `VENDORED.sha256`, and rewrites this file.

Everything else is manual. Work through this checklist after every run:

- [ ] **Merge this file back.** `write_vendored_md` replaces `VENDORED.md` wholesale with a shorter template that
      carries no Licence row, no "What the plugin installs", no installer-fallback note, no "Known gaps" table and
      none of this checklist. Keep a copy before running the script and merge the two afterwards.
- [ ] **Reconcile the compile flags.** The committed closures record `pip-compile --allow-unsafe --generate-hashes
      --no-index` under Python 3.12 and still carry extras (`mcp[cli]==…`, `fastmcp-slim[client,server]==…`,
      `py-key-value-aio[filetree,keyring,memory]==…`). The script compiles `--allow-unsafe --generate-hashes
      --strip-extras`, without `--no-index`, from differently named `.in` files, on the first of python3.13 / 3.12 /
      3.11 it finds. Re-running it therefore rewrites all three files in a different shape. Decide which form is
      canonical and pin the compile interpreter — `pip-compile` resolves environment markers only for the
      interpreter it runs on.
- [ ] **Bump `SERVER_VERSION`** in `scripts/launch-mcp.sh` and `scripts/install-server.sh`. Nothing cross-checks
      them: `validate_plugin.py` compares `VENDORED.sha256` against the wheel and against `wheel_sha256` in
      `scripts/data/mcp_tools.yaml`, and never looks at either script. A missed bump leaves `uvx` mode pulling the
      previous release from PyPI and `doctor.sh` reporting the old number.
- [ ] **Regenerate `scripts/data/mcp_tools.yaml`** — `verified_version`, `wheel_sha256` and the per-group tool lists
      — from a live `tools/list` + `prompts/list` + `resources/list`, and review the diff: new or renamed tools
      change the hook matchers and the agents' `tools:` lines. The `scripts/render_references.py --capture` flag
      that this file, `mcp_tools.yaml` and `vendor_server.sh` all point at does not exist (the script accepts only
      `--check` and `--print`), so the capture is a hand edit today.
- [ ] **Re-check the write surface and the annotations** against that same live `tools/list`: the "Known gaps" rows
      below, the `destructive_tools` list, and every claim in the docs that the bundled server registers no write
      tool.
- [ ] **Sweep the remaining version strings.** `grep -rn '0\.2\.6' plugins/teradata-vantage --include='*.md'
      --include='*.json' --include='*.yaml'` — the version is written into many more files than the two scripts.
- [ ] **Re-test bridge mode** (`npx mcp-remote@<pin>`, pinned in `scripts/launch-mcp.sh`) if the new server's
      transport set changed.
- [ ] Run `python3 scripts/validate_plugin.py` and `pytest scripts/tests`, then record the change in `CHANGELOG.md`;
      users reinstall with `/teradata-vantage:setup install`.

One known dead branch: `--extras fs` is documented as refused, but `scripts/install-server.sh` still has a
`TERADATA_MCP_ALLOW_FS=1` override that appends `servers/requirements-<version>-fs.txt`. `vendor_server.sh` never
produces that file (its `EXTRAS` list is `tdvs bar`), so the override fails at pip with "Could not open requirements
file" instead of at the documented refusal.

Verify a wheel by hand at any time:

```bash
sha256sum servers/teradata_mcp_server-0.2.6-py3-none-any.whl
curl -s https://pypi.org/pypi/teradata-mcp-server/0.2.6/json | python3 -c 'import json,sys; [print(u["digests"]["sha256"]) for u in json.load(sys.stdin)["urls"] if u["packagetype"]=="bdist_wheel"]'
```

## Known gaps in 0.2.6 and the upstream changes to propose

The plugin documents the server as it is. These are the gaps that matter to an interactive client and the pull
requests worth filing against `Teradata/teradata-mcp-server` (small, env-gated, upstream naming):

| Gap in 0.2.6 | Consequence | Proposed upstream change |
|---|---|---|
| The SQLAlchemy engine is built with `pool_pre_ping=True` only — no `pool_recycle`, no `pool_use_lifo` | after a network drop or a database restart a pooled connection can block for the TCP dead-time; the pre-ping itself can hang on a NAT-dropped flow | `pool_recycle` (env `TD_POOL_RECYCLE`, default 3600) and `pool_use_lifo=True` |
| No per-statement timeout is passed to the driver (`connect_args` carries none) | a runaway query holds its connection until the database ends it; the only remedy is `ABORT SESSION` (which the plugin's write gate prompts for) | `connect_args={"request_timeout": TD_REQUEST_TIMEOUT}` on the main engine |
| No server-side wall-clock cap around tool execution (`asyncio.wait_for`) | a hung tool keeps the worker; Claude Code's per-server timeout (`.mcp.json` `timeout`, 180 s here) is the only bound | an env-gated `asyncio.wait_for` backstop around the executor call |
| `docs/server_guide/CUSTOMIZING.md` example tool ends with `LIMIT %(limit)s` | copied verbatim, the tool fails on Teradata (3706 syntax error) | documentation fix: `SELECT TOP %(limit)s …` |
| Tools that write are annotated read-only. `_TOOL_ANNOTATIONS` in `app.py` is keyed `tdvs_grant_user` / `tdvs_revoke_user`, but the registered names are `tdvs_grant_user_permission` / `tdvs_revoke_user_permission`, so neither override ever matches; `_PREFIX_ANNOTATIONS` then applies `readOnlyHint=True, idempotentHint=True` to every `tdvs_`, `sql_` and `rag_` tool, and no registration sets a `title` at all | `tdvs_create`, `tdvs_update`, `tdvs_destroy`, both permission tools, `sql_Execute_Full_Pipeline` (drops and re-creates its clustering tables) and `rag_Execute_Workflow` (creates and inserts into its query table) advertise themselves as read-only and idempotent. A client that decides whether to confirm from those hints would not prompt | fix the two override keys, set `readOnlyHint=False, destructiveHint=True` on `rag_Execute_Workflow`, `sql_Execute_Full_Pipeline` and the destructive `tdvs_` tools, and give every registration a `title` |
| `base_saveDDL` writes a file. It creates `output_dir` (default `./ddls_extracted`) and writes `<table>_DDL.sql` into it with no validation of the path, while carrying the `base_` prefix annotation `readOnlyHint=True` | it is the one bundled tool that writes outside the database — to your own machine in the local stdio mode, to the server host in bridge mode — and the plugin's `tv_readonly` / `tv_analyst` patterns matched it until 2026-09-08, when `saveDDL` was added to their negative lookahead and the tool was added to `destructive_tools` so it prompts under `tv_all` / `tv_dba` | `readOnlyHint=False` for `base_saveDDL`, and validation of `output_dir` |

Until those land, the plugin's `setup` skill states the timeout situation plainly and the `teradata-recovery` skill
covers ending a wedged session.

**The plugin does not read the server's annotations, on purpose.** Its `PreToolUse` matchers in `hooks/hooks.json`
and the `destructive_tools` list in `scripts/data/mcp_tools.yaml` classify by tool NAME, so `tdvs_destroy`,
`tdvs_update`, both permission tools, `bar_manageJob`, `base_saveDDL`, `sql_Execute_Full_Pipeline` and `rag_Execute_Workflow` raise
an approval prompt whatever the server advertises — a wrong hint cannot disarm a name-based gate. Two consequences
worth stating plainly: `tdvs_create` is deliberately absent from that list (it creates, it does not destroy) and so
is ungated, and no hook covers `base_saveDDL`. The `tdvs_` rows only apply when the opt-in `tdvs` extra is
installed. These are mistake-prevention guards, not a security boundary; the database's own GRANTs remain the
access control.

### Upstream drift, checked 2026-09-08

- PyPI has no release newer than 0.2.6 (uploaded 2026-08-13, `Requires-Python: >=3.11`), so the plugin stays on it.
- `v0.2.6...main` is 18 commits ahead and 1 behind. Seventeen are dependabot bumps; the single functional change is
  `a9362be`, "fix: Set sql_ tools as writable (readOnlyHint=False) (#418)", which flips the `sql_` prefix only. On
  `main` the `tdvs_` key mismatch and the read-only `rag_` annotation are both unchanged, and no `title` is set.
- The unreleased `fastmcp4` branch (`5fa76b1`, 2026-09-03, still versioned 0.2.6) is the breaking one: it moves the
  pins to `fastmcp>=4.0.2`, `mcp[cli]>=2.0.0`, `pydantic>=2.12` and `starlette>=1.0.1`, and drops `sse` from the
  transport choices, leaving `stdio` and `streamable-http`. It also fixes the two `tdvs_*_permission` override keys
  and annotates `tdvs_destroy` destructive. When that ships in a release, treat it as a full re-vendor: run the
  whole checklist above, including the bridge-mode re-test.

### Filing status

**None of the changes above has been filed upstream.** They are written as proposals here so the
analysis is not lost, not because a pull request exists. Filing them is a deliberate act against
someone else's repository and belongs to whoever maintains this plugin, not to an automated run.

Each row is already in proposal shape: the gap, the consequence, and the specific change. To file
one, open an issue on `Teradata/teradata-mcp-server` quoting the row, and reference the upstream
version this was measured against — 0.2.6, tag `v0.2.6`, commit `0de3368b`.

Two of them are worth filing first, because they cost an upstream user something today rather than
in an edge case:

- **The `CUSTOMIZING.md` `LIMIT` example.** Copied verbatim, the documented example tool fails on
  Teradata with error 3706. It is a one-line documentation fix and it misleads every reader who
  follows the guide.
- **The tool annotations.** Seven tools that write — including two that create and drop tables —
  advertise `readOnlyHint=True, idempotentHint=True`, because the two override keys in
  `_TOOL_ANNOTATIONS` do not match the registered tool names. A client that decides whether to
  confirm from those hints would not prompt before a destructive call. This plugin does not rely on
  the hints, which is why it is safe here; another client might.
