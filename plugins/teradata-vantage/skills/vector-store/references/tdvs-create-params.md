# tdvs_create / tdvs_update parameter reference — every field the bundled server (teradata-mcp-server 0.2.6) accepts, with types, legal values and the traps

Source: the server's pydantic models `VectorStoreCreate` / `VectorStoreUpdate` (upstream `tools/tdvs/types.py`,
0.2.6). Unknown fields are **silently dropped** (`extra='ignore'`) — a misspelled or unsupported parameter does
not error, the store is simply built without it. Legal-value lists come from the Teradata vector-store REST
service as observed on Vantage 20.00 (from Teradata documentation; verify against your release).

**Every field on this page belongs INSIDE the `vs_create` (or `vs_update`) object, not at the top level of the
tool call.** The tool signature is `handle_tdvs_create(conn, vs_name, vs_create)` — `vs_name` is the only
top-level parameter, and a flat call that spreads these fields alongside it fails validation:

```
tdvs_create(vs_name="my_store", vs_create={"description": "...", "target_database": "<db>", ...})
```

## Required on create

| Field | Type | Notes |
|---|---|---|
| `vs_name` | str | **TOP-LEVEL tool parameter, not a field of `vs_create`** — the store name |
| `description` | str | free text |
| `object_names` | **str** | the source table/view as `<db>.<table>`. The MCP model is a STRING (REST accepts a list); pass one name. |

## Strongly recommended on create

| Field | Type | Notes |
|---|---|---|
| `target_database` | str | **Effectively mandatory.** Omitted → the logon user's database → `3524` when that is DBC. Must be a normal working database. |
| `key_columns` | list[str] | unique key column(s) of the source |
| `data_columns` | list[str] | text column(s) to embed; UNICODE charset (6706 otherwise) |
| `embeddings_model` | str | model name as the endpoint knows it |
| `embeddings_base_url` | str | `http://<host>:<port>/v1` — the `/v1` root; the service appends `/embeddings`. Must be paired with `embeddings_model` (400 otherwise). REST key is `base_url_embeddings`. |
| `embeddings_dims` | int | must equal the model's real width — probe first |
| `metric` | str | `COSINE` \| `EUCLIDEAN` \| `DOTPRODUCT` |
| `search_algorithm` | str | `HNSW` \| `KMEANS` \| `VECTORDISTANCE` (NOT `FLAT` / `IVF_FLAT`) |
| `top_k` | int | default number of matches returned by search |

## Index tuning — HNSW (`search_algorithm='HNSW'`)

| Field | Type | Meaning |
|---|---|---|
| `ef_construction` | int | neighbours considered while building the graph (higher = better recall, slower build) |
| `ef_search` | int | neighbours considered at query time (higher = better recall, slower search) |
| `num_connpernode` | int | connections per node (M) |
| `maxnum_connpernode` | int | upper bound on connections per node |
| `num_layers` | int | maximum graph layers |
| `apply_heuristics` | bool | neighbour-selection heuristic on/off |
| `seed` | int | reproducible build |

Creates an extra table `<db>.vectorstore_<vs>_hnsw_model`.

## Index tuning — KMEANS (`search_algorithm='KMEANS'`)

| Field | Type | Meaning |
|---|---|---|
| `train_numclusters` | int | clusters trained (k) |
| `search_numcluster` | int | clusters probed per query |
| `max_iternum` | int | k-means iteration cap |
| `stop_threshold` | float | convergence threshold |
| `initial_centroids_method` | str | centroid initialisation method (e.g. `RANDOM`, `KMEANS++`; verify on your release) |
| `num_init` | int | number of initialisations |
| `seed` | int | reproducible training |

## Search thresholds and reranking

| Field | Type | Meaning |
|---|---|---|
| `search_threshold` | float | minimum score for a match to be returned |
| `rerank_weight` | float | weight of the reranker vs the vector score |
| `relevance_top_k` | int | matches passed to the reranker |
| `relevance_search_threshold` | float | threshold applied during reranking |
| `ranking_url` | str | reranking service URL (create only) |

## RAG / `tdvs_ask` (create time only for the URL)

| Field | Type | Meaning |
|---|---|---|
| `chat_completion_model` | str | model used by `tdvs_ask`; a store without it cannot answer `tdvs_ask` |
| `completions_base_url` | str | OpenAI-compatible completions root — **create only**; changing it = destroy + recreate |
| `prompt` | str | system prompt used when generating answers |
| `chat_completion_max_tokens` | int | answer length cap |

## File-backed stores (document ingest)

| Field | Type | Meaning |
|---|---|---|
| `document_files` | list[str] | files to parse and chunk |
| `chunk_size` | int | characters per chunk — **rejected on table-backed stores** ("You cannot provide chunk_size for content-based vector store") |
| `optimized_chunking` | bool | Teradata's splitter instead of fixed-size |
| `header_height`, `footer_height` | int | page header/footer to strip |
| `ingest_host`, `ingest_port` | str, int | the document-parsing service (create only). Without it, use the table-backed path. |

## Metadata-based stores (schema search rather than content search)

| Field | Type | Meaning |
|---|---|---|
| `include_objects`, `exclude_objects` | list[str] | tables/views included/excluded |
| `include_patterns`, `exclude_patterns` | list[str] | name patterns |
| `sample_size` | int | rows sampled per object when embedding |

## Cloud-specific

| Field | Type | Meaning |
|---|---|---|
| `batch` | bool | batch embedding (AWS only) |
| `ignore_embedding_errors` | bool | continue past embedding failures (AWS only) |

## Storage

| Field | Type | Meaning |
|---|---|---|
| `vector_column` | str | name of the vector column in the index table |

## `tdvs_update` — differences from create

| Field | Type | Legal values / notes |
|---|---|---|
| `description` | str | required |
| `object_names` | str | required; the table/view to add or remove |
| `alter_operation` | str | **`ADD` \| `DELETE`** (required) |
| `update_style` | str | `MINOR` \| `MAJOR` |
| everything else | | same fields as create, minus the THIRTEEN create-only ones below |

Measured against the vendored 0.2.6 wheel (`tools/tdvs/types.py`): `VectorStoreCreate` declares 48 fields and
`VectorStoreUpdate` 37. The thirteen that exist **only on create** are

`batch`, `chunk_size`, `completions_base_url`, `data_columns`, `embeddings_base_url`, `footer_height`,
`header_height`, `ingest_host`, `ingest_port`, `key_columns`, `optimized_chunking`, `ranking_url`,
`vector_column`.

Neither model declares a `model_config`, so pydantic v2's default `extra='ignore'` applies: passing one of
these to `tdvs_update` is **silently dropped**. The call succeeds, the setting does not change, and nothing
reports it. To change a create-only property you must destroy the store and re-create it.

`tdvs_update` always raises the plugin's approval prompt (a `DELETE` alter removes indexed data). `object_names`
and an uploaded file are mutually exclusive on update ("You cannot provide object_names for file-based vector store").

## Not available through the MCP model (0.2.6)

- `is_embedded` / `is_normalized` / `embedding_data_columns` / `metadata_columns` — the bring-your-own-embeddings
  create shape of the teradataml/REST client. Passing them to `tdvs_create` is silently ignored and yields a
  content-based store that then fails to embed. Use the REST API or the `teradatagenai` client for those.
- An empty password in a JSON REST body means "use the server default" and fails with
  `401 TD2 logon mechanism requires username and password`; the MCP server authenticates from `DATABASE_URI`
  or `TD_PAT`+`TD_PEM`, so this only matters if you call REST directly.

## Reading back what was created

`tdvs_get_details(<vs>)` returns the declared configuration (model, dims, metric, algorithm, top_k, target
database) — not row counts. Verify data with `SELECT COUNT(*) FROM <db>.vectorstore_<vs>_index`.
