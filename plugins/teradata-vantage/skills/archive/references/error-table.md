# NOS and Iceberg error codes — what the message actually means, in archiving context

The codes an archive or restore actually raises, with the step that raises them. The plugin's full
error list is `../../teradata-sql/references/error-codes.md`; this page adds the NOS/OTF context that
decides what to do next.

| Code | Where | Actually means → do this |
|---|---|---|
| 6881 | WRITE_NOS | "Only Simplified Authorization object is allowed" — you passed the TRUSTED auth; use `<db>.<auth>`. |
| 6953 | CREATE FOREIGN TABLE | Authorization definition does not match — you passed the SIMPLIFIED auth; use `<auth>_ft`. |
| 3706 | FOREIGN TABLE / DATALAKE | (a) qualified `EXTERNAL SECURITY` auth name — unqualify; (b) column list on inferred Parquet — remove; (c) "Column partitioning is not supported" — DBS Control 245; (d) "Open Table Format support is not enabled" — DBS Control 732, not syntax. |
| 4969 | NOS read/write | Network/TLS, not SQL: virtual-hosted LOCATION / HTTPS-vs-HTTP (NOS 101) / untrusted certificate. |
| 9134 | WRITE_NOS / OTF | WRITE_NOS: object path exists — fresh sub-path. OTF: `CREATE VIEW` unsupported — UNION ALL query. |
| 3803 / 3807 | register / later steps | Exists / does not exist — the foreign-table name drifted; one deterministic name, DROP-then-CREATE. |
| 5407 / 2666 | temperature predicate | Date expression does not match the column type (SUBSTRING on datetime / CAST of string-with-time). |
| 4893 | DROP TABLE on OTF | PURGE ALL is required. |
| 7825 | OTF DROP / CREATE | DROP: the OTF table does not exist (tolerate in a pre-drop). CREATE: UDF wrapper around a write failure (`ICEBERG_EXPORT … created concurrently`) — catalog/version problem, see the reference. |
| 7454 / 5404 / 3654 | union / restore across tiers | Microsecond TIMESTAMP → TIMESTAMP(0) / datetime overflow on `SELECT *` / select lists differ — explicit columns + type bridge. |
| 5589 | CREATE DATALAKE | `TD_ICEBERG_READ does not exist` — OTF library not installed (syntax was fine). |
| 6938 | CREATE DATALAKE | `Authorization '<name>' does not exist` — OTF IS enabled; fix the auth name (the enablement probe). |
| 9723 | TD_SNAPSHOTS | Only `SELECT *` is accepted. |
| 3524 | as DBC | Authorizations/datalakes cannot be owned by DBC — service user. |

