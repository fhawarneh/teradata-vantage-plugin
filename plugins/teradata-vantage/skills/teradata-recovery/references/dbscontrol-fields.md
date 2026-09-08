# DBS Control fields that gate features, and how to read, change and verify them

Purpose: the eight `dbscontrol` fields behind "it worked before the rebuild" failures (NOS, Parquet foreign tables, Open Table Format), the exact read/apply/verify command forms, and the persistence rules. Runs on the Teradata node as root; `dbscontrol` is `/usr/tdbms/bin/dbscontrol`. Field numbers and names are as observed on Vantage 20.x (from Teradata documentation where noted; verify against your release with `dbscontrol -a`).

## Rules

- ALWAYS capture the current value before a `modify`, and put "Rollback value: `<field>` was `<old>`" in the change record. `dbscontrol` has no undo; the GDO is the only copy.
- ALWAYS `write` before `quit`; a `modify` without `write` is discarded silently.
- NEVER assume a field survived a rebuild. A move of the system (same GDO) carries every field; a rebuild from a fresh image resets every field to the shipped default. Rebuild runbooks re-apply; move runbooks verify.
- Verify by exercising the feature from a FRESH session, not by re-reading the field. A pooled MCP connection can keep returning the old error after the flag is correct.
- Some changes are deferred until a `tpareset`; others take effect on the next session. Where the source did not record which, it is marked unknown below: test on a non-production system rather than assume.
- Treat licence-class fields (for example `ColumnarPurchased`) as "verify with your licence", not "set to TRUE".

## The eight fields

| Screen / field | Name | Required for | Failure when wrong (verbatim) | Takes effect |
|---|---|---|---|---|
| `internal 732` | `JavaOTFFlags` (bitmask) | Open Table Format (Iceberg / Delta Lake) via `CREATE DATALAKE` | `Error 3706: Syntax error: Open Table Format support is not enabled on the system.` The message names the parser and reads like unsupported syntax; it is this flag. | Observed to take effect for a NEW session without a reset. |
| `internal 665` | `AaSManagement` | OTF database-side installer (creates/modifies an aaS user) | `pre-install-checks.sh` fails with "Disallow modifications to aaS users" | Unknown; re-run the pre-check after `write`. |
| `internal 733` | `NativeOTFFlags` | Native Iceberg read path (`0` = enabled) | Native Iceberg reads disabled | Unknown. |
| `internal 282` | `DisableMOTF` | Managed OTF (`TRUE` disables) | Managed OTF unavailable | Unknown; only flip if a documented workflow needs it. |
| `nos 1` | `EnableNOS` | `READ_NOS` / `WRITE_NOS` / `CREATE FOREIGN TABLE` at all | NOS functions unavailable | Unknown; verify with a `READ_NOS` metadata call. |
| `nos 101` | `Disable HTTPS` | Object stores served over plain HTTP (`TRUE`); TLS stores need `FALSE` | `WRITE_NOS` -> `Error 4969 ... SSL wrong version number` when NOS speaks HTTPS to an HTTP endpoint | Unknown (immediate vs reset not recorded); verify with `WRITE_NOS` from a new session. |
| `internal 245` | `ColumnarPurchased` | Parquet foreign tables (inferred Parquet is internally column-partitioned) | `CREATE FOREIGN TABLE` over Parquet -> `Error 3706 ... Column partitioning is not supported` (the DDL has no PARTITION clause; the message misdirects) | Unknown. Licence-class: confirm entitlement before setting. |
| `internal 618` / `internal 662` | `DisableClientRoutineCreation` / `DisableServerRoutineCreation` | OTF installer (creates client and server routines); must be `FALSE` | Installer pre-check fails | Unknown. |

Related, not in `dbscontrol`: `Start DBS` (field 0) and `Start With Logons` (field 2) live in `ctl` -> `screen debug`; both are written with `wr` and are DEFERRED until `tpareset`. `Start With Logons = None` persists across boots and shows as `DBS state is 3: quiescent`.

### Decode tables

`internal 732 JavaOTFFlags` (bitmask; add the bits you want disabled):

```text
0   00000000  Default: Iceberg read/write and Delta Lake read/write enabled
1   00000001  Disable Iceberg reads
2   00000010  Disable Iceberg writes
4   00000100  Disable Delta Lake reads
8   00001000  Disable Delta Lake writes
15  00001111  Everything disabled (a shipped default seen on at least one developer image)
```

`internal 665 AaSManagement`: `0` = default (same as 2), `1` = disallow modifications to aaS users, `2` = allow. A shipped default of `1` has been seen; the OTF installer needs `0` or `2`.

## Read

```bash
/usr/tdbms/bin/dbscontrol -a                                   # dump every field
/usr/tdbms/bin/dbscontrol -a | grep -E ' 732\. | 733\. | 282\. | 245\. | 665\. | 618\. | 662\. '
/usr/tdbms/bin/dbscontrol -a | grep -iE 'EnableNOS|Disable ?HTTPS|ColumnarPurchased|AaSManagement|JavaOTFFlags'
```

Fields print as `<number>. <Name> = <value>` within their screen section. Match names case-insensitively and tolerate a space in `Disable HTTPS`; the exact label spelling has not been captured verbatim. Save the full `dbscontrol -a` output to a dated file before changing anything: it is the rollback record.

## Apply (non-interactive form)

The syntax differs by screen: `internal` takes `modify internal <n> = <value>` (spaces, numeric or boolean values); `nos` has been applied as `modify nos <n>=TRUE`. Both need `write` then `quit`.

```bash
# OTF: enable Iceberg and Delta Lake read/write
printf 'modify internal 732 = 0\nwrite\nquit\n' | /usr/tdbms/bin/dbscontrol

# OTF installer prerequisite
printf 'modify internal 665 = 2\nwrite\nquit\n' | /usr/tdbms/bin/dbscontrol

# NOS against a plain-HTTP object store
printf 'modify nos 101=TRUE\nwrite\nquit\n' | /usr/tdbms/bin/dbscontrol

# Parquet foreign tables (only if licensed)
printf 'modify internal 245=TRUE\nwrite\nquit\n' | /usr/tdbms/bin/dbscontrol
```

Record, for each: date, field, old value, new value, reason, and whether a `tpareset` followed. If the field is one of the "unknown" rows above and the feature still fails from a fresh session, schedule a `tpareset -f "<reason>"` in a maintenance window; do not reset a production system to test a guess.

## Verify by provoking a deeper error

The success signal is the error MOVING FORWARD in the pipeline, not disappearing. Use obviously fake object names so nothing can partially succeed.

OTF (field 732), from a NEW session:

```sql
CREATE DATALAKE zz_probe_lake
  EXTERNAL SECURITY DEFINER TRUSTED CATALOG zz_no_such_auth1,
  EXTERNAL SECURITY DEFINER TRUSTED STORAGE zz_no_such_auth2
  USING catalog_type('hive') catalog_location('thrift://catalog.example:9083')
        storage_location('s3://probe-bucket/') storage_endpoint('http://store.example:9000')
        storage_region('us-east-1')
  TABLE FORMAT iceberg;
```

Still `Error 3706 ... Open Table Format support is not enabled` means the flag did not take. `Error 6938: Authorization 'zz_no_such_auth2' does not exist` means OTF is ENABLED and the parser got past the gate; that is the expected failure for the fake names. Then confirm the database-side install: `SELECT TableName, TableKind FROM DBC.TablesV WHERE DatabaseName = 'TD_OTFDB' AND TableKind = 'L';` should list `TD_ICEBERG_READ`, `TD_ICEBERG_WRITE`, `TD_DELTA_READ`, `TD_DELTA_WRITE`. Datalakes registered: `SELECT DatalakeName, OTFTableFormat, CatalogType, StorageLocation FROM DBC.DatalakeInfoV;`.

NOS (fields 1 and 101): run a `READ_NOS` metadata call against a real bucket with `RETURNTYPE('NOSREAD_KEYS')`. `Error 4969` sub-texts are three different faults: `Couldn't resolve host name` = wrong LOCATION style (use path-style `/s3/<host>:<port>/<bucket>/<path>/`); `SSL connect error` = TLS handshake failure; `SSL wrong version number` = HTTPS spoken to a plain-HTTP endpoint = field 101. Branch on the text, never on the number. Field 101 and a TLS-serving store are mutually exclusive: put a trusted certificate on the store later and 101 must go back to `FALSE`.

Parquet (field 245): `CREATE FOREIGN TABLE` over a Parquet prefix. `3706 ... Column partitioning is not supported` means the field (or the licence) is still off; a `6953`/`3706` authorization-shape error means the gate is open and your authorization object is the next thing to fix.

## Persistence and ordering

- Fields live in the GDO on the node's storage. Move the system: kept. Rebuild from an image: LOST, every one of them. After any rebuild, run the read command and compare against the saved dump.
- `dbscontrol` needs a running DBS to write. During a startup-recovery crash loop (recovery skill Step 4) no field can be changed; fix the loop first.
- Authorization objects are not schema: `SHOW AUTHORIZATION` omits the password, so a rebuild must re-create every authorization from the stored secret. Keep the secrets with the backup, never in the runbook.
