# ClearScape analytic functions — discovery, call shape, and what fails silently

The `TD_*` functions are **table operators**, not scalar functions. They take whole tables as input and
return a table, so they live in the `FROM` clause with their own bracketed argument block. Nothing about
their syntax resembles an ordinary function call, and the dictionary will not teach it to you.

## Discovery — what the dictionary can and cannot tell you

```sql
-- Does it exist, and what kind of thing is it?
SELECT DatabaseName, FunctionName, FunctionType
FROM   DBC.FunctionsV
WHERE  UPPER(FunctionName) = 'TD_KMEANS';

-- How rich is this install?
SELECT COUNT(*) FROM DBC.FunctionsV WHERE DatabaseName = 'TD_SYSFNLIB';
```

`FunctionType` is worth reading:

| Value | Meaning |
|---|---|
| `L` | table operator — the `ON … USING …` form below |
| `F` | ordinary scalar function — call it inline in a `SELECT` list |
| `C` | `…_contract` companion — internal machinery, never call it |

Measured on Vantage 20.00: 1,160 functions in `TD_SYSFNLIB`, 594 of them `TD_*`.

**Two discovery routes that do not work, so you do not waste time on them:**

- `DBC.FunctionParametersV` **does not exist** — `Error 3807`. There is no parameter catalog.
- `HELP FUNCTION TD_SYSFNLIB.TD_KMeans` **returns zero rows with a full column header.** It succeeds and
  tells you nothing. This is the single most misleading result in the area: it looks like an answer.

So parameters come from the release documentation, or from the error message.

## The error message is the real parameter reference

`Error 7810` is specific and names the argument. It is faster than any doc lookup:

```
Error in function TD_ScaleFit: Either TargetColumns or AttributeNameColumn/AttributeValueColumn
                               must be specified.
Error in function TD_ScaleFit: NotAParam argument is not supported by the function.
Error in function TD_OneHotEncodingFit: Argument ISINPUTDENSE is required.
Error in function TD_CategoricalSummary: TargetColumns must be of CHAR/VARCHAR type.
```

Call the function with the arguments you are sure of, read what it asks for, add it, repeat. Two or three
round trips settle a signature. Do this on a tiny input, never on the real table.

## The call shape

```sql
SELECT * FROM TD_<Name> (
  ON <input>            AS InputTable
  ON <small_input>      AS FitTable DIMENSION
  USING ArgName('value') OtherArg(3)
) AS d;
```

Rules that are not obvious:

- Every input is an `ON` clause with a **role name** (`InputTable`, `FitTable`, `ModelTable`). The role is
  part of the contract; it is not a table alias.
- `DIMENSION` marks the small side so it is replicated rather than redistributed. Omit it on a fit table and
  the plan degrades badly.
- Values in `USING` are **quoted strings even when they name a column** — `TargetColumns('amt')`, not
  `TargetColumns(amt)`. Numbers are bare: `NBins(3)`.
- The whole call needs a trailing alias (`AS d`). Without it you get a syntax error that does not mention
  the alias.
- `OUT TABLE OutputTable(<db>.<t>)` is **not supported** for these operators:
  `Error 3706: OUT TABLE clause is not supported for table operator yet`. Capture output with
  `CREATE TABLE … AS ( … ) WITH DATA` instead.
- A derived table in an `ON` clause must reference a real table. A standalone
  `(SELECT 1 UNION ALL SELECT 2)` fails with `Error 3888: A SELECT for a UNION, INTERSECT or MINUS must
  reference a table`.

## The silent no-op — check output columns, not row counts

A registered function can return **zero rows and echo your input columns back**, with no error at any level.
Measured on one Vantage 20.00 system, same 300-row table, same session:

| Behaviour | Functions |
|---|---|
| Correct result | `TD_ColumnSummary`, `TD_OutlierFilterFit`, `TD_BinCodeFit`, `TD_GetRowsWithoutMissingValues` |
| Zero rows, input columns echoed, no error | `TD_ScaleFit`, `TD_SimpleImputeFit`, `TD_KMeans` |
| Explicit error | `TD_CategoricalSummary` |

Ruled out as causes: row count (identical at 5 and 300 rows), nulls, database qualification
(`TD_SYSFNLIB.` prefixed and bare), and missing arguments (every documented argument tried; unsupported
ones are rejected by name, so the parser is reading them). The likely explanation is a partially installed
or unlicensed analytics feature — the shell is registered, the implementation behind it is not — but that
is a hypothesis. The behaviour is the fact.

**The test is the output column names.** A genuine fit table carries the function's own columns:

```
TD_BinCodeFit      -> TD_ColumnName_BINFIT, TD_MinValue_BINFIT, TD_MaxValue_BINFIT, TD_Bins_BINFIT, ...
TD_OutlierFilterFit-> TD_LOWERPERCENTILE_OFTFIT, TD_MAXTHRESHOLD_OFTFIT, TD_IQRMULTIPLIER_OFTFIT, ...
```

If what comes back is `id, amt, seg` — the columns you passed in — the function did nothing. Never wrap
that in `CREATE TABLE … AS`: it persists an empty, wrong-shaped fit table, and the failure then surfaces
three steps downstream pointing nowhere near the cause.

## The fit / transform contract

16 pairs exist on Vantage 20.00. `…Fit` learns parameters and returns them as a table; `…Transform`
applies that table to data.

```
TD_BinCodeFit/Transform            TD_OneHotEncodingFit/Transform     TD_ScaleFit/Transform
TD_SimpleImputeFit/Transform       TD_OutlierFilterFit/Transform      TD_OrdinalEncodingFit/Transform
TD_TargetEncodingFit/Transform     TD_PolynomialFeaturesFit/Transform TD_RandomProjectionFit/Transform
TD_RowNormalizeFit/Transform       TD_NonLinearCombineFit/Transform   TD_FunctionFit/Transform
TD_SparseScaleFit/Transform        TD_CurrTransform                   TD_DenseScaleTransform
```

Two rules that matter more than the syntax:

1. **Persist the fit table.** It is the artifact that makes training and scoring reproducible, and it
   belongs in source control alongside the model.
2. **Never re-fit on scoring data.** Fitting a scaler or an encoder on the scoring set leaks the
   distribution and inflates every metric. Fit on train, transform both.

A verified end-to-end pair, measured — 300 rows in, 300 out:

```sql
CREATE TABLE <db>.bin_fit AS (
  SELECT * FROM TD_BinCodeFit (
    ON <db>.<train_table> AS InputTable
    USING TargetColumns('amt') MethodType('EQUAL-WIDTH') NBins(3)
  ) AS d
) WITH DATA;

SELECT * FROM TD_BinCodeTransform (
  ON <db>.<score_table> AS InputTable
  ON <db>.bin_fit       AS FitTable DIMENSION
  USING Accumulate('id')
) AS d;
-- id | amt  ->  61 | 'amt_2'
```

`Accumulate` names the columns to carry through untouched. Anything not in `Accumulate` and not a target
column is dropped from the output — the usual cause of "the transform lost my key".

`TD_ColumnTransformer` applies several fit tables in one pass. Prefer it over a chain once the pipeline is
fixed: each separate call is another full pass over the data.

## When a function is missing or broken

Say so, then offer the route that works on this system:

| Wanted | Fallback that needs no analytics feature |
|---|---|
| Column profile | `TD_ColumnSummary`, or the `qlty_*` MCP tools |
| Scaling | `(x - AVG(x) OVER ()) / STDDEV_SAMP(x) OVER ()` |
| Binning | `NTILE(n) OVER (ORDER BY x)`, or a `CASE` on measured boundaries |
| One-hot | `CASE WHEN seg = 'A' THEN 1 ELSE 0 END` per category |
| Imputation | `COALESCE(x, (SELECT AVG(x) FROM …))` |
| Clustering / modelling | train outside, score in-database with BYOM — see `byom-scoring.md` |

Plain SQL is not a consolation prize here. It runs in the same engine on the same data with the same
parallelism, and it is what the fit/transform pair compiles down to anyway.
