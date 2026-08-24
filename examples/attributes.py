"""The one door this package leaves closed, and what opening it costs.

    python examples/attributes.py

By default, `.name` reads a key from a mapping and does nothing else. On any other type it is
refused, because attribute traversal on arbitrary objects is where essentially every published
Python sandbox escape has started.

`attribute_types` opts a type back in, limited to the attribute names you list. This file shows
it working, shows what it does not open, and then shows the thing that makes it a real decision:
**an attribute can run code**, and the expression that reads it cannot tell.

The safer shape, when it is available to you, is the last section: convert to plain data at the
boundary and nothing is reachable at all.
"""

from dataclasses import dataclass

from safeexpr import Evaluator, SafeExprError, standard_registry


@dataclass
class Customer:
    plan: str
    region: str
    api_key: str = "sk-live-do-not-log-me"


class Account:
    """A class with a property, which is a method wearing an attribute's clothes."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        self.queries = 0

    @property
    def balance(self) -> float:
        self.queries += 1  # in a real system this is a database round trip
        return 42.0


def main() -> None:
    plain = Evaluator(registry=standard_registry())
    customer = Customer("pro", "eu")

    print("== closed by default ==\n")
    try:
        plain.evaluate("customer.plan", {"customer": customer})
    except SafeExprError as error:
        print(f"  customer.plan  !! {error.message}")

    print("\n== opened, for two names on one type ==\n")
    opted = Evaluator(
        registry=standard_registry(),
        attribute_types={Customer: {"plan", "region"}},
    )
    print("  attribute_types={Customer: {'plan', 'region'}}\n")
    context = {"customer": customer}
    plan = opted.evaluate("customer.plan", context)
    is_pro = opted.evaluate('customer.plan == "pro"', context)
    print(f"  customer.plan                 -> {plan!r}")
    print(f'  customer.plan == "pro"        -> {is_pro!r}')
    for source in ["customer.api_key", "customer.__class__", "customer.__dict__"]:
        try:
            opted.evaluate(source, {"customer": customer})
        except SafeExprError as error:
            print(f"  {source:<29} !! {error.message[:88]}")

    print("\n== what is still closed on an opted-in type ==\n")
    print("  Everything not on the list, dunders included, and dunders are refused earlier still:")
    print("  by name, before evaluation, so the list cannot accidentally contain one.")

    print("\n== the part that makes this a decision ==\n")
    account = Account("c-1")
    with_property = Evaluator(
        registry=standard_registry(),
        attribute_types={Account: {"customer_id", "balance"}},
    )
    print(f"  queries before: {account.queries}")
    for _ in range(3):
        with_property.evaluate("account.balance > 10", {"account": account})
    print(f"  queries after three evaluations of `account.balance > 10`: {account.queries}")
    print(
        "\n  `balance` is a property, so reading it ran your code, three times, from inside an\n"
        "  expression. A lazy loader, a descriptor with a side effect, a cached_property that\n"
        "  fills on first touch: all of them are reachable through a name on that list, and the\n"
        "  expression author cannot tell the difference between one of those and a field.\n"
        "\n  That is the cost. It is limited to the names you list, and what you list is yours to\n"
        "  defend."
    )

    print("\n== the shape that gives nothing up ==\n")
    as_data = {"plan": customer.plan, "region": customer.region}
    print(f"  {{'plan': customer.plan, 'region': customer.region}}  ->  {as_data}")
    as_data_result = plain.evaluate('customer.plan == "pro"', {"customer": as_data})
    print(f'  customer.plan == "pro"        -> {as_data_result!r}')
    print(
        "\n  Nothing registered, nothing reachable, and the expression cannot see an object at\n"
        "  all. Converting at the boundary is one line per field and it is the recommendation:\n"
        "  `attribute_types` is for the case where the conversion is genuinely impractical."
    )


if __name__ == "__main__":
    main()
