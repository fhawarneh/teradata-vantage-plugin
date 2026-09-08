# NOS transport and object inspection — LOCATION form, TLS vs plain HTTP, the two DBS Control gates, READ_NOS probes

Companion to the `archive` skill, Path A (A6). Field numbers, messages and defaults vary by release — verify
against yours. Changing a DBS Control field needs OS access to the Teradata node (the `teradata-recovery`
skill's host-operations rules apply); record the value BEFORE you change it, and keep the rollback value.

## 1. LOCATION form

Path-style only:

```
LOCATION('/s3/<host>:<port>/<bucket>/<path>/')
```

Virtual-hosted style (`/s3/<bucket>.<host>/…`) fails with **4969** "Couldn't resolve host name" — on an
on-prem store the bucket is not part of the hostname. The `/s3/` prefix stays the same for every
S3-compatible store, whatever the vendor.

## 2. Transport — two exclusive postures

ALWAYS pick one, and make the store match it:

- **TLS** — a certificate that every Teradata node trusts.
- **Plain HTTP** — DBS Control NOS field **101** `Disable HTTPS` = TRUE, against a plain-HTTP store.

A mismatch between the posture and the endpoint fails with **4969**, message "SSL wrong version number" or
"SSL connect error". That is the whole symptom: there is no separate authentication error to look for, so
read the 4969 text first — "Couldn't resolve host name" is the LOCATION form (section 1), the two SSL
messages are the transport.

## 3. The Parquet foreign-table gate

A `CREATE FOREIGN TABLE` over inferred Parquet that fails with **3706** "Column partitioning is not
supported" is not a syntax problem: DBS Control internal field **245** `ColumnarPurchased` is not TRUE on
that system. NEVER rewrite the DDL to chase this one — the column list is not the cause (a `(Location
VARCHAR(2048), Payload DATASET STORAGE FORMAT PARQUET)` list is the JSON/CSV form and raises 3706 of its
own, which is a different fault with the same code).

## 4. Both settings live in the GDO

Fields 101 and 245 are GDO settings, and they are lost when the system is rebuilt from an image — a rebuild
that restores every database can still leave `WRITE_NOS` failing with 4969 and foreign tables failing with
3706 until both are re-applied. Re-check them as part of any post-rebuild verification, not only when a
statement has already failed.

## 5. Inspecting the objects and their Parquet schema (read-only)

`READ_NOS` needs a dummy `ON` row — there is no bare form of it:

```sql
SELECT * FROM READ_NOS ( ON (SELECT 1 AS x) USING LOCATION('<location>') AUTHORIZATION(<db>.<auth>)
                         RETURNTYPE('NOSREAD_KEYS') ) AS s;             -- keys, sizes, timestamps
SELECT * FROM READ_NOS ( ON (SELECT 1 AS x) USING LOCATION('<location>') AUTHORIZATION(<db>.<auth>)
                         RETURNTYPE('NOSREAD_PARQUET_SCHEMA') ) AS s;   -- Parquet columns
```

Both are SELECTs, so they pass the read guard and need no approval. Use `NOSREAD_KEYS` to confirm that a
`WRITE_NOS` run actually landed objects under the sub-path you gave it, and `NOSREAD_PARQUET_SCHEMA` to see
the types the foreign side will infer before you write the federated view's CASTs.
