---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
append_system_prompt: |
  This case grades your recommendation and the SQL shape you describe, not a result set. Do not
  call any Teradata tool and do not look the schema up.
---
I trained a churn model in scikit-learn. The scoring table in Teradata has about 400 million rows.
My plan is to pull it into a pandas DataFrame with `teradatasql`, run `model.predict`, and write the
scores back. Is that the right approach?
