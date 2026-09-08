"""The grounding cache and verified-query repository.

The properties worth pinning are the ones that keep a cache from becoming a liability: staleness is
always reported, a corrupt store degrades to empty rather than failing, DDL invalidates, promotion
needs a human, and lookup never guesses at meaning.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("grounding", PLUGIN / "scripts" / "grounding.py")
grounding = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(grounding)

TARGET = "unit-test-system"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------- fingerprinting


def test_fingerprint_is_stable_and_case_insensitive():
    assert grounding.fingerprint("Host-A/db") == grounding.fingerprint("  host-a/DB  ")
    assert grounding.fingerprint("host-a") != grounding.fingerprint("host-b")


def test_fingerprint_does_not_leak_the_target():
    """Only the digest reaches disk: a listing of the cache must reveal nothing about the system."""
    target = "prod-warehouse.internal:1025/finance"
    fp = grounding.fingerprint(target)
    for fragment in ("prod-warehouse", "internal", "finance", "1025"):
        assert fragment not in fp
    assert fragment not in grounding.store_dir(target).replace(str(Path.home()), "")


# ---------------------------------------------------------------- schema grounding


def test_schema_round_trips_and_is_case_insensitive():
    grounding.remember_schema(TARGET, "db.tbl", {"columns": [{"name": "a"}]})
    got = grounding.recall_schema(TARGET, "DB.TBL")
    assert got and got["columns"] == [{"name": "a"}]


def test_every_recall_reports_age_and_staleness():
    """A cached fact without its age is indistinguishable from a fresh one. That is the whole risk."""
    grounding.remember_schema(TARGET, "db.tbl", {"columns": []})
    got = grounding.recall_schema(TARGET, "db.tbl")
    assert got["age_seconds"] == 0
    assert got["stale"] is False


def test_a_fact_past_its_ttl_is_marked_stale(monkeypatch):
    grounding.remember_schema(TARGET, "db.tbl", {"columns": []})
    monkeypatch.setattr(grounding, "SCHEMA_TTL_SECONDS", -1)
    assert grounding.recall_schema(TARGET, "db.tbl")["stale"] is True


def test_a_fact_with_no_timestamp_is_stale_not_fresh():
    """Unknown age must fail closed. A missing timestamp is not evidence of freshness."""
    aged = grounding._age({"columns": []}, 3600)
    assert aged["age_seconds"] is None
    assert aged["stale"] is True


def test_missing_object_returns_none_not_an_empty_dict():
    assert grounding.recall_schema(TARGET, "db.nothing") is None


def test_a_corrupt_store_behaves_as_empty_and_never_raises():
    """This is an accelerator; it is never allowed to be the reason a session fails."""
    grounding.remember_schema(TARGET, "db.tbl", {"columns": []})
    path = grounding._path(TARGET, "schema")
    Path(path).write_text("{ this is not json", encoding="utf-8")
    assert grounding.recall_schema(TARGET, "db.tbl") is None
    grounding.remember_schema(TARGET, "db.tbl2", {"columns": []})
    assert grounding.recall_schema(TARGET, "db.tbl2") is not None


def test_forget_schema_targets_one_or_all():
    grounding.remember_schema(TARGET, "db.a", {})
    grounding.remember_schema(TARGET, "db.b", {})
    assert grounding.forget_schema(TARGET, "db.a") == 1
    assert grounding.recall_schema(TARGET, "db.a") is None
    assert grounding.recall_schema(TARGET, "db.b") is not None
    assert grounding.forget_schema(TARGET) == 1


# ---------------------------------------------------------------- DDL invalidation


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE db.tbl ADD c INT",
        "DROP TABLE db.tbl",
        "RENAME TABLE db.tbl TO db.other",
        "CREATE OR REPLACE VIEW db.tbl AS SELECT 1",
    ],
)
def test_ddl_drops_the_cached_object(sql):
    """A column list that survived an ALTER is worse than no cache: it is confidently wrong."""
    grounding.remember_schema(TARGET, "db.tbl", {"columns": [{"name": "a"}]})
    assert grounding.invalidate_for_statement(TARGET, sql) == ["db.tbl"]
    assert grounding.recall_schema(TARGET, "db.tbl") is None


def test_a_read_invalidates_nothing():
    grounding.remember_schema(TARGET, "db.tbl", {"columns": []})
    assert grounding.invalidate_for_statement(TARGET, "SELECT * FROM db.tbl") == []
    assert grounding.recall_schema(TARGET, "db.tbl") is not None


def test_invalidation_matches_the_bare_name_too():
    """DDL often names the object unqualified once the database is the default."""
    grounding.remember_schema(TARGET, "db.sales_fact", {"columns": []})
    assert grounding.invalidate_for_statement(TARGET, "ALTER TABLE sales_fact ADD c INT") == ["db.sales_fact"]


# ---------------------------------------------------------------- the promotion gate


def test_promotion_requires_a_human_confirmation():
    """SQL that runs is not SQL that is correct: a wrong join returning plausible numbers runs fine."""
    with pytest.raises(grounding.PromotionRefused):
        grounding.verify_query(TARGET, "q", "SELECT 1", confirmed_by="", executed_cleanly=True)


def test_promotion_requires_that_it_actually_ran():
    with pytest.raises(grounding.PromotionRefused):
        grounding.verify_query(TARGET, "q", "SELECT 1", confirmed_by="someone", executed_cleanly=False)


@pytest.mark.parametrize("question,sql", [("", "SELECT 1"), ("q", "   ")])
def test_promotion_requires_both_halves(question, sql):
    with pytest.raises(grounding.PromotionRefused):
        grounding.verify_query(TARGET, question, sql, confirmed_by="x", executed_cleanly=True)


def test_a_promoted_query_round_trips_with_its_provenance():
    grounding.verify_query(
        TARGET, "Top 10 sales", "SELECT TOP 10 * FROM db.t",
        confirmed_by="reviewer", executed_cleanly=True, objects=["db.t"], note="checked against finance",
    )
    got = grounding.recall_query(TARGET, "top 10 sales")
    assert got["confirmed_by"] == "reviewer"
    assert got["objects"] == ["db.t"]
    assert got["stale"] is False


# ---------------------------------------------------------------- lookup is mechanical, by design


def test_lookup_normalises_case_and_whitespace_only():
    grounding.verify_query(TARGET, "Top 10 sales", "SELECT 1", confirmed_by="r", executed_cleanly=True)
    assert grounding.recall_query(TARGET, "  TOP   10    SALES ") is not None


def test_lookup_does_not_guess_at_meaning():
    """Deciding two questions mean the same thing is a judgement about meaning.

    That judgement belongs to the model reading `list`, never to keyword or regex logic here. If
    this test ever fails because someone added fuzzy matching, the fix is to remove the matching.
    """
    grounding.verify_query(TARGET, "Top 10 sales", "SELECT 1", confirmed_by="r", executed_cleanly=True)
    assert grounding.recall_query(TARGET, "show me the biggest 10 sales") is None
    assert grounding.recall_query(TARGET, "top 10 sale") is None


def test_list_hands_over_everything_so_the_model_can_choose():
    for q in ("b question", "a question"):
        grounding.verify_query(TARGET, q, "SELECT 1", confirmed_by="r", executed_cleanly=True)
    listed = grounding.list_queries(TARGET)
    assert [e["question"] for e in listed] == ["a question", "b question"]
    assert all("stale" in e and "age_seconds" in e for e in listed)


def test_use_count_increments_without_touching_the_sql():
    grounding.verify_query(TARGET, "q", "SELECT 1", confirmed_by="r", executed_cleanly=True)
    grounding.note_use(TARGET, "q")
    grounding.note_use(TARGET, "q")
    got = grounding.recall_query(TARGET, "q")
    assert got["uses"] == 2 and got["sql"] == "SELECT 1"


def test_re_promoting_keeps_the_use_count():
    grounding.verify_query(TARGET, "q", "SELECT 1", confirmed_by="r", executed_cleanly=True)
    grounding.note_use(TARGET, "q")
    grounding.verify_query(TARGET, "q", "SELECT 2", confirmed_by="r2", executed_cleanly=True)
    got = grounding.recall_query(TARGET, "q")
    assert got["uses"] == 1 and got["sql"] == "SELECT 2"


# ---------------------------------------------------------------- isolation and CLI


def test_two_systems_never_share_a_cache():
    grounding.remember_schema("system-a", "db.t", {"columns": [{"name": "a"}]})
    assert grounding.recall_schema("system-b", "db.t") is None


def test_cli_recall_miss_exits_nonzero_and_says_so(capsys):
    assert grounding.main(["--target", TARGET, "recall-schema", "db.nope"]) == 1
    assert json.loads(capsys.readouterr().out)["found"] is False


def test_cli_refuses_promotion_with_a_distinct_exit_code(capsys):
    code = grounding.main(
        ["--target", TARGET, "verify-query", "--question", "q", "--sql", "SELECT 1", "--confirmed-by", ""]
    )
    assert code == 3
    assert json.loads(capsys.readouterr().out)["promoted"] is False


def test_cli_rejects_json_that_is_not_an_object(capsys):
    assert grounding.main(["--target", TARGET, "remember-schema", "db.t", "--json", "[1,2]"]) == 2


def test_the_store_is_not_world_readable():
    """It records what an operator asked and which objects answered it."""
    grounding.remember_schema(TARGET, "db.t", {})
    mode = os.stat(grounding._path(TARGET, "schema")).st_mode & 0o077
    assert mode == 0, f"store is group/other accessible (mode bits {oct(mode)})"
