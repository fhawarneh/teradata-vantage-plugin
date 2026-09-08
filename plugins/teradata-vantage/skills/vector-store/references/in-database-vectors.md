# In-database vectors without the vector-store service — VECTOR32, TD_VECTORDISTANCE, TD_VECTORNORMALIZE, ONNX embeddings (BYOM and IVSM), wide-FLOAT layout

These primitives live in the database itself and need no vector-store service. Generic SQL only — substitute
your own `<db>`, `<table>`, model and tokenizer tables. Observed on Vantage 20.00; function availability and
argument names vary by release (from Teradata documentation and live probes; verify against your release).
The plugin's read guard passes `SELECT … FROM TD_VECTORDISTANCE(...)` through `base_readQuery`; the
CREATE/INSERT statements are writes — run them through `base_writeQuery` only where the connected server
provides that tool (the bundled upstream 0.2.6 server registers no write tool), otherwise in your own SQL
client. INSERT/DROP prompt for approval under the write gate.

## 1. What exists, and what only looks related

| Object | Purpose |
|---|---|
| `SYSUDTLIB.VECTOR`, `SYSUDTLIB.VECTOR32` | UDTs usable directly in `CREATE TABLE`; VECTOR32 = 32-bit floats, ≤ 32,768 bytes → ≤ 8,192 dims |
| `TD_SYSFNLIB.TD_VECTORDISTANCE` | top-K nearest neighbours; returns **distance** (lower = more similar; ~0.005 near vs ~0.63 far on cosine) |
| `TD_SYSFNLIB.TD_VECTORNORMALIZE` | unit-vector / other normalisation of embedding columns |
| `mldb.ONNXEmbeddings` (BYOM package) | server-side sentence embeddings from an ONNX model + tokenizer stored in tables |
| `ivsm.tokenizer_encode`, `ivsm.IVSM_score`, `ivsm.vector_to_columns` | the alternative in-database embedding pipeline (IVSM package) |
| `TD_KMEANS`, `TD_KMEANSPREDICT`, `TD_SILHOUETTE` | ClearScape clustering; accept VECTOR32 input |

NOT similarity functions: `TD_SYSAI.Cosine`, `TD_SYSAI.L2Dist`, `TD_SYSAI.JACCARD` are data-profiling
signature UDFs that take an `AllStats VARCHAR` argument. Using them for embeddings produces nonsense, not an error.

## 2. Column charset — the 6706 rule

Text you intend to embed MUST be `CHARACTER SET UNICODE`. A fresh Vantage image defaults to LATIN, and one
em-dash, curly quote or non-Latin letter in an INSERT fails the whole batch with:

```
Teradata 6706: The string contains an untranslatable character
```

What it actually means: the value cannot be represented in the column's LATIN character set — not a driver
or encoding bug in your client. Fix the DDL (`VARCHAR(n) CHARACTER SET UNICODE`), or ASCII-fold before
loading if the schema cannot change. Comments in SQL may use typography; data may not.

## 3. Two storage layouts

### 3a. Wide-FLOAT columns (portable; works with every ClearScape function)

```sql
CREATE MULTISET TABLE <db>.doc_embeddings (
  chunk_id     VARCHAR(150) CHARACTER SET UNICODE NOT NULL,
  doc_name     VARCHAR(128) CHARACTER SET UNICODE,
  chunk_number INTEGER,
  chunk_text   VARCHAR(20000) CHARACTER SET UNICODE,
  created_ts   TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP(0),
  emb_0 FLOAT, emb_1 FLOAT, /* … */ emb_383 FLOAT
) PRIMARY INDEX (chunk_id);
```

Generate the `emb_0 … emb_N-1` list mechanically; the column-range syntax `'[emb_0:emb_383]'` addresses
them in every function call. Keep `TIMESTAMP(0)` if you insert Python datetimes — `TIMESTAMP(0)` rejects
microseconds with **5404** "Datetime field overflow" (truncate at the insert path).

### 3b. A native `VECTOR32` column (compact; check per-function support)

```sql
CREATE MULTISET TABLE <db>.doc_vectors (
  chunk_id  VARCHAR(150) CHARACTER SET UNICODE NOT NULL,
  chunk_text VARCHAR(20000) CHARACTER SET UNICODE,
  emb       SYSUDTLIB.VECTOR32
) PRIMARY INDEX (chunk_id);

-- constructors take a COMMA-separated list (not spaces); a string CAST also works
SELECT CAST(NEW SYSUDTLIB.Vector32('1.0,2.5,-3.25') AS VARCHAR(100));   -- -> 1,2.5,-3.25
INSERT INTO <db>.doc_vectors VALUES ('c1', 'text', CAST('0.5,0.5,0.5,0.5' AS SYSUDTLIB.Vector32));
```

Constructor variants seen: `Vector32_Constructor(VARCHAR(64000))`, `…2(VARBYTE(64000))`, `…3(CLOB)`,
`…4(SYSUDTLIB.VECTOR)`; a bound parameter longer than VARCHAR limits is promoted to CLOB by the driver.
Round-trip VECTOR32 → CLOB → `NEW Vector32(...)` is lossless (distance 0.0).
**Footgun:** `CAST(vec AS VARCHAR(64000) CHARACTER SET LATIN)` **silently truncates** above roughly 1,600
dims (a 4,096-d vector serialises to ~75,000 characters). Read wide vectors back as CLOB.

Function support over a VECTOR32 column (live probes; verify on your release): `TD_VECTORDISTANCE` ✅
(requires `EmbeddingSize(N)`), `TD_VECTORNORMALIZE` ✅ (output column keeps the input's name), `TD_KMEANS` ✅
(centroids come back as VECTOR32), `TD_KMEANSPREDICT` ✅ (`OutputDistance('true')` gives cluster id and
distance in one call); `TD_KNN`, `td_oneclasssvm`, `TD_OutlierFilterFit` ❌ **7810** "Unsupported data type"
— run those over derived scalar columns instead.

## 4. Similarity search with TD_VECTORDISTANCE

### 4a. Wide-FLOAT layout (query vector staged in a volatile table)

```sql
CREATE VOLATILE TABLE vq (vec_id INTEGER, emb_0 FLOAT, /* … */ emb_383 FLOAT)
  PRIMARY INDEX (vec_id) ON COMMIT PRESERVE ROWS;
INSERT INTO vq VALUES (1, ?, ?, /* … */ ?);          -- bind the 384 floats from your client

SELECT target_id, reference_id, distance, 1.0 - distance AS cosine_similarity
FROM TD_VECTORDISTANCE (
  ON vq                    AS TargetTable
  ON <db>.doc_embeddings   AS ReferenceTable DIMENSION
  USING TargetIDColumn('vec_id')   TargetFeatureColumns('[emb_0:emb_383]')
        RefIDColumn('chunk_id')    RefFeatureColumns('[emb_0:emb_383]')
        DistanceMeasure('cosine')  TopK(8)
) AS dt
ORDER BY distance;
```

- `DistanceMeasure`: `cosine`, `euclidean`, `manhattan` (verify the list on your release).
- The output is a **distance**; report similarity as `1.0 - distance` for cosine only.
- Join `reference_id` back to `chunk_text` in a wrapping SELECT to return readable hits.

### 4b. Native VECTOR32 column — `EmbeddingSize(N)` is mandatory

```sql
SELECT reference_id, distance
FROM TD_VECTORDISTANCE (
  ON (SELECT 1 AS vec_id, CAST('<comma-separated floats>' AS SYSUDTLIB.Vector32) AS emb) AS TargetTable
  ON <db>.doc_vectors AS ReferenceTable DIMENSION
  USING TargetIDColumn('vec_id') TargetFeatureColumns('emb')
        RefIDColumn('chunk_id')  RefFeatureColumns('emb')
        EmbeddingSize(384) DistanceMeasure('cosine') TopK(8)
) AS dt ORDER BY distance;
```

`EmbeddingSize` defaults to **1** = elements per feature column — correct for the wide-FLOAT layout, fatal
for a VECTOR32 column. Without it → **9134**; `UseSIMD('true')` → **9134** on the build measured (never set
it); mixed dimensionality within one reference set → **9134**. 9134 here means "unsupported/invalid function
option combination" — it is unrelated to the OTF and NOS meanings of the same code.

Scaling note: a brute-force 768-d TD_VECTORDISTANCE took ~5.8 s against only a few hundred reference rows on
a small system; for large corpora use the vector-store service's HNSW index rather than TD_VECTORDISTANCE.

### 4c. Normalise once, then use dot-product-style comparisons

```sql
SELECT * FROM TD_VECTORNORMALIZE (
  ON <db>.doc_vectors AS InputTable
  USING TargetColumns('emb') EmbeddingSize(384) Approach('UNITVECTOR') Accumulate('chunk_id')
) AS dt;    -- output column is still named emb
```

## 5. Embeddings inside the database — `mldb.ONNXEmbeddings` (BYOM), the 3-ON-clause form

Prerequisites: the BYOM package installed; an ONNX sentence-embedding model and its tokenizer loaded into two
tables — `<model_db>.onnx_models (model_id, model)` and `<model_db>.onnx_tokenizers (model_id, tokenizer)`.

```sql
CREATE MULTISET VOLATILE TABLE emb_in (id INTEGER, txt VARCHAR(8000) CHARACTER SET UNICODE)
  ON COMMIT PRESERVE ROWS;
INSERT INTO emb_in VALUES (1, 'text to embed');       -- ASCII or UNICODE-safe text

CREATE MULTISET VOLATILE TABLE emb_out AS (
  SELECT * FROM mldb.ONNXEmbeddings (
    ON (SELECT id, txt FROM emb_in)
    ON (SELECT model_id, model     FROM <model_db>.onnx_models     WHERE model_id = '<model_id>') DIMENSION
    ON (SELECT model_id, tokenizer FROM <model_db>.onnx_tokenizers WHERE model_id = '<model_id>') DIMENSION
    USING Accumulate('id', 'txt')
          ModelOutputTensor('sentence_embedding')
          OutputFormat('FLOAT32(384)')
  ) AS a
) WITH DATA ON COMMIT PRESERVE ROWS;

SELECT * FROM emb_out ORDER BY id;   -- columns: id, txt, out_0 … out_383
```

- Exactly **three ON clauses**: data, model (`DIMENSION`), tokenizer (`DIMENSION`). Two clauses is the
  classic mistake and fails with a function-argument error.
- `OutputFormat('FLOAT32(N)')` must match the model's width; output columns are `out_0 … out_N-1` — rename to
  `emb_0 … emb_N-1` when inserting into the wide-FLOAT table so one column-range covers both.
- Token accuracy is the model's own tokenizer's — the same text embeds identically inside and outside the DB.

## 6. The IVSM pipeline (tokenizer_encode → IVSM_score → vector_to_columns)

Alternative when the IVSM package is installed instead of BYOM's ONNXEmbeddings. Model table
`<model_db>.embedding_models (model_id, model)`, tokenizer table `<model_db>.embedding_tokenizers (model_id, model)`:

```sql
CREATE TABLE <db>.txt_tokenized AS (
  SELECT id, txt, IDS AS input_ids, attention_mask
  FROM ivsm.tokenizer_encode (
    ON (SELECT id, txt FROM <db>.txt_source)
    ON (SELECT model AS tokenizer FROM <model_db>.embedding_tokenizers WHERE model_id = '<model_id>') DIMENSION
    USING ColumnsToPreserve('id', 'txt') OutputFields('IDS', 'ATTENTION_MASK')
          MaxLength(512) PadToMaxLength('False') TokenDataType('INT64')
  ) AS dt
) WITH DATA;

CREATE TABLE <db>.txt_embeddings AS (
  SELECT * FROM ivsm.IVSM_score (
    ON <db>.txt_tokenized
    ON (SELECT * FROM <model_db>.embedding_models WHERE model_id = '<model_id>') DIMENSION
    USING ColumnsToPreserve('id', 'txt') ModelType('ONNX')
          BinaryInputFields('input_ids', 'attention_mask') BinaryOutputFields('sentence_embedding')
          Caching('inquery')
  ) AS a
) WITH DATA;

CREATE TABLE <db>.txt_embeddings_store AS (
  SELECT * FROM ivsm.vector_to_columns (
    ON <db>.txt_embeddings
    USING ColumnsToPreserve('id', 'txt') VectorDataType('FLOAT32') VectorLength(384)
          OutputColumnPrefix('emb_') InputColumnName('sentence_embedding')
  ) AS a
) WITH DATA;
```

The third step yields the wide-FLOAT layout (`emb_0 … emb_383`) that section 4a searches, and that
`TD_KMEANS (… TargetColumns('[2:385]') …)` clusters. Re-runs: `DROP TABLE` first (3803 otherwise; prompts).

## 7. Putting it together — a minimal RAG loop in SQL

1. Chunk documents in your client (~700 chars, ~100 overlap, sentence boundaries) → rows in a UNICODE table.
2. Embed the chunk rows in-database (section 5 or 6) → wide-FLOAT table (3a).
3. Embed the question the same way (one-row volatile input) → volatile `vq`.
4. `TD_VECTORDISTANCE … TopK(k)` (4a) → join to `chunk_text` → answer from those rows.

Verify with counts at every step (`COUNT(*)` of chunks = rows in the embedding table); a mismatch usually
means a 6706 batch failure was swallowed by the loader.
