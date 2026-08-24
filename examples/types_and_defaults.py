"""Conversions that refuse rather than guess, and the difference between nothing and falsy.

    python examples/types_and_defaults.py

Three things worth taking from this.

**`str` converts primitives and refuses everything else.** Converting an arbitrary object would
run that object's own `__str__` to produce the text, which is your code, called from inside an
expression, on a value you did not intend to expose. That refusal is the same one behind the
absence of f-strings.

**`default` tests for nothing, not for falsy.** `default(0, 10)` is `0`, because zero is a value
a rule may well have meant. `or` is the falsy-coalescing operator when that is what you want, and
the two are shown side by side below because picking the wrong one is a quiet bug.

**`default` cannot rescue a missing key.** `user.absent` raises before `default` is ever called,
because the argument is evaluated first. The section at the end shows the three ways round it.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())

SETTINGS = {"retries": 0, "label": "", "region": None, "timeout": 30}


def show(source: str, context: dict | None = None) -> None:
    try:
        print(f"  {source:<44} -> {RULES.evaluate(source, context or {})!r}")
    except SafeExprError as error:
        print(f"  {source:<44} !! {error.message}")


def main() -> None:
    print("== conversions ==\n")
    show('int("42")')
    show('int(" 42 ")')
    show("int(1.9)")
    show('int("1.9")')
    show('int("0x10")')
    show('float("1.5")')
    show("float(2)")
    show("str(42)")
    show("str(True)")
    show("str(None)")
    show('str({"a": 1})')
    print("\n  Nothing is coerced quietly. A conversion that cannot be made is an error with a")
    print("  sentence in it, not a `None` that turns into a wrong answer three steps later.")

    print("\n== truthiness ==\n")
    show("bool(1)")
    show("bool(0)")
    show('bool("")')
    show("bool([])")
    show("bool([0])")
    show("is_none(None)")
    show("is_none(0)")
    show('is_none("")')

    print("\n== default, and the trap next to it ==\n")
    context = {"settings": SETTINGS}
    show('default(settings.region, "eu")', context)
    show("default(settings.retries, 3)", context)
    show("settings.retries or 3", context)
    show('default(settings.label, "unnamed")', context)
    show('settings.label or "unnamed"', context)
    print(
        "\n  `retries: 0` means do not retry. `default` keeps it; `or` replaces it with 3, which\n"
        "  is a config file being ignored and a support ticket that starts with `it retries\n"
        "  anyway`. Same for an empty label somebody set on purpose."
    )

    print("\n== what default cannot do ==\n")
    show('default(settings.absent, "fallback")', context)
    print(
        "\n  Arguments are evaluated before the call, so the missing field raises first. Three\n"
        "  ways round it, in order of preference:\n"
    )
    print("  1. give the field a value at the boundary, where the schema lives")
    show('default(settings.region, "eu")', context)
    print("  2. reach through a mapping you control")
    show('default(settings["region"], "eu")', context)
    print("  3. ask first, when the key really is optional")
    show('"absent" in settings', context)
    show('settings["absent"] if "absent" in settings else "fallback"', context)


if __name__ == "__main__":
    main()
