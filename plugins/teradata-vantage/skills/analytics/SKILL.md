---
name: analytics
description: Use when the user wants to run analytics, statistics, feature engineering or machine-learning scoring inside Teradata Vantage rather than exporting rows to pandas or a notebook - the in-database ClearScape TD_* functions for transformation, profiling, model training and prediction, and the BYOM path for scoring an ONNX, PMML, H2O or Dataiku model on data that never leaves the database.
when_to_use: run this analysis in the database; ClearScape Analytics; TD_ functions; in-database machine learning; score a model in Teradata; BYOM; ONNXPredict; PMMLPredict; H2OPredict; train a model on Teradata; one-hot encode; scale features; impute missing values; TD_KMeans; TD_DecisionForest; TD_GLM; feature engineering in SQL; should I pull this into pandas; TD_ColumnTransformer; TD_ScaleFit.
license: MIT
metadata:
  skill_type: documentation
  category: teradata
  version: "1.0.0"
argument-hint: "[what functions exist | transform <db>.<table> | train <algorithm> | score <model> | byom]"
allowed-tools:
  - mcp__plugin_teradata-vantage_teradata__base_readQuery
  - mcp__plugin_teradata-vantage_teradata__base_tableDDL
  - mcp__plugin_teradata-vantage_teradata__base_columnDescription
  - mcp__plugin_teradata-vantage_teradata__qlty_columnSummary
  - mcp__plugin_teradata-vantage_teradata__qlty_univariateStatistics
---

# In-database analytics on Teradata Vantage

The reason to do analytics here rather than in a notebook: the data does not move. A model scored over
a billion rows in the database transfers a result set; the same model scored in pandas transfers a
billion rows first. That is the whole argument, and it is the one to make to the user.

## Rule 0 — check what this release actually installs

Function availability varies by release and by what was licensed. **Never write a `TD_*` call from
memory.** Ask the database:

```sql
SELECT DatabaseName, FunctionName
FROM   DBC.FunctionsV
WHERE  FunctionName = 'TD_KMEANS';                     -- one function

SELECT COUNT(*) FROM DBC.FunctionsV WHERE DatabaseName = 'TD_SYSFNLIB';   -- how rich is this install
```

Measured on Vantage 20.00: 1,160 functions in `TD_SYSFNLIB`, of which 594 are `TD_*`. Names ending
`_CONTRACT`, `MAP` or `REDUCE` are internal machinery — do not call them directly.

If a function is absent, say so and offer the SQL or `qlty_*` equivalent. Do not substitute a similar
name and hope.

Two plausible names checked while writing this page do NOT exist on Vantage 20.00:
`TD_PolynomialFeatures` (the real pair is `TD_PolynomialFeaturesFit` / `TD_PolynomialFeaturesTransform`)
and `TD_DBSCAN` (no clustering function of that name is installed). Both fail with `Error 3807`.

## Rule 1 — presence in the dictionary does not mean the function works

This is the trap that costs a whole afternoon, and the dictionary check above will not catch it. A `TD_*`
table operator can be registered, resolvable and callable, and still **return zero rows while echoing your
input columns back at you** — no error, no warning, nothing in the log.

Measured on one Vantage 20.00 system, calling eight functions on the same 300-row table:

| Outcome | Functions |
|---|---|
| Correct result | `TD_ColumnSummary`, `TD_OutlierFilterFit`, `TD_BinCodeFit`, `TD_GetRowsWithoutMissingValues` |
| **Zero rows, input columns echoed, no error** | `TD_ScaleFit`, `TD_SimpleImputeFit`, `TD_KMeans` |
| Explicit, useful error | `TD_CategoricalSummary` (`TargetColumns must be of CHAR/VARCHAR type`) |

The three silent ones behaved identically at 5 rows and at 300, with and without nulls, qualified as
`TD_SYSFNLIB.TD_ScaleFit` and unqualified, and with every extra argument tried. It is not a data-volume
floor and not a syntax mistake. The most likely explanation is a partially installed or unlicensed
analytics feature, where the function shell is registered but the implementation behind it is not — but
treat that as a hypothesis, and treat the behaviour as the fact.

**So smoke-test before you build on a function.** Run it on a handful of rows and check the OUTPUT COLUMN
NAMES, not the row count:

```sql
SELECT * FROM TD_ScaleFit (
  ON (SELECT <id_col>, <num_col> FROM <db>.<table>) AS InputTable
  USING TargetColumns('<num_col>') ScaleMethod('STD')
) AS d;
```

A real fit table comes back with the function's own columns — `TD_BinCodeFit` returns `TD_Bins_BINFIT`,
`TD_ColumnName_BINFIT` and friends; `TD_OutlierFilterFit` returns `TD_..._OFTFIT` columns. **If the columns
that come back are the columns you put in, the function did nothing.** Say so and stop; do not wrap it in
`CREATE TABLE … AS`, because that persists an empty, wrong-shaped fit table and every downstream step then
fails for a reason that points nowhere near the cause.

Report a silent no-op to the user plainly: the function is registered, it is not working on this system,
here is the smoke test that shows it, and here is the SQL or `qlty_*` route that does work.

## What is there, by job

| Job | Representative functions | Count on Vantage 20.00 |
|---|---|---|
| Transform and prepare | `TD_ScaleFit` / `TD_ScaleTransform`, `TD_OneHotEncodingFit`, `TD_SimpleImputeFit`, `TD_ColumnTransformer`, `TD_BinCodeFit`, `TD_OutlierFilterFit`, `TD_PolynomialFeaturesFit` | 57 |
| Train | `TD_KMeans`, `TD_DecisionForest`, `TD_GLM`, `TD_NaiveBayes`, `TD_XGBoost`, `TD_KNN` | 38 |
| Predict | `TD_KMeansPredict`, `TD_DecisionForestPredict`, `TD_GLMPredict`, `TD_NaiveBayesPredict`, `TD_HNSWPredict` | 15 |
| Statistics and tests | `TD_UnivariateStatistics`, `TD_ColumnSummary`, `TD_CategoricalSummary`, `TD_ANOVA`, `TD_ChiSq`, `TD_ZTest`, `TD_QQNorm` | 19 |
| Text | `TD_TextParser`, `TD_NGramSplitter`, `TD_SentimentExtractor`, `TD_TextMorph` | 16 |
| Call an external model service | `TD_API_AzureML`, `TD_API_SageMaker`, `TD_API_VertexAI` | 3 |

Counts are what the dictionary reported on one Vantage 20.00 system; treat them as an order of magnitude,
not a guarantee, and re-run the query above on the user's system.

## The fit / transform split

Most preparation functions come in pairs. `…Fit` **learns** the parameters and returns them as a table;
`…Transform` **applies** a fit table to data. Persist the fit table — it is the artifact that makes
training and scoring reproducible, and re-fitting on scoring data silently leaks the target.

This pair is measured end to end on Vantage 20.00 — 300 rows in, 300 rows out, the numeric column
replaced by bin labels:

```sql
CREATE TABLE <db>.bin_fit AS (
  SELECT * FROM TD_BinCodeFit (
    ON <db>.<train_table> AS InputTable
    USING TargetColumns('amt') MethodType('EQUAL-WIDTH') NBins(3)
  ) AS d
) WITH DATA;
-- 1 row, columns TD_ColumnName_BINFIT, TD_MinValue_BINFIT, TD_MaxValue_BINFIT, TD_Bins_BINFIT, ...

SELECT * FROM TD_BinCodeTransform (
  ON <db>.<score_table> AS InputTable
  ON <db>.bin_fit       AS FitTable DIMENSION
  USING Accumulate('id')
) AS d;
-- 300 rows: id, amt -> 'amt_1' | 'amt_2' | 'amt_3'
```

`TD_ScaleFit` / `TD_ScaleTransform` follow exactly the same shape and are the pair most documentation
reaches for first — but see Rule 1: `TD_ScaleFit` was one of the three silent no-ops on the system
measured here. Smoke-test it before you build on it.

The shape is always the same: one or more `ON` clauses naming inputs, a `DIMENSION` marker on the small
side, and a `USING` block of parameters. Column names inside `USING` are quoted strings, not identifiers.

`TD_ColumnTransformer` applies several fit tables in one pass — prefer it over a chain of transforms when
the pipeline is fixed, because each separate call is another pass over the data.

## Bring your own model (BYOM)

Train anywhere, score in the database. The scoring functions live in **`TD_MLDB`** — not `mldb`, which is
what older documentation says and which fails with `Database 'mldb' does not exist` (measured on
Vantage 20.00).

| Function | Scores |
|---|---|
| `TD_MLDB.ONNXPredict` | any model exported to ONNX |
| `TD_MLDB.PMMLPredict` | PMML |
| `TD_MLDB.H2OPredict` | H2O MOJO |
| `TD_MLDB.DataikuPredict` | Dataiku |
| `TD_MLDB.DataRobotPredict` | DataRobot |
| `TD_MLDB.ONNXSeq2Seq` | sequence-to-sequence ONNX models |
| `TD_MLDB.ONNXEmbeddings` | sentence embeddings — see the `vector-store` skill |

The model is stored as a row in a table (a `BLOB`), so scoring is a join, and the model is version-
controlled by the table. `references/byom-scoring.md` carries the load and score forms and the failures
worth knowing before the first attempt.

## When NOT to do it in the database

Be honest about this; the credibility of the recommendation depends on it.

- **The data is already small.** A few hundred thousand rows that fit in memory are usually faster and far
  easier to iterate on in pandas or R.
- **The algorithm is not there.** Check `DBC.FunctionsV` first. Deep learning training, most gradient-
  boosting variants and anything needing a custom loss are not in the catalogue — train outside and bring
  the model back with BYOM.
- **You are still exploring.** In-database analytics rewards a settled pipeline. Rapid iteration on a
  sample belongs in a notebook; move it in-database when it stops changing.
- **The result is the whole table.** If every row comes back anyway, you have moved the data regardless.

The strong case is: large data, a settled pipeline, a small result, and a scoring step that must run where
the data is governed.

## Reporting

- Show the SQL you ran. These functions have long `USING` blocks and the reader must be able to re-run it.
- Name the fit table when one was created; it is an artifact with a lifecycle, not scratch.
- Give the row counts in and out. A transform that silently dropped rows is the common failure, usually a
  null in a `TargetColumns` column.
- `CREATE TABLE … AS` around a function result is a `[WRITE]`. Hand it to the user; the read guard denies
  it through `base_readQuery` and the bundled server has no write tool.

## References

- `references/clearscape-functions.md` — the function families, the `ON`/`DIMENSION`/`USING` call shape,
  the fit/transform contract, and how to discover what a release installs.
- `references/byom-scoring.md` — loading a model into a table and scoring with `TD_MLDB.*`.
