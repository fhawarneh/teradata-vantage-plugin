# Teradata reserved words — aliases that fail, substitutions, quoting rules

Purpose: the identifier half of `teradata-sql`; consult it on any `Error 3707` or `Error 3706` that
names a token you meant as an alias, and before naming derived columns.

## 1. What the messages actually mean

- `Error 3707 Syntax error, expected something like a name or a Unicode delimited identifier ... between
  the 'AS' keyword and the '<word>' keyword` — the token after `AS` is a KEYWORD to the parser, not a
  name. You used a reserved word as an alias.
- `Error 3707 Syntax error, expected something like '(' between the 'VARCHAR' keyword and ')'` — `VARCHAR`
  without a length inside `CAST`.
- `Error 3707 ... expected something like a ',' or ')' between the word '<x>' and ...` — usually a
  comma-form `SUBSTRING(col, 1, 4)` or a call to a function that does not exist (`MONTH(col)`).
- `Error 3706 Syntax error: expected something between the 'AS' keyword and the '<word>' keyword` —
  same class, when the word is reserved in a different way; also raised for `LIMIT` and for
  `CAST(expr AS <alias>)` where `<alias>` is not a type.
- `Error 3706 Syntax error: <word> does not match a defined Type name` — the token after `AS` inside
  a CAST is your alias, not a type. Close the CAST with a type, then alias outside.
- `... expected '(' between 'mode' keyword and ','` — `mode` selected bare (`SELECT j.mode, ...`).

## 2. Aliases that fail, and what to write instead

| Do not alias as | Write | Why it fails |
|---|---|---|
| `value` | `val`, `metric_value` | reserved |
| `key` | `k`, `key_id`, `dim_key` | reserved |
| `date` | `dt`, `date_val`, `event_date` | reserved (also the type name) |
| `time` | `time_val`, `event_time` | reserved |
| `timestamp` | `ts`, `event_ts` | reserved |
| `position` | `pos` | function name |
| `type` | `type_name`, `type_cd` | reserved |
| `status` | `status_val`, `status_cd` | reserved-ish; ambiguous in joins |
| `count` | `cnt`, `row_count`, `n` | aggregate name |
| `sum` / `avg` / `min` / `max` | `total`, `avg_val`, `lo`, `hi` | aggregate names |
| `period` | `period_val`, `period_key` | PERIOD type |
| `level` | `lvl` | reserved |
| `ct` | `coltype`, `cnt` | reserved (CT = CREATE TABLE abbreviation) |
| `cs` | `scheme`, `cs_val` | reserved (CS = CASESPECIFIC abbreviation) |
| `mode` | `mode_val`, `access_mode` | reserved (LOCKING ... MODE) |
| `month` / `year` / `day` / `hour` / `minute` / `second` | `mth`, `yr`, `dy`, `hr`, `mi`, `sec` | interval/EXTRACT field names |
| `zone` | `tz`, `zone_name` | TIME ZONE keyword |
| `rank` | `rnk`, `rank_no` | window function |
| `percent` | `pct` | SAMPLE/TOP PERCENT keyword |
| `row_number` / `rows` / `range` | `rn`, `row_cnt`, `rng` | window keywords |
| `user` / `account` / `role` / `profile` / `database` / `table` / `column` / `index` / `view` | `user_name`, `acct`, `role_name`, `prof`, `db_name`, `tbl`, `col`, `idx`, `vw` | object keywords |
| `title` / `format` / `default` / `null` / `case` / `when` / `end` / `order` / `group` / `top` / `sample` | append a noun: `title_txt`, `format_str`, `default_val`, ... | clause keywords |
| `result` / `session` / `statistics` / `stats` / `lock` / `access` / `check` | `res`, `sess`, `stat_val`, `lck`, `acc`, `chk` | reserved |

Single-token aliases only: `AS avg_value`, never `AS avg value` (`Error 3706`).

## 3. When the reserved word is a real column name

Quote it with double quotes, exactly as stored: `SELECT "month", "date" FROM <db>.<table>`. Rules:

- Double-quoted identifiers are case-preserving in the statement but Teradata matches them
  case-insensitively against the dictionary by default (doc; verify — a quoted name with a different
  case is accepted on default installations).
- A bare reserved word in a `WHERE` or `GROUP BY` sometimes parses (`WHERE month >= '2025-11'`), a
  bare one in the SELECT list more often does not. Quote consistently in every clause rather than
  learning which positions tolerate it.
- Never quote with backticks or square brackets; both are syntax errors.
- Positional `GROUP BY 1, 2` and `ORDER BY 1` avoid repeating a quoted name.
- Do not change a column's stored name to avoid quoting; a rename is a `base_writeQuery` (`ALTER
  TABLE ... RENAME`) that prompts for approval and breaks every consumer.

## 4. Identifier rules (doc; verify against your release)

- Object names up to 128 characters on current releases (30 on very old ones); letters, digits, `_`,
  `$`, `#`; must start with a letter (or `_`/`$`/`#` when quoted). A quoted identifier may contain
  spaces and other characters, and then must always be quoted.
- Names are stored as typed but compared case-insensitively; `DBC.TablesV.TableName` etc. return the
  stored case, so compare dictionary names with `UPPER()` or `(NOT CASESPECIFIC)` rather than a bare `=`.
- `SEL` and `INS`/`UPD`/`DEL` abbreviations are accepted by Teradata but rejected by this plugin's read
  guard, which checks for `SELECT`/`WITH`/`EXPLAIN`/`SHOW`/`HELP` at the start. Spell them out.
- String literals use single quotes; an embedded quote doubles (`'O''Brien'`). An unclosed literal is
  `Error 3760 String not terminated before end of text`.

## 5. Commonly hit reserved words (doc; not exhaustive)

`ABORT ACCESS ACCOUNT ADD ALL ALTER AND ANY AS ASC AVG BEGIN BETWEEN BY BYTE BYTEINT CASE CASESPECIFIC
CAST CHAR CHARACTER CHECK COLLECT COLUMN COMMENT COMMIT COUNT CREATE CS CT CURRENT_DATE CURRENT_TIME
CURRENT_TIMESTAMP DATABASE DATE DAY DEC DECIMAL DEFAULT DELETE DESC DISTINCT DROP ELSE END ESCAPE
EXISTS EXPLAIN FALLBACK FLOAT FOR FOREIGN FORMAT FROM GRANT GROUP HAVING HELP HOUR IN INDEX INSERT
INTEGER INTERSECT INTERVAL INTO IS JOIN KEY LEVEL LIKE LOCK LOCKING MAX MIN MINUS MINUTE MODE MODIFY
MONTH NOT NULL ON OR ORDER PERCENT PERIOD POSITION PRIMARY PROFILE QUALIFY RANGE RANK RESULT REVOKE
ROLE ROLLBACK ROW ROWS SAMPLE SECOND SELECT SESSION SET SHOW STATISTICS SUM TABLE TIME TIMESTAMP
TITLE TOP TYPE UNION UPDATE USER VALUE VALUES VIEW WHERE WITH YEAR ZONE`

When a word is not on this list and still fails after `AS`, treat the parser as the authority: rename
the alias with a noun suffix (`_val`, `_cd`, `_name`) and move on. The authoritative list is the
"Restricted Words" appendix of the SQL Fundamentals manual for your release.

## 6. Recovery recipe

1. Read the token the message names between quotes.
2. If it is after `AS` in a SELECT list: rename the alias per §2.
3. If it is a column you did not name: quote it per §3.
4. If it is `VARCHAR`/`CHAR`: add the length.
5. If it is `LIMIT`/`FETCH`/`OFFSET`: rewrite with `TOP n` or `QUALIFY ROW_NUMBER() ... <= n`.
6. Re-run once. If the same code returns naming a different token, repeat; if it returns the same
   token, stop and show the statement and the verbatim message.
