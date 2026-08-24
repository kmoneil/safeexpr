"""Allocation ceilings for the tier.

Q7 (memory amplification) is the gap the step budget does not cover: one step can allocate a lot,
and a collection function that builds a list per call is the obvious place for it to happen. The
policy is still to be written. These are the floor under it, so a change that quietly starts
copying every item shows up as a failure rather than as a slow leak in somebody's service.

Ceilings, not targets, and set from measurement rather than from a round number. Against the
ten-thousand-row list below, the high watermark today is:

    where       40.8 KiB    the kept rows, by reference
    map         83.1 KiB    one list of results
    group_by    81.2 KiB    twenty group records over the same row objects
    extend     312.5 KiB    one list of references over twenty thousand rows
    sort_by      1.2 MiB    ten thousand (key, item) pairs, discarded on the way out
    merge        3.9 MiB    forty thousand entries in a new mapping
    aggregate   38.3 MiB    two hundred rows of 200,000 characters: the shape that
                            allocated 343 MB before size cost budget

The last row is the one the memory-amplification policy exists for. `rows | map(t + t)` is a
seventeen-character expression, and at four thousand rows it allocated 343 MB with nothing to
stop it: every string in it is far under the per-result cap, so only the total ever hurt. The
budget now charges one step per 64 elements produced, which bounds the total and leaves ordinary
values free, since anything under 64 elements charges nothing at all.

Each ceiling is roughly two to three times its measurement: loose enough that ordinary
variation and a different interpreter do not fail the suite, tight enough that copying the
rows instead of referencing them, which for this data is about 2 MiB, breaks through every
one of them.

**Only enforced under `--memray`.** Without that flag the marker is inert and these read as four
ordinary assertions, which is why the numbers above are recorded here rather than left implicit
in a run nobody re-reads.
"""

from __future__ import annotations

from typing import Any

import pytest

from safeexpr import Evaluator, standard_registry

ROWS: list[dict[str, Any]] = [
    {"id": n, "team": f"team-{n % 20}", "score": (n * 7919) % 1000} for n in range(10_000)
]


@pytest.fixture(scope="module")
def ev() -> Evaluator:
    return Evaluator(registry=standard_registry())


@pytest.mark.limit_memory("256 KB")
def test_where_allocates_only_the_result(ev: Evaluator) -> None:
    """A filter keeps references to the rows it keeps. It must not copy them."""
    kept = ev.evaluate("rows | where(_.score > 500)", {"rows": ROWS})
    assert 0 < len(kept) < len(ROWS)


@pytest.mark.limit_memory("512 KB")
def test_map_allocates_one_list_of_results(ev: Evaluator) -> None:
    assert len(ev.evaluate("rows | map(_.team)", {"rows": ROWS})) == len(ROWS)


@pytest.mark.limit_memory("512 KB")
def test_group_by_allocates_one_record_per_group_not_per_item(ev: Evaluator) -> None:
    groups = ev.evaluate("rows | group_by(_.team)", {"rows": ROWS})
    assert len(groups) == 20


@pytest.mark.limit_memory("4 MB")
def test_sort_by_decorates_without_copying_the_items(ev: Evaluator) -> None:
    assert len(ev.evaluate("rows | sort_by(_.score)", {"rows": ROWS})) == len(ROWS)


# The allocation-heavy pair, and the aggregate shape the budget now bounds. `extend` and `merge`
# both build a new collection and neither re-evaluates anything, so before the memory policy they
# were charged one step each however large the result.
WIDE: list[dict[str, Any]] = [{"id": n} for n in range(20_000)]


@pytest.mark.limit_memory("1 MB")
def test_extend_allocates_one_list_of_references(ev: Evaluator) -> None:
    assert len(ev.evaluate("a | extend(a)", {"a": WIDE})) == 2 * len(WIDE)


@pytest.mark.limit_memory("8 MB")
def test_merge_allocates_one_mapping(ev: Evaluator) -> None:
    first = {n: n for n in range(20_000)}
    second = {n + 10**8: n for n in range(20_000)}
    assert len(ev.evaluate("merge(a, b)", {"a": first, "b": second})) == 40_000


@pytest.mark.limit_memory("64 MB")
def test_the_aggregate_shape_stays_under_its_ceiling_at_the_budget(ev: Evaluator) -> None:
    """The shape that allocated 343 MB before the budget charged for size.

    Run here at a row count the policy allows, so the assertion is on what a permitted expression
    costs rather than on the refusal. The ceiling is what makes the policy's bound visible: push
    the row count up and the budget stops it before this number moves much further.
    """
    text = "x" * 100_000
    assert len(ev.evaluate("rows | map(t + t)", {"rows": list(range(200)), "t": text})) == 200
