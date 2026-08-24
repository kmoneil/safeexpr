"""Every way an expression can fail, what each one carries, and who has to fix it.

    python examples/errors.py

Every failure here is a `SafeExprError`. That is a stronger promise than it looks: the parse
boundary alone can produce `SyntaxError`, `ValueError`, `UnicodeEncodeError`, `MemoryError` and
`RecursionError` out of CPython, and which one you get varies by version. One `except` catches
all of it.

Three things worth taking from this.

**The class says who has to fix it.** A `ValidationError` is the expression author's problem, a
`ReservedNameError` is the host's, and an `InternalError` is ours. That distinction is what lets
a rule editor say something useful instead of `error`.

**An error carries a message, the source, and a position. Nothing else.** No reference to a
causing exception, no args carried through from one, no notes copied from one. An
`AttributeError` carries `.obj`, a live reference to the object whose lookup failed, and a
`raise ... from None` leaves it reachable through `__context__`. Errors here are constructed
after the handler exits rather than wrapped inside it, which is the only spelling that drops it.

**One thing about your data does reach the message: a type's name.** Never a value, never a
`repr`, and a name is a string rather than a class object, so there is nothing to climb from it.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())

CONTEXT = {
    "customer": {"tier": "gold", "balance": 5.0, "orders": [{"total": 1.0}]},
    "flags": [1, 2],
    "first": "a key of my own",
}

CASES = [
    ("a name that is not there", "custmer.tier", CONTEXT),
    ("a field that is not there", "customer.balence > 0", CONTEXT),
    ("a function that is not there", "frist(flags)", CONTEXT),
    ("types that do not compare", "customer.tier > 3", CONTEXT),
    ("types that do not combine", 'customer.balance + "x"', CONTEXT),
    ("division by zero", "1 / 0", {}),
    ("a syntax error", "customer.tier == ", CONTEXT),
    ("a construct outside the language", "[x for x in flags]", CONTEXT),
    ("an f-string", 'f"{customer}"', CONTEXT),
    ("a method call", "customer.tier.upper()", CONTEXT),
    ("a dunder", "customer.__class__", CONTEXT),
    ("a name collision on a pipe", "flags | first", CONTEXT),
    ("a number too wide to build", "2 ** 100000000", {}),
    ("a source over the byte cap", "1 + " * 600 + "1", {}),
]


def taxonomy() -> None:
    """Every case, caught by one `except`, with the class that says who has to fix it."""
    print(f"== one except, {len(CASES)} failures ==\n")
    print(f"  {'what happened':<34} {'class':<20} message")
    print(f"  {'-' * 34} {'-' * 20} {'-' * 40}")
    for label, source, context in CASES:
        try:
            RULES.evaluate(source, context)
        except SafeExprError as error:
            print(f"  {label:<34} {type(error).__name__:<20} {error.message[:76]}")
        else:
            print(f"  {label:<34} {'(no error)':<20}")


def suggestions() -> None:
    """Misspellings get a suggestion where there is an unambiguous one."""
    print("== the suggestions ==\n")
    for source in ["custmer", "customer.blance", "frist(flags)", "wherre(flags, _ > 1)"]:
        try:
            RULES.evaluate(source, CONTEXT)
        except SafeExprError as error:
            print(f"  {source:<24} {error.message}")


def main() -> None:
    taxonomy()

    print("\n== what an error carries ==\n")
    try:
        RULES.evaluate("(customer.tier\n == unknown)", CONTEXT)
    except SafeExprError as error:
        print(f"  message : {error.message}")
        print(f"  source  : {error.source!r}")
        print(f"  lineno  : {error.lineno}")
        print(f"  offset  : {error.offset}")
        print(f"  cause   : {error.__cause__!r}")
        print(f"  context : {error.__context__!r}")

    print("\n== annotated(), which is what a rule author should see ==\n")
    for source in ["customer.balence > 0", "flags | frist", "customer.tier > 3"]:
        try:
            RULES.evaluate(source, CONTEXT)
        except SafeExprError as error:
            for line in error.annotated().splitlines():
                print(f"  {line}")
            print()

    suggestions()

    print("\n== the one thing a message says about your data ==\n")

    class Order:
        pass

    try:
        RULES.evaluate("order > 10", {"order": Order()})
    except SafeExprError as error:
        print(f"  {error.message}")
    print(
        "\n  A class name from your context, and nothing else: never a value, never a repr, and\n"
        "  a string rather than a class object, so there is nothing to climb from it. The\n"
        "  alternative, `cannot compare these two things`, turns every mismatch into a ticket."
    )

    print("\n== where to catch it ==\n")
    print("  config load, or a rule editor  -> reject the rule, show annotated() to its author")
    print("  per record, in a loop          -> log it with the rule name, keep evaluating")
    print("  a request path                 -> fail closed, and do not put the message in the")
    print("                                    response: it names keys and types from your data")
    print(
        "\n  The one class not shown above is InternalError, which means something raised that\n"
        "  this package did not anticipate. It exists so that even a bug here arrives as a\n"
        "  SafeExprError rather than as whatever CPython happened to raise, and reaching one is\n"
        "  a bug worth reporting. BudgetExceededError has its own example: examples/budget.py."
    )


if __name__ == "__main__":
    main()
