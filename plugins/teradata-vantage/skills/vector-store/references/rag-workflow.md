# `rag_Execute_Workflow` — what it is, and what it is not

The bundled server exposes one RAG tool. It is narrower than its name suggests, and it is configured in a
file inside the server package rather than through its arguments — so it will not work against a real
deployment until someone edits that file. Say that before recommending it.

## The signature

```
rag_Execute_Workflow(question: str, k: int | None = None)
```

Two arguments. **Everything else — which databases, which tables, which model, which embedding approach —
comes from `rag_config.yml` inside the installed server package.** There is no argument for the vector
store, so the tool answers against whatever that file points at.

## What it actually does

Per the tool's own documentation, the pipeline is:

1. Read configuration from `rag_config.yml`.
2. Store the question in a query table, stripping a leading `/rag ` prefix.
3. Embed the question — either through BYOM `ONNXEmbeddings` or through the IVSM functions, depending on
   the configured `version`.
4. Semantic search against **precomputed** chunk embeddings.
5. Return the matching context chunks.

Two things follow, and both matter:

- **It retrieves; it does not ingest.** There is no chunking, no document loading, no embedding of your
  corpus. It assumes a populated vector table already exists. Someone else's pipeline built that, and if
  nobody did, this tool has nothing to search.
- **It returns context, not an answer.** Step 5 is the end. The chunks come back for the model to answer
  from — which is the correct division of labour, but it means "run the RAG workflow" does not produce a
  finished answer on its own.

**It also writes.** The tool creates its query table if it does not exist and inserts each question into
it. That is a side effect worth mentioning to anyone who assumed a retrieval tool was read-only.

## The configuration is the whole problem

`rag_config.yml` ships with **demo defaults that will not match any real system** — a placeholder database
name used for every one of the query, model and vector databases, a vector table named for whatever
dataset the example was built from, and a specific embedding model id. Against a real deployment the tool
fails on a missing database or table, and the error points at the configuration, not at the question.

The fields that must be set before it can work:

| Section | Field | What it needs to be |
|---|---|---|
| `version` | | `ivsm` or `byom` — which embedding path. Defaults to `ivsm` |
| `databases` | `query_db`, `model_db`, `vector_db` | real databases; they may differ |
| `tables` | `vector_table` | the existing table of chunk embeddings |
| `tables` | `query_table`, `query_embedding_store` | where questions are recorded — the tool creates these |
| `tables` | `model_table`, `tokenizer_table` | for the BYOM path, where the model and tokenizer live |
| `model` | `model_id` | must match a row in `model_table` |
| `retrieval` | `default_k`, `max_k` | how many chunks come back |
| `vector_store_schema` | required and metadata fields | the columns your vector table actually has |

Because the file lives inside the installed package, changing it means editing the installed server and
restarting it. That is a deployment step, not a conversation step — hand it to the user.

## Two upstream defects to know

- **The tool's documentation names `mldb.ONNXEmbeddings`.** That database does not exist. Measured on
  Vantage 20.00, `mldb` fails with `Database 'mldb' does not exist` and the functions resolve under
  **`TD_MLDB`**. If the BYOM path fails with a missing-database error, this is why. The `analytics` skill
  covers `TD_MLDB` properly.
- **The IVSM path needs the IVSM functions installed**, which is a separate in-database package from BYOM.
  Neither is guaranteed. Check before promising either: query `DBC.FunctionsV` for the function names, the
  way the `analytics` skill's Rule 0 describes.

## When to use it, and when not to

**Use it** when the user already has a chunk-embedding table built by their own pipeline, the config has
been pointed at it, and they want retrieval wired into the conversation.

**Do not reach for it** when:

- There is no vector table yet. Building one is the job, and this tool does not do it.
- The user wants a managed store with a lifecycle. That is Enterprise Vector Store and the `tdvs_*` tools
  — see the main `vector-store` skill. `tdvs_ask` and `tdvs_similarity_search` need no config file.
- The user wants to search a table they can point at directly. `tdvs_similarity_search` is far less
  ceremony.
- You only need distance between vectors you already have. `TD_VECTORDISTANCE` and the `VECTOR32` type are
  plain SQL — see `in-database-vectors.md`.

The honest summary for a user asking about RAG on Teradata: there are three paths — the managed
Enterprise Vector Store, plain in-database vector SQL, and this config-driven retrieval tool over a
corpus someone else embedded. The first two are usually what they want.
