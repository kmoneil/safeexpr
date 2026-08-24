"""Feature flags whose predicate is data, not a deploy.

    python examples/feature_flags.py

The job: a flag is on for some users and not others, the predicate changes weekly, and nobody
wants to ship a release for each change.

Three things worth taking from this.

**The host decides, the expression answers.** `and` returns one of its operands rather than a
boolean, exactly as Python does, so a flag check calls `bool()` on the result rather than
trusting whatever the last operand happened to be.

**A percentage rollout needs a number from the host.** The language has no clock and no random
source on purpose: an expression is a pure function of its context, so the same rule against the
same data gives the same answer forever. Hash the user id outside and pass the bucket in.

**A broken flag should not take the others down.** The loop catches per rule, because a flag
referring to a field that stopped existing is a config bug, and a config bug should degrade one
flag rather than every one after it in the dictionary.
"""

import zlib

from safeexpr import Evaluator, SafeExprError, standard_registry

FLAGS = {
    "new-checkout": 'user.plan in ["pro", "enterprise"] and user.region == "eu"',
    "beta-search": "user.beta_opt_in and user.signup_days > 30",
    "slow-rollout": "user.bucket < 25",
    "everyone": "True",
    "broken": "user.plna == 'pro'",
}

USERS = [
    {"id": "u-4821", "plan": "pro", "region": "eu", "beta_opt_in": False, "signup_days": 90},
    {"id": "u-14", "plan": "free", "region": "us", "beta_opt_in": True, "signup_days": 400},
    {"id": "u-1", "plan": "enterprise", "region": "eu", "beta_opt_in": True, "signup_days": 5},
]

RULES = Evaluator(registry=standard_registry())


def bucket(user_id: str) -> int:
    """A stable 0-99 bucket for a user, computed in the host where hashing belongs."""
    return zlib.crc32(user_id.encode()) % 100


def enabled(flag: str, user: dict) -> bool:
    context = {"user": {**user, "bucket": bucket(user["id"])}}
    return bool(RULES.evaluate(FLAGS[flag], context))


def main() -> None:
    print("== flags, as data ==\n")
    for name, rule in FLAGS.items():
        print(f"  {name:<14} {rule}")

    print("\n== per user ==\n")
    for user in USERS:
        on, broken = [], []
        for flag in FLAGS:
            try:
                if enabled(flag, user):
                    on.append(flag)
            except SafeExprError as error:
                broken.append(f"{flag}: {error.message}")
        print(f"  {user['id']:<8} bucket {bucket(user['id']):>2}  on: {on}")
        for note in broken:
            print(f"           skipped {note}")

    print(
        "\n  `broken` fails for every user, and it fails alone. A flag that references a field\n"
        "  which stopped being collected is a config bug; catching per rule is what keeps it\n"
        "  from becoming an outage in the four flags evaluated after it."
    )

    print("\n== why bool() is not decoration ==\n")
    user = USERS[1]
    raw = RULES.evaluate("user.beta_opt_in and user.signup_days", {"user": user})
    print(f"  user.beta_opt_in and user.signup_days   -> {raw!r}   ({type(raw).__name__})")
    print(f"  bool(that)                              -> {bool(raw)!r}")
    print("\n  `and` returns an operand, not a boolean. The host makes the decision.")


if __name__ == "__main__":
    main()
