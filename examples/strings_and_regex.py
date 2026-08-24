"""The string tier, why there is no interpolation, and the one function with a gate on it.

    python examples/strings_and_regex.py

Three things worth taking from this.

**There is no f-string, and that is a security decision rather than a missing feature.** `f"{x}"`
calls `x.__format__`, `f"{x!r}"` calls `__repr__`, `f"{x!s}"` calls `__str__`, and `f"{x:{spec}}"`
hands `__format__` a spec computed at runtime. That is four ways to run a context object's own
code, wearing a syntax a static check reads as one node. Use `+` or `join`.

**`slugify` is ASCII in core.** Accented Latin letters decompose to their base letter; a script
with no ASCII form is dropped rather than transliterated. Transliteration needs a Unicode
database this package will not depend on, and returning something wrong would be worse than
returning nothing.

**`matches` refuses patterns that can backtrack catastrophically, before they compile.** No
input-length cap helps against `^(a+)+$`, so the pattern is the thing that gets refused.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())


def show(source: str, context: dict | None = None) -> None:
    try:
        print(f"  {source:<52} -> {RULES.evaluate(source, context or {})!r}")
    except SafeExprError as error:
        print(f"  {source:<52} !! {error.message}")


def main() -> None:
    print("== the tier ==\n")
    show('lower("  Hello World  ") | strip')
    show('"  Hello World  " | strip | lower')
    show('upper("eu")')
    show('split("a,b,c", ",")')
    show('split("one two  three")')
    show('join(["2026", "08", "24"], "-")')
    show('replace("a-b-c", "-", "/")')
    show('starts_with("invoice-2026.pdf", "invoice-")')
    show('ends_with("invoice-2026.pdf", ".pdf")')
    show('contains("invoice-2026.pdf", "2026")')
    show('slugify("Hello, World!")')

    print("\n== building a string without interpolation ==\n")
    context = {"order": {"id": "A-17", "lines": 3}}
    show('f"order {order.id}"', context)
    show('"order " + order.id', context)
    show('join(["order", order.id, "has", str(order.lines), "lines"], " ")', context)
    print(
        "\n  `str` converts primitives and refuses everything else, for the same reason the\n"
        "  f-string is refused: converting an arbitrary object runs that object's own code."
    )
    show("str(order)", context)

    print("\n== slugify, and what it will not guess ==\n")
    for text in ["Hello, World!", "  Ünïcode  Test!! ", "Ærøskøbing", "日本語", "  "]:
        show(f'slugify("{text}")')
    print(
        "\n  Nothing is transliterated. `Ü` decomposes to `u` because Unicode says so; `Æ` and\n"
        "  `ø` have no decomposition, so they are dropped, and a string with no ASCII form at\n"
        "  all comes back empty rather than guessed at. A slug is usually a URL or a filename,\n"
        "  and a wrong one is a broken link that looks fine in a test."
    )

    print("\n== matches ==\n")
    show('matches("invoice-2026-08.pdf", "^invoice-[0-9]{4}-[0-9]{2}\\\\.pdf$")')
    show('matches("hello", "ell")')
    show('matches("hello", "^ell")')
    show('matches("HELLO", "hello")')
    print("\n  Unanchored by default: `matches` asks whether the pattern is found anywhere.")

    print("\n== the gate ==\n")
    for pattern in ["^(a+)+$", "(a|a)*$", "^(?>a+)+$", "^(a++)+$", "^(a{3})+$", "^(a{1,3})+$", "["]:
        source = f'matches("aaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "{pattern}")'
        try:
            print(f"  {pattern:<14} -> {RULES.evaluate(source)!r}")
        except SafeExprError as error:
            print(f"  {pattern:<14} !! {error.message}")
    print(
        "\n  A pattern is refused if it nests one backtrackable repeat inside another, or\n"
        "  repeats an alternation whose branches match the same text. Atomic groups and\n"
        "  possessive quantifiers reset that, and so does an *exact* count: `{3}` leaves\n"
        "  nothing to choose, where `{1,3}` still does. The gate is deliberately conservative\n"
        "  and refuses a few patterns that happen to be fast."
    )


if __name__ == "__main__":
    main()
