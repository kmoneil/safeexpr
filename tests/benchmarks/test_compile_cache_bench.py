"""What compiling once instead of every call is worth, cold and warm, side by side.

`evaluate()` parses, rewrites and validates the source. All of that depends only on
`(source, registry)`, and the registry is fixed when the evaluator is built, so all of it is work
that can be done once. Measured on 3.13.15 before the cache existed, medians of eleven interleaved
rounds, with the phases timed separately:

    case            total    parse   shadow  transform  validate     eval    fixed
    feature flag     33.1      6.9      8.5        7.1      13.1      2.7    91.7%
    authorization    28.8      5.1      7.6        7.1      12.2      2.5    91.4%
    workflow         25.8      4.9      6.8        6.2      10.7      2.3    91.2%
    alerting 1k   1,061.0      6.0      7.7        8.9      11.0  1,031.2     2.8%
    pipeline 1k   1,641.1     15.7     16.8       20.5      24.8  1,568.1     4.4%

Microseconds. `docs/performance.md` says "a rule that does not touch a collection is free: a
feature flag is eleven steps whether your context holds ten records or a hundred thousand." Those
eleven steps cost **2.7 us** and the call cost **33.1 us**.

With the cache, same box, medians of eleven rounds:

    case            cold     warm   speedup
    feature flag    40.6      2.9    14.00x
    authorization   38.4      2.8    13.68x
    workflow        34.0      2.6    13.34x
    alerting 1k  1,193.3  1,105.4     1.08x
    pipeline 1k  1,704.9  1,609.9     1.06x

**The two collection rows are not claimed as a win.** Their fixed cost was already under 5%, so
there is nothing there to remove, and both figures are inside this box's noise. The flat rows are
what this is about, and `docs/performance.md` says they are most rules in most systems.

The cold column is *slower* than the pre-cache total, by about 20%, and that is real: a cold call
now also builds the entry and stores it. It is paid once per distinct source per evaluator and it
is why the cold row is measured here rather than assumed away.

**The guard is not this file.** A benchmark drifts and its threshold gets raised. `ast.parse`
running exactly once across two hundred evaluations does not, and that is asserted in
`tests/test_compile_cache.py`, which is where a bypass of the cache will actually be caught.
"""

from __future__ import annotations

from typing import Any

import pytest

from safeexpr import Evaluator, standard_registry

ITEMS = 1000
CONTEXT: dict[str, Any] = {
    "user": {"plan": "pro", "region": "eu"},
    "resource": {"owner_id": 7},
    "principal": {"id": 3, "roles": ["admin"]},
    "event": {"type": "deploy", "env": "staging"},
    "metrics": [{"value": n} for n in range(ITEMS)],
    "threshold": ITEMS // 2,
    "orders": [
        {"customer_id": f"c{n % 50}", "status": "paid" if n % 3 else "open", "items": [1, 2]}
        for n in range(ITEMS)
    ],
}

# The design's canonical five: three that touch no collection and two that do.
CASES: dict[str, str] = {
    "feature_flag": 'user.plan == "pro" and user.region in ["us", "eu"]',
    "authorization": 'resource.owner_id == principal.id or "admin" in principal.roles',
    "workflow": 'event.type == "deploy" and event.env != "prod"',
    "alerting": "metrics | where(_.value > threshold) | first",
    "pipeline": (
        'orders | where(_.status == "paid") | group_by(_.customer_id)'
        ' | map(merge(_, {"n": len(_.items)}))'
    ),
}


@pytest.fixture(scope="module")
def ev() -> Evaluator:
    return Evaluator(registry=standard_registry())


@pytest.mark.benchmark
@pytest.mark.parametrize("case", list(CASES))
def test_warm(benchmark: Any, ev: Evaluator, case: str) -> None:
    """The steady state: what a host actually pays after the first call for a source."""
    source = CASES[case]
    ev.evaluate(source, CONTEXT)
    benchmark.group = f"compile_cache:{case}"
    benchmark.name = f"warm:{case}"
    assert benchmark(ev.evaluate, source, CONTEXT) is not None


@pytest.mark.benchmark
@pytest.mark.parametrize("case", list(CASES))
def test_cold(benchmark: Any, ev: Evaluator, case: str) -> None:
    """The first call for a source, measured rather than assumed away.

    `pedantic` with a setup that empties the cache, because an ordinary `benchmark(...)` call
    would warm it on its first round and measure the warm path for every round after.
    """
    source = CASES[case]
    benchmark.group = f"compile_cache:{case}"
    benchmark.name = f"cold:{case}"

    def setup() -> tuple[tuple[Any, ...], dict[str, Any]]:
        ev._cache.clear()  # noqa: SLF001
        return (source, CONTEXT), {}

    assert benchmark.pedantic(ev.evaluate, setup=setup, rounds=200) is not None
