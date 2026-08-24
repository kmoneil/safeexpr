"""Saved reports: group, aggregate, sort, take, written once and run against today's data.

    python examples/rollups.py

The job: a report definition that lives in a config store, runs against whatever the data is now,
and is readable by the person who asked for it.

Three things worth taking from this.

**`group_by` returns a list, not a mapping.** Each group is `{"key": ..., "items": [...]}`, in the
order each key was first seen. A mapping would put your data's keys where a rule reads names, and
would throw away the order of first appearance, which is information.

**Aggregation composes from parts that already exist.** There is no `sum_by`. There is
`sum(pluck(_.items, "total"))`, which is three functions you already know doing exactly what it
says, and it extends to any aggregate without adding a name to the registry.

**`where` before `group_by` changes what the groups are.** A customer whose every order was
cancelled is absent from the result rather than present with a zero, and which of those you want
is a decision the rule makes rather than the engine.
"""

from safeexpr import Evaluator, standard_registry

ORDERS = [
    {"customer": "acme", "region": "eu", "status": "paid", "total": 120.0, "lines": 2},
    {"customer": "acme", "region": "eu", "status": "paid", "total": 80.0, "lines": 1},
    {"customer": "globex", "region": "us", "status": "paid", "total": 150.0, "lines": 3},
    {"customer": "globex", "region": "us", "status": "refunded", "total": -150.0, "lines": 3},
    {"customer": "initech", "region": "eu", "status": "cancelled", "total": 900.0, "lines": 9},
    {"customer": "hooli", "region": "us", "status": "paid", "total": 45.0, "lines": 1},
]

REPORTS = {
    "revenue by customer": """(
    orders
    | where(_.status == "paid")
    | group_by(_.customer)
    | map({"customer": _.key, "orders": len(_.items), "revenue": sum(pluck(_.items, "total"))})
    | sort_by(_.revenue, True)
)""",
    "top region": """(
    orders
    | where(_.status == "paid")
    | group_by(_.region)
    | map({"region": _.key, "revenue": sum(pluck(_.items, "total"))})
    | max_by(_.revenue)
)""",
    "average order value": """(
    orders
    | where(_.status == "paid")
    | pluck("total")
    | sum
) / len(where(orders, _.status == "paid"))""",
    "biggest single order": '(orders | where(_.status == "paid") | max_by(_.total)).customer',
    "customers with a refund": """(
    orders
    | where(_.status == "refunded")
    | unique_by(_.customer)
    | pluck("customer")
)""",
    "lines per status": """(
    orders
    | group_by(_.status)
    | map({"status": _.key, "lines": sum(pluck(_.items, "lines"))})
    | sort_by(_.status)
)""",
}

RULES = Evaluator(registry=standard_registry())


def main() -> None:
    print("== the orders ==\n")
    for order in ORDERS:
        print(f"  {order}")

    print("\n== the reports ==\n")
    for name, source in REPORTS.items():
        print(f"  {name}")
        print(f"    -> {RULES.evaluate(source, {'orders': ORDERS})!r}\n")

    print("== what `where` before `group_by` decided ==\n")
    with_filter = RULES.evaluate(REPORTS["revenue by customer"], {"orders": ORDERS})
    without = RULES.evaluate(
        """(
        orders
        | group_by(_.customer)
        | map({"customer": _.key, "revenue": sum(pluck(_.items, "total"))})
        | sort_by(_.revenue, True)
        )""",
        {"orders": ORDERS},
    )
    print(f"  filtered first: {[row['customer'] for row in with_filter]}")
    print(f"  not filtered:   {[row['customer'] for row in without]}")
    print(
        "\n  `initech` has one cancelled order worth 900. Filtering first drops the customer;\n"
        "  not filtering keeps them and counts the money. Neither is a bug, and only one of\n"
        "  them is the report somebody asked for."
    )

    print("\n== the shape group_by actually returns ==\n")
    print('  orders | where(_.status == "paid") | group_by(_.region)')
    groups = RULES.evaluate(
        'orders | where(_.status == "paid") | group_by(_.region)', {"orders": ORDERS}
    )
    for group in groups:
        print(f"    key={group['key']!r}  {len(group['items'])} item(s)")
    print(
        "\n  A list of {key, items}, in first-seen order. That is why `_.key` and `_.items` are\n"
        "  the names a `map` after a `group_by` reaches for."
    )


if __name__ == "__main__":
    main()
