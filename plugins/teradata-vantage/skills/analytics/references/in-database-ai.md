# In-database AI — LLM inference where the data already is

Same argument as BYOM, one step further. Classifying or summarising a hundred million rows by calling an
API from a client means shipping a hundred million rows out and the answers back. Doing it in the
database ships a result set. The difference is the whole point, and for regulated text it is often the
only acceptable shape.

Four routes exist, and **which of them you have is a property of the system, not of Teradata**. Check
before promising any of them.

## Route 1 — `AI_AskLLM`

A table operator in `TD_SYSFNLIB`, present on the Vantage 20.00 measured here:

```sql
SELECT DatabaseName, FunctionName, FunctionType
FROM   DBC.FunctionsV
WHERE  UPPER(FunctionName) = 'AI_ASKLLM';        -- TD_SYSFNLIB.AI_ASKLLM, type L (table operator)
```

What was established by running it:

- It takes **exactly two input tables**. One `ON` clause fails with
  `Error in function AI_ASKLLM: An invalid number of input tables has been provided; only two input
  tables are accepted.`
- The two inputs need **specific aliases**, and the usual `InputTable` / `ModelTable` pair is not it —
  with two tables supplied the error becomes
  `Error in function AI_ASKLLM: Invalid input alias(es) encountered. Please refer to the user guide.`

**The alias names are not discoverable from the database.** `DBC.FunctionParametersV` does not exist and
`HELP FUNCTION` on a table operator returns zero rows with a full column header (see
`clearscape-functions.md`). They must come from the documentation for your release — this is the case
the `docs` skill exists for. Do not guess them: a wrong alias produces the same error as a wrong number
of tables, so guessing looks like progress without being progress.

What you *can* do without the manual is prove the function is there and report exactly what it demands.
That is a better answer than a plausible-looking call that fails.

## Route 2 — call a hosted model service from SQL

```sql
SELECT DatabaseName, FunctionName FROM DBC.FunctionsV
WHERE  FunctionName IN ('TD_API_VertexAI','TD_API_AzureML','TD_API_SageMaker');
```

All three are present on the measured system. They send rows to the provider and return the response as
a column, so the data does leave the database — say that plainly, because it is the opposite of the
argument for Route 1 and it matters for anything governed.

Measured: `TD_API_VertexAI` called without arguments returns
`Error in function API_VertexAI: AccessToken argument is required.` — the credential is a `USING`
argument, which means **it appears in the statement text and therefore in DBQL**. Prefer a route that
does not put a token in the query log; if you must use these, say so to the user.

## Route 3 — the `chat_*` MCP tools

`chat_completeChat` and `chat_aggregatedCompleteChat` wrap Teradata's **CompleteChat** table operator,
which calls an OpenAI-compatible inference server. `chat_aggregatedCompleteChat` additionally groups the
responses by unique text and drops empty ones, which is what you want for classification over many rows
— you get the distribution rather than a million individual strings.

⚠️ **They register only when both conditions hold:** the CompleteChat operator is installed in the
database, **and** `CHAT_API_KEY` is set for the server. Otherwise the tools are simply absent from the
tool list — not an error you will see, just two tools that are not there.

On the system measured, **CompleteChat is NOT installed**:

```sql
SELECT COUNT(*) FROM DBC.FunctionsV WHERE UPPER(FunctionName) LIKE '%COMPLETECHAT%';   -- 0
```

So the honest sequence is: check the function exists, check the tools are in your tool list, and only
then offer this route.

## Route 4 — embeddings, which are the common case

Most "use AI on this data" requests are really retrieval, not generation. `TD_MLDB.ONNXEmbeddings`
turns text into vectors in-database with no external call at all, and the `vector-store` skill covers
what to do with them. Check whether the user needs an answer generated or a document found — the second
is cheaper, fully in-database, and usually what was meant.

## Choosing, and saying why

| Need | Route | Data leaves the database? |
|---|---|---|
| Find similar documents | `ONNXEmbeddings` + vector store | no |
| Score a trained model | BYOM (`byom-scoring.md`) | no |
| Classify or summarise text in bulk | `AI_AskLLM`, or CompleteChat via `chat_*` | no |
| Reach a specific hosted model | `TD_API_*` | **yes** |

State which route you used and whether data left the database. For a regulated dataset that sentence is
the answer, not a footnote.

## Reporting

- Name the route and confirm its availability the way Rule 0 does — `DBC.FunctionsV` for the functions,
  the tool list for the `chat_*` tools.
- **Never present a hosted-API call as in-database.** They are different privacy stories.
- LLM output is non-deterministic. If a result feeds anything downstream, persist it with the model and
  prompt that produced it, or it cannot be reproduced or audited.
- Give a cost and volume estimate before running generation over a large table. A per-row LLM call across
  a hundred million rows is not a query; it is a project.
