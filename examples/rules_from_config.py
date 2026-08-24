"""Rules in a config file, validated when the file loads rather than when a request arrives.

    python examples/rules_from_config.py

The job: the rules are JSON next to the service, somebody edits them without a deploy, and a
broken rule fails the config load rather than the first customer who trips it.

Three things worth taking from this.

**Evaluate against a sample, do not just parse.** Parsing catches `customer.plan ==` with nothing
after it. Only an evaluation catches `customer.lifetime_vlaue`, which is the typo that reaches
production and quietly evaluates nothing. Keeping one representative record beside the rules is
what makes that check possible, and it doubles as the schema nobody wrote down.

**Check the reserved names too.** A context key that collides with a registry function is refused
on the right of a `|`, and finding that out at startup is nicer than finding it out at 3am.

**Report every problem, not the first.** A config with four broken rules should tell you about
four, or you will fix them one deploy at a time.
"""

import json

from safeexpr import Evaluator, SafeExprError, standard_registry

CONFIG_TEXT = """
{
  "sample": {
    "customer": {"plan": "pro", "lifetime_value": 250.0, "days_since_order": 400, "orders": 4},
    "first": "a key that collides with a function",
    "rows": []
  },
  "rules": [
    {"name": "vip",           "when": "customer.lifetime_value > 10000"},
    {"name": "at-risk",       "when": "customer.days_since_order > 90"},
    {"name": "new-customer",  "when": "customer.orders == 0"},
    {"name": "typo",          "when": "customer.lifetime_vlaue > 100"},
    {"name": "malformed",     "when": "customer.plan == "},
    {"name": "not-a-language","when": "[c for c in customer]"},
    {"name": "too-expensive", "when": "rows | map(where(rows, _ == _2)) | len"}
  ]
}
"""

RULES = Evaluator(registry=standard_registry(), budget=50_000)


def load(config: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Split the rules into the ones that work and the ones that do not, with reasons."""
    sample = config["sample"]
    working, broken = [], []
    for rule in config["rules"]:
        try:
            RULES.evaluate(rule["when"], sample)
        except SafeExprError as error:
            broken.append((rule["name"], f"{type(error).__name__}: {error.message}"))
        else:
            working.append(rule)
    return working, broken


def collisions(sample: dict) -> list[str]:
    """Context keys that a registry function would shadow on the right of a pipe."""
    return sorted(set(sample) & RULES.function_names)


def main() -> None:
    config = json.loads(CONFIG_TEXT)
    # A sample is only as good as its shape *and* its size: a rule that is quadratic in the
    # length of a collection cannot be caught by a sample holding an empty one.
    config["sample"]["rows"] = list(range(200))

    print("== the config ==\n")
    for rule in config["rules"]:
        print(f"  {rule['name']:<16} {rule['when']}")

    print("\n== loading it ==\n")
    working, broken = load(config)
    for rule in working:
        print(f"  ok       {rule['name']}")
    for name, reason in broken:
        print(f"  BROKEN   {name:<16} {reason[:96]}")

    print(f"\n  {len(working)} loaded, {len(broken)} rejected, out of {len(config['rules'])}.")
    print(
        "\n  Every one of those was found before a request arrived, and all of them were found\n"
        "  in one pass. `too-expensive` is the one no parse could have caught: it is a valid,\n"
        "  well-typed rule that is quadratic in the length of its input, and the budget is what\n"
        "  noticed.\n"
        "\n  It is also the one that shows what a sample has to be. The rows here are 200 long;\n"
        "  with an empty list that rule loads clean and fails in production. A sample has to be\n"
        "  representative in size as well as in shape, or it validates less than it looks like\n"
        "  it does."
    )

    print("\n== the collision check ==\n")
    found = collisions(config["sample"])
    print(f"  context keys that shadow a function: {found}")
    if found:
        print("  `flags | first` would call the function, so this is worth knowing at startup.")

    print("\n== a rule that is valid and still wrong ==\n")
    sample = config["sample"]
    print("  customer.days_since_order > 90")
    print(f"    against the sample -> {RULES.evaluate('customer.days_since_order > 90', sample)}")
    print(
        "\n  Nothing here can tell you a rule means what its author intended. What a load-time\n"
        "  check buys is that every rule is *runnable* against the shape of your data, which\n"
        "  is the whole class of failure that otherwise arrives one customer at a time."
    )

    print("\n== the shape to copy ==\n")
    print("  1. keep one representative record beside the rules, in the same file")
    print("  2. evaluate every rule against it when the config loads")
    print("  3. fail the load, or refuse the individual rule, but never fail open")
    print("  4. check set(sample) & evaluator.function_names while you are there")
    print("  5. size the budget for the biggest collection a rule will see, and no larger")


if __name__ == "__main__":
    main()
