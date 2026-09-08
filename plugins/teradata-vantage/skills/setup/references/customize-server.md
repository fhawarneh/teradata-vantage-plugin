# Customize the bundled server - CONFIG_DIR rules, curated profiles, the `<domain>_objects.yml` walkthrough (tools, cubes, prompts, glossary), QueryBand auditing, service-account setup, and the honest 0.2.6 timeout story.

## How CONFIG_DIR works (upstream teradata-mcp-server 0.2.6)

- The launcher always passes `--config_dir "${TERADATA_MCP_CONFIG_DIR:-${CLAUDE_PLUGIN_ROOT}/config}"`. Upstream
  defaults CONFIG_DIR to the process working directory when unset, which for a stdio server launched from an arbitrary
  project is a hazard; the plugin never relies on it.
- The server loads ONE config directory. If you point `TERADATA_MCP_CONFIG_DIR` at your own directory, the plugin's
  `config/profiles.yml` is no longer read: copy it into your directory first and extend it there.
- **`profiles.yml` merges by top-level key.** Packaged defaults are loaded first; then each top-level key in your
  file REPLACES the packaged key of the same name entirely, and every key you do not mention survives. So a file
  defining `tv_readonly`, `tv_analyst`, `tv_dba`, `tv_all` adds those and leaves `all`, `dba`, `eda`, `dataScientist`,
  `bar`, `llmUser`, `graph` untouched - but a profile you name `dba` wipes the packaged `dba`.
- Custom objects live in files named `<something>_objects.yml` in the config directory (the `_objects.yml` suffix is
  what the loader scans reliably). Objects override packaged objects of the same name.
- Special files handled by the layered loader, never scanned for objects: `profiles.yml`, `chat_config.yml`,
  `rag_config.yml`, `sql_opt_config.yml`.
- NEVER add a `run:` block to a profile. It can push `database_uri`/transport values into the server's environment
  before parsing and bypass the plugin's credential handling.

Profile patterns are regular expressions matched with `re.match` (a prefix match). Write `^dba_.*`, not `^dba_*`
(the latter works only by accident). Exclude test prompts with `^(?!_?test).*`. Give every profile an explicit
`resource:` list, otherwise it exposes no MCP resources at all.

## Walkthrough: `config/example_domain_objects.yml.example`

The `.example` suffix keeps it out of the server's scan. Copy it to your own directory as `<domain>_objects.yml`,
replace `<db>` and the columns, then expose it through a profile.

```bash
mkdir -p ~/teradata-mcp-config
cp "${CLAUDE_PLUGIN_ROOT}/config/profiles.yml" ~/teradata-mcp-config/profiles.yml
cp "${CLAUDE_PLUGIN_ROOT}/config/example_domain_objects.yml.example" ~/teradata-mcp-config/sales_objects.yml
```

### 1. A parameterised tool (`type: tool`)

```yaml
sales_top_customers:
  type: tool
  description: Top N customers by total sales amount
  sql: |
    SELECT TOP %(limit)s customer_id, SUM(amount) AS total_sales
    FROM <db>.sales_fact
    GROUP BY customer_id
    ORDER BY total_sales DESC
  parameters:
    limit:
      description: Number of customers to return
      type_hint: int
      default: 10
```

- Parameters bind as `%(name)s`; `type_hint` accepts `str`, `int`, `float`.
- Row limits are `SELECT TOP %(limit)s` - **never `LIMIT`**. The upstream CUSTOMIZING guide's own example uses
  `LIMIT %(limit)s`, which Teradata rejects with `Error 3706 Syntax error`; do not copy it.
- Aliases: never `count`, `date`, `value`, `key`, `type`, `status`, `period`, `level` (reserved -> `Error 3707`).
- Always qualify `<db>.<table>` (`Error 3807 Object does not exist` otherwise, because the session default database is
  whatever the URI says).

### 2. A cube (`type: cube`)

```yaml
sales_by_region:
  type: cube
  description: Sales measures by region, product and month
  sql: |
    SELECT region, product, EXTRACT(YEAR FROM sale_date) AS sale_year,
           EXTRACT(MONTH FROM sale_date) AS sale_month, amount, quantity
    FROM <db>.sales_fact
  dimensions:
    region:     {description: Sales region,          expression: region}
    product:    {description: Product name,          expression: product}
    sale_month: {description: Calendar month (1-12), expression: sale_month}
  measures:
    total_sales: {description: Sum of sale amounts, expression: SUM(amount)}
    units:       {description: Units sold,          expression: SUM(quantity)}
```

The server registers a tool named after the cube with parameters `dimensions`, `measures`, `dim_filters`,
`meas_filters`, `order_by`, `top` and generates:

```sql
SELECT TOP <top> * FROM (
  SELECT <dimension expressions>, <measure expression> AS <measure name>
  FROM ( SELECT * FROM ( <cube.sql> ) a  WHERE <dim_filters> ) AS c
  GROUP BY <dimension expressions>
) AS a WHERE <meas_filters> ORDER BY <order_by>;
```

Consequences for authors: every `expression` lands inside a GROUP BY over your base SQL; an unknown measure fails with
`Measure '<m>' not found in cube '<name>'`, an unknown dimension is passed through as a raw column name (and fails in
Teradata, usually `5628`). Guard denominators with `NULLIF(...,0)`; cast before dividing DECIMAL by DECIMAL (Teradata
keeps the numerator's scale) and cast to `DECIMAL(18,2)`/`FLOAT` before multiplying large sums (`Error 2616 Numeric
overflow`). Dimension and measure names are folded into the glossary automatically.

### 3. A prompt (`type: prompt`) and a glossary (`type: glossary`)

```yaml
sales_analyst:
  type: prompt
  description: Sales analysis assistant
  prompt: >
    You are a sales data analyst working on Teradata Vantage. Answer only from query results,
    cite the SQL you ran, and use SELECT TOP n for row limits.

glossary:
  type: glossary
  customer:
    definition: A person or organisation that purchased goods or services.
    synonyms: [client, buyer]
```

Prompts surface as slash commands (as exposed by `/mcp`); prompt text is Python `.format(**kwargs)` templated, so
escape literal braces as `{{ }}`. A glossary registers three MCP resources: `glossary://all`, `glossary://definitions`,
`glossary://term/{term_name}`.

### 4. Expose it and restart

Append to `~/teradata-mcp-config/profiles.yml`:

```yaml
sales:
  tool:     [^sales_.*, ^base_(?!(writeQuery|dynamicQuery)$).*]
  prompt:   [^sales_.*]
  resource: [.*]
```

Then, in the shell that launches Claude Code:

```bash
export TERADATA_MCP_CONFIG_DIR=~/teradata-mcp-config
export TERADATA_MCP_PROFILE=sales
```

New session; `/mcp` should list `sales_top_customers` and `sales_by_region`. Custom tools run the SQL you declared -
the plugin's read guard does not inspect them, so keep them read-only unless you intend otherwise.

### Authoring help from upstream

Teradata maintains an agent skill that builds these YAML files from your existing documentation:
`teradata-mcp-customisation` under https://github.com/Teradata/teradata-mcp-server/tree/main/agentic (examples for
tool, cube, prompt, glossary, profiles plus reference notes on cube mechanics and parameter substitution). Use it
rather than re-deriving the object grammar; this plugin does not duplicate it.

## Auditing what the agent ran (QueryBand + DBQL)

Every tool call sets a session QueryBand before running, e.g.
`APPLICATION=teradata-mcp-server;PROFILE=tv_all;PROCESS_ID=<host>:<pid>;TOOL_NAME=base_readQuery;REQUEST_ID=...;SESSION_ID=...;`
(plus `PROXYUSER`, `AUTH_SCHEME`, `AUTH_HASH`, `CLIENT_IP`, `USER_AGENT`, `TENANT` when applicable). With DBQL enabled
you can report on it (from the upstream security guide; verify column names against your release):

```sql
SELECT GetQueryBandValue(QueryBand, 0, 'TOOL_NAME') AS tool_name,
       UserName,
       COUNT(*)          AS request_cnt,
       AVG(ElapsedTime)  AS elapsed_avg
FROM DBC.QryLogV
WHERE GetQueryBandValue(QueryBand, 0, 'APPLICATION') = 'teradata-mcp-server'
  AND CAST(StartTime AS DATE) = CURRENT_DATE
GROUP BY 1, 2
ORDER BY 3 DESC;
```

Precondition: query logging must be on for the user (`BEGIN QUERY LOGGING ... ON <user>`) and you need SELECT on
`DBC.QryLogV`. Without DBQL the query returns nothing - that is absence of logging, not absence of activity.

## Service account (proxy user) instead of one shared application user

Default deployments connect as the URI user (the "application user" pattern): everyone reaching the server inherits
that user's rights. Over streamable-http with `AUTH_MODE=basic`, upstream supports a proxy-user pattern where the
server connects as a service account and executes as the authenticated end user (`PROXYUSER` in the QueryBand), so
existing roles and row-level security keep applying. SQL from the upstream security guide (adapt names; run as a DBA):

```sql
CREATE USER mcp_svc AS
    PASSWORD = <initial_password>
   ,PERM  = 10e9
   ,SPOOL = 10e9
   ,ACCOUNT = 'service_account';

GRANT CTCONTROL ON mcp_svc TO sysdba WITH GRANT OPTION;

GRANT CONNECT THROUGH mcp_svc
  TO PERMANENT <end_user_1> WITHOUT ROLE
   , PERMANENT <end_user_2> WITHOUT ROLE;
```

Then the server's `DATABASE_URI` uses `mcp_svc`, and each client authenticates with its own database credentials
(`Authorization: Basic ...`) or a JWT. Notes: proxy sessions use the end user's default role unless `WITH ROLE` is
given; a user cannot proxy as itself (`Error 9203`); `AUTH_MODE` values other than `none`/`basic` are not implemented
in 0.2.6; with the stdio transport (the plugin's local venv mode) authentication is bypassed entirely, so the proxy
pattern applies to bridge deployments only.

## Timeouts, honestly (0.2.6)

- Upstream builds its connection pool with `pool_pre_ping=True` only: **no `pool_recycle`, no per-statement
  `request_timeout`, no tool-execution wall clock**. A statement that runs for an hour keeps running for an hour.
- The only bound the plugin adds is Claude Code's per-server MCP timeout, `180000` ms in `.mcp.json`. When it fires,
  the tool call fails on the client side; the SQL is **still executing on Teradata**.
- Diagnose: `mcp__plugin_teradata-vantage_teradata__dba_sessionInfo` (or `SELECT SessionNo, UserName, LogonDate,
  LogonTime, CurrentRole FROM DBC.SessionInfoV WHERE UserName = '<uri_user>'`) shows the server's sessions.
- End a runaway statement (needs the ABORT SESSION privilege). The bundled 0.2.6 server has no write tool and the read
  guard denies `ABORT` on `base_readQuery`, so on the bundled server the DBA runs this in their own client; on a
  bridged server that exposes `base_writeQuery` the write gate asks first:

```sql
ABORT SESSION <hostid>.<sessionno>;          -- one session
ABORT SESSION <hostid>.<sessionno> LOGOFF;   -- abort and log it off
```

(from Teradata documentation; verify the host-id/session-number form against your release). After a Teradata restart
or a dropped network path, pooled connections may be dead: restart the server (new Claude Code session for stdio,
restart the remote process for bridge) rather than waiting for `pool_pre_ping` to notice.
- Pool sizing for many parallel tool calls: `TD_POOL_SIZE` (5) and `TD_MAX_OVERFLOW` (10) are the upstream knobs.

## Where to report server bugs

Issues in the server itself belong upstream: https://github.com/Teradata/teradata-mcp-server/issues. This plugin
vendors the unmodified 0.2.6 wheel (see `servers/VENDORED.md`) and never patches it.
