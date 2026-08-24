"""The step budget: what it counts, what it costs, and how to pick one.

    python examples/budget.py

Every evaluation runs under a counter, decremented per node evaluated plus each function's
declared cost. When it reaches zero the evaluation raises `BudgetExceededError` rather than
running on.

Three things worth taking from this.

**A counter, not a timer.** No signal, so it works off the main thread and off POSIX. No thread
and no executor, so nothing is left running after the refusal. The same input gives the same
answer on every platform and in every thread, which is what makes the bound reviewable rather
than hopeful.

**It bounds work, which shape cannot.** The language has no loops, so termination is structural
and the budget is the backstop behind it. What the backstop is for is `map(a, where(b, _ == _2))`:
short, shallow, and O(n*m).

**It bounds memory too.** Producing a value costs steps in proportion to its size, so the counter
that bounds time also bounds allocation. Anything under 64 elements is charged nothing, so
ordinary rules are unaffected.
"""

from safeexpr import BudgetExceededError, Evaluator, standard_registry


def rows(count: int) -> list[dict]:
    return [{"v": index, "tags": ["a", "b"]} for index in range(count)]


def steps_used(source: str, context: dict, ceiling: int = 50_000_000) -> int:
    """Find what an expression actually costs, by bisecting the budget it needs."""
    low, high = 1, ceiling
    while low < high:
        middle = (low + high) // 2
        try:
            Evaluator(registry=standard_registry(), budget=middle).evaluate(source, context)
        except BudgetExceededError:
            low = middle + 1
        else:
            high = middle
    return low


def main() -> None:
    print("== what things cost ==\n")
    cases = [
        (
            "a feature flag",
            'user.plan == "pro" and user.region in ["us", "eu"]',
            {"user": {"plan": "pro", "region": "eu"}},
        ),
        ("a filter over 100 rows", "rows | where(_.v > 50) | len", {"rows": rows(100)}),
        ("a filter over 1,000 rows", "rows | where(_.v > 50) | len", {"rows": rows(1000)}),
        ("a filter over 10,000 rows", "rows | where(_.v > 50) | len", {"rows": rows(10000)}),
        (
            "a pipeline over 1,000 rows",
            "rows | where(_.v > 10) | map(_.v * 2) | sum",
            {"rows": rows(1000)},
        ),
        ("one matches() call", 'matches("abc-123", "^[a-z]+-[0-9]+$")', {}),
    ]
    for label, source, context in cases:
        used = steps_used(source, context)
        size = len(context.get("rows", []))
        rate = f"{used / size:.2f} steps/item" if size else ""
        print(f"  {label:<28} {used:>8,} steps   {rate}")

    print(
        "\n  A rule that does not touch a collection is flat: eleven steps whether your context\n"
        "  holds ten records or a hundred thousand. A rule that does costs a few steps per\n"
        "  item, four for a filter and eight for a three-stage pipeline, and that rate is\n"
        "  stable across three orders of magnitude. Stability is the useful part: it is what\n"
        "  makes a budget expressible in items rather than in nodes."
    )

    print("\n== running out ==\n")
    small = Evaluator(registry=standard_registry(), budget=500)
    try:
        small.evaluate("rows | where(_.v > 50) | len", {"rows": rows(10_000)})
    except BudgetExceededError as error:
        print(f"  budget    : {error.budget}")
        print(f"  message   : {error.message}")
        print(f"  position  : line {error.lineno}, column {error.offset}")

    print("\n== the O(n*m) rule the budget exists for ==\n")
    source = "left | map(where(right, _ == _2))"
    context = {"left": list(range(60)), "right": list(range(60))}
    print(f"  {source}")
    print(f"  over 60 x 60 items -> {steps_used(source, context):,} steps from 33 characters")
    print(
        "\n  Short, shallow, and quadratic. Source length, expression depth and the power cap\n"
        "  all bound what an expression *is*. Only the budget bounds what it *does*."
    )

    print("\n== choosing one ==\n")
    print("  budget = items x 6 x 10      six steps per item, times ten for headroom\n")
    print(
        "  The measured shapes run 4 to 8 steps per item, so the ten is real headroom\n"
        "  rather than a round number, and it is this project's own rule applied to a\n"
        "  measurement rather than to a guess.\n"
    )
    for items, budget in [
        ("a request-sized context", 10_000),
        ("1,000 records", 60_000),
        ("10,000 records", 600_000),
        ("100,000 records", 6_000_000),
    ]:
        default = "  <- the shipped default" if budget == 6_000_000 else ""
        print(f"    {items:<26} {budget:>10,}{default}")
    print(
        "\n  Lower is better when you can. The budget is the one bound between a rule somebody\n"
        "  got wrong and a process that stops answering, and a rule over a request-sized\n"
        "  context needs three orders of magnitude less than the default.\n"
        "\n  There is deliberately no value meaning unlimited: a bound you cannot express is a\n"
        "  bound you cannot review."
    )

    print("\n== the budget is per call, not per evaluator ==\n")
    shared = Evaluator(registry=standard_registry(), budget=20_000)
    for attempt in range(3):
        used = shared.evaluate("rows | where(_.v > 50) | len", {"rows": rows(1000)})
        print(f"  call {attempt + 1} -> {used} matches, and the next call starts from 20,000 again")
    print(
        "\n  Every evaluation starts from the number the evaluator was built with, which is what\n"
        "  makes one evaluator safe to share between threads: two threads never spend each\n"
        "  other's budget. See examples/threads.py."
    )


if __name__ == "__main__":
    main()
