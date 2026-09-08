# OTF (Iceberg / Delta) enablement on Teradata Vantage — install gates, feature flags, DATALAKE bootstrap, and how to prove it works

Companion to the `archive` skill, Path B. Everything below was observed on Vantage 20.00 builds with the
`tdotf` package; field numbers, messages and package paths can differ by release — verify against yours.
Steps marked **[OS access]** run on the Teradata node as root (or a DBA with `dbscontrol` rights) — the
`teradata-recovery` skill's host-operations rules apply, and the plugin's Bash hook prompts before
`dbscontrol` when a Teradata connection is configured. Record every DBS Control value BEFORE you change it;
keep the rollback value in your notes.

## 1. Three separate things must be true — fixing one is not enough

1. The OTF function library is **installed** in the database (`TD_OTFDB` populated).
2. The OTF feature **flag** is enabled (DBS Control internal field 732 `JavaOTFFlags`).
3. A **DATALAKE** object exists, owned by a non-DBC service user, pointing at a reachable catalog + object store.

Read-only probes that tell you which of the three is missing (run through `base_readQuery`):

```sql
-- (1) library installed? expect TD_ICEBERG_READ, TD_ICEBERG_WRITE, TD_DELTA_READ, TD_DELTA_WRITE (kind 'L')
SELECT TableName, TableKind FROM DBC.TablesV WHERE DatabaseName='TD_OTFDB' AND TableKind='L';
SELECT DatabaseName, FunctionName FROM DBC.FunctionsV
WHERE UPPER(FunctionName) LIKE '%ICEBERG%' OR UPPER(FunctionName) LIKE '%DATALAKE%';   -- 0 rows = not installed
-- baseline: plain NOS, independent of OTF
SELECT FunctionName FROM DBC.FunctionsV WHERE UPPER(FunctionName) LIKE '%NOS%';        -- READ_NOS, WRITE_NOS, READ_NOS_CONTRACT
-- (3) datalakes registered?
SELECT DatalakeName, OTFTableFormat, CatalogType, CatalogLocation, StorageLocation FROM DBC.DatalakeInfoV;
```

`DBC.DatalakeInfoV` existing proves only that the OTF dictionary/parser is compiled in — it does NOT prove
the library is installed or the flag is on.

## 2. Installer prerequisites **[OS access]**

The `tdotf` RPM may already be on the node (`rpm -qa | grep -i tdotf`; scripts under
`/opt/teradata/tdotf/lib/scripts/`). Its `pre-install-checks.sh` requires these DBS Control values:

| Field | Required | Why |
|---|---|---|
| `DisableClientRoutineCreation` | FALSE | installer creates client routines |
| `DisableServerRoutineCreation` | FALSE | installer creates server routines |
| `AaSManagement` (internal **665**) | 0 or 2 | installer creates/modifies the OTF aaS user; `1` = "disallow modifications to aaS users" fails the check |

```bash
/usr/tdbms/bin/dbscontrol -a | grep -iE "DisableClientRoutineCreation|DisableServerRoutineCreation|AaSManagement"
printf "modify internal 665 = 2\nwrite\nquit\n" | /usr/tdbms/bin/dbscontrol      # record the old value first
/opt/teradata/tdotf/lib/scripts/pre-install-checks.sh; echo "rc=$?"                # expect rc=0
```

Run the installer from its scripts directory: `./install.sh --teradata_host <tdpid> --db_admin_user <dba>
--db_admin_pass <prompted> --create_tasm_rules true --restart_javaotf true`. `--teradata_host` takes a
**tdpid**, not a hostname. It loads ~180 jars into `TD_OTFDB` (minutes); log at
`/opt/teradata/tdotf/lib/scripts/install.log`. Never paste the DBA password into a chat transcript — run the
installer in your own terminal.

**Error 3541 during install** — "The request to assign new PERMANENT space is invalid": what it actually
means is that `DBC` does not have the several GB of unallocated PERM the `TD_OTFDB` default asks for. Check
`DBC.DatabasesV.PermSpace` / `DBC.DiskSpaceV` for DBC, free or grow space, re-run. See the `health` skill's
space-management reference.

## 3. The feature flag everyone misses — DBS Control internal 732 `JavaOTFFlags` **[OS access]**

Even with `TD_OTFDB` fully populated, `CREATE DATALAKE` can fail with:

```
Error 3706: Syntax error: Open Table Format support is not enabled on the system.
```

**What the message actually means:** not a syntax problem. The parser reports the gate; the gate is the DBS
Control bitmask 732:

```
0   00000000  Default — Iceberg read/write and Delta Lake read/write enabled
1   00000001  Disable Iceberg reads
2   00000010  Disable Iceberg writes
4   00000100  Disable Delta Lake reads
8   00001000  Disable Delta Lake writes
15  00001111  Disable everything   (observed as the shipped value on an express image)
```

```bash
/usr/tdbms/bin/dbscontrol -a | grep -E "732\.|733\.|282\."     # record current values
printf "modify internal 732 = 0\nwrite\nquit\n" | /usr/tdbms/bin/dbscontrol
```

Related fields: **733 `NativeOTFFlags`** (native Iceberg read path; 0 = enabled) and **282 `DisableMOTF`**
(Managed OTF). The flag is read at session start — open a **fresh** session before re-testing. GDO settings
survive a VM move but are lost on a rebuild from image; keep them in your runbook.

### Proving the flag took — the deliberately invalid probe

Run a `CREATE DATALAKE` that names authorizations that do not exist:

```sql
CREATE DATALAKE zz_probe
  EXTERNAL SECURITY DEFINER TRUSTED CATALOG zz_no_auth,
  EXTERNAL SECURITY DEFINER TRUSTED STORAGE zz_no_auth2
  USING catalog_type('hive') catalog_location('thrift://<metastore-host>:9083')
        storage_location('s3://<bucket>/') storage_endpoint('http://<object-store-host>:<port>')
        storage_region('us-east-1') TABLE FORMAT iceberg;
```

- `Error 3706 … not enabled` → the flag did not take (wrong session, or `write` not issued).
- **`Error 6938: Authorization 'zz_no_auth2' does not exist` → OTF is ENABLED.** That semantic failure on
  the fake names is exactly the signal you want; nothing was created.
- `Error 5589: Function 'TD_ICEBERG_READ' does not exist` → the flag is on but the library is not installed
  (`CREATE DATALAKE` validates by invoking the OTF operator). Go back to section 2 — do not debug the catalog.

## 4. Supporting infrastructure — catalog and object store

Supported `catalog_type` values seen: `hive`, `glue`, `unity`; `rest` was rejected ("Unsupported Catalog
Type: rest") on the build measured — verify against your release.

**Hive Metastore must map the `s3://` scheme.** Teradata emits `storage_location('s3://…')`, but Hadoop's
`hadoop-aws` registers only `s3a://`. Set in the metastore's `hive-site.xml` / `core-site.xml`:

```xml
<property><name>fs.s3.impl</name><value>org.apache.hadoop.fs.s3a.S3AFileSystem</value></property>
<!-- plus the matching fs.s3a.endpoint / access.key / secret.key / path.style.access / connection.ssl.enabled -->
```

and put `hadoop-aws` + `aws-java-sdk-bundle` on the metastore classpath (`HIVE_AUX_JARS_PATH`). Without this
the datalake registers but table creation never materialises a namespace directory in the store.

**Version pinning caveat.** The `tdotf` package bundles a specific Hive metastore *client*
(inspect `hive-common-*.jar` / `hive-exec-*.jar` inside `/opt/teradata/tdotf/lib/dependencies-iceberg.tar.gz`).
Run a metastore server of the same major line; do not assume a newer metastore is compatible. Hive 3.1.x
images: set `IS_RESUME=true` after the first schema init (else the entrypoint re-runs `schematool
-initSchema` every start and crash-loops on `FUNCTION 'NUCLEUS_ASCII' already exists`) and supply config as
`hive-site.xml` (3.1.x does not read `metastore-site.xml`).

**TLS.** The OTF write path runs in a JVM on the Teradata node that does not trust self-signed certificates.
Either use plain HTTP to the object store (`fs.s3a.connection.ssl.enabled=false` on the metastore side) or
import the store's CA into that JVM's truststore. This is separate from the plain-NOS posture (DBS Control
NOS field 101 `Disable HTTPS`) — the two paths are gated independently.

Create the bucket before bootstrapping the datalake.

## 5. Bootstrap the DATALAKE (SQL; run as the service user)

Datalakes **cannot be owned by DBC**: the `EXTERNAL SECURITY` authorization resolves in the creating user's
home database, and Teradata forbids authorizations in DBC (**3524**). Use a dedicated service user. These are
DDL statements: run them in the service user's own SQL client (the bundled 0.2.6 MCP server registers no write
tool; `base_writeQuery` exists only where the connected server provides one, and `CREATE USER`/`GRANT` prompt
for approval there). Never paste the passwords into a chat transcript.

```sql
-- as a DBA
CREATE USER <otf_svc> FROM <parent_db> AS PERM = 200e6, PASSWORD = '<set-in-your-terminal>';
GRANT CREATE SERVER ON TD_SERVER_DB TO <otf_svc>;        -- datalakes are foreign servers; GRANT CREATE DATALAKE is not valid syntax
GRANT DROP SERVER   ON TD_SERVER_DB TO <otf_svc>;        -- only if teardown is expected

-- as <otf_svc> (home database = <otf_svc>)
CREATE AUTHORIZATION <catalog_auth> AS DEFINER TRUSTED USER '<catalog-user>'   PASSWORD '<catalog-secret>';
CREATE AUTHORIZATION <storage_auth> AS DEFINER TRUSTED USER '<ACCESS_KEY_ID>'  PASSWORD '<SECRET_ACCESS_KEY>';
CREATE DATALAKE <datalake>
  EXTERNAL SECURITY DEFINER TRUSTED CATALOG <catalog_auth>,
  EXTERNAL SECURITY DEFINER TRUSTED STORAGE <storage_auth>
  USING catalog_type('hive') catalog_location('thrift://<metastore-host>:9083')
        storage_location('s3://<bucket>/') storage_endpoint('http://<object-store-host>:<port>')
        storage_region('us-east-1') TABLE FORMAT iceberg;
```

Rules:
- Authorization names in `EXTERNAL SECURITY` MUST be **unqualified** (`<catalog_auth>`, not
  `<db>.<catalog_auth>`) — qualified names raise **3706**, the same rule as `CREATE FOREIGN TABLE`.
- If a **different** user (for example the connection the MCP server uses) will later create/read tables in
  the datalake, the same two authorizations must ALSO exist in `TD_SERVER_DB`
  (`CREATE AUTHORIZATION TD_SERVER_DB.<storage_auth> …`), because the auth name resolves in the datalake's
  container at runtime.
- Location attributes are **immutable** — changing metastore host, endpoint or bucket is
  `DROP DATALAKE <datalake>` + `CREATE DATALAKE` (destructive; prompts).
- Verify: `SELECT DatalakeName, OTFTableFormat, CatalogType, CatalogLocation, StorageLocation FROM DBC.DatalakeInfoV;`

## 6. First table — the working write path is CTAS

```sql
CREATE DATABASE <datalake>.<otf_db>;
CREATE TABLE <datalake>.<otf_db>.<probe> AS (SELECT 1 AS id, 'iceberg' AS label) WITH DATA;
SELECT * FROM <datalake>.<otf_db>.<probe>;
SELECT * FROM TD_SNAPSHOTS(ON <datalake>.<otf_db>.<probe>) AS d;     -- SELECT * only (9723 otherwise)
DROP TABLE <datalake>.<otf_db>.<probe> PURGE ALL;                    -- PURGE ALL required (4893)
```

- `CREATE TABLE … (cols) TBLPROPERTIES ('write.format.default'='parquet')` followed by `INSERT` is the
  documented explicit-DDL shape; it was NOT verified to success here, and `INSERT … SELECT` into an existing
  OTF table wrote corrupted (massively duplicated) rows on the builds measured. Use CTAS; one table per slice.
- Object layout written by Teradata: `<otf_db>/<table>/data/TD_*.parquet`,
  `<otf_db>/<table>/metadata/*.metadata.json` (take the lexicographically last one for the current state:
  `format-version`, `table-uuid`, `schemas`, `snapshots[].summary{operation,total-records,total-data-files,
  total-files-size}`, `current-snapshot-id`), and manifest/snapshot `*.avro`. `total-files-size` of the
  current snapshot is the authoritative compressed data size for outcome reporting.
- Cross-engine proof: read the table back with an independent Iceberg client (PyIceberg with a Hive catalog,
  Spark, Trino). That is the point of the format — and the decisive test in the next section.

## 7. Known failure signature — a version-skewed build

Symptom: CTAS fails with

```
Error 7825 in UDF/XSP/UDM TD_OTFDB.ICEBERG_EXPORT:
  SQLSTATE 38001: [1212]: Table was created concurrently: <ns>.<tbl>
```

then reads report `Table does not exist` and the store shows an **empty** `metadata/` prefix.

**What the message actually means:** a red herring. In `/var/opt/teradata/tdotf/otf.log` **[OS access]** the
stack is `org.apache.iceberg.exceptions.AlreadyExistsException … BaseMetastoreCatalog` — Iceberg's catalog
code converts a `CommitFailedException` into `AlreadyExistsException`, so the real commit error is swallowed.
Observed with an OTF package two releases ahead of the database (`tdotf 20.00.30` on `tdbms 20.00.28`); the
matched pair (`tdbms 20.00.22` + `tdotf_iceberg 20.00.22`) worked end-to-end.

Decisive bisect before blaming the catalog or the store: with an independent client against the SAME
metastore, bucket and credentials, create a namespace, create a table, append, read back. If the client
succeeds at every step and Teradata still fails (and even fails to read the client-written table with
"Metadata file lacks a schema or snapshot information"), the fault is Teradata-side: align the `tdotf` and
`tdbms` versions or raise it with the vendor. Things that changed nothing in the observed case, so do not
spend time on them: metastore major version, Iceberg format version 1 vs 2, metastore locking
(`hive.support.concurrency`), object-store checksums, DNS/credentials.

Plain `READ_NOS`/`WRITE_NOS` are unaffected by all of the above (gated by `EnableNOS` in the NOS screen).

## 8. Rollback and hygiene

- Keep the pre-change values of 665, 732, 733, 282 and restore them if OTF is abandoned.
- `DROP DATALAKE` needs `DROP SERVER` on `TD_SERVER_DB`; it does not delete data in the store.
- Never store the object-store secret in a SQL file or transcript; `SHOW AUTHORIZATION` omits it, so a
  rebuilt authorization needs the secret re-supplied in your own terminal.
