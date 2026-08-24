"""Authorization policy an auditor can read, in a file rather than in a call graph.

    python examples/access_control.py

The job: who may do what, written somewhere a reviewer can see all of it at once, without the
policy becoming a program.

Three things worth taking from this.

**An unknown action denies.** A missing rule is not an empty rule. `POLICY.get(action)` returning
`None` is the line that decides whether this is a policy engine or a hole, and it is here rather
than at the bottom because that is the shape of the bug.

**A broken rule denies too.** Any `SafeExprError` is a denial, not a pass. A policy that fails
open when its rule has a typo is worse than no policy, because it looks like one.

**The context is plain data.** The actor and the resource are dictionaries, not ORM objects. That
is not a limitation of this package so much as the safe habit it encourages: convert at the
boundary and nothing about your objects is reachable at all. See examples/attributes.py for the
other route and what it costs.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

POLICY = {
    "invoice:read": '"finance" in actor.roles or resource.owner_id == actor.id',
    "invoice:void": '"finance-admin" in actor.roles and resource.status == "draft"',
    "invoice:export": '"finance" in actor.roles and actor.mfa and resource.region == actor.region',
    "report:read": "True",
    "user:impersonate": '"root" in actor.roles and actor.mfa and actor.break_glass',
    "broken:rule": '"finance" in actor.rolez',
}

ACTORS = {
    "alice": {"id": "u-1", "roles": ["finance"], "mfa": True, "region": "eu", "break_glass": False},
    "bob": {"id": "u-9", "roles": ["support"], "mfa": False, "region": "us", "break_glass": False},
    "root": {"id": "u-0", "roles": ["root"], "mfa": True, "region": "eu", "break_glass": True},
}

INVOICE = {"owner_id": "u-9", "status": "draft", "region": "eu"}

RULES = Evaluator(registry=standard_registry())


def allowed(action: str, actor: dict, resource: dict) -> tuple[bool, str]:
    """Return the decision and the reason, because a denial with no reason is unappealable."""
    rule = POLICY.get(action)
    if rule is None:
        return False, "no rule for this action"
    try:
        return bool(RULES.evaluate(rule, {"actor": actor, "resource": resource})), rule
    except SafeExprError as error:
        return False, f"rule is broken: {error.message}"


def main() -> None:
    print("== the policy ==\n")
    for action, rule in POLICY.items():
        print(f"  {action:<18} {rule}")

    print("\n== decisions, against one draft invoice owned by u-9 ==\n")
    actions = [*POLICY, "invoice:delete"]
    print("  " + "action".ljust(18) + "".join(name.ljust(9) for name in ACTORS))
    for action in actions:
        row = "  " + action.ljust(18)
        for actor in ACTORS.values():
            decision, _ = allowed(action, actor, INVOICE)
            row += ("allow" if decision else "deny").ljust(9)
        print(row)

    print("\n== the two denials that are not about the rule ==\n")
    for action in ["invoice:delete", "broken:rule"]:
        decision, reason = allowed(action, ACTORS["alice"], INVOICE)
        print(f"  {action:<16} {'allow' if decision else 'deny':<6} {reason}")

    print(
        "\n  Both fail closed. An action with no rule is not an action everybody may take, and a\n"
        "  rule that raises is not a rule that passed. Those two lines are the ones to keep if\n"
        "  you copy nothing else from this file."
    )

    print("\n== bob reads his own invoice ==\n")
    decision, rule = allowed("invoice:read", ACTORS["bob"], INVOICE)
    print(f"  {rule}")
    actor_id, owner_id = ACTORS["bob"]["id"], INVOICE["owner_id"]
    print(f"  actor.id={actor_id!r} resource.owner_id={owner_id!r} -> {decision}")
    print(
        "\n  `or` short-circuits, so the ownership check only runs for actors without the role.\n"
        "  That is Python's evaluation order, unchanged, which is the point of parsing Python."
    )


if __name__ == "__main__":
    main()
