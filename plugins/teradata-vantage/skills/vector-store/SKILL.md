---
name: vector-store
description: Use when creating, inspecting, searching, updating, repairing or destroying a Teradata Enterprise Vector Store through the tdvs_* tools, or for vector work directly in the database (VECTOR32, TD_VECTORDISTANCE, in-database ONNX embeddings). Covers the mandatory target_database rule, the embeddings model/base-URL pairing, legal search algorithms, the vectorstore_* layout and CREATE FAILED repair.
when_to_use: create a vector store; semantic or similarity search over <db>.<table>; RAG on Teradata; tdvs; embed this table; vector store stuck in CREATE FAILED or CREATING; why does tdvs_create fail with 3524; HNSW vs KMEANS; TD_VECTORDISTANCE; ONNXEmbeddings or BYOM embeddings; Vector32 column; grant a user access to the vector store; rag_Execute_Workflow; RAG on Teradata.
license: MIT
metadata:
  skill_type: workflow
  category: teradata
  version: "1.0.0"
argument-hint: "[list | details <vs_name> | create <vs_name> from <db>.<table> | search <vs_name> \"question\" | repair <vs_name> | in-database]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__tdvs_list
  - mcp__plugin_teradata-vantage_teradata__tdvs_get_details
  - mcp__plugin_teradata-vantage_teradata__tdvs_get_health
  - mcp__plugin_teradata-vantage_teradata__tdvs_similarity_search
  - mcp__plugin_teradata-vantage_teradata__tdvs_ask
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - Read
---

# Teradata Enterprise Vector Store (TDVS) and in-database vectors

A vector store is a **service-managed** object: the `tdvs_*` tools call the Teradata vector-store REST
service, which writes physical tables into the database. You therefore have two views of one thing, and
both matter:

- **Control plane** — `tdvs_list`, `tdvs_get_details`, `tdvs_get_health`, `tdvs_create`, `tdvs_update`,
  `tdvs_destroy`, `tdvs_similarity_search`, `tdvs_ask`, `tdvs_grant_user_permission`, `tdvs_revoke_user_permission`.
- **Data plane** — real tables and views you read with `base_readQuery`.

ALWAYS use a `tdvs_*` tool for what the service owns (create / update / destroy / search) and `base_readQuery`
for inspection, verification and diagnosis. When the service is not deployed, or for hand-rolled vector work,
use the in-database primitives in `references/in-database-vectors.md`. This is a community plugin, not
affiliated with Teradata; product behaviour below was observed on Vantage 20.00 — verify against your release.

**Availability.** The `tdvs_*` tools exist only when the local server was installed with the `tdvs` extra
(`/teradata-vantage:setup install` with `server_extras=tdvs`, ~170 MB more dependencies) AND `TD_BASE_URL`
points at the vector-store service (`http://<host>:<port>`); auth is the `DATABASE_URI` user/password or
`TD_PAT` + `TD_PEM` together. Unset `TD_BASE_URL` → every `tdvs_*` call fails with "TD_BASE_URL environment
variable is not set"; an unresolvable host → `NameResolutionError`. Profiles `tv_readonly`/`tv_dba` hide these
tools; `tv_analyst` exposes only the read/search ones. The server also exposes two MCP prompts
(`tdvs_tools_prompt`, `tdvs_rag_prompt`) as slash commands, as exposed by `/mcp`.

**Safety model.** `tdvs_destroy`, `tdvs_update`, `tdvs_grant_user_permission` and
`tdvs_revoke_user_permission` always raise an approval prompt (tool name is the classification).
`base_readQuery` is held read-only by a hook. The catalog repair below is a `DELETE`: it goes through
`base_writeQuery` (which prompts) only when the connected server provides that tool — the bundled upstream
0.2.6 server registers no write tool, so otherwise hand the exact statement to the DBA to run in their own
SQL client and verify with `base_readQuery`. These are mistake guards, not a security boundary.

## Hard rules (each has caused a real failure)

### 1. `target_database` is mandatory on create — and `DATABASE_URI` must not default to DBC
Omit it and the service defaults to the logon user's database. When that is `dbc` the create dies half-way:
```
Teradata 3524: The user does not have CREATE TABLE access to database DBC
[SQL: CREATE MULTISET TABLE "DBC".vectorstore_temp_<name> AS ( ... ) WITH NO DATA]
```
What it actually means: nobody — not even `dbc` — can `CREATE TABLE` in `DBC`. ALWAYS pass an explicit
`target_database` that is a normal working database. The same trap hits the MCP server's own connection:
`teradata://user:pw@host:1025/<working_db>` — the path segment must be a working database, because the
service's Python client materialises DataFrame results into temp tables there (`tdvs_get_health` and
`tdvs_similarity_search` fail with 3524 while a plain status call succeeds — a confusing partial failure).

### 2. `embeddings_model` and the embeddings base URL travel together
```
400: You must provide embeddings_model and base_url_embeddings together.
```
Pass `embeddings_base_url` as the **`/v1` root** of any OpenAI-compatible embeddings endpoint; the service
appends `/embeddings` and reports it back as `http://<host>:<port>/v1/embeddings`. (The REST key is
`base_url_embeddings`; the MCP/teradataml parameter is `embeddings_base_url` — the tool maps it.) The
endpoint is called FROM the database-side host — verify reachability from there, not from your laptop.

### 3. `embeddings_dims` must match the model
Confirm the real width before building a large store by embedding one probe string
(`curl <base>/v1/embeddings -d '{"model":"<m>","input":"probe"}'` → count `data[0].embedding`). A mismatch
corrupts the index. Common widths: 384 (bge-small class), 768 (nomic/SigLIP class); a `VECTOR32` column caps
at 32,768 bytes → 8,192 dims.

### 4. Creation is asynchronous
`tdvs_create` returns **202 Accepted** with status `CREATING`; the tool call may time out while the build
continues — that is NOT a failure. Poll `tdvs_list` / `tdvs_get_details` or the catalog view until
`vs_status = READY`. Statuses seen: `CREATING`, `READY`, `CREATE FAILED`. Read the status from the field the
surface you are using returns (`vs_status` in the catalog/MCP row; `status` in raw REST) — polling the wrong
one spins to timeout.

## Legal parameter values (the ones agents get wrong)

- `search_algorithm`: **`HNSW` | `KMEANS` | `VECTORDISTANCE`** (Teradata names). `FLAT` / `IVF_FLAT` are
  FAISS names and produce an invalid create.
  - HNSW knobs: `ef_construction`, `ef_search`, `num_connpernode`, `maxnum_connpernode`, `num_layers`,
    `apply_heuristics`, `seed`.
  - KMEANS knobs: `train_numclusters`, `search_numcluster`, `max_iternum`, `stop_threshold`,
    `initial_centroids_method`, `num_init`, `seed`.
  - VECTORDISTANCE: exact brute-force; no index knobs; fine for small stores.
- `metric`: `COSINE` | `EUCLIDEAN` | `DOTPRODUCT`.
- `alter_operation` (update): `ADD` | `DELETE`; `update_style`: `MINOR` | `MAJOR`.
Full field list with types: `references/tdvs-create-params.md`.

## Physical layout in the database

For store `<vs>` created with `target_database = <db>`:

| Object | Kind | Holds |
|---|---|---|
| `<db>.vectorstore_<vs>_index` | table | `vector_index`, `vector_index_normalized` (both `SYSUDTLIB.Vector32`), the key column(s), `TD_TEMP_ID`, `DataBaseName`, `TableName` |
| `<db>.vectorstoreV_<vs>` | view | joined read surface: content columns AND vector columns |
| `<db>.vectorstore_combined_<vs>` | view | content projection only (key + data columns) |
| `<db>.vectorstore_<vs>_hnsw_model` | table | trained HNSW model (HNSW stores only) |

Service catalog: `TD_SYSAI.TD_VectorStoresV` (`vs_name`, `vs_status`, `database_name`) over the base table
`TD_SYSAI.TD_VectorStores`. NEVER invent object names — list them:
```sql
SELECT vs_name, vs_status, database_name FROM TD_SYSAI.TD_VectorStoresV ORDER BY 1;
SELECT TableName, TableKind FROM DBC.TablesV WHERE DatabaseName='<db>' AND TableName LIKE 'vectorstore%<vs>%' ORDER BY 1;
```
**Verify an ingest with row counts, not with `tdvs_get_details`** (it reports declared config only):
```sql
SELECT COUNT(*) FROM <db>.vectorstore_<vs>_index;   -- must equal
SELECT COUNT(*) FROM <db>.<source_table>;           -- the source (or chunk) row count
```

## The V1 / V2 boundary
- **V1** (`/data-insights/api/v1/...`, "vector stores") — what every `tdvs_*` tool uses; backed by
  `TD_SYSAI.TD_VectorStoresV`. Needs a database release that ships `TD_SYSAI` V1 objects.
- **V2** (`/api/v2/collections`, "vector collections") — backed by `TD_SYSAI.TD_CollectionsV`; required by
  `langchain_teradata.TeradataVectorStore`. A newer database release than V1.
```sql
SELECT TableName FROM DBC.TablesV WHERE DatabaseName='TD_SYSAI' AND TableKind='V';   -- is TD_CollectionsV present?
```
If it is absent, V2 calls fail with `Object 'TD_SYSAI.TD_CollectionsV' does not exist` — that is a release
gap, not a service outage. Confirm V1 health with `tdvs_get_health` before reporting anything as down.

## Create — the checklist
1. `base_tableDDL` / `base_columnDescription` on the source; pick `key_columns` (unique) and `data_columns`
   (text to embed). Text columns should be `CHARACTER SET UNICODE` — a LATIN column fails on the first
   non-Latin character with **6706** "The string contains an untranslatable character".
2. Probe the embedding endpoint for dims (rule 3).
3. `tdvs_create(vs_name="<store>", vs_create={description, target_database:"<db>",
   object_names:"<db>.<table>", key_columns:[…], data_columns:[…], embeddings_model:…,
   embeddings_base_url:"<…/v1>", embeddings_dims:N, metric:'COSINE',
   search_algorithm:'HNSW'|'KMEANS'|'VECTORDISTANCE', top_k:…})`.
   **`vs_name` is a TOP-LEVEL tool parameter; every other field goes inside the `vs_create` object.**
   The same split applies to the other tools: `tdvs_update(vs_name=…, vs_update={…})`,
   `tdvs_similarity_search(vs_name=…, vs_similaritysearch={…})`, `tdvs_ask(vs_name=…, vs_ask={…})`,
   while `tdvs_get_details`, `tdvs_get_health` and `tdvs_destroy` take `vs_name` alone and
   `tdvs_grant_user_permission` / `tdvs_revoke_user_permission` take `vs_name, user_name, permission`.
   A flat call omitting the envelope fails validation. Add `chat_completion_model` +
   `completions_base_url` only if `tdvs_ask` (server-side RAG) is wanted — the completions base URL can be set
   at create time only.
4. Poll until `READY`; then verify with the index row count.

Ingest shapes: **table/view-backed** (`object_names` + `key_columns` + `data_columns`; the dependency-free
path — chunk documents into rows first) or **file-backed** (`document_files` + `chunk_size`,
`optimized_chunking`, `header_height`, `footer_height`; needs the document-parsing service `ingest_host`/
`ingest_port`). The service rejects chunking parameters on a table-backed store
("You cannot provide chunk_size for content-based vector store") and rejects `object_names` on a file-backed
update. The MCP model declares `object_names` as a **string** (REST takes a list) and has no `is_embedded`
field (bring-your-own-embeddings) — such extra fields are silently dropped, producing a content store that then
fails; use the REST/teradataml client for `is_embedded=True` stores.

## Search behaviour
- `tdvs_similarity_search(vs_name="<store>", vs_similaritysearch={question:"…"})` → top-k rows with a score;
  **higher = better**; content may be truncated. `question` is required and the only other accepted fields are
  `batch_data`, `batch_id_column` and `batch_query_column` — a per-call `top_k` is NOT accepted, because
  `top_k` is a create-time store property (see the line below). Scores degrade honestly for out-of-corpus questions (measured ~0.66 in-corpus vs ~0.49 out).
- `tdvs_ask` needs a store created with a chat model; on a search-only store it fails — use
  `tdvs_similarity_search` and answer from the retrieved rows instead, or destroy + recreate with a chat model.
- `top_k`, `metric`, `search_algorithm` are store properties (create time), visible via `tdvs_get_details`.

## Recovering a `CREATE FAILED` store
A store that failed during creation becomes **unmanageable through the API** — `tdvs_get_details`,
`tdvs_destroy` and a raw REST `DELETE` all return
```
400: You must provide chat_completion_model for main model in guardrails configuration
```
What it actually means: the service loads the incomplete config before acting. `tdvs_destroy` works on a
healthy store; this is specific to failed ones. Repair = remove the catalog row, then drop the half-built objects:
```sql
SELECT vs_name, vs_status, database_name FROM TD_SYSAI.TD_VectorStoresV WHERE vs_name='<vs>';   -- confirm CREATE FAILED
DELETE FROM TD_SYSAI.TD_VectorStores WHERE vs_name = '<vs>';                                     -- prompts; describe it first
SELECT TableName FROM DBC.TablesV WHERE DatabaseName='<db>' AND TableName LIKE 'vectorstore%<vs>%';  -- leftovers to DROP
```
State exactly which rows/objects will be removed and wait for approval before each write.

## Permissions
`tdvs_grant_user_permission` / `tdvs_revoke_user_permission` change who can query a store (both prompt).
They do not grant database SELECT on the source table — that stays a normal `GRANT`.

## Reporting
State measured facts: store name, status, target database, embedding model + dims, metric, algorithm, index
row count vs source row count. NEVER claim a store is ready without `vs_status = READY`; NEVER claim an ingest
succeeded without a row count; NEVER report a missing V2 view as an outage.

## References
- `references/tdvs-create-params.md` — full `tdvs_create` field list with types.
- `references/in-database-vectors.md` — `VECTOR32`, `TD_VECTORDISTANCE`, and BYOM embeddings without a store.
- `references/rag-workflow.md` — `rag_Execute_Workflow`: it retrieves over a corpus someone else embedded,
  is driven by a config file inside the server package, and writes a query table. Read before recommending it.
