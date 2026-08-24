"""Validating records at ingest, with the checks owned by the team that owns the data.

    python examples/data_validation.py

The job: reject bad rows with a message per rule, where the rules can change without a deploy of
the pipeline.

Three things worth taking from this.

**Order the checks so the structural ones run first.** `len(record.items) > 0` has to pass before
a check that sums `record.items` means anything, and this file shows what happens when it does
not: an empty basket has a total of zero, and zero equals zero, so a sum check *passes* on a row
that has nothing in it. That is arithmetic behaving correctly and a rule set saying something it
did not mean.

**A missing field is a failure, not a false.** `record.emial` raises rather than evaluating to
nothing, so a typo in a check is loud. That is the whole reason this package refuses to return
`None` for an absent key.

**Regular expressions are gated.** The email pattern here is deliberately boring. A pattern that
can backtrack catastrophically is refused before it compiles, because no input-length cap saves
you from one. The last section shows the refusal and the two ways through it.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

CHECKS = [
    ("shape", "no items", "len(record.items) > 0"),
    ("shape", "missing email", 'record.email != ""'),
    ("format", "email looks wrong", r'matches(record.email, "^[^@ ]+@[^@ ]+\\.[a-z]{2,}$")'),
    ("range", "age out of range", "record.age >= 18 and record.age < 120"),
    (
        "consistency",
        "total does not match items",
        "record.total == sum(pluck(record.items, 'price'))",
    ),
    ("consistency", "currency not supported", 'record.currency in ["EUR", "USD", "GBP"]'),
]

RECORDS = [
    {
        "id": "r-1",
        "email": "bob@example.com",
        "age": 31,
        "currency": "EUR",
        "items": [{"price": 10.0}, {"price": 5.0}],
        "total": 15.0,
    },
    {
        "id": "r-2",
        "email": "not-an-email",
        "age": 12,
        "currency": "XBT",
        "items": [],
        "total": 0.0,
    },
    {
        "id": "r-3",
        "email": "carol@example.com",
        "age": 44,
        "currency": "USD",
        "items": [{"price": 20.0}],
        "total": 99.0,
    },
]

RULES = Evaluator(registry=standard_registry())


def problems(record: dict) -> list[str]:
    """Every check that fails, in order, stopping the group after a structural failure."""
    found = []
    for group, message, rule in CHECKS:
        try:
            if not RULES.evaluate(rule, {"record": record}):
                found.append(message)
                if group == "shape":
                    return [*found, "(later checks skipped: the row is not the right shape)"]
        except SafeExprError as error:
            found.append(f"{message}: check is broken ({error.message})")
    return found


def main() -> None:
    print("== the checks ==\n")
    for group, message, rule in CHECKS:
        print(f"  [{group:<11}] {message:<28} {rule}")

    print("\n== the records ==\n")
    for record in RECORDS:
        found = problems(record)
        verdict = "accepted" if not found else "rejected"
        print(f"  {record['id']}  {verdict}")
        for problem in found:
            print(f"        - {problem}")

    print("\n== the check that passes for the wrong reason ==\n")
    empty = {"items": [], "total": 0.0}
    print("  record.total == sum(pluck(record.items, 'price'))")
    print("    against an empty basket ->", RULES.evaluate(CHECKS[4][2], {"record": empty}))
    print(
        "\n  An empty list sums to 0, which is right, and 0 == 0, which is also right. The rule\n"
        "  set is what is wrong: a consistency check over a collection means nothing until a\n"
        "  shape check has established there is a collection. Hence the early return above."
    )

    print("\n== the pattern gate ==\n")
    for pattern in [r"^[a-z]+@[a-z]+\\.[a-z]{2,}$", "^(a+)+$", "^(?>a+)+$"]:
        source = f'matches("bob@example.com", "{pattern}")'
        try:
            print(f"  {pattern:<28} -> {RULES.evaluate(source)!r}")
        except SafeExprError as error:
            print(f"  {pattern:<28} !! {error.message}")

    print(
        "\n  `^(a+)+$` against a 29-character input takes about seven seconds, so no length cap\n"
        "  helps and the pattern itself is refused. An atomic group `(?>...)` or a possessive\n"
        "  quantifier `a++` resets that, and both work on every supported Python."
    )


if __name__ == "__main__":
    main()
