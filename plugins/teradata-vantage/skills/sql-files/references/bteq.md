# BTEQ dot-command reference — session control, formatting, flow control, export/import, return codes, and the script patterns worth copying.

BTEQ (Basic Teradata Query) is the general-purpose Teradata client: it reads a script of SQL statements
and dot-commands from standard input and writes results to standard output. It is the right tool for
DDL, small-to-moderate DML, reports and orchestration; it is the wrong tool for bulk loading — see
`tpt.md`.

Content below marked *(from Teradata documentation; verify against your release)* was not observed
directly. Command names and syntax are stable across releases; exact severity numbers are the part
worth confirming before a scheduler depends on them.

## Invocation

```bash
bteq < load_daily.bteq > load_daily.log 2>&1
echo ".LOGON prod/analyst" | bteq          # interactive after logon
bteq -c UTF8 < script.bteq                 # session character set
```

The process return code is whatever `.QUIT n` / `.EXIT n` set, so a scheduler sees it. A script with no
explicit `.QUIT` exits on the outcome of the last operation, which is rarely what you want.

## Session

| Command | Does |
|---|---|
| `.LOGON tdpid/username` | Connect; the password is prompted. **Never write the password in the file.** |
| `.LOGON tdpid/username,password` | Works, and puts the credential into version control, shell history and every backup. Do not. |
| `.RUN FILE=<path>` | Read commands from another file — the supported way to keep a `.LOGON` line with a credential in a separately permissioned file |
| `.LOGOFF` | End the session, keep BTEQ running |
| `.QUIT n` / `.EXIT n` | End BTEQ with return code `n` |
| `.SESSIONS n` | Request `n` sessions before `.LOGON` (parallelism for `.IMPORT`) |
| `DATABASE <db>;` | SQL, not a dot-command. Sets the default database — still qualify objects |

## Formatting output

| Command | Does |
|---|---|
| `.SET WIDTH n` | Output line width. Default is narrow enough to wrap real result sets |
| `.SET TITLEDASHES OFF` | Drop the dashed rule under column headings |
| `.SET HEADING '...'` / `.SET FOOTING '...'` | Page heading and footing |
| `.SET FORMAT ON\|OFF` | Page formatting: headings, page breaks |
| `.SET SEPARATOR '<s>'` | Column separator — with `FORMAT OFF`, how you produce a delimited extract |
| `.SET RECORDMODE ON` | Raw, unformatted records |
| `.SET NULL AS '<text>'` | Rendering for NULL in report output |
| `.SET ECHOREQ OFF` | Do not echo each statement into the log |
| `.SET ERROROUT STDOUT` | Send errors to stdout so one redirect captures everything |
| `.OS <command>` | Run a shell command from inside the script |
| `.REMARK '<text>'` | Write a line to the output — how a script narrates its own progress |

## Flow control and error handling

Two variables carry the outcome of the statement just executed:

- **`ERRORCODE`** — the Teradata error number; `0` means success.
- **`ACTIVITYCOUNT`** — the number of rows the statement affected.

Both refer to the *last* statement only. Test them immediately, before anything else runs.

| Command | Does |
|---|---|
| `.IF <condition> THEN <command>` | Conditional. The condition tests `ERRORCODE`, `ACTIVITYCOUNT` or a substituted value |
| `.LABEL <name>` | A branch target |
| `.GOTO <name>` | Jump forward to the label. BTEQ scans forward, so the label must appear later in the file |
| `.SET ERRORLEVEL <code> SEVERITY <n>` | Map a specific Teradata error to a severity — how you make an expected condition non-fatal |
| `.SET ERRORLEVEL UNKNOWN SEVERITY <n>` | Default severity for unlisted errors |
| `.SET MAXERROR <n>` | Abort as soon as a severity exceeds `n` |
| `.SET RETRY ON\|OFF` | Retry a request that failed on a restartable condition |
| `.REPEAT n` | Re-execute the following request `n` times — used with `.IMPORT` |

Severity levels *(from Teradata documentation; verify against your release)*: `0` success, `4` warning,
`8` user error, `12` severe error. These are the values a scheduler usually keys on, so confirm them
against your own release before writing a contract around a specific number.

### The pattern to copy

```
.SET ERROROUT STDOUT
.SET MAXERROR 8

.LOGON prod/etl_service

DATABASE analytics;

.REMARK 'step 1: refresh the staging table'
DELETE FROM analytics.sales_stage ALL;
.IF ERRORCODE <> 0 THEN .GOTO failed

INSERT INTO analytics.sales_stage
SELECT * FROM analytics.sales_landing WHERE load_date = CURRENT_DATE;
.IF ERRORCODE <> 0 THEN .GOTO failed
.IF ACTIVITYCOUNT = 0 THEN .GOTO empty

.REMARK 'step 2: publish'
DELETE FROM analytics.sales_fact WHERE load_date = CURRENT_DATE;
.IF ERRORCODE <> 0 THEN .GOTO failed

INSERT INTO analytics.sales_fact SELECT * FROM analytics.sales_stage;
.IF ERRORCODE <> 0 THEN .GOTO failed

.REMARK 'done'
.LOGOFF
.QUIT 0

.LABEL empty
.REMARK 'nothing to load for today - exiting clean'
.LOGOFF
.QUIT 0

.LABEL failed
.REMARK 'FAILED - see the error above'
.LOGOFF
.QUIT 8
```

Three things make this script trustworthy and are the things usually missing: every statement is
followed by an `ERRORCODE` test, "no rows" is distinguished from "failure", and the exit code differs
between the two so a scheduler can tell them apart.

## Export

`.EXPORT` redirects result rows to a file until `.EXPORT RESET`.

| Form | Produces |
|---|---|
| `.EXPORT REPORT FILE=<path>` | Formatted text, as displayed |
| `.EXPORT DATA FILE=<path>` | Raw client-format records, no formatting |
| `.EXPORT INDICDATA FILE=<path>` | Raw records with a null-indicator bitmap — the form that round-trips NULLs |
| `.EXPORT DIF FILE=<path>` | Data Interchange Format |

```
.SET FORMAT OFF
.SET SEPARATOR '|'
.EXPORT REPORT FILE=regions.psv
SELECT region, SUM(total_amount) FROM analytics.sales_fact GROUP BY region;
.EXPORT RESET
```

For a delimited extract, `FORMAT OFF` plus `SEPARATOR` is the reliable combination; trailing spaces
from fixed-width character columns are the usual surprise — `TRIM()` in the SELECT rather than
post-processing the file.

## Import

`.IMPORT` reads a file and feeds it to the following request through `USING`:

```
.IMPORT DATA FILE=customers.dat
.REPEAT *
USING (customer_id INTEGER, customer_name VARCHAR(100), created_date DATE)
INSERT INTO analytics.customer_dim (customer_id, customer_name, created_date)
VALUES (:customer_id, :customer_name, :created_date);
```

`.REPEAT *` runs the request once per input record. `.REPEAT n` limits it to `n` — the way to test a
load on the first few rows before committing to the whole file.

**BTEQ imports row by row.** It is appropriate for thousands of rows, not millions. Above roughly a
hundred thousand rows the load belongs in a bulk utility — see `tpt.md`.

## Common failures

| Symptom | What it actually means |
|---|---|
| Script "succeeds" after a failed statement | No `ERRORCODE` test. BTEQ continues by default; add the test after every statement that matters |
| `Error 3807` object does not exist, in a script that worked elsewhere | The script relies on `DATABASE <db>;` and was run with a different default. Qualify as `<db>.<table>` |
| Dot-command ignored | It does not start in column 1, or it shares a line with something else |
| A statement never runs | A missing `;` merged it into the statement above |
| Export file has wrapped or truncated lines | `.SET WIDTH` is at its default. Raise it, or use `FORMAT OFF` for machine-read output |
| Import loads nothing, reports no error | `.REPEAT` missing — without it the request runs once |
| Credentials in the log | `.SET ECHOREQ OFF`, and never put the password on the `.LOGON` line |
