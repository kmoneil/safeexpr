"""The one collision pipes create, why it is refused rather than resolved, and the ways out.

    python examples/reserved_names.py

Registry membership is what tells the pipe transform that a `|` is a pipe rather than bitwise or,
and that decision never looks at your data. That is what makes an expression mean the same thing
whatever it is evaluated against, and it is the whole reason functions are opt-in rather than on
by default.

The price: **a function name on the right of a `|` always wins**. With `first` registered,
`flags | first` calls the function, whatever your context says `first` is.

Rather than quietly reading past your key, that is refused, with a `ReservedNameError` naming it.
Note which class that is: not an `EvaluationError`, because the expression is not wrong. The
host's data and the host's registry both claim a name, and only the host can fix it.
"""

from safeexpr import Evaluator, ReservedNameError, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())

CONTEXT = {"flags": [1, 2, 3], "first": "a value of my own", "min": 10, "metrics": [{"value": 40}]}


def show(source: str, context: dict) -> None:
    try:
        print(f"  {source:<44} -> {RULES.evaluate(source, context)!r}")
    except SafeExprError as error:
        print(f"  {source:<44} !! {type(error).__name__}")
        for line in error.annotated().splitlines():
            print(f"       {line}")


def main() -> None:
    print("== the collision ==\n")
    print(f"  context = {CONTEXT}\n")
    show("flags | first", CONTEXT)

    print("\n== and everywhere it is not a collision ==\n")
    show("first", CONTEXT)
    show("first(flags)", CONTEXT)
    show("metrics | where(_.value > min)", CONTEXT)
    print(
        "\n  A bare name reads your data, so `first` is your string and `min` is 10. `first(x)`\n"
        "  can only mean the function, because a value from the context cannot be called at\n"
        "  all. The right of a `|` is the one position where the author might have meant\n"
        "  either, which is exactly where the refusal is."
    )

    print("\n== knowing at startup rather than at 3am ==\n")
    collisions = sorted(set(CONTEXT) & RULES.function_names)
    print(f"  sorted(set(context) & rules.function_names) -> {collisions}")
    print(f"  {len(RULES.function_names)} names are reserved with the standard registry:\n")
    names = sorted(RULES.function_names)
    for start in range(0, len(names), 8):
        print("    " + " ".join(name.ljust(12) for name in names[start : start + 8]))
    print(f"\n  With an empty registry, exactly one: {sorted(Evaluator().function_names)}")
    print("  `bitor` is reserved everywhere, because it is how you say bitwise or.")

    print("\n== three ways out ==\n")

    print("  1. rename the key in your context")
    renamed_key = {key: value for key, value in CONTEXT.items() if key != "first"}
    renamed_key["my_first"] = CONTEXT["first"]
    show("flags | first", renamed_key)
    show("my_first", renamed_key)

    print("\n  2. rename the function")
    registry = standard_registry()
    registry["head"] = registry.pop("first")
    renamed = Evaluator(registry=registry)
    print("    registry['head'] = registry.pop('first')")
    head = renamed.evaluate("flags | head", CONTEXT)
    print(f"    flags | head                               -> {head!r}")
    print(
        f"    first                                      -> {renamed.evaluate('first', CONTEXT)!r}"
    )

    print("\n  3. drop the function, if nothing uses it")
    registry = standard_registry()
    del registry["first"]
    dropped = Evaluator(registry=registry)
    print("    del registry['first']")
    print(
        f"    first                                      -> {dropped.evaluate('first', CONTEXT)!r}"
    )

    print("\n== bitwise or, when you meant bitwise or ==\n")
    show("bitor(6, 3)", CONTEXT)
    print(f"  6 | 3 with an empty registry                 -> {Evaluator().evaluate('6 | 3')!r}")
    print(
        "\n  With a registry, `|` between two values is a pipe and `6 | 3` is a call to a\n"
        "  function named 3, which is not a name. `bitor` is the spelling that always works."
    )

    print("\n== what the error class is telling you ==\n")
    try:
        RULES.evaluate("flags | first", CONTEXT)
    except ReservedNameError as error:
        print(f"  type(error).__name__ = {type(error).__name__}")
        print(f"  error.name           = {error.name!r}")
        print(f"  a SafeExprError?     {isinstance(error, SafeExprError)}")
        print(f"  an EvaluationError?  {type(error).__name__ == 'EvaluationError'}")
        print(
            "\n  Deliberately its own class. A caller that catches EvaluationError to report\n"
            "  `your rule is wrong` would be blaming the author of a rule that is fine."
        )


if __name__ == "__main__":
    main()
