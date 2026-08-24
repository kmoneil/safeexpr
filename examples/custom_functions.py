"""Adding your own functions, including one that takes an expression rather than a value.

    python examples/custom_functions.py

The registry is a plain dictionary of name to `Function`, so adding, renaming and removing are
all one line. What is worth reading carefully is the four fields on `Function`, because three of
them are decisions rather than plumbing.

**`arity`** is checked before your function runs, which is what lets a `TypeError` from inside it
mean "this value is wrong" rather than "this argument count is wrong".

**`cost`** is what one call charges the step budget, as a per-call figure. Work that scales with
the input is charged per item by the budget itself.

**`lazy`** names the argument positions the evaluator must *not* evaluate. A function with a lazy
position receives a `LazyExpr` and calls `.evaluate(item)` once per item. That is how
`where(_.price > 10)` works with no lambda in the language, and it is available to you.

The rules a function you add has to hold to are in the last section, and they are not style
preferences: a registry function is inside the trust boundary.
"""

from safeexpr import Evaluator, Function, FunctionError, SafeExprError, standard_registry


def _round_to(value, places):
    """A plain function: values in, value out."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FunctionError(f"needs a number, got `{type(value).__name__}`")
    if not isinstance(places, int) or isinstance(places, bool):
        raise FunctionError("needs a whole number of places")
    return round(value, places)


def _count_where(items, predicate):
    """A lazy function: the second argument arrives unevaluated, once per item."""
    if not isinstance(items, list):
        raise FunctionError(f"needs a list, got `{type(items).__name__}`")
    return sum(1 for item in items if predicate.evaluate(item))


def _percentile(items, fraction):
    """An aggregate the standard tier does not carry, added in four lines."""
    if not isinstance(items, list) or not items:
        raise FunctionError("needs a non-empty list")
    ordered = sorted(items)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def build() -> Evaluator:
    registry = standard_registry()
    registry["round_to"] = Function("round_to", _round_to, arity=(2, 2))
    registry["count_where"] = Function(
        "count_where", _count_where, lazy=frozenset({1}), arity=(2, 2), cost=2
    )
    registry["percentile"] = Function("percentile", _percentile, arity=(2, 2), cost=3)
    return Evaluator(registry=registry)


RULES = build()

ORDERS = [{"total": 120.0}, {"total": 40.0}, {"total": 10.0}, {"total": 300.0}]


def show(source: str, context: dict | None = None) -> None:
    try:
        print(f"  {source:<50} -> {RULES.evaluate(source, context or {})!r}")
    except SafeExprError as error:
        print(f"  {source:<50} !! {type(error).__name__}: {error.message}")


def main() -> None:
    print("== three functions that are not in the standard registry ==\n")
    context = {"orders": ORDERS}
    show("round_to(1.23456, 2)")
    show("orders | count_where(_.total > 50)", context)
    show('orders | pluck("total") | percentile(0.5)', context)
    show('orders | pluck("total") | percentile(0.95)', context)

    print("\n== what the declarations bought ==\n")
    show("round_to(1.2)")
    show("round_to(1.2, 2, 3)")
    show('round_to("x", 2)')
    show("round_to(True, 2)")
    print(
        "\n  The first two are the evaluator's arity check, before the function runs. The last\n"
        "  two are the function's own refusal, and they arrive as an EvaluationError naming the\n"
        "  function, with the position of the call in the source."
    )

    print("\n== the lazy argument, seen from inside ==\n")
    calls = []

    def _traced(items, predicate):
        for item in items:
            calls.append(item)
            predicate.evaluate(item)
        return len(calls)

    registry = standard_registry()
    registry["traced"] = Function("traced", _traced, lazy=frozenset({1}), arity=(2, 2))
    Evaluator(registry=registry).evaluate("orders | traced(_.total > 50)", context)
    print(f"  the predicate was evaluated once per item, for {len(calls)} items")
    print(f"  and it was never re-parsed: one AST node, {len(calls)} evaluations")

    print("\n== renaming and removing ==\n")
    registry = standard_registry()
    registry["head"] = registry.pop("first")
    del registry["slugify"]
    renamed = Evaluator(registry=registry)
    print("  registry['head'] = registry.pop('first');  del registry['slugify']")
    print("   ", renamed.evaluate("[1, 2, 3] | head"))
    try:
        renamed.evaluate('slugify("x")')
    except SafeExprError as error:
        print("   ", error.message)
    print(
        "\n  Useful when a registry name collides with a key in your data: the collision is\n"
        "  refused on the right of a `|`, and renaming either side clears it. See\n"
        "  examples/reserved_names.py."
    )

    print("\n== the rules a function you add has to hold to ==\n")
    print("  1. Reject with FunctionError, carrying a string and nothing else.")
    print("     No value, no caught exception, no args passed through. The evaluator catches")
    print("     your error inside an exception handler, so anything reachable from it is")
    print("     reachable through __context__.")
    print("  2. Do not name your function in the message. The evaluator prefixes it:")
    show('round_to("x", 2)')
    print("  3. No reflection. No type(), getattr(), __class__ or dir(). A class object is")
    print("     climbable; a type's *name* is a string and is not.")
    print("  4. No I/O and no clock. An expression is a pure function of its context, and one")
    print("     function that reads a file breaks that for every rule in the system.")
    print("  5. Bound your output. A function that can return much more than it was given is a")
    print("     memory amplifier the step budget prices only approximately.")


if __name__ == "__main__":
    main()
