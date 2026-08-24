"""What the mapping guard in `_attribute` costs, and the method that can see it.

`_attribute` runs **once per attribute per item**, so a thousand-row `map(_.name)` executes its
mapping test a thousand times. That test was once `isinstance(value, Mapping)` alone, and
`collections.abc.Mapping.__instancecheck__` is Python-level: it dispatches into
`_abc._abc_instancecheck`. `cProfile` over the canonical pipeline put `<frozen abc>.
__instancecheck__` at 300,000 calls for 200 evaluations, about 1,500 per evaluation, with
`_attribute` the third-largest entry by cumulative time.

The fix is `isinstance(value, (dict, Mapping))`. `isinstance` walks a tuple left to right and
stops at the first hit, so a plain `dict` is answered by a C type check and the ABC is never
entered. Behaviour is identical either way, which is the whole reason this file exists: nothing
fails if the tuple is reordered.

## The measurement, 2026-08-24, CPython 3.13.15

Three arms alternated **within one process**, fifteen rounds, medians. Percentages are the gain
over the ABC-only arm. Four independent runs of the harness below, 1,000 rows:

    case          arm                   run 1    run 2    run 3    run 4
    pipeline      (dict, Mapping)      +10.3%    +9.3%    +7.6%   +11.1%
                  type(v) is dict      +10.2%   +10.7%    +6.2%   +10.8%
                  isinstance(v, dict)   +7.9%   +10.8%    +7.4%   +11.1%
    alerting      (dict, Mapping)       +7.1%    +4.9%    +6.3%    +7.1%
                  type(v) is dict       +8.0%    +5.4%    +8.7%    +7.0%
                  isinstance(v, dict)   +7.0%    +9.7%    +8.9%    +8.6%
    map(lower())  (dict, Mapping)       +6.6%    +9.4%    +6.8%    +6.5%
                  type(v) is dict       +6.5%   +10.9%    +8.3%    +5.5%
                  isinstance(v, dict)   +8.6%    +9.9%    +9.0%    +7.4%

**Take 6 to 11%.** The three arms are indistinguishable from each other on plain dicts; every
difference between them is inside the run-to-run spread of a single arm.

They are *not* indistinguishable when the rows are a dict subclass, which is what decided the
spelling. Same method, rows rebuilt as `OrderedDict` and then as `ChainMap`:

    rows           case          type(v) is dict   isinstance(v, dict)
    OrderedDict    alerting                +0.1%               +10.9%
    OrderedDict    map(lower())            -1.2%                +6.5%
    ChainMap       alerting                -0.3%                -0.7%
    ChainMap       map(lower())            -1.4%                -1.2%

`type(v) is dict` is exact, so `OrderedDict`, `Counter` and `defaultdict` miss the fast path and
gain nothing. `isinstance` catches them, and the ChainMap rows show what the wider test costs when
it misses: about 1%, which is inside the noise. So the tuple form wins outright, and it is also
already the house pattern (`_guards._SIZED` and `_guards.HASHABLE_CONTAINERS` are concrete tuples
passed to `isinstance` for exactly this reason).

## Two things the measurement contradicted

**This change was specified as `type(value) is dict`**, on the strength of an earlier reading in
which `isinstance(v, dict)` in this position was worth roughly nothing while the identity test was
worth roughly ten percent. That does not reproduce: four interleaved runs put the two inside each
other's spread on every shape. The shipped line is the one the re-measurement chose rather than the
one it was specified with, which is the same lesson as the paragraph below it, arriving twice.

**Patching `Evaluator._attribute` measures nothing.** `_DISPATCH` is built in the class body and
binds the original function objects, so `_eval` keeps calling the unpatched handler and the A/B
comes out at 0.5% however large the real difference is. The harness below swaps
`Evaluator._DISPATCH` instead. Worth knowing well beyond this file: **overriding a node handler on
`Evaluator`, or on a subclass of it, silently has no effect.**

## Why a `timeit` is not evidence here

In isolation the three checks measure 27.4ns, 27.9ns and 99.9ns, which predicts a gain but not its
size, and predicts nothing at all about the tuple's ordering. A fast path is worth what it saves
*in the branch it short-circuits*, so it has to be measured in place. Running arm A eleven times
and then arm B eleven times is not enough either: on this box that lands the drift on whichever
went second and produced a +15% reading for a change that touched nothing. Alternate within one
process, take medians, and re-run before believing anything under 10%.

**The durable guard is not in this file.** A timing gate drifts and gets raised; a call count does
not. `tests/test_eval.py::TestTheMappingFastPath` asserts that a plain dict reaches the ABC
**zero** times and a `ChainMap` reaches it once per row, and reads the tuple's order off the
source. Those fail on a reorder. This file is the receipt for the number.
"""

from __future__ import annotations

import ast
import inspect
import statistics
import textwrap
import time
from collections import ChainMap, OrderedDict
from typing import Any

import pytest

import safeexpr._eval as eval_module
from safeexpr import Evaluator, standard_registry

ROWS: list[dict[str, Any]] = [
    {
        "id": n,
        "customer_id": f"c{n % 50}",
        "status": "paid" if n % 3 else "open",
        "value": (n * 31) % 500,
        "items": [1, 2],
        "name": f"Ünïcödé Näme {n}",
    }
    for n in range(1000)
]

# The three shapes this was measured on. All of them read a field per item, which is what makes
# them attribute-bound rather than merely collection-bound.
CASES: dict[str, str] = {
    "pipeline": (
        'rows | where(_.status == "paid") | group_by(_.customer_id)'
        ' | map(merge(_, {"n": len(_.items)}))'
    ),
    "alerting": "rows | where(_.value > threshold) | first",
    "map_lower": "rows | map(lower(_.name))",
}


# **The dispatch table, not the method.** `_DISPATCH` is built in the class body and binds the
# original function objects, so `Evaluator._eval` keeps calling the unpatched handler however
# thoroughly `Evaluator._attribute` is replaced. An A/B written the obvious way measures 0.5% and
# is wrong by a factor of twenty. Reached once here, so the rest of this file reads plainly.
_HANDLERS = Evaluator._DISPATCH  # noqa: SLF001


@pytest.fixture(scope="module")
def ev() -> Evaluator:
    return Evaluator(registry=standard_registry())


@pytest.mark.benchmark
@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("kind", ["dict", "OrderedDict", "ChainMap"])
def test_attribute_hot_path(benchmark: Any, ev: Evaluator, case: str, kind: str) -> None:
    """Both halves of the guard, watched.

    `dict` takes the concrete half and `ChainMap` the ABC half; `OrderedDict` is the row that
    decided the spelling, since it is a dict subclass and therefore only takes the fast path
    under `isinstance` rather than under an identity test.
    """
    rows = _rebuilt(kind)
    benchmark.group = f"{case}:{kind}"
    result = benchmark(ev.evaluate, CASES[case], {"rows": rows, "threshold": 250})
    assert result is not None


def _rebuilt(kind: str) -> list[Any]:
    build = {"dict": dict, "OrderedDict": OrderedDict, "ChainMap": lambda row: ChainMap(dict(row))}
    return [build[kind](row) for row in ROWS]


def _abc_only_handler() -> Any:
    """The shipped `_attribute` with the fast path taken back out, built from its own source.

    Transformed rather than transcribed, so the arm cannot drift away from the code it is being
    compared against: a hand-written copy of "the old handler" stops being the old handler the
    first time anything else in `_attribute` changes, and the comparison silently starts measuring
    that instead.
    """
    source = textwrap.dedent(inspect.getsource(Evaluator._attribute))  # noqa: SLF001
    swapped = source.replace("isinstance(value, (dict, Mapping))", "isinstance(value, Mapping)")
    assert swapped != source, (
        "the mapping guard in `_attribute` is no longer the line this arm removes, so the A/B "
        "below would compare the handler against an identical copy of itself and report 0%"
    )
    namespace: dict[str, Any] = dict(vars(eval_module))
    # The one `exec` in the suite. It compiles a source-transformed copy of a function this
    # repository ships, in that module's own namespace, to measure it against itself. Nothing
    # user-controlled reaches it.
    exec(compile(swapped, "<abc-only>", "exec"), namespace)  # noqa: S102
    return namespace["_attribute"]


def ab_medians(rounds: int = 15, reps: int = 3) -> dict[str, tuple[float, float]]:
    """Interleaved medians for the shipped guard against the ABC-only arm.

    Alternates the two handlers **within one process**, round by round, so drift lands on both
    arms rather than on whichever went second.

    Args:
        rounds: How many alternations to take the median over.
        reps: Evaluations per sample.

    Returns:
        Case name to (ABC-only seconds, shipped seconds), both medians.
    """
    evaluator = Evaluator(registry=standard_registry())
    context = {"rows": ROWS, "threshold": 250}
    arms = {"abc": _abc_only_handler(), "fast": _HANDLERS[ast.Attribute]}
    samples: dict[tuple[str, str], list[float]] = {
        (case, arm): [] for case in CASES for arm in arms
    }

    def once(source: str) -> float:
        started = time.perf_counter()
        for _ in range(reps):
            evaluator.evaluate(source, context)
        return (time.perf_counter() - started) / reps

    try:
        for arm, handler in arms.items():  # noqa: B007 - warm both arms before measuring
            _HANDLERS[ast.Attribute] = handler
            for source in CASES.values():
                once(source)
        for _ in range(rounds):
            for case, source in CASES.items():
                for arm, handler in arms.items():
                    _HANDLERS[ast.Attribute] = handler
                    samples[case, arm].append(once(source))
    finally:
        _HANDLERS[ast.Attribute] = arms["fast"]
    return {
        case: (statistics.median(samples[case, "abc"]), statistics.median(samples[case, "fast"]))
        for case in CASES
    }


@pytest.mark.benchmark
def test_the_fast_path_is_not_slower_than_the_abc_alone() -> None:
    """**Direction, not magnitude, and the difference is the point.**

    The measured gain is 6 to 11%, and this box's own noise floor is above the project's 10%
    regression gate: `test_scalar_tiers_bench.py` records two runs of identical code differing by
    12% on a single row. So asserting the 6 to 11% here would be asserting something this machine
    cannot reliably see, and it would be disabled within a month of the first false red.

    What is assertable is the sign. Interleaving removes the drift that would otherwise decide it,
    and a fast path that had stopped short-circuiting would show up as *equal*, not as slower, so
    the tolerance below is deliberately loose. **The test that fails on a real regression is the
    call count in `tests/test_eval.py`, not this one.**
    """
    results = ab_medians(rounds=7)
    slower = {case: (abc, fast) for case, (abc, fast) in results.items() if fast > abc * 1.15}
    assert not slower, (
        f"the shipped guard measured more than 15% slower than the ABC-only arm on {slower}. "
        f"Interleaved medians, so this is not drift. Re-run `ab_medians()` before acting on it."
    )


@pytest.mark.benchmark
def test_the_two_arms_agree_on_every_answer() -> None:
    """A faster arm that computed something else would not be a win.

    Cheap to assert and worth asserting: the ABC-only arm above is built by rewriting the shipped
    handler's source, and a transformation that broke it would otherwise show up as a very good
    benchmark number.
    """
    evaluator = Evaluator(registry=standard_registry())
    abc_only = _abc_only_handler()
    shipped = _HANDLERS[ast.Attribute]
    for kind in ("dict", "OrderedDict", "ChainMap"):
        context = {"rows": _rebuilt(kind), "threshold": 250}
        for source in CASES.values():
            try:
                _HANDLERS[ast.Attribute] = abc_only
                before = evaluator.evaluate(source, context)
                _HANDLERS[ast.Attribute] = shipped
                after = evaluator.evaluate(source, context)
            finally:
                _HANDLERS[ast.Attribute] = shipped
            assert before == after, f"{kind}: `{source}` disagreed across the two arms"
