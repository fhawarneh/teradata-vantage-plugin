---
type: llm
weight: 1
---
`teradatasql.paramstyle` is `qmark`: placeholders are `?`, bound positionally from a sequence
passed as the second argument to `execute`.

PASS requires ALL of:
- The corrected code uses a `?` placeholder and still passes the value as a parameter (a
  sequence), rather than interpolating it into the SQL string.
- The explanation says the driver does not substitute `%s`, so the literal text reaches
  Teradata — the failure is a Teradata syntax error, not a Python one.

FAIL if the fix formats the value into the SQL string (f-string, `%` or `.format()`), if it
keeps `%s` or switches to named `:region` placeholders, or if it explains the failure as
something other than the parameter style.
