---
name: client-development
description: Use when writing or debugging application code that connects to Teradata Vantage - the teradatasql Python driver, teradataml, SQLAlchemy through teradatasqlalchemy, or JDBC. Covers connecting, the qmark parameter style, transaction and session mode, the DBAPI exception hierarchy, pooling, and the patterns that turn a working script into a slow or unsafe one.
when_to_use: connect to Teradata from Python; teradatasql; teradataml; teradatasqlalchemy; JDBC URL for Teradata; how do I parameterise a query; my placeholders do not work; TypeError or 3706 with %s placeholders; executemany; insert a dataframe into Teradata; ANSI vs TERA mode; autocommit; connection pool; LOGMECH LDAP or KRB5; read a Teradata table into pandas; which library should I use.
license: MIT
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
metadata:
  skill_type: documentation
  category: teradata
  version: "1.0.0"
---

# Writing application code against Teradata Vantage

Choosing the library, connecting, and the handful of details that separate code that works from code
that works on the developer's laptop only.

**Dialect rules are not repeated here.** `TOP` not `LIMIT`, `IS NULL`, reserved-word aliases, date
arithmetic and `<db>.<table>` qualification are in the `teradata-sql` skill and apply to every string
of SQL your code builds.

## 1. Which library

| You are | Use | Why |
|---|---|---|
| Running SQL from Python and handling rows yourself | **`teradatasql`** | The official DBAPI 2.0 driver. No ORM, no dependencies beyond itself |
| Using SQLAlchemy, or an ORM/framework that expects it | **`teradatasqlalchemy`** | The SQLAlchemy dialect; wraps `teradatasql` |
| Doing analytics — dataframes, in-database functions, model scoring | **`teradataml`** | Teradata's dataframe API. Operations push down into the database instead of pulling rows out |
| On the JVM | **JDBC** (`terajdbc4`) | The reference driver |
| Loading millions of rows | **Not a driver.** A bulk utility | See the `sql-files` skill's TPT reference |

Verified on this host: `teradatasql` 20.0.0.63, `teradatasqlalchemy` 20.0.0.9 (SQLAlchemy 2.x),
`teradataml` 20.00.00.11.

## 2. Connecting

`teradatasql.connect()` takes either a JSON string or keyword arguments. Keywords are the readable
form; every value is converted to a string internally, and booleans are lowercased for you.

```python
import teradatasql

with teradatasql.connect(
    host="warehouse.example.com",
    user="analyst",
    password=os.environ["TD_PASSWORD"],   # never a literal
    database="analytics",                 # default database, not a restriction
    logmech="LDAP",                       # TD2 (default), LDAP, KRB5, JWT, TDNEGO
) as con:
    with con.cursor() as cur:
        cur.execute("SELECT TOP 5 region, total_amount FROM analytics.sales_fact")
        for row in cur.fetchall():
            print(row)
```

- **Both the connection and the cursor are context managers.** Use `with`; a leaked connection holds a
  session, and sessions are a bounded resource on the system.
- **`database=` sets the default database only.** It does not confine the connection. Keep qualifying
  objects as `<db>.<table>` — an unqualified name resolves against whatever default the deployment
  happens to have, and fails with `[Error 3807]` when that differs.
- **Never build the password into the source or a connection string in version control.** Read it from
  the environment or a secret store.
- Connection parameters beyond the common ones above are handled by the driver's native layer rather
  than by Python, so a misspelling is not caught locally *(consult your driver release's parameter list;
  the names are stable across releases)*.

### The connection string form

A `teradata://user:password@host:1025/database` URI is what tools and config files usually carry. If
you parse one yourself, URL-encode the password — an `@`, `:` or `/` in a password silently truncates
the parse and produces a confusing authentication failure.

## 3. Parameters — the mistake almost everyone makes first

**`teradatasql.paramstyle` is `qmark`.** Placeholders are `?`, positionally.

```python
# CORRECT
cur.execute(
    "SELECT customer_id, total_amount FROM analytics.sales_fact "
    "WHERE region = ? AND sale_date >= ?",
    ["EMEA", datetime.date(2026, 1, 1)],
)

# WRONG - psycopg style. The driver does not substitute, so the literal text reaches
# Teradata and fails with a syntax error (3706), or worse, does nothing you expect.
cur.execute("... WHERE region = %s", ("EMEA",))

# WRONG - named style. Same outcome.
cur.execute("... WHERE region = :region", {"region": "EMEA"})

# NEVER - string interpolation. This is an injection hole and it defeats statement caching.
cur.execute(f"... WHERE region = '{region}'")
```

If you are porting code from Postgres or MySQL, the `%s` → `?` conversion is the first thing to do and
the first thing to check when the port "mysteriously" fails.

**Batches** go through `executemany`, which the driver sends as a single multi-statement request rather
than a loop — the difference between seconds and hours on ten thousand rows:

```python
cur.executemany(
    "INSERT INTO analytics.sales_stage (sale_id, region, total_amount) VALUES (?, ?, ?)",
    rows,                                  # a list of sequences
)
```

Never write `for row in rows: cur.execute(...)`. It is the single most common cause of "the load takes
all night".

## 4. Transactions and session mode

Teradata has two session modes, and they differ in a way that changes your code:

| | `TERA` (Teradata mode) | `ANSI` |
|---|---|---|
| Commit | Each statement commits unless you open an explicit transaction | You must `COMMIT`; work is held open until you do |
| Case comparison | Not case-specific by default | Case-specific |
| Truncation on insert | Silently truncates character data | Raises an error |

Set it at connect time with `tmode="ANSI"` or `tmode="TERA"` *(from Teradata documentation; the default
depends on your driver and system configuration — check rather than assume)*. The DBAPI's
`con.commit()` and `con.rollback()` behave accordingly.

Two practical rules:

- **Decide the mode explicitly** in anything that writes. A job that runs correctly against a TERA
  system and silently leaves an open transaction on an ANSI one is a real and common failure.
- **DDL commits.** Do not expect to roll back a `CREATE`, `DROP` or `ALTER`.

## 5. Errors

`teradatasql` raises the standard DBAPI hierarchy: `Error` → `DatabaseError` → `OperationalError`,
`ProgrammingError`, `IntegrityError`, `DataError`, `InternalError`, `NotSupportedError`, plus
`InterfaceError` and `Warning`.

The useful information is the Teradata error number in the message text, not the Python class — the
driver maps many distinct Teradata conditions onto `OperationalError`. Extract the code and act on it:

```python
import re, teradatasql

try:
    cur.execute(sql)
except teradatasql.DatabaseError as exc:
    m = re.search(r"\[Error (\d+)\]", str(exc))
    code = int(m.group(1)) if m else None
    if code == 3807:      # object does not exist - usually an unqualified name
        ...
    elif code == 2646:    # no more spool space - the query needs rewriting, not retrying
        ...
    raise
```

Codes worth handling by name rather than retrying blindly: `3807` object does not exist, `3806`
duplicate object, `2646` spool exhausted, `3541` no PERM space in the **parent** database, `2644` no
more room in a database, `6706` untranslatable character, `5404` datetime overflow. The `teradata-sql`
skill carries what each one actually means; the plugin's coach hook injects the same when a code
appears in a tool result.

**Do not blanket-retry.** A `2646` or `3807` will fail identically every time; only genuine transport
failures are worth a retry, and then with backoff.

## 6. Pooling

**`teradatasql` ships no connection pool.** Each `connect()` opens a session, and sessions are limited
system-wide. Options:

- **SQLAlchemy** through `teradatasqlalchemy` gives you `QueuePool` for free — the usual answer for a
  web application:
  ```python
  from sqlalchemy import create_engine
  engine = create_engine(
      "teradatasql://analyst:***@warehouse.example.com/analytics",
      pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=3600,
  )
  ```
  `pool_pre_ping=True` and a `pool_recycle` shorter than any network idle timeout are what stop the
  "first request after lunch always fails" class of bug.
- **A long-lived single connection** is fine for a batch job, and wrong for a server.
- **A connection per request, unpooled**, will exhaust sessions under load and is the usual root cause
  when a service starts failing to log on under traffic rather than at startup.

## 7. teradataml — when the data should not leave the database

`teradataml` binds a session and gives you a `DataFrame` whose operations translate into SQL and run in
the database. Use it when the alternative is pulling a large table into pandas.

```python
from teradataml import create_context, remove_context, DataFrame, copy_to_sql

create_context(host="warehouse.example.com", username="analyst", password=os.environ["TD_PASSWORD"])
try:
    df = DataFrame("sales_fact")                       # no rows moved yet
    top = df[df.region == "EMEA"].groupby("store_id").sum()
    pdf = top.to_pandas()                              # rows move HERE, and only these
    copy_to_sql(pdf, table_name="sales_summary", if_exists="replace")
finally:
    remove_context()
```

- `DataFrame(...)` is lazy. Nothing executes until you materialise with `to_pandas()`, `head()` or a
  print. A pipeline built and then materialised once is one query; materialising at each step is many.
- `copy_to_sql` writes a pandas frame back. For anything large, a bulk utility is still the right tool.
- `create_context` / `remove_context` are process-global. Always `remove_context()` in a `finally`.

## 8. Verifying before you ship

The plugin's read path answers the questions that otherwise become runtime failures — use it rather
than guessing from the code:

- `base_tableDDL` on every table the code writes to — is it SET or MULTISET, what is the primary index,
  are there constraints your insert will violate?
- `base_columnDescription` — exact types and lengths, so a `VARCHAR(20)` column does not meet a
  40-character value in production for the first time.
- An `EXPLAIN` through `base_readQuery` on the query your code builds — the `tune` skill reads the plan.

## Reference

- `references/connection-recipes.md` — connection recipes per environment and auth mechanism, the
  JDBC URL form, SQLAlchemy engine configuration, and TLS and proxy settings.
