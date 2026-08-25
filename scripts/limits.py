#!/usr/bin/env python
"""Where every limit in this package comes from.

Nearly every constant here is a number somebody could have picked out of the air, so each one is
set from a measurement instead, at **ten times observed need or more**. This script is that
measurement, in the repository and runnable by anybody:

    python scripts/limits.py            # the table
    python scripts/limits.py --json     # the same numbers, for a machine
    python scripts/limits.py --quick    # skip the 100,000-item runs

`tests/test_limits.py` imports the same functions and asserts the ten-times property, so the
justification is checked by the suite rather than written in a comment that can quietly stop
being true.

**Three things this measures that are easy to get wrong.**

*Steps per item*, because a budget is meaningless without it. The design originally proposed
100,000 steps, which at the measured 4 to 6 steps per item covers about twenty thousand items and
raises on a hundred thousand: the default has to come from the workload, not from a round number.

*Observed need for each cap*, separately from where the interpreter gives out. A cap has a floor
(what real expressions and real data require) and a ceiling (where CPython stops coping), and
both have to be measured before a number between them means anything.

*Time per charged step*, which is the only way to find work the budget cannot see. A function that
walks its input in C without evaluating anything per item is nearly free to the counter and not
at all free to the machine, and that gap is invisible unless it is measured directly. It is how
`rows | map(sum(nums))` was found buying eighteen minutes of work from the default budget.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from safeexpr import BudgetExceededError, Evaluator, standard_registry
from safeexpr._eval import DEFAULT_STEP_BUDGET, MAX_COMPILE_CACHE, MAX_POWER_RESULT_BITS
from safeexpr._guards import (
    MAX_DATA_NESTING,
    MAX_RESULT_SIZE,
    SIZE_CHARGE_UNIT,
)
from safeexpr._parse import MAX_SOURCE_BYTES, parse
from safeexpr._pipes import pipe_targets, transform
from safeexpr._validate import MAX_EXPRESSION_DEPTH, validate

# DESIGN section 3's five, which are what the package promises to serve.
CANONICAL: tuple[tuple[str, str], ...] = (
    ("feature flag", 'user.plan == "pro" and user.region in ["us", "eu"]'),
    ("alerting rule", "metrics | where(_.value > threshold) | first"),
    ("authorization", 'resource.owner_id == principal.id or "admin" in principal.roles'),
    (
        "pipeline",
        'orders | where(_.status == "paid") | group_by(_.customer_id)'
        ' | map(merge(_, {"n": len(_.items)}))',
    ),
    ("workflow", 'event.type == "deploy" and event.env != "prod"'),
)

# Rules deliberately more tangled than the canonical five, to find the ceiling on how deeply a
# real expression nests rather than how deeply a simple one does.
COMPLEX: tuple[tuple[str, str], ...] = (
    (
        "gnarly authorization",
        'resource.owner_id == principal.id or ("admin" in principal.roles and'
        ' resource.env != "prod") or (principal.team == resource.team and'
        ' resource.visibility in ["team", "public"])',
    ),
    (
        "nested predicate",
        'customers | where(_.orders | any_(_.total > _2.threshold and _.status == "paid"))'
        " | map(_.name)",
    ),
    (
        "pipeline with a computed field",
        'orders | where(_.status == "paid" and _.total > floor) | group_by(_.region)'
        ' | map(merge(_, {"n": len(_.items), "top": _.items | sort_by(_.total, True) | first}))',
    ),
    (
        "string munging chain",
        'users | map(slugify(lower(strip(_.name)) + "-" + str(_.id))) | unique_by(_) | take(20)',
    ),
)

# Contexts a host might really pass, for the data-nesting cap.
SHAPES: tuple[tuple[str, Any], ...] = (
    ("an order", {"customer_id": "c1", "status": "paid", "items": [{"sku": "a", "qty": 2}]}),
    (
        "a webhook payload",
        {
            "repository": {"owner": {"login": "a"}, "topics": ["x"]},
            "commits": [{"author": {"name": "n"}, "files": [{"path": "p"}]}],
        },
    ),
    ("a deep config tree", {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}),
)


def context_for(items: int) -> dict[str, Any]:
    """Build a context of the given size for the canonical use cases."""
    return {
        "user": {"plan": "pro", "region": "eu"},
        "metrics": [{"value": n} for n in range(items)],
        "threshold": items // 2,
        "resource": {"owner_id": 7},
        "principal": {"id": 3, "roles": ["admin"]},
        "orders": [
            {
                "customer_id": f"c{n % 50}",
                "status": "paid" if n % 3 else "open",
                "items": [1, 2],
            }
            for n in range(items)
        ],
        "event": {"type": "deploy", "env": "staging"},
    }


def steps_for(source: str, context: dict[str, Any]) -> int:
    """The smallest budget the expression completes under: what it costs, in steps.

    Measured through the public surface rather than by reading the counter, so this is the number
    a host would observe. Doubling to find a ceiling and then bisecting costs a few dozen
    evaluations.

    Args:
        source: The expression.
        context: The names available to it.

    Returns:
        The step count.
    """

    def completes(budget: int) -> bool:
        try:
            Evaluator(registry=standard_registry(), budget=budget).evaluate(source, context)
        except BudgetExceededError:
            return False
        return True

    high = 1
    while not completes(high):
        high *= 2
    low, ceiling = high // 2 + 1, high
    while low < ceiling:
        middle = (low + ceiling) // 2
        if completes(middle):
            ceiling = middle
        else:
            low = middle + 1
    return low


def seconds_for(source: str, context: dict[str, Any], rounds: int = 5) -> float:
    """The smallest wall time observed for one evaluation.

    **Minimum rather than median, which is the rule this project already settled on** for every
    wall-clock assertion in the suite: interference can only ever add time and never remove it, so
    the smallest observation is the closest thing to an operation's own cost, while a genuinely
    slow operation is slow in the minimum too and nothing is weakened.

    This function was the exception, and it was an exception because it lives in `scripts/`
    rather than in `tests/` and the sweep that hardened the rest did not reach it. The median of
    three samples flaked `test_the_aggregates_are_within_a_small_factor` roughly one run in five
    on a busy machine, which is a test nobody would keep believing.
    """
    evaluator = Evaluator(registry=standard_registry())
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        evaluator.evaluate(source, context)
        samples.append(time.perf_counter() - started)
    return min(samples)


def expression_depth(source: str) -> int:
    """How deeply an expression nests, by the same count the validator's cap uses."""

    def walk(node: ast.AST, depth: int = 0) -> int:
        children = list(ast.iter_child_nodes(node))
        return depth if not children else max(walk(child, depth + 1) for child in children)

    return walk(parse(source))


def data_depth(value: Any, depth: int = 0, limit: int = 200) -> int:
    """How deeply a value nests, stopping at `limit` so a cycle cannot run away."""
    if depth >= limit:
        return depth
    if isinstance(value, dict):
        return max((data_depth(v, depth + 1, limit) for v in value.values()), default=depth)
    if isinstance(value, (list, tuple)):
        return max((data_depth(v, depth + 1, limit) for v in value), default=depth)
    return depth


def workload(items: int) -> dict[str, dict[str, float]]:
    """Steps and seconds for each canonical use case at one size."""
    context = context_for(items)
    return {
        label: {"steps": steps_for(source, context), "seconds": seconds_for(source, context)}
        for label, source in CANONICAL
    }


def observed_need(items: int = 100_000) -> dict[str, int]:
    """The floor each cap has to clear, measured rather than assumed.

    Args:
        items: How large a context to measure against. The two scale-dependent entries,
            `result_size` and `steps`, are proportional to it; the two depth entries are not.
            `tests/test_limits.py` measures at a tenth of this and scales, so the suite stays
            quick while the published run stays at the committed size.

    Returns:
        The measured floor for each cap.
    """
    rules = [source for _, source in CANONICAL] + [source for _, source in COMPLEX]
    context = context_for(items)
    evaluator = Evaluator(registry=standard_registry())
    largest = max(
        len(evaluator.evaluate(source, context))
        for source in (
            "metrics | where(_.value > threshold)",
            'orders | where(_.status == "paid")',
            "orders | map(_.customer_id)",
        )
    )
    return {
        "expression_depth": max(expression_depth(source) for source in rules),
        "data_depth": max(data_depth(shape) for _, shape in SHAPES),
        "result_size": largest,
        "steps": max(entry["steps"] for entry in workload(items).values()),  # type: ignore[misc]
    }


# **How many rounds `blind_spots` takes, and it is not the five everything else takes.**
#
# What that function reports is a *ratio* of two independent minimums, and the reference in the
# denominator is the fragile half: a reference that happens to measure fast inflates every ratio
# above it. Measured on two hosted runners, eight repeats each, `pluck` against `map`:
#
#     runner            rounds=5                    rounds=15
#     ubuntu-latest     17.18 to 17.40  (0.22)      17.28 to 17.48  (0.20)
#     macos-latest      14.75 to 26.25 (11.49)      17.53 to 20.82  (3.28)
#
# The macOS reference swings **52.6%** across its own minimums at five rounds and 26.4% at
# fifteen; the Linux one swings 1.4%. So five rounds is enough on one platform and not on the
# other, and the ratio inherits all of it. Fifteen costs 0.27 s per call at 20,000 items and 2.8 s
# at 200,000, which is the price of a number that means the same thing on both.
#
# **Taking a minimum is still right and taking more of them is what was missing.** More rounds
# move a minimum down, never up, so this converges rather than drifting.
BLIND_SPOT_ROUNDS = 15


def blind_spots(items: int = 200_000, rounds: int = BLIND_SPOT_ROUNDS) -> dict[str, float]:
    """Nanoseconds of wall time per charged step, per function.

    **The measurement that finds work the counter cannot see.** A number far above the reference
    means the budget is charging far too little for what the function actually does.

    Args:
        items: How large a collection to measure against.
        rounds: Samples per case, minimum taken. Fifteen rather than the five everything else
            takes, because this reports a ratio and the reference in its denominator is the
            unstable half. See `BLIND_SPOT_ROUNDS`.

    Returns:
        Nanoseconds per charged step, per function.
    """
    context = {
        "nums": list(range(items)),
        "rows": [{"k": n} for n in range(items)],
        "words": ["w"] * items,
    }
    cases = {
        "map (reference)": "rows | map(_.k)",
        "pluck": 'rows | pluck("k")',
        "sum": "nums | sum",
        "min": "nums | min",
        "max": "nums | max",
        "join": 'words | join(",")',
        "unique_by": "nums | unique_by(_)",
        "sort_by": "nums | sort_by(_)",
    }
    return {
        label: seconds_for(source, context, rounds=rounds) / steps_for(source, context) * 1e9
        for label, source in cases.items()
    }


def compile_one(source: str, registry: dict[str, Any]) -> tuple[Any, ...]:
    """One cache entry, built the way `Evaluator.evaluate` builds it.

    Spelled out here rather than reached through a private method, so the measurement is of the
    thing the cache holds rather than of whatever an internal happens to be called this week.
    """
    tree = parse(source)
    targets = pipe_targets(tree, registry)
    return validate(transform(tree, registry), source), targets


def compile_cache(trees: int = 100) -> dict[str, float]:
    """What one evaluator's compile cache can hold, in bytes.

    **The measurement that sets `MAX_COMPILE_CACHE`**, and the only limit in this file whose basis
    is what it costs rather than what a rule needs. Compiling depends only on `(source, registry)`,
    so an evaluator keeps the result; an unbounded dict keyed on caller-supplied text is a denial
    of service, so the cache is bounded, and the bound is only reviewable next to the ceiling it
    buys.

    Two shapes, because they differ by a factor of forty: an ordinary rule, and the widest input
    the source cap admits. A flat literal is the worst case per byte, since every element is a
    node and none of them nests.

    Args:
        trees: How many to build before dividing. One tree is too small to measure.

    Returns:
        Bytes per tree for each shape, and the ceiling each implies at the bound.
    """
    typical = CANONICAL[0][1]
    # The widest source the cap admits, built rather than typed so it tracks `MAX_SOURCE_BYTES`.
    widest = "[0]"
    while len(f"[{widest[1:-1]}, 0]".encode()) <= MAX_SOURCE_BYTES:
        widest = f"[{widest[1:-1]}, 0]"
    registry = standard_registry()
    measured: dict[str, float] = {}
    for label, source in (("typical", typical), ("widest", widest)):
        compile_one(source, registry)  # warm every import this path touches
        tracemalloc.start()
        held = [compile_one(source, registry) for _ in range(trees)]
        current, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del held
        measured[f"{label}_bytes"] = current / trees
        measured[f"{label}_ceiling_bytes"] = current / trees * MAX_COMPILE_CACHE
    measured["widest_source_bytes"] = float(len(widest.encode()))
    return measured


LIMITS: tuple[tuple[str, int, str], ...] = (
    ("MAX_SOURCE_BYTES", MAX_SOURCE_BYTES, "set by 3.11's parser cliff, not by observed need"),
    ("MAX_EXPRESSION_DEPTH", MAX_EXPRESSION_DEPTH, "expression_depth"),
    ("MAX_DATA_NESTING", MAX_DATA_NESTING, "data_depth"),
    ("MAX_RESULT_SIZE", MAX_RESULT_SIZE, "result_size"),
    ("DEFAULT_STEP_BUDGET", DEFAULT_STEP_BUDGET, "steps"),
    ("SIZE_CHARGE_UNIT", SIZE_CHARGE_UNIT, "a rate rather than a cap; see the module"),
    ("MAX_POWER_RESULT_BITS", MAX_POWER_RESULT_BITS, "set by measured time, not by need"),
    ("MAX_COMPILE_CACHE", MAX_COMPILE_CACHE, "set by its memory ceiling, not by need"),
)


def report(quick: bool = False) -> dict[str, Any]:
    """Collect everything this script measures."""
    sizes = (1_000, 10_000) if quick else (1_000, 10_000, 100_000)
    return {
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "workloads": {str(size): workload(size) for size in sizes},
        "observed_need": observed_need(),
        "blind_spots": blind_spots(20_000 if quick else 200_000),
        "compile_cache": compile_cache(),
        "limits": {name: value for name, value, _ in LIMITS},
    }


def _print(data: dict[str, Any]) -> None:
    print(f"safeexpr limits, measured on Python {data['python']}\n")
    print("Canonical use cases")
    for size, cases in data["workloads"].items():
        print(f"  at {int(size):,} items")
        for label, entry in cases.items():
            per_item = entry["steps"] / int(size)
            print(
                f"    {label:16} {entry['steps']:>10,} steps"
                f"  {entry['seconds'] * 1000:8.2f} ms  {per_item:5.2f} steps/item"
            )
    need = data["observed_need"]
    print("\nEvery cap against what was measured to be needed")
    for name, value, basis in LIMITS:
        if basis in need:
            print(f"  {name:22} {value:>10,}  need {need[basis]:>9,}  {value / need[basis]:6.1f}x")
        else:
            print(f"  {name:22} {value:>10,}  {basis}")
    cache = data["compile_cache"]
    print(
        f"\nCompile cache: {MAX_COMPILE_CACHE} entries per evaluator, and what that can hold\n"
        f"  a typical rule       {cache['typical_bytes'] / 1024:8.1f} KiB/tree"
        f"  ->{cache['typical_ceiling_bytes'] / 1024 / 1024:7.2f} MiB at the bound\n"
        f"  a {int(cache['widest_source_bytes']):,}-byte literal"
        f"  {cache['widest_bytes'] / 1024:8.1f} KiB/tree"
        f"  ->{cache['widest_ceiling_bytes'] / 1024 / 1024:7.2f} MiB at the bound"
    )
    print("\nTime per charged step: far above the reference means the budget cannot see the work")
    reference = data["blind_spots"]["map (reference)"]
    for label, nanoseconds in sorted(data["blind_spots"].items(), key=lambda kv: kv[1]):
        print(f"  {label:18} {nanoseconds:9.1f} ns/step  {nanoseconds / reference:6.2f}x")


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Measure the basis for every limit.")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--quick", action="store_true", help="skip the 100,000-item runs")
    parsed = parser.parse_args()
    data = report(quick=parsed.quick)
    if parsed.json:
        print(json.dumps(data, indent=2))
    else:
        _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
