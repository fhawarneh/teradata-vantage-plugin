"""Tests for the workflow scripts' agent fan-out — workflows/*.js.

The workflow runtime kills a run once it has spawned 1000 agents over its lifetime, and a run
that dies mid-stage returns NO report: everything already done is lost. Every stage that fans
out must therefore be bounded by a constant in the script, and whatever the bound drops must be
named in a ``log()`` call — a silent cap reads as "covered everything".

These tests read the scripts as text (there is no JS runtime in the test environment) and
reproduce each workflow's own arithmetic from the constants it declares.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = PLUGIN_ROOT / "workflows"

#: The runtime's lifetime cap. A workflow must stay strictly under it in its worst case.
RUNTIME_AGENT_CAP = 1000

#: The runtime rejects a single parallel()/pipeline() call with more than this many items.
RUNTIME_ITEMS_PER_CALL = 4096


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _const(source: str, name: str) -> int:
    hit = re.search(r"^const\s+" + name + r"\s*=\s*(\d+)", source, re.MULTILINE)
    assert hit, f"{name} is not declared as a numeric const"
    return int(hit.group(1))


def test_every_workflow_is_present():
    names = sorted(p.name for p in WORKFLOWS.glob("*.js"))
    assert names == ["drop-impact.js", "health-audit.js", "profile-database.js", "sql-review.js"]


# --------------------------------------------------------------------------- sql-review
def test_sql_review_bounds_the_verification_fan_out():
    """The candidate fan-out is capped. Before the fix only the INPUTS were capped."""
    src = _read("sql-review.js")
    assert "const AGENT_BUDGET" in src, "sql-review must declare an explicit agent budget"
    assert "MAX_CANDIDATES" in src, "sql-review must bound the candidate fan-out"
    assert "candidates.length = MAX_CANDIDATES" in src, "the bound must actually truncate the list"


def test_sql_review_names_what_the_bound_drops():
    """No silent caps: the dropped candidates are listed, with the remedy."""
    src = _read("sql-review.js")
    block = src[src.index("const MAX_CANDIDATES"):src.index("phase('Verify')")]
    assert "DROPPING" in block
    assert "NOT covered by this report" in block
    assert "dropped.map(" in block, "the log must name each dropped candidate, not just count them"
    assert "votes: 1" in block, "the log must offer the remedy that widens coverage"


def test_sql_review_never_downgrades_votes_silently():
    """`need` is 2-of-3 at votes:3. Auto-lowering VOTES would void the advertised guarantee."""
    src = _read("sql-review.js")
    assert re.search(r"const VOTES = a\.votes === 1 \? 1 : 3", src)
    assert "VOTES = 1" not in src.replace("const VOTES", ""), "VOTES must never be reassigned"
    assert "const need = VOTES === 3 ? 2 : 1" in src


def _sql_review_worst_case(files_given: bool, files: int, candidates: int, votes: int) -> int:
    """Reproduce the script's arithmetic: collect + one reviewer per file + votes*verified + report."""
    src = _read("sql-review.js")
    budget = _const(src, "AGENT_BUDGET")
    spent = (0 if files_given else 1) + files + 1
    max_candidates = max(1, (budget - spent) // votes)
    return (0 if files_given else 1) + files + votes * min(candidates, max_candidates) + 1


@pytest.mark.parametrize("votes", [1, 3])
@pytest.mark.parametrize("files_given", [True, False])
def test_sql_review_worst_case_stays_under_the_runtime_cap(votes, files_given):
    """At the hard file cap and an unbounded reviewer harvest, the run still finishes."""
    hard_max_files = _const(_read("sql-review.js"), "HARD_MAX_FILES")
    worst = _sql_review_worst_case(files_given, hard_max_files, 100_000, votes)
    assert worst < RUNTIME_AGENT_CAP, f"{worst} agents would exceed the {RUNTIME_AGENT_CAP} cap"


def test_sql_review_default_run_would_have_blown_the_cap_unbounded():
    """The regression this pins: 100 files x 300 candidates x 3 votes was 1002 agents."""
    unbounded = 1 + 100 + 3 * 300 + 1
    assert unbounded > RUNTIME_AGENT_CAP
    assert _sql_review_worst_case(False, 100, 300, 3) < RUNTIME_AGENT_CAP


def test_sql_review_report_failure_does_not_discard_the_findings():
    """The final bare `await agent()` is the abort site; a throw there must not lose the run."""
    src = _read("sql-review.js")
    tail = src[src.index("phase('Report')"):]
    assert "try {" in tail and "} catch (e) {" in tail
    assert "survivors: report ? undefined : survivors" in tail


# --------------------------------------------------------------------------- the other three
# (workflow, hard-cap constant, agents per item, fixed agents: discovery + report)
BOUNDED = [
    ("health-audit.js", "HARD_MAX_DB", 1, 3),        # discover + system probe + report
    ("profile-database.js", "HARD_MAX_TABLES", 2, 2),  # discover + report; probe + grade per table
    ("drop-impact.js", "MAX_OBJECTS", 5, 1),         # report; resolve + 3 probes + assess per object
]


@pytest.mark.parametrize("script,cap_name,per_item,fixed", BOUNDED)
def test_other_workflows_bound_their_fan_out(script, cap_name, per_item, fixed):
    src = _read(script)
    cap = _const(src, cap_name)
    assert cap > 0
    assert f".slice(0, {cap_name})" in src or f".slice({cap_name})" in src or f"slice(0, MAX" in src, (
        f"{script} must truncate its work list against {cap_name}"
    )
    worst = cap * per_item + fixed
    assert worst < RUNTIME_AGENT_CAP, f"{script}: {worst} agents would exceed the {RUNTIME_AGENT_CAP} cap"
    assert cap < RUNTIME_ITEMS_PER_CALL


@pytest.mark.parametrize("script,cap_name,per_item,fixed", BOUNDED)
def test_other_workflows_name_what_they_skip(script, cap_name, per_item, fixed):
    src = _read(script)
    assert "SKIPPING" in src, f"{script} must log the work its cap drops"
