"""One evaluator, many threads, and the counter that made that a contract rather than a hope.

    python examples/threads.py

`Evaluator` is immutable after construction and safe to share. This file demonstrates the three
claims that make that true rather than asserting them.

**Immutable after construction.** The registry and the attribute allowlist are copied rather than
held, so a host that keeps the dictionary it passed in cannot change what an evaluator can do
afterwards. `__slots__` means nothing can be attached later either.

**The budget is per call.** Everything one evaluation needs, the step counter and the `_` scope
stack included, lives in a call-scoped object. A counter on the instance would have made a shared
evaluator quietly wrong under concurrency, with an error naming the budget rather than the
sharing.

**Nothing here starts a thread or installs a signal handler.** There is no interaction with
whatever your host already does about either, which is a consequence of the budget being a
counter rather than a clock.
"""

from concurrent.futures import ThreadPoolExecutor

from safeexpr import BudgetExceededError, Evaluator, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())
DEFAULT_BUDGET = RULES.budget

RECORDS = [{"id": index, "value": index * 3} for index in range(2000)]


def nothing_can_be_attached(evaluator: Evaluator) -> None:
    """An evaluator is immutable after construction, and this is what that means."""
    print("\n== and nothing can be attached to an evaluator afterwards ==\n")
    for attempt, assign in (
        ("evaluator.budget = 1", lambda: setattr(evaluator, "budget", 1)),
        ("evaluator.sneaky = 'x'", lambda: setattr(evaluator, "sneaky", "x")),
    ):
        try:
            assign()
        except AttributeError as error:
            print(f"  {attempt:<24} -> AttributeError: {error}")
        else:
            print(f"  {attempt:<24} -> it worked, which it must not")
    print(f"\n  nothing was attached: {not hasattr(evaluator, 'sneaky')}")
    print(f"  the budget is unchanged: {evaluator.budget == DEFAULT_BUDGET}")
    print(
        "\n  `__slots__`, and a read-only property. The wording of those two AttributeErrors is\n"
        "  CPython's and moves between versions; the two lines under them are the promise."
    )


def main() -> None:
    print("== one evaluator, eight threads, two thousand records ==\n")
    rule = "record.value % 7 == 0 and record.id > 10"
    with ThreadPoolExecutor(max_workers=8) as pool:
        hits = list(pool.map(lambda r: RULES.evaluate(rule, {"record": r}), RECORDS))
    print(f"  {rule}")
    print(f"  matched {sum(1 for hit in hits if hit)} of {len(RECORDS)} records")

    serial = [RULES.evaluate(rule, {"record": record}) for record in RECORDS]
    print(f"  the same answers as a serial run: {serial == hits}")

    print("\n== a thread that exhausts its budget refuses its own evaluation ==\n")
    tight = Evaluator(registry=standard_registry(), budget=400)
    big = {"rows": [{"v": index} for index in range(1000)]}
    small = {"rows": [{"v": index} for index in range(10)]}

    def attempt(context: dict) -> str:
        try:
            tight.evaluate("rows | where(_.v > 1) | len", context)
        except BudgetExceededError:
            return "refused"
        return "answered"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, [big, small, big, small, big, small]))
    print(f"  outcomes: {outcomes}")
    print(
        "\n  Three refusals and three answers, from one evaluator, at the same time. The budget\n"
        "  is charged per call, so the threads that ran out did not spend anybody else's."
    )

    print("\n== the registry is copied, not held ==\n")
    registry = standard_registry()
    evaluator = Evaluator(registry=registry)
    registry["len"] = None
    del registry["upper"]
    print("  after mutating the dict that was passed in:")
    length = evaluator.evaluate("len([1, 2, 3])")
    uppered = evaluator.evaluate('upper("x")')
    print(f"    len([1, 2, 3])   -> {length!r}")
    print(f'    upper("x")       -> {uppered!r}')

    nothing_can_be_attached(evaluator)

    print("\n== the one thing that is shared process-wide ==\n")
    pattern = "^[a-z]+-[0-9]{4}$"
    source = f'matches("invoice-2026", "{pattern}")'
    cold = Evaluator(registry=standard_registry(), budget=13)
    print(f"  {source}")
    try:
        print(f"  first call (cold pattern cache)  -> {cold.evaluate(source)!r}, within 13 steps")
        print(f"  second call (warm)               -> {cold.evaluate(source)!r}, within 13 steps")
    except SafeExprError as error:
        print(f"  !! {error.message}")
    print(
        "\n  A bounded cache of compiled patterns, shared across evaluators and threads.\n"
        "  Compiling a pattern is a pure function of the pattern string, so a hit and a miss\n"
        "  produce the same result, and `matches` is charged its declared cost either way. The\n"
        "  language has no clock, so nothing inside an expression can observe the cache at all."
    )


if __name__ == "__main__":
    main()
