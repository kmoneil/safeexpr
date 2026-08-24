"""The shortest useful program, and the one decision it makes for you.

    python examples/quickstart.py

Three things this is showing.

**One import and one call.** `evaluate(source, context)` parses, validates and evaluates. There
is nothing to configure to get an answer.

**The context is your data, and dots read it.** `user.plan` and `user["plan"]` are the same
lookup. Nothing else about the value is reachable, which is the point of the package.

**`evaluate` has no functions in it.** It is a wrapper around an evaluator with an empty
registry, which is already a usable language: comparison, arithmetic, boolean logic, field
access and indexing. Calling `len` needs a registry, and the second half shows why that is one
argument rather than a default.
"""

from safeexpr import Evaluator, SafeExprError, evaluate, standard_registry


def main() -> None:
    print("== evaluate(), with no setup at all ==\n")

    print("  2 + 2                                     ->", evaluate("2 + 2"))

    user = {"plan": "pro", "region": "eu", "seats": 12}
    rule = 'user.plan == "pro" and user.region in ["us", "eu"]'
    print(f"  {rule}\n    against {user}\n    ->", evaluate(rule, {"user": user}))

    print("\n  dots and brackets are the same lookup:")
    print(
        "   ",
        evaluate("user.seats", {"user": user}),
        "==",
        evaluate('user["seats"]', {"user": user}),
    )

    print("\n== and what it will not do ==\n")
    for source in ["len(user.plan)", "user.__class__", "__import__('os')"]:
        try:
            evaluate(source, {"user": user})
        except SafeExprError as error:
            print(f"  {source:<24} !! {type(error).__name__}: {error.message}")

    print("\n== functions are one argument away ==\n")

    rules = Evaluator(registry=standard_registry())
    print(
        "  len(user.plan)                            ->",
        rules.evaluate("len(user.plan)", {"user": user}),
    )
    print(
        "  upper(user.region)                        ->",
        rules.evaluate("upper(user.region)", {"user": user}),
    )

    print(
        "\n  Opting in rather than defaulting is deliberate: every registered name becomes\n"
        "  reserved on the right of a `|`. Run examples/reserved_names.py for what that means."
    )

    print("\n== build the evaluator once, evaluate many times ==\n")

    orders = [
        {"id": "a", "status": "paid", "total": 120.0},
        {"id": "b", "status": "open", "total": 40.0},
        {"id": "c", "status": "paid", "total": 10.0},
    ]
    pipeline = 'orders | where(_.status == "paid") | map(_.total) | sum'
    print(f"  {pipeline}\n    ->", rules.evaluate(pipeline, {"orders": orders}))
    print(
        "\n  One `Evaluator` is immutable and safe to share between threads, so the shape that\n"
        "  scales is one at import time and a call per record. See examples/threads.py."
    )


if __name__ == "__main__":
    main()
