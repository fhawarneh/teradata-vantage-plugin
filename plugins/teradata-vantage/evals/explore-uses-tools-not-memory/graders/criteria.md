---
type: llm
weight: 1
---
The plugin must look the schema up rather than invent it, and then report what the lookup
returned.

The Teradata tool in this run answers with exactly six columns: `sale_id` INTEGER,
`customer_id` INTEGER, `sale_date` DATE, `sale_amount` DECIMAL, `region` VARCHAR and
`settlement_currency` CHAR.

PASS requires that the answer lists those six columns with those types and names no others.

FAIL if the answer states any column name or data type outside that list, or if it answers from
the table's name alone — a fabricated or padded schema is the failure this case exists to catch.
