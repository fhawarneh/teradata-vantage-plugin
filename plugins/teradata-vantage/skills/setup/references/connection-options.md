# Connection options - DATABASE_URI forms, URL-encoding, LOGMECH, the bridge URL, pool/row-limit settings and the ClearScape Analytics Experience sandbox.

## The connection string

The bundled server reads `DATABASE_URI` (upstream teradata-mcp-server 0.2.6):

```
teradata://<user>:<password>@<host>:<port>/<database>
teradata://<user>:<password>@<host>:1025/<database>?LOGMECH=LDAP
```

| part | rule |
|---|---|
| `<host>` | DNS name or IP of the Teradata system (or of your SSH/VPN tunnel endpoint) |
| `<port>` | `1025` unless the DBA changed it or a tunnel remaps it; defaults to 1025 when omitted |
| `<database>` | the session's default database. NEVER `dbc` - see 3524 below. Use the schema you query most or a working database you own |
| `<user>:<password>` | URL-encode anything that is not a letter, digit, `-`, `_`, `.` or `~` |
| query string | `LOGMECH=` accepted; the server rewrites the URI into a `teradatasql` URL and appends `LOGMECH` (from Teradata documentation; verify other driver parameters against your release before relying on them) |

URL-encoding table (the connect helper does this for you; do it by hand only for the environment/option paths):

| character | encoded | character | encoded |
|---|---|---|---|
| `@` | `%40` | `/` | `%2F` |
| `:` | `%3A` | `?` | `%3F` |
| `#` | `%23` | `%` | `%25` |
| space | `%20` | `&` | `%26` |

Example: password `p@ss/w:rd` -> `p%40ss%2Fw%3Ard`.

## LOGMECH (logon mechanism)

| value | when | typical failure if wrong |
|---|---|---|
| `TD2` (default) | database-authenticated users | - |
| `LDAP` | directory-authenticated warehouses | `Error 8017 The UserId, Password or Account is invalid` even with a correct password |
| `KRB5` | Kerberos single sign-on; needs a valid ticket on the machine running the server | logon failure / ticket errors |
| `JWT` | token logon; the token is passed to the driver as `LOGDATA=token=<jwt>` (from Teradata documentation; verify the URI form against your release) | 8017 / token errors |

Set it once: `LOGMECH=LDAP` in the environment, the connect helper's LOGMECH prompt, or `?LOGMECH=LDAP` on the URI.
The plugin defaults `LOGMECH=TD2` when nothing is set.

## Where the plugin looks, in order

1. `DATABASE_URI` in the environment of the shell that launched Claude Code (explicit environment always wins).
2. Plugin option `database_uri` (`/plugin manage`), which reaches the launcher as `CLAUDE_PLUGIN_OPTION_DATABASE_URI`.
3. `${CLAUDE_PLUGIN_DATA}/teradata.env`, written by `scripts/teradata-vantage-connect` in the user's own terminal
   (mode 0600; keys `DATABASE_URI LOGMECH TERADATA_MCP_URL TD_BASE_URL TD_PAT TD_PEM`). File values fill gaps only.

An empty value or the literal text `${DATABASE_URI}` counts as unset. Nothing is ever printed: the doctor reports only
`credential source: environment | plugin-option | file | none`.

Hygiene: `--show` prints the file with the password redacted; `--remove` deletes it. Turn on the plugin option
`enable_prompt_secret_guard` if you want a warning whenever a `scheme://user:password@` string is pasted into a prompt
(active only in sessions where a Teradata connection is configured).

## The bridge URL (use a server you already run)

```
TERADATA_MCP_URL=http://127.0.0.1:8001/mcp/      # or the plugin option mcp_url, or teradata-vantage-connect --url ...
```

- Streamable-http endpoints of teradata-mcp-server end in `/mcp/` (trailing slash). SSE is deprecated upstream.
- The bridge runs `npx mcp-remote@0.8.3 <url>` (pinned) and needs Node/npx. No credential is needed on this side; the
  remote server holds its own `DATABASE_URI`.
- If the remote server runs with `AUTH_MODE=basic` it expects `Authorization: Basic base64(user:secret)` or a Bearer
  JWT and proxies as that user (`PROXYUSER` in the QueryBand). Configure the header on the bridge/remote as your
  mcp-remote version documents; with stdio (local venv) authentication is bypassed entirely by the server.
- A bare `GET` on the URL returns HTTP 406 when the server is up - that is the liveness signal, not an error.

## Vector store and backup groups

| variable | needed for | notes |
|---|---|---|
| `TD_BASE_URL` | `tdvs_*` tools | the Vector Store / Open Analytics base URL; group is enabled only when non-empty AND the `tdvs` extra is installed |
| `TD_PAT` + `TD_PEM` | `tdvs_*` token auth | both required for PAT auth; otherwise the URI user/password are used |
| `DSA_BASE_URL` or `DSA_HOST` + `DSA_PORT` | `bar_*` tools | plus the `bar` extra; otherwise the group is disabled with a log line |

## Server settings passed through

| variable | default | meaning |
|---|---|---|
| `TERADATA_MCP_PROFILE` | `tv_all` | which tools/prompts/resources are exposed (`config/profiles.yml`) |
| `TERADATA_MCP_CONFIG_DIR` | `${CLAUDE_PLUGIN_ROOT}/config` | the server's `CONFIG_DIR`; always set explicitly by the launcher (upstream defaults to the process CWD) |
| `DEFAULT_ROW_LIMIT` / `MAX_ROW_LIMIT` | 1000 / 50000 | upstream row caps for query tools |
| `TD_POOL_SIZE` / `TD_MAX_OVERFLOW` / `TD_POOL_TIMEOUT` | 5 / 10 / 30 | SQLAlchemy pool; raise pool size for many parallel tool calls |
| `TERADATA_MCP_PROGRESSIVE` | false | expose only `search_tool`, `execute_tool`, `base_readQuery` |
| `TERADATA_MCP_AUDIT_LOG` | unset | opt-in JSON-L audit of tool calls (observe-only) |
| `TERADATA_MCP_MODE` | `auto` | force `bridge`, `venv`, `uvx` or `docker` |
| `TERADATA_MCP_PYTHON` | auto | interpreter (3.11+) used by install and venv discovery |

There is no server-side query timeout and no `pool_recycle` in upstream 0.2.6. The 180 s `timeout` in `.mcp.json` is
the only client-side bound. Every variable reaches the server at process start only: new session after a change.

## Error 3524 and the default database

`Error 3524 The user does not have CREATE TABLE access to database DBC` means the session default database is DBC.
No one can create objects there. The URI path is the session default: `.../1025/<working_db>`. The connect helper warns
when you answer `dbc` to the default-database prompt.

## Zero-cost test system

Teradata publishes a free hosted sandbox, ClearScape Analytics Experience (https://www.teradata.com/getting-started/demos/clearscape-analytics).
Its environment page shows the host (a `*.env.clearscape.teradata.com` name), the user name and the password you
chose. Use those values with the connect helper; the port is 1025 and `LOGMECH=TD2`. Environments hibernate when idle,
so a first logon after a pause can hang for a minute or two while the system wakes - see the hang entry in
`references/troubleshooting.md`.
