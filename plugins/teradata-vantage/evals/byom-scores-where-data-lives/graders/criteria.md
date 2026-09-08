---
type: llm
weight: 1
---

The user proposes extracting 400 million rows to score them client-side. The response must
recommend scoring in the database instead and describe that path accurately.

PASS requires ALL of:
- It advises against extracting 400 million rows, with the reason being the data movement rather
  than a vague appeal to best practice.
- It recommends scoring inside Teradata by exporting the scikit-learn model to ONNX and using the
  in-database BYOM scoring path.
- Any database qualifier it gives for the BYOM scoring functions is `TD_MLDB`. If it names a
  database at all, naming `mldb` is a FAIL — that database does not exist and the call fails.
- It conveys that the model is stored as bytes in an ordinary user table and referenced by the
  scoring call, rather than implying a separate model registry service.

Also credit, but do not require, any of: filtering the model table to a single version; noting the
input columns must match the model's expected features in name and type; verifying on a small
sample before the full run.

FAIL if it endorses the extract-and-score plan as the right approach, or if it names `mldb` as the
BYOM database.

Recommending in-database scoring while also noting when extraction is legitimate — a small table,
an algorithm with no in-database equivalent, exploratory work — is a PASS, not a hedge.
