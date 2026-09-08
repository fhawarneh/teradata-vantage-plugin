---
name: sql-files
description: Use when reading, writing or reviewing a Teradata SQL or BTEQ script on disk - a .sql, .bteq, .btq, .ddl or .dml file - rather than a statement sent through a tool. Covers BTEQ script structure and its error handling, what the sqlfluff lint hook does and where its Teradata dialect stops, and how a script file differs from an interactive statement.
when_to_use: editing or reviewing a .sql / .bteq / .btq / .ddl / .dml file; write a BTEQ script; why did sqlfluff flag this; sqlfluff says unparsable or PRS on my BTEQ; ERRORCODE; ACTIVITYCOUNT; .IF ERRORCODE; .LOGON in a script; .EXPORT / .IMPORT; MAXERROR; what return code did the script exit with; convert this query into a runnable script; load script; TPT script.
license: MIT
user-invocable: false
paths:
  - "**/*.sql"
  - "**/*.bteq"
  - "**/*.btq"
  - "**/*.ddl"
  - "**/*.dml"
metadata:
  skill_type: documentation
  category: teradata
  version: "1.0.0"
---

# Teradata SQL and BTEQ files

This skill activates on the file, not on the conversation. It covers what changes when Teradata SQL
lives in a script instead of arriving through a tool call.

**The dialect rules are not repeated here.** Every rule about `TOP` versus `LIMIT`, `IS NULL`,
reserved-word aliases, `CAST` shape, `GROUP BY`, date arithmetic and `<db>.<table>` qualification lives
in the `teradata-sql` skill, and applies identically in a file. Read that first; this file adds only
what is script-specific.

## 1. What is different about a file

- **Nothing runs it.** Editing a `.sql` file executes nothing. Do not describe an edit as though the
  statement has been applied, and never say a table was changed because a script that changes it was
  written.
- **Multiple statements are normal.** The read guard's one-statement rule governs `base_readQuery`
  calls, not files. A script legitimately holds many statements separated by `;`.
- **The file may not be pure SQL.** A `.bteq`/`.btq` file interleaves SQL with BTEQ dot-commands, which
  are a different language. Treat a line beginning with `.` as a command, not as SQL.
- **You cannot verify it against the database by running it.** To check that objects and columns exist,
  use `base_tableDDL` or `base_columnDescription` on the referenced objects — never assume the schema
  from the script's own text, which may be stale or aspirational.

## 2. The lint hook

A `PostToolUse` hook runs `sqlfluff` after you write or edit a `.sql .bteq .btq .ddl .dml` file. Know
its exact behaviour so you neither over-trust it nor apologise for it:

- **It only runs if `sqlfluff` is already installed.** The plugin never installs it and never nags. No
  findings does not mean the file is clean — it may mean the tool is absent. `doctor.sh` reports which.
- **Your repository's own configuration wins, unless it could run code.** If a `.sqlfluff`, `setup.cfg`,
  `tox.ini`, `pep8.ini` or `pyproject.toml` between the file and the git root sets a `dialect`, sqlfluff runs
  bare and that configuration applies. Otherwise the plugin passes its own `config/sqlfluff-teradata.cfg` with
  `--dialect teradata`. The one exception: sqlfluff resolves `library_path` and `load_macros_from_path`
  relative to the config it found and IMPORTS them, so a config carrying either key — or any `templater`
  other than `raw` — would execute Python from that checkout on the first `.sql` write. The hook declines to
  lint under such a config and says so once. Nothing is ever written into your repository.
- **Up to 15 violations are reported**, as context for you, not as a gate. A finding is advice.
- **`TERADATA_SQL_LINT=off`** disables it.

### Where the dialect stops

sqlfluff's `teradata` dialect is real but partial, and two consequences matter:

- **BTEQ is only a small subset.** Most dot-commands are not modelled. For that reason the hook
  **drops `PRS` (unparsable) findings on `.bteq` and `.btq` files** — an unparsable region there is
  usually a dot-command sqlfluff does not know, not a defect. Do not "fix" valid BTEQ to satisfy a
  parser, and do not report a dropped `PRS` as a problem.
- **Newer syntax may not parse.** Constructs added in recent Vantage releases can surface as `PRS` on a
  plain `.sql` file too. Judge the SQL against the `teradata-sql` rules first; the linter is a second
  opinion, not the authority. When they disagree and you are confident the SQL is valid, say so and
  move on.

Two mechanical checks are appended by the hook regardless of sqlfluff: a `LIMIT n` clause and a
`= NULL` comparison. Both are always wrong on Teradata — fix them without debate.

## 3. BTEQ script structure

A BTEQ script is a sequence of dot-commands and SQL statements read from standard input. The skeleton:

```
.LOGON tdpid/username                     -- password prompted, never written in the file
.SET WIDTH 200
.SET ERROROUT STDOUT

DATABASE analytics;

SELECT COUNT(*) FROM analytics.sales_fact;
.IF ERRORCODE <> 0 THEN .QUIT 8

.LOGOFF
.QUIT 0
```

**Rules for writing one:**

- NEVER write a literal password into a `.LOGON` line. Omit it so BTEQ prompts, or keep the credential
  in a separate protected file invoked with `.RUN FILE=<path>`. A password in a script ends up in
  version control, in shell history and in a backup.
- ALWAYS terminate SQL statements with `;`. Dot-commands take no semicolon.
- A dot-command must start in column 1 and occupies its own line.
- `DATABASE <db>;` sets the default database, but still qualify objects as `<db>.<table>` — a script is
  read far from where it was written (Teradata error 3807 when the assumption breaks).
- Put `.LOGOFF` before `.QUIT` so the session closes cleanly.

### Error handling — the part that is usually missing

Two built-in variables carry the outcome of the last statement:

| Variable | Holds |
|---|---|
| `ERRORCODE` | The Teradata error number of the statement just executed; `0` means success |
| `ACTIVITYCOUNT` | The number of rows the statement affected |

Use them explicitly. A BTEQ script without error checks runs to the end and exits successfully after a
failed statement, which is how a broken load reports success:

```
INSERT INTO analytics.sales_fact_stage SELECT * FROM analytics.sales_fact_landing;
.IF ERRORCODE <> 0 THEN .QUIT 8

.IF ACTIVITYCOUNT = 0 THEN .GOTO nothing_loaded

.LABEL nothing_loaded
```

- `.QUIT n` / `.EXIT n` end the script and set the process return code — this is what a scheduler reads.
- `.SET ERRORLEVEL <code> SEVERITY <n>` maps a specific Teradata error to a severity, so an expected
  condition (an object that may legitimately not exist) does not have to abort the run.
- `.SET MAXERROR <n>` aborts the script as soon as a severity exceeds `n`.
- `.LABEL <name>` with `.GOTO <name>` gives you branching; the label must appear after the `.GOTO`
  reaches it in file order.

*(Severity conventions and the full dot-command set are from Teradata documentation; verify the exact
values against your release before relying on a specific number in a scheduler contract.)*

### Export and import

`.EXPORT` redirects results to a file until `.EXPORT RESET`; `.IMPORT` reads a file into a
parameterised statement using `USING`. Formats are `REPORT` (formatted text, the default),
`DATA` (raw), `INDICDATA` (raw with null indicators) and `DIF`.

```
.EXPORT REPORT FILE=daily_totals.txt
SELECT region, SUM(total_amount) FROM analytics.sales_fact GROUP BY region;
.EXPORT RESET
```

BTEQ moves data one row at a time and is the wrong tool above roughly a few hundred thousand rows —
see `references/tpt.md` for what to use instead.

## 4. Reviewing a directory of scripts

For three or more files, launch the review workflow rather than reading them one by one — it reviews
each file in parallel and then verifies every finding through three independent lenses before
reporting it:

```
Workflow({name: "teradata-vantage:sql-review", args: {root: "sql/", explain: false}})
```

`explain: true` additionally permits `EXPLAIN` on the reviewed statements, which produces a plan and
never executes them.

## References

- `references/bteq.md` — the dot-command reference: session, formatting, flow control, export/import,
  return codes, and the patterns worth copying.
- `references/tpt.md` — when a script should not be BTEQ at all: the bulk utilities, what each one is
  for, and the limits that decide between them.
