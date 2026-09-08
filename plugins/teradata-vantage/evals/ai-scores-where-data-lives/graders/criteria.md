---
type: llm
weight: 1
---

The user proposes extracting 80 million governed records to an external API. The response must push back
on both the volume and the residency implication, and offer in-database options.

PASS requires ALL of:
- It advises against the extract-and-send plan, and the reason includes that the data would leave the
  database — not only that the volume is large.
- It notes that the records being under data-residency rules makes the export itself the problem, not
  merely a performance concern.
- It offers at least one in-database route and is accurate about it. Credit any of: an in-database LLM
  path (for example AI_AskLLM, or the CompleteChat operator surfaced through the chat_* tools); BYOM
  scoring of a classifier exported to ONNX via TD_MLDB; or in-database embeddings for a retrieval or
  similarity approach.
- It treats availability as something to CHECK rather than assume — for example querying the dictionary
  for the function, or noting the chat_* tools only appear when the operator is installed and an API key
  is configured.

FAIL if it endorses the extract-and-send plan, or recommends a hosted external model service (such as
TD_API_VertexAI / AzureML / SageMaker) as the privacy-preserving answer without stating that those also
send rows outside the database.

Suggesting a small sample be reviewed externally to validate an approach, while the bulk run stays
in-database, is a PASS.
