"""Timings for the tier's hot paths.

What makes these hot rather than merely slow is the shape of the work: a predicate in `where` is
an AST subtree evaluated once per item, so a thousand-row filter is a thousand walks of the same
tree. That is the design's "parse once, evaluate N times" claim, and it is the number worth
watching: a regression here is a regression in the only loop this package has.

`bare_comparison` is not a collections benchmark. It is the reference point, the cost of one
parse, validate and evaluate with no collection involved, so a change in the numbers below can be
attributed to the tier rather than to the evaluator underneath it.

Measured over the thousand rows below, mean per evaluation, on the machine this was written on.
The second column is before the step budget existed and the third is with it:

                        no budget   with budget
    bare_comparison         17 us        19 us     one expression, no collection
    pluck                  129 us       137 us     dict lookups; no tree walked per item
    max_by                 525 us       577 us
    map                    465 us       632 us
    unique_by              530 us       598 us
    sort_by                540 us       620 us
    group_by               542 us       637 us
    where                  892 us     1,153 us     a bigger predicate tree than map's
    where_then_map       1,166 us     1,344 us
    canonical_pipeline   1,545 us     1,807 us     the design's fourth canonical use case
    nested_predicate    12,047 us    14,067 us     twenty thousand evaluations, and it shows

**The budget costs roughly 10 to 17%, and that column is the price of the guarantee.** It is a
counter read and written once per node evaluated, about 30ns against roughly 80ns for the
dispatch beside it, and no spelling measured cheaper: a chained assignment, a countdown tested
for truthiness and a list cell all came out the same. Paying it buys the only bound on *work*
this package has; without it `map(a, where(b, _ == _2))` is O(n*m) from a thirty-character
source, which is the denial of service expr-lang shipped.

`pluck` is the control that proves where the cost goes: it walks no tree per item, and it is the
one row that barely moves. Everything charged per item moves by roughly the ratio of nodes
evaluated to work done.

Two of those rows are worth reading rather than skimming. **`pluck` is far faster than the `map`
that does the same thing**, because it reads a key directly instead of walking an AST per item;
that is a better argument for it existing than the dynamic field name it was added for. And
**`where` costs more than `map`** on the same thousand rows, which is not the loop being slower
but the predicate being a larger tree: the per-item cost here is the size of the expression, not
the size of the item.

Two of those are worth reading rather than skimming. **`pluck` is 3.5x faster than the `map`
that does the same thing**, because it reads a key directly instead of walking an AST per item;
that is a better argument for it existing than the dynamic field name it was added for. And
**`where` costs nearly twice `map`** on the same thousand rows, which is not the loop being
slower but the predicate being a larger tree: the per-item cost here is the size of the
expression, not the size of the item.

These are absolute figures on one noisy box, where a mean can move 15% between runs, so the
per-row percentages above are worth less than the shape of the table. Treat them as an order of
magnitude and a ranking, and compare like against like with `--benchmark-compare` on the same
machine.
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
