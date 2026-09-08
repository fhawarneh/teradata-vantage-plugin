#!/usr/bin/env python3
"""td_temperature.py -- generate Teradata SQL for data-temperature classification and archive slicing.

Prints SQL to stdout. It never connects to a database, needs nothing beyond the Python 3 standard library,
and encodes the Teradata dialect rules that are easy to get wrong when written by hand:

  * the date expression is chosen from the column's REAL DBC.ColumnsV ColumnType code:
      DA / TS / SZ (DATE, TIMESTAMP, TIMESTAMP WITH TIME ZONE)  ->  CAST(<col> AS DATE)
      CV / CF / anything else (string dates, unknown)            ->  TRYCAST(SUBSTRING(<col> FROM 1 FOR 10) AS DATE)
    (SUBSTRING on a datetime raises Error 5407; CAST of a string-with-time to DATE raises Error 2666)
  * AS_OF anchors on MAX(<date>) of the table by default (historical data is not "today")
  * cutoffs are INTEGER-DAY subtraction (AS_OF - 365) as house style -- it reads the same on a DATE and on a
    TIMESTAMP cast to DATE. INTERVAL '365' DAY is also valid Teradata (an interval literal is sized from its
    own digits, up to four) and is not an error to be corrected.
  * an exact N% slice uses ROW_NUMBER inside a derived table (Teradata rejects an analytic function in a
    WHERE subquery), ordered coldest-first for whole-table slices
  * a deterministic keep/offload split uses HASHBUCKET(HASHROW(<key>)) -- never ABS(HASHROW()), which fails
    because HASHROW returns BYTE; the split is deterministic but not uniform

Sub-commands (run with -h for each):
  lookup      SQL that returns the ColumnType code of a column (run it first when the type is unknown)
  classify    4-bucket classification (cold / warm / hot / uncastable) plus a completeness check
  predicate   the WHERE predicate for one band (cold | warm | hot)
  slice       an exact N% slice predicate (of a band or of the whole table) plus its verification COUNT
  hash-split  keep-K% / offload-(100-K)% predicates by hash bucket plus the completeness check

Examples:
  td_temperature.py lookup   sales_db.sales_fact sale_ts
  td_temperature.py classify sales_db.sales_fact --date-column sale_ts --column-type TS
  td_temperature.py classify sales_db.sales_fact --date-column sale_dt --column-type CV --cold-days 730 --warm-days 180
  td_temperature.py predicate sales_db.sales_fact --date-column sale_ts --column-type TS --band cold
  td_temperature.py slice    sales_db.sales_fact --percent 20 --id-column sale_id --band cold --date-column sale_ts --column-type TS
  td_temperature.py slice    sales_db.sales_fact --percent 20 --id-column sale_id --date-column sale_ts --column-type TS   # whole table, coldest first
  td_temperature.py hash-split sales_db.sales_fact --key sale_id --keep 70

Output is SQL text only (comments start with --). Feed the statements to your SQL client or to the plugin's
base_readQuery tool one at a time; none of the generated statements modifies data.
"""

from __future__ import annotations

import argparse
import re
import sys

__version__ = "1.0.0"

# DBC.ColumnsV ColumnType codes that carry a DATE component and therefore take CAST(<col> AS DATE).
# (TZ is TIME WITH TIME ZONE and AT is TIME: no date component -- they are not date columns.)
DATETIME_TYPE_CODES = ("DA", "TS", "SZ")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
_QUOTED = re.compile(r'^"[^"]+"$')
_DATE_LITERAL = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UsageError(Exception):
    pass


# ----------------------------------------------------------------------------------------------- helpers


def _ident(name: str, what: str) -> str:
    """Accept a bare Teradata identifier or a double-quoted one; reject anything else."""
    n = (name or "").strip()
    if not n:
        raise UsageError(f"{what} is required")
    if _IDENT.match(n) or _QUOTED.match(n):
        return n
    raise UsageError(f"{what} {n!r} is not a valid identifier (use letters, digits, _ $ #, or double-quote it)")


def _qualified(name: str) -> tuple[str, str]:
    """Split <db>.<table>; both parts are validated identifiers."""
    n = (name or "").strip()
    if n.count(".") != 1:
        raise UsageError(f"table must be given as <database>.<table>, got {n!r}")
    db, t = n.split(".", 1)
    return _ident(db, "database"), _ident(t, "table")


def _bare(ident: str) -> str:
    """The identifier without surrounding double quotes, for use inside a string literal."""
    return ident[1:-1] if _QUOTED.match(ident) else ident


def date_expr(column: str, column_type: str) -> str:
    """The DATE-typed expression for a column, chosen by its DBC ColumnType code."""
    code = (column_type or "").strip().upper()
    if code in DATETIME_TYPE_CODES:
        return f"CAST({column} AS DATE)"
    return f"TRYCAST(SUBSTRING({column} FROM 1 FOR 10) AS DATE)"


def as_of_expr(de: str, db: str, t: str, basis: str, as_of: str | None) -> str:
    """AS_OF reference: a DATE literal, CURRENT_DATE, or the table's MAX(<date>)."""
    if as_of:
        if not _DATE_LITERAL.match(as_of):
            raise UsageError("--as-of must be YYYY-MM-DD")
        return f"DATE '{as_of}'"
    if basis == "today":
        return "CURRENT_DATE"
    return f"(SELECT MAX({de}) FROM {db}.{t})"


def band_predicate(de: str, mx: str, band: str, cold_days: int, warm_days: int) -> str:
    """Half-open temperature bands using integer-day subtraction."""
    if band == "cold":
        return f"{de} < {mx} - {cold_days}"
    if band == "warm":
        return f"{de} >= {mx} - {cold_days} AND {de} < {mx} - {warm_days}"
    if band == "hot":
        return f"{de} >= {mx} - {warm_days}"
    if band == "all":
        return ""
    raise UsageError(f"unknown band {band!r}")


def _check_days(cold_days: int, warm_days: int) -> None:
    if warm_days < 1:
        raise UsageError("--warm-days must be >= 1")
    if cold_days <= warm_days:
        raise UsageError("--cold-days must be greater than --warm-days")


def _header(title: str, lines: list[str]) -> str:
    out = [f"-- td_temperature.py {__version__} :: {title}"]
    out += [f"-- {ln}" for ln in lines]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------ sub-commands


def cmd_lookup(a: argparse.Namespace) -> str:
    db, t = _qualified(a.table)
    col = _ident(a.column, "column")
    # alias must be coltype -- `ct` is a Teradata reserved word (Error 3707)
    sql = (
        "SELECT TRIM(ColumnName) AS colname, TRIM(ColumnType) AS coltype, ColumnLength AS collength,\n"
        "       DecimalTotalDigits AS dtot, DecimalFractionalDigits AS dfrac\n"
        f"FROM DBC.ColumnsV\nWHERE DatabaseName = '{_bare(db)}' AND TableName = '{_bare(t)}'\n"
        f"  AND UPPER(TRIM(ColumnName)) = UPPER('{_bare(col)}');"
    )
    return "\n".join(
        [
            _header(
                "ColumnType lookup",
                [
                    "DA / TS / SZ -> pass --column-type <code> and the classifier uses CAST(col AS DATE)",
                    "CV / CF (string dates) or anything else -> TRYCAST(SUBSTRING(col FROM 1 FOR 10) AS DATE)",
                    "TZ / AT are TIME types without a date component: not usable as a temperature column",
                ],
            ),
            sql,
            "",
            "-- candidate date columns by name (mechanical hint only; never pick a column ending in id / _id):",
            "SELECT TRIM(ColumnName) AS colname, TRIM(ColumnType) AS coltype\n"
            f"FROM DBC.ColumnsV\nWHERE DatabaseName = '{_bare(db)}' AND TableName = '{_bare(t)}'\n"
            "  AND (TRIM(ColumnType) IN ('DA','TS','SZ')\n"
            "       OR LOWER(ColumnName) LIKE ANY ('%date%','%datetime%','%timestamp%','%\\_dt' ESCAPE '\\','%dttm%',\n"
            "                                        '%\\_ts' ESCAPE '\\','%created%','%modified%','%posted%','%occurred%'))\n"
            "  AND LOWER(ColumnName) NOT LIKE '%id'\nORDER BY ColumnId;",
        ]
    )


def cmd_classify(a: argparse.Namespace) -> str:
    db, t = _qualified(a.table)
    col = _ident(a.date_column, "--date-column")
    _check_days(a.cold_days, a.warm_days)
    de = date_expr(col, a.column_type)
    mx = as_of_expr(de, db, t, a.basis, a.as_of)
    cold = band_predicate(de, mx, "cold", a.cold_days, a.warm_days)
    warm = band_predicate(de, mx, "warm", a.cold_days, a.warm_days)
    hot = band_predicate(de, mx, "hot", a.cold_days, a.warm_days)
    basis_label = (
        f"DATE '{a.as_of}'" if a.as_of else ("CURRENT_DATE" if a.basis == "today" else "MAX(date) of the table")
    )
    ft = f"{db}.{t}"
    parts = [
        _header(
            "temperature classification",
            [
                f"table {ft}; date column {col} (ColumnType {a.column_type or 'unknown'} -> {de})",
                f"AS_OF = {basis_label}; cold = older than {a.cold_days} days; warm = {a.warm_days}..{a.cold_days} days; "
                f"hot = within {a.warm_days} days",
                "uncastable = rows whose date expression is NULL; they stay in block storage (safe)",
            ],
        ),
        "-- AS_OF reference date",
        f"SELECT {mx} AS as_of_date;" if not mx.startswith("(") else f"SELECT MAX({de}) AS as_of_date FROM {ft};",
        "",
        "-- 4-bucket classification (chart-ready: one row per bucket)",
        f"SELECT 'cold' AS temperature, CAST(COUNT(*) AS BIGINT) AS row_count FROM {ft} WHERE {cold}\n"
        f"UNION ALL SELECT 'warm', CAST(COUNT(*) AS BIGINT) FROM {ft} WHERE {warm}\n"
        f"UNION ALL SELECT 'hot',  CAST(COUNT(*) AS BIGINT) FROM {ft} WHERE {hot}\n"
        f"UNION ALL SELECT 'uncastable', CAST(COUNT(*) AS BIGINT) FROM {ft} WHERE {de} IS NULL;",
        "",
        "-- completeness check: every value below must equal total_rows minus the other three",
        f"SELECT CAST(COUNT(*) AS BIGINT) AS total_rows,\n"
        f"       SUM(CASE WHEN {cold} THEN 1 ELSE 0 END) AS cold_rows,\n"
        f"       SUM(CASE WHEN {warm} THEN 1 ELSE 0 END) AS warm_rows,\n"
        f"       SUM(CASE WHEN {hot} THEN 1 ELSE 0 END) AS hot_rows,\n"
        f"       SUM(CASE WHEN {de} IS NULL THEN 1 ELSE 0 END) AS uncastable_rows\n"
        f"FROM {ft};",
    ]
    return "\n".join(parts)


def cmd_predicate(a: argparse.Namespace) -> str:
    db, t = _qualified(a.table)
    col = _ident(a.date_column, "--date-column")
    _check_days(a.cold_days, a.warm_days)
    de = date_expr(col, a.column_type)
    mx = as_of_expr(de, db, t, a.basis, a.as_of)
    pred = band_predicate(de, mx, a.band, a.cold_days, a.warm_days)
    ft = f"{db}.{t}"
    return "\n".join(
        [
            _header(f"{a.band} band predicate", [f"table {ft}; use it as: WHERE {pred}"]),
            f"-- predicate\n{pred}",
            "",
            "-- rows in the band",
            f"SELECT CAST(COUNT(*) AS BIGINT) AS band_rows FROM {ft} WHERE {pred};",
        ]
    )


def cmd_slice(a: argparse.Namespace) -> str:
    db, t = _qualified(a.table)
    idc = _ident(a.id_column, "--id-column")
    if not 1 <= a.percent <= 99:
        raise UsageError("--percent must be between 1 and 99")
    ft = f"{db}.{t}"
    scope_pred = ""
    order_by = idc
    notes = [f"table {ft}; exactly {a.percent}% by ROW_NUMBER over {idc}"]
    if a.band != "all":
        if not a.date_column:
            raise UsageError("--date-column is required with --band cold|warm|hot")
        _check_days(a.cold_days, a.warm_days)
        col = _ident(a.date_column, "--date-column")
        de = date_expr(col, a.column_type)
        mx = as_of_expr(de, db, t, a.basis, a.as_of)
        scope_pred = band_predicate(de, mx, a.band, a.cold_days, a.warm_days)
        notes.append(f"scope: the {a.band} band ({scope_pred})")
    elif a.date_column:
        col = _ident(a.date_column, "--date-column")
        de = date_expr(col, a.column_type)
        order_by = f"(CASE WHEN {de} IS NULL THEN 1 ELSE 0 END), {de} ASC, {idc}"
        notes.append("scope: whole table, ordered coldest-first (NULL dates last)")
    else:
        notes.append("scope: whole table, ordered by the id column (pass --date-column for coldest-first)")
    scope = f" WHERE ({scope_pred})" if scope_pred else ""
    pred = (
        f"{idc} IN (SELECT id FROM (SELECT {idc} AS id, ROW_NUMBER() OVER (ORDER BY {order_by}) AS rn\n"
        f"                         FROM {ft}{scope}) d\n"
        f"          WHERE rn <= (SELECT CAST(COUNT(*) AS BIGINT) * {a.percent} / 100 FROM {ft}{scope}))"
    )
    return "\n".join(
        [
            _header("exact percentage slice", notes),
            "-- slice predicate (ROW_NUMBER must live in a derived table)",
            pred,
            "",
            "-- expected slice size",
            f"SELECT CAST(COUNT(*) AS BIGINT) * {a.percent} / 100 AS expected_rows FROM {ft}{scope};",
            "",
            "-- verification: rows selected by the predicate",
            f"SELECT CAST(COUNT(*) AS BIGINT) AS slice_rows FROM {ft} WHERE {pred};",
            "",
            "-- after archiving, remove EXACTLY the archived rows by membership in the archive object,",
            "-- never by re-running this predicate (a re-sort of the live table mid-delete):",
            f"--   DELETE FROM {ft} WHERE {idc} IN (SELECT {idc} FROM <archive_object>);",
        ]
    )


def cmd_hash_split(a: argparse.Namespace) -> str:
    db, t = _qualified(a.table)
    if not 1 <= a.keep <= 99:
        raise UsageError("--keep must be between 1 and 99")
    keys = [_ident(k, "--key") for k in a.key]
    hashed = keys[0] if len(keys) == 1 else " || '|' || ".join(f"TRIM({k})" for k in keys)
    bucket = f"MOD(HASHBUCKET(HASHROW({hashed})), 100)"
    ft = f"{db}.{t}"
    keep = f"{bucket} < {a.keep}"
    offload = f"{bucket} >= {a.keep}"
    return "\n".join(
        [
            _header(
                "deterministic hash split",
                [
                    f"table {ft}; keep {a.keep}% (buckets 0..{a.keep - 1}); offload {100 - a.keep}% (buckets {a.keep}..99)",
                    "HASHBUCKET(HASHROW()) is stable per key value: the same rows are selected on every run",
                    "deterministic but NOT uniform (a nominal 20% can land at 13-20%); use `slice` for an exact N%",
                ],
            ),
            f"-- keep in block storage\n{keep}",
            "",
            f"-- offload to object storage\n{offload}",
            "",
            "-- completeness: kept_rows + offloaded_rows must equal total_rows (no gap, no overlap)",
            f"SELECT CAST(COUNT(*) AS BIGINT) AS total_rows,\n"
            f"       SUM(CASE WHEN {keep} THEN 1 ELSE 0 END) AS kept_rows,\n"
            f"       SUM(CASE WHEN {offload} THEN 1 ELSE 0 END) AS offloaded_rows\n"
            f"FROM {ft};",
        ]
    )


# --------------------------------------------------------------------------------------------- argparse


def _add_date_args(p: argparse.ArgumentParser, required: bool) -> None:
    p.add_argument("--date-column", required=required, help="the table's date/timestamp column")
    p.add_argument(
        "--column-type",
        default="",
        help="DBC.ColumnsV ColumnType code of the date column (DA, TS, SZ, CV, CF ...). "
        "Unknown/empty -> the TRYCAST(SUBSTRING) string form. Get it with the `lookup` sub-command.",
    )
    p.add_argument("--cold-days", type=int, default=365, help="older than this = cold (default 365)")
    p.add_argument("--warm-days", type=int, default=90, help="within this = hot (default 90)")
    p.add_argument(
        "--basis",
        choices=("max_date", "today"),
        default="max_date",
        help="AS_OF anchor: MAX(date) of the table (default) or CURRENT_DATE",
    )
    p.add_argument("--as-of", default=None, help="fixed AS_OF date literal YYYY-MM-DD (overrides --basis)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="td_temperature.py",
        description="Generate Teradata temperature-classification and archive-slicing SQL (prints SQL only; never connects).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Sub-commands", 1)[0],
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    s = sub.add_parser("lookup", help="SQL returning a column's ColumnType code (+ candidate date columns)")
    s.add_argument("table", help="<database>.<table>")
    s.add_argument("column", help="column to look up")
    s.set_defaults(fn=cmd_lookup)

    s = sub.add_parser("classify", help="4-bucket temperature classification SQL")
    s.add_argument("table", help="<database>.<table>")
    _add_date_args(s, required=True)
    s.set_defaults(fn=cmd_classify)

    s = sub.add_parser("predicate", help="WHERE predicate for one temperature band")
    s.add_argument("table", help="<database>.<table>")
    s.add_argument("--band", choices=("cold", "warm", "hot"), required=True)
    _add_date_args(s, required=True)
    s.set_defaults(fn=cmd_predicate)

    s = sub.add_parser("slice", help="exact N%% slice predicate (of a band or of the whole table)")
    s.add_argument("table", help="<database>.<table>")
    s.add_argument("--percent", type=int, required=True, help="1..99")
    s.add_argument("--id-column", required=True, help="a stable unique column (PK / UPI)")
    s.add_argument(
        "--band",
        choices=("cold", "warm", "hot", "all"),
        default="all",
        help="take N%% within this band (default all = whole table, coldest first when --date-column is given)",
    )
    _add_date_args(s, required=False)
    s.set_defaults(fn=cmd_slice)

    s = sub.add_parser("hash-split", help="keep-K%% / offload predicates by HASHBUCKET(HASHROW(key))")
    s.add_argument("table", help="<database>.<table>")
    s.add_argument("--key", action="append", required=True, help="split key column (repeat for a composite key)")
    s.add_argument("--keep", type=int, required=True, help="percentage to KEEP in block storage, 1..99")
    s.set_defaults(fn=cmd_hash_split)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(args.fn(args))
    except UsageError as e:
        print(f"td_temperature.py: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
