"""Timings for the tier's hot paths.

What makes these hot rather than merely slow is the shape of the work: a predicate in `where` is
an AST subtree evaluated once per item, so a thousand-row filter is a thousand walks of the same
tree. That is the design's "parse once, evaluate N times" claim, and it is the number worth
watching: a regression here is a regression in the only loop this package has.

`bare_comparison` is not a collections benchmark. It is the reference point, the cost of one
parse, validate and evaluate with no collection involved, so a change in the numbers below can be
attributed to the tier rather than to the evaluator underneath it.

Measured over the thousand rows below, mean per evaluation, on the machine the tier was written
on:

    bare_comparison        18 us     one expression, no collection
    pluck                 142 us     a thousand dict lookups, no tree walked per item
    max_by                504 us
    map                   505 us
    unique_by             542 us
    sort_by               573 us
    group_by              576 us
    where                 914 us     a bigger predicate tree than map's, walked a thousand times
    where_then_map      1,139 us
    canonical_pipeline  1,497 us     the design's fourth canonical use case
    nested_predicate   11,553 us     twenty thousand evaluations, and it shows

Two of those are worth reading rather than skimming. **`pluck` is 3.5x faster than the `map`
that does the same thing**, because it reads a key directly instead of walking an AST per item;
that is a better argument for it existing than the dynamic field name it was added for. And
**`where` costs nearly twice `map`** on the same thousand rows, which is not the loop being
slower but the predicate being a larger tree: the per-item cost here is the size of the
expression, not the size of the item.

These are absolute figures on one noisy box, where a mean can move 15% between runs. Treat them
as an order of magnitude and a ranking, and compare like against like with
`--benchmark-compare` on the same machine.
"""

from __future__ import annotations

from typing import Any

import pytest

from safeexpr import Evaluator, standard_registry

ROWS: list[dict[str, Any]] = [
    {"id": n, "team": f"team-{n % 20}", "score": (n * 7919) % 1000, "status": "paid"}
    for n in range(1000)
]


@pytest.fixture(scope="module")
def ev() -> Evaluator:
    return Evaluator(registry=standard_registry())


@pytest.mark.benchmark
@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("bare_comparison", 'rows[0].status == "paid"'),
        ("where", "rows | where(_.score > 500)"),
        ("map", "rows | map(_.team)"),
        ("where_then_map", "rows | where(_.score > 500) | map(_.id)"),
        ("sort_by", "rows | sort_by(_.score)"),
        ("group_by", "rows | group_by(_.team)"),
        ("unique_by", "rows | unique_by(_.team)"),
        ("pluck", 'rows | pluck("team")'),
        ("max_by", "rows | max_by(_.score)"),
        (
            "canonical_pipeline",
            'rows | where(_.status == "paid") | group_by(_.team) '
            '| map(merge(_, {"n": len(_.items)}))',
        ),
        ("nested_predicate", "rows | where(groups | any_(_ == _2.team))"),
    ],
    ids=lambda value: value if isinstance(value, str) and " " not in value else "",
)
def test_tier_hot_path(benchmark: Any, ev: Evaluator, name: str, source: str) -> None:
    context = {"rows": ROWS, "groups": [f"team-{n}" for n in range(20)]}
    benchmark.group = name
    result = benchmark(ev.evaluate, source, context)
    assert result is not None
