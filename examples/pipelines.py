"""Pipes, `_`, and the nine functions that take an expression instead of a value.

    python examples/pipelines.py

`x | f(a)` is rewritten to `f(x, a)` at parse time, from the registry alone. It never looks at
your data, so an expression means the same thing whatever it is evaluated against.

Three things worth taking from this.

**`_` is the item, and it exists only inside an argument that takes an expression.** Nine
functions take one. Outside those, `_` is an error rather than an unset name, which is why
`orders | first | _.id` does not work and `(orders | first).id` does.

**There is no lambda, and none is needed.** `where(_.total > 50)` hands the function the
comparison itself, unevaluated, and the function asks it for a value once per item. Parse once,
evaluate N times, with no syntax for a user-defined function anywhere in the language.

**Nesting shadows.** Inside a nested expression argument, `_` is the inner item and `_2` is the
one outside it. Two levels is where readability gives out.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

ORDERS = [
    {"id": "a", "customer": "acme", "status": "paid", "total": 120.0, "items": ["x", "y"]},
    {"id": "b", "customer": "globex", "status": "open", "total": 40.0, "items": ["x"]},
    {"id": "c", "customer": "acme", "status": "paid", "total": 10.0, "items": ["x", "y", "z"]},
    {"id": "d", "customer": "initech", "status": "paid", "total": 300.0, "items": ["q"]},
]

TEAMS = [
    {"name": "core", "members": [{"n": "ana", "active": True}, {"n": "bo", "active": False}]},
    {"name": "ops", "members": [{"n": "cy", "active": True}]},
]

RULES = Evaluator(registry=standard_registry())


def show(source: str, context: dict) -> None:
    try:
        print(f"  {source}\n    -> {RULES.evaluate(source, context)!r}\n")
    except SafeExprError as error:
        print(f"  {source}\n    !! {type(error).__name__}: {error.message}\n")


def main() -> None:
    print("== the same call, two ways ==\n")
    show('where(orders, _.status == "paid")', {"orders": ORDERS[:2]})
    show('orders | where(_.status == "paid")', {"orders": ORDERS[:2]})
    print("  `x | f(a)` is `f(x, a)`. The pipe form reads in the order the data moves.\n")

    print("== the tour ==\n")
    context = {"orders": ORDERS}
    show('orders | where(_.status == "paid") | pluck("id")', context)
    show("orders | map(_.total) | sum", context)
    show('orders | sort_by(_.total, True) | take(2) | pluck("id")', context)
    show("(orders | max_by(_.total)).id", context)
    show("orders | group_by(_.customer) | map(len(_.items))", context)
    show('orders | unique_by(_.customer) | pluck("customer")', context)
    show("orders | any_(_.total > 200)", context)
    show("orders | all_(_.total > 5)", context)
    show("orders | len", context)

    print("== filter before you map ==\n")
    show('orders | where(_.status == "paid") | map(len(_.items)) | sum', context)
    print(
        "  The other order, `map(...) | where(...)`, charges the map for every row rather than\n"
        "  for the ones that survived. Same answer, more steps. See docs/performance.md.\n"
    )

    print("== where `_` is not in scope ==\n")
    show("orders | first | _.id", context)
    show("(orders | first).id", context)
    show("first(orders).id", context)
    print(
        "  `first` takes no expression argument, so nothing is bound on its right. Reach into\n"
        "  the result with ordinary syntax instead.\n"
    )

    print("== nesting: `_` inside `_` ==\n")
    show(
        'teams | map({"team": _.name, "active": len(where(_.members, _1.active))})',
        {"teams": TEAMS},
    )
    show("teams | map(_2.name)", {"teams": TEAMS})
    print(
        "  `_` and `_1` are the innermost item; `_2` is one level out. Reaching past what is in\n"
        "  scope names both numbers rather than returning nothing.\n"
    )

    print("== a pipeline over several lines ==\n")
    report = """(
    orders
    | where(_.status == "paid")
    | group_by(_.customer)
    | map({"customer": _.key, "revenue": sum(pluck(_.items, "total"))})
    | sort_by(_.revenue, True)
)"""
    print("\n".join(f"  {line}" for line in report.splitlines()))
    print(f"    -> {RULES.evaluate(report, context)!r}\n")
    print(
        "  The brackets are load-bearing and they are Python's rule, not ours: a bare newline\n"
        "  ends an expression. Inside a pair of brackets it does not."
    )


if __name__ == "__main__":
    main()
