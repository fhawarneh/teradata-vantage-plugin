# Connection recipes — per language and authentication mechanism, plus SQLAlchemy engine configuration, TLS, and the failures each setting prevents.

Parameter names below are passed through to the driver's native layer rather than validated in Python,
so a typo is not caught locally. They are stable across releases, but *(consult your driver release's
parameter list)* before relying on an unusual one.

## Python — `teradatasql`

```python
import os, teradatasql

con = teradatasql.connect(
    host="warehouse.example.com",
    user="analyst",
    password=os.environ["TD_PASSWORD"],
    database="analytics",     # default database only
    dbs_port="1025",          # default; set when the site uses another
    logmech="TD2",            # TD2 | LDAP | KRB5 | JWT | TDNEGO
    encryptdata="true",       # encrypt the session
    tmode="ANSI",             # ANSI | TERA - decide explicitly in anything that writes
)
```

`connect()` also accepts the whole thing as one JSON string, which is what configuration files usually
carry:

```python
con = teradatasql.connect('{"host":"warehouse.example.com","user":"analyst","password":"..."}')
```

### By authentication mechanism

| Mechanism | Set | Notes |
|---|---|---|
| Teradata (default) | `logmech="TD2"` | Username and password held in the database |
| Directory | `logmech="LDAP"` | The site's directory validates. Usually the right answer in an enterprise — no password stored per application |
| Kerberos | `logmech="KRB5"` | Needs a valid ticket in the caller's environment; there is no password to supply |
| Token | `logmech="JWT"`, plus the token | For federated or service-to-service access |
| Negotiate | `logmech="TDNEGO"` | Lets the system pick |

If authentication fails immediately and the password is definitely right, check `logmech` first — a
directory account attempted as `TD2` fails exactly like a wrong password.

## Python — SQLAlchemy

```python
from sqlalchemy import create_engine, text

engine = create_engine(
    "teradatasql://analyst:%s@warehouse.example.com/analytics" % quote_plus(pw),
    connect_args={"logmech": "LDAP", "tmode": "ANSI", "encryptdata": "true"},
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,      # detects a connection the network dropped while idle
    pool_recycle=3600,       # shorter than any firewall/idle timeout in the path
)

with engine.connect() as con:
    rows = con.execute(text("SELECT TOP 5 region FROM analytics.sales_fact")).fetchall()
```

- **URL-encode the password** (`urllib.parse.quote_plus`). An `@`, `:`, `/` or `#` in a password
  truncates the URL parse and produces an authentication error that looks like bad credentials.
- **`pool_pre_ping` plus `pool_recycle`** are what prevent the "first request after an idle period always
  fails" pattern — a firewall or load balancer silently drops idle TCP sessions and the pool hands out
  a dead one.
- Prefer `connect_args` over cramming options into the URL query string: it is explicit and it avoids a
  second layer of escaping.

## Java — JDBC

```java
String url = "jdbc:teradata://warehouse.example.com"
           + "/DATABASE=analytics,DBS_PORT=1025,LOGMECH=LDAP,TMODE=ANSI,ENCRYPTDATA=ON";
try (Connection con = DriverManager.getConnection(url, user, password);
     PreparedStatement ps = con.prepareStatement(
         "SELECT customer_id FROM analytics.sales_fact WHERE region = ?")) {
    ps.setString(1, "EMEA");
    try (ResultSet rs = ps.executeQuery()) { while (rs.next()) { /* ... */ } }
}
```

The driver is `terajdbc4`. Note the URL shape: parameters follow the host after a `/`, separated by
commas — not the `?a=b&c=d` query string most JDBC drivers use. Placeholders are `?`, as in Python.

## TLS

`encryptdata=true` (`ENCRYPTDATA=ON` in JDBC) encrypts the session. Where the site presents a
certificate that must be validated, the driver takes an `sslmode` and a CA bundle
*(from Teradata documentation; the accepted values and the certificate parameter name vary by driver
release — check yours)*. Two rules that hold regardless:

- Turning verification off to "make it work" converts a certificate problem into a silent
  man-in-the-middle exposure. Fix the trust store instead.
- A connection that hangs rather than refusing is a firewall dropping packets, not a TLS problem. A
  refused connection answers immediately; a filtered one waits for the timeout.

## Failure quick reference

| Symptom | Usual cause |
|---|---|
| Hangs, then times out | Port 1025 filtered by a firewall. A closed port refuses immediately |
| Authentication fails with a correct password | `logmech` wrong — a directory account attempted as `TD2` |
| Fails only when the password contains punctuation | The URI was not URL-encoded |
| Works locally, fails in a container | The container cannot resolve or reach the host; check DNS before credentials |
| First request after idle always fails | No `pool_pre_ping` / `pool_recycle`; the pool is handing out a dropped connection |
| `[Error 3807] object does not exist` in one environment only | Code relies on the default `database=` instead of qualifying `<db>.<table>` |
| Sessions exhausted under load | A connection per request with no pool, or connections never closed — use `with` |
| Character data silently truncated on insert | `TMODE=TERA`; ANSI mode raises an error instead |
