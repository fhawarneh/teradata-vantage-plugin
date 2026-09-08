# BYOM — scoring an external model inside Teradata

Train wherever you like; score where the data already is. The model travels as a few kilobytes of
serialised bytes, the data does not travel at all.

## Where the functions live

**`TD_MLDB`.** Not `mldb` — that is what older documentation says, and it fails with
`Database 'mldb' does not exist` (measured on Vantage 20.00).

Confirm what this system installs before promising anything:

```sql
SELECT TableName, TableKind FROM DBC.TablesV WHERE DatabaseName = 'TD_MLDB' ORDER BY 1;
```

Measured on Vantage 20.00 — 26 objects, of which these are the ones you call:

| Function | Scores | Kind |
|---|---|---|
| `TD_MLDB.ONNXPredict` | any model exported to ONNX | table operator |
| `TD_MLDB.PMMLPredict` | PMML | table operator |
| `TD_MLDB.H2OPredict` | H2O MOJO | table operator |
| `TD_MLDB.DataikuPredict` | Dataiku | table operator |
| `TD_MLDB.DataRobotPredict` | DataRobot | table operator |
| `TD_MLDB.ONNXEmbeddings` | text → embedding vectors | table operator |
| `TD_MLDB.ONNXSeq2Seq` | sequence-to-sequence ONNX models | table operator |

Each also ships a `…Scalar` and `…ScalarUif` variant (`ONNXPredictScalar`, `PMMLPredictScalar`,
`ONNXEmbeddingsScalar`, …) for calling inline in a `SELECT` list on a single value rather than over a
table. The `…_contract` objects are internal machinery — never call them.

ONNX is the format to recommend when the user has a choice. scikit-learn, PyTorch, TensorFlow, XGBoost and
LightGBM all export to it, so one scoring path covers the whole team.

## It runs in a JVM, and that has consequences

`TD_MLDB` contains `MLDB_JAR` and `BYOM_UTIL_JAR` (`TableKind = 'D'`), and a `JVMMemStats` function. The
scoring functions execute Java inside the database, which means:

- **The first call after a restart pays a JVM cold start** and can take far longer than the scoring work
  justifies. Do not benchmark the first call, and warn the user before they conclude BYOM is slow.
- **Memory is a real limit.** Large models and wide batches hit the JVM heap, not the database's spool.
  `SELECT * FROM TD_MLDB.JVMMemStats(…)` is how you look at it when scoring fails or stalls.
- A failure inside the JVM can surface as a Java exception in the error text rather than a Teradata error
  code. Read past the `Error 7810` prefix to the message body.
- **A malformed model can hang rather than fail.** Measured: `TD_MLDB.ONNXPredict` over a 300-row table
  with a deliberately invalid one-byte BLOB as the model did not return in ten minutes and had to be
  cancelled — no error, no partial result. The database stayed responsive throughout, so this is the
  scoring call alone. Do not assume a bad model produces a clean rejection.

Because of that last point, **always run the first scoring call against a small sample with a client-side
timeout set**, and cancel rather than wait. A BYOM call that has produced nothing after a couple of minutes
on a small input is not warming up; something is wrong with the model bytes or the input signature.

## The model lives in an ordinary table

There is no model registry. You create a table, and the model is a row in it — usually
`(model_id VARCHAR, model BLOB)`. That is a feature: the model is versioned, backed up, permissioned and
lineage-tracked by exactly the same machinery as every other table.

```sql
CREATE TABLE <db>.models (
  model_id  VARCHAR(64) NOT NULL,
  model     BLOB,
  loaded_at TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP(0)
) PRIMARY INDEX (model_id);
```

Loading the bytes is a client-side job — a parameterised `INSERT` binding the file as a BLOB, from Python
(`teradatasql`), BTEQ, or the Teradata `save_byom` helper. **The bundled MCP server cannot do it**: the
read guard denies writes through `base_readQuery`, and no tool streams a local file into a BLOB. Hand the
user the `INSERT`; do not pretend to run it.

Keep one row per model version and never overwrite in place. `model_id` is the only thing tying a score
back to the artifact that produced it.

## The scoring call

```sql
SELECT * FROM TD_MLDB.ONNXPredict (
  ON <db>.<score_table>                                    AS InputTable
  ON (SELECT * FROM <db>.models WHERE model_id = 'churn_v3')
                                                           AS ModelTable DIMENSION
  USING Accumulate('customer_id')
) AS d;
```

The shape is the table-operator shape from `clearscape-functions.md`, with three things specific to BYOM:

- **Filter the model table to ONE row.** Passing a table with several models does not pick the newest; the
  behaviour is not something to rely on. Filter on `model_id` inside the `ON` clause, every time.
- **`DIMENSION` on the model side is not optional in practice.** The model must be replicated to every AMP.
  Without it the plan redistributes data to meet the model, which is the opposite of the point.
- **`Accumulate` names the columns carried through to the output.** The prediction columns are appended.
  Anything not accumulated is dropped — so accumulate the key, or you cannot join the score back to
  anything.

**The input columns must match the model's expected features, in name and in type.** This is the most
common failure and it is not always a clean error: a mismatch can produce nulls or nonsense rather than a
refusal. Check the model's input signature on the training side (`onnx.load(...).graph.input` for ONNX) and
build a view that presents exactly those columns, in that order, with those types. Ship the view alongside
the model — it is part of the artifact.

## Verify before you trust it

Score a sample whose answer you already know, and compare against the training environment:

```sql
SELECT * FROM TD_MLDB.ONNXPredict (
  ON (SELECT * FROM <db>.<score_table> SAMPLE 20)          AS InputTable
  ON (SELECT * FROM <db>.models WHERE model_id = 'churn_v3') AS ModelTable DIMENSION
  USING Accumulate('customer_id')
) AS d;
```

Predictions that are all identical, all null, or all the majority class mean the features did not reach the
model — a name mismatch, a type coercion, or a column silently dropped for not being accumulated. Treat a
degenerate score distribution as a failure, not a result. Compare against the same 20 rows scored in the
training environment before anything downstream consumes the output.

## Embeddings

`TD_MLDB.ONNXEmbeddings` turns text into vectors in-database with a sentence-transformer exported to ONNX,
which keeps document text inside the governed boundary instead of sending it to a hosted embedding API.
That is the argument to make for regulated data.

The vector-store skill covers what to do with the output —
`vector-store/references/in-database-vectors.md` for storage and distance computation, and the `tdvs_*`
tools for a managed store. Do not duplicate that here; embeddings are where these two skills meet.

## Reporting

- Name the `model_id` and the table it came from in every answer. A score without its model version cannot
  be reproduced or audited.
- Say whether the run was a verification sample or the full set.
- `CREATE TABLE … AS` around a scoring call is a `[WRITE]`. Hand it over; the read guard denies it.
- If the JVM cold start dominated the timing, say so rather than reporting it as the scoring cost.
