# Recipes

Nine jobs people actually reach for an expression language to do. Each one is a complete program:
the rules are data, the host code is short, and the output shown is what it prints.

Every recipe here has a runnable twin in [`examples/`](../examples/README.md), and the code blocks
below are executed by [`tests/test_docs.py`](../tests/test_docs.py).

**Jump to:** [Feature flags](#feature-flags) · [Alert rules](#alert-rules) ·
[Access control](#access-control) · [Validating records](#validating-records) ·
[Routing](#routing-notifications) · [Pricing](#pricing-and-discounts) ·
[Rollups](#rollups-and-reporting) · [URL allowlists](#url-allowlists) ·
[Rules from a config file](#rules-from-a-config-file)

## Feature flags

The problem: a flag is on for some users and not others, the predicate changes weekly, and you do
not want a deploy for each change.

```python
from safeexpr import Evaluator, standard_registry

FLAGS = {
    "new-checkout": 'user.plan in ["pro", "enterprise"] and user.region == "eu"',
    "beta-search": "user.beta_opt_in and user.signup_days > 30",
    "everyone": "True",
}

rules = Evaluator(registry=standard_registry())


def enabled(flag: str, user: dict) -> bool:
    return bool(rules.evaluate(FLAGS[flag], {"user": user}))


user = {"plan": "pro", "region": "eu", "beta_opt_in": False, "signup_days": 90}
print([flag for flag in FLAGS if enabled(flag, user)])
# ['new-checkout', 'everyone']
```

**Why `bool(...)` on the result.** An expression returns whatever it evaluates to, and `and`
returns one of its operands rather than a boolean, exactly as Python does. A flag check wants a
decision, so the host makes one.

A percentage rollout needs a number the expression can compare, and the expression has no clock
and no random source on purpose. Hash the user id in the host and pass the bucket in:

```python
import zlib


def bucket(user_id: str) -> int:
    return zlib.crc32(user_id.encode()) % 100


rules.evaluate("bucket < 25", {"bucket": bucket("user-4821")})  # bucket 15, so in
# True
```

## Alert rules

The problem: an on-call rota wants to write its own thresholds, and a bad threshold should be a
refused rule rather than a paging storm at 3am.

```python
from safeexpr import Evaluator, SafeExprError, standard_registry

ALERTS = [
    {"name": "error-rate", "when": "metrics.error_rate > 0.05", "page": True},
    {"name": "slow-p99", "when": "metrics.p99_ms > 800 and metrics.rpm > 100", "page": True},
    {"name": "disk", "when": "any_(hosts, _.disk_pct > 90)", "page": False},
]

rules = Evaluator(registry=standard_registry())
context = {
    "metrics": {"error_rate": 0.07, "p99_ms": 410, "rpm": 950},
    "hosts": [{"name": "web-1", "disk_pct": 62}, {"name": "web-2", "disk_pct": 94}],
}

for alert in ALERTS:
    try:
        if rules.evaluate(alert["when"], context):
            print(f"FIRING {alert['name']}  page={alert['page']}")
    except SafeExprError as error:
        print(f"BROKEN  {alert['name']}: {error.message}")
# FIRING error-rate  page=True
# FIRING disk  page=False
```

The `except` is the part worth copying. A rule that references a metric which stopped being
collected raises `no field ...`, and an alerting loop that lets that escape stops evaluating every
rule after it.

## Access control

The problem: who may do what, written where an auditor can read it, without the policy becoming a
program.

```python
from safeexpr import Evaluator, standard_registry

POLICY = {
    "invoice:read": '"finance" in actor.roles or resource.owner_id == actor.id',
    "invoice:void": '"finance-admin" in actor.roles and resource.status == "draft"',
    "report:export": '"analyst" in actor.roles and actor.mfa',
}

rules = Evaluator(registry=standard_registry())


def allowed(action: str, actor: dict, resource: dict) -> bool:
    rule = POLICY.get(action)
    if rule is None:
        return False  # deny by default, never by absent rule
    return bool(rules.evaluate(rule, {"actor": actor, "resource": resource}))


actor = {"id": "u-1", "roles": ["finance"], "mfa": True}
invoice = {"owner_id": "u-9", "status": "draft"}

print(allowed("invoice:read", actor, invoice))
print(allowed("invoice:void", actor, invoice))
print(allowed("invoice:delete", actor, invoice))
# True
# False
# False
```

**An unknown action denies.** A missing rule is not an empty rule, and `POLICY.get(action)`
returning `None` is the case that decides whether this is a policy engine or a hole.

## Validating records

The problem: rejecting bad rows at ingest, with a message per rule, written by the team that owns
the data rather than the team that owns the pipeline.

```python
from safeexpr import Evaluator, standard_registry

CHECKS = [
    ("email looks wrong", r'matches(record.email, "^[^@ ]+@[^@ ]+\\.[a-z]{2,}$")'),
    ("age out of range", "record.age >= 18 and record.age < 120"),
    ("no items", "len(record.items) > 0"),
    ("total does not match items", "record.total == sum(pluck(record.items, 'price'))"),
]

rules = Evaluator(registry=standard_registry())


def problems(record: dict) -> list[str]:
    context = {"record": record}
    return [message for message, rule in CHECKS if not rules.evaluate(rule, context)]


good = {"email": "bob@example.com", "age": 31, "items": [{"price": 10}, {"price": 5}], "total": 15}
bad = {"email": "not-an-email", "age": 12, "items": [], "total": 0}

print(problems(good))
print(problems(bad))
# []
# ['email looks wrong', 'age out of range', 'no items']
```

**The pattern is a raw string, and the doubled backslash is not a typo.** The expression source
has to contain `\\.`, because the evaluator parses that source with Python's own parser, and a
single `\.` inside a string literal is an unrecognised escape. Two languages nest here, and this
is the one line where you pay for it.

The fourth check passes on the second record because an empty list sums to zero, which is
arithmetic behaving correctly and a rule set saying something it did not mean. Ordering checks so
that the structural ones run first, and stopping at the first failure, is usually what you want.

## Routing notifications

The problem: which channel an event goes to, changed by the people who get woken up.

```python
from safeexpr import Evaluator, standard_registry

ROUTES = [
    ("pagerduty", 'event.severity == "critical" and event.env == "prod"'),
    ("slack-eng", 'event.severity in ["critical", "warning"]'),
    ("digest", "True"),
]

rules = Evaluator(registry=standard_registry())


def route(event: dict) -> str:
    for channel, rule in ROUTES:
        if rules.evaluate(rule, {"event": event}):
            return channel
    return "digest"


print(route({"severity": "critical", "env": "prod"}))
print(route({"severity": "warning", "env": "staging"}))
print(route({"severity": "info", "env": "prod"}))
# pagerduty
# slack-eng
# digest
```

First match wins, and the last rule is `True` so the list is total. A routing table whose last row
can fail to match is a table with a silent drop in it.

## Pricing and discounts

The problem: a discount rule per campaign, priced by someone in marketing, applied to a basket.

```python
from safeexpr import Evaluator, standard_registry

CAMPAIGNS = [
    {"code": "BULK10", "when": "len(basket.items) >= 10", "off": "0.10"},
    {"code": "PRO5", "when": 'customer.plan == "pro"', "off": "0.05"},
    {"code": "FIRST", "when": "customer.orders == 0", "off": "0.15"},
]

rules = Evaluator(registry=standard_registry())
context = {
    "basket": {"items": [{"price": 20.0}] * 12},
    "customer": {"plan": "pro", "orders": 4},
}

subtotal = rules.evaluate("sum(pluck(basket.items, 'price'))", context)
best = max(
    (
        float(rules.evaluate(c["off"], context))
        for c in CAMPAIGNS
        if rules.evaluate(c["when"], context)
    ),
    default=0.0,
)
print(f"subtotal {subtotal:.2f}  discount {best:.0%}  total {subtotal * (1 - best):.2f}")
# subtotal 240.00  discount 10%  total 216.00
```

Discounts stack badly and expensively, so the host takes the best one rather than summing them.
That decision belongs in the host, not in each campaign's rule, which is the general shape:
**expressions answer questions, the host makes policy out of the answers.**

## Rollups and reporting

The problem: a saved report definition, written once, run against whatever the data is today.

```python
from safeexpr import Evaluator, standard_registry

REPORT = """(
    orders
    | where(_.status == "paid")
    | group_by(_.customer)
    | map({"customer": _.key, "orders": len(_.items), "revenue": sum(pluck(_.items, "total"))})
    | sort_by(_.revenue, True)
    | take(3)
)"""

rules = Evaluator(registry=standard_registry())
orders = [
    {"customer": "acme", "status": "paid", "total": 120.0},
    {"customer": "acme", "status": "paid", "total": 80.0},
    {"customer": "globex", "status": "paid", "total": 150.0},
    {"customer": "initech", "status": "open", "total": 900.0},
]
for row in rules.evaluate(REPORT, {"orders": orders}):
    print(row)
# {'customer': 'acme', 'orders': 2, 'revenue': 200.0}
# {'customer': 'globex', 'orders': 1, 'revenue': 150.0}
```

**An expression can span lines, inside parentheses.** The source is parsed by Python's own
parser, so the rule for wrapping is Python's: a bare newline ends the expression, and a pair of
brackets around it lets a pipeline run one step per line, indented, reading the way a query does. Note that `initech` is absent
rather than zero: `where` runs first, so a customer with no paid orders is not a group.

## URL allowlists

The problem: a webhook destination somebody else supplied, checked against a rule you can change
without a deploy.

```python
from safeexpr import Evaluator, standard_registry

RULE = (
    'url_host(target) in ["hooks.example.com", "api.partner.test"]'
    ' and starts_with(url_path(target), "/v2/")'
)

rules = Evaluator(registry=standard_registry())
for target in [
    "https://hooks.example.com/v2/inbound",
    "https://hooks.example.com/v1/inbound",
    "https://evil.test/v2/inbound",
    "hooks.example.com/v2/inbound",
]:
    print(bool(rules.evaluate(RULE, {"target": target})), target)
# True https://hooks.example.com/v2/inbound
# False https://hooks.example.com/v1/inbound
# False https://evil.test/v2/inbound
# False hooks.example.com/v2/inbound
```

The last line is the trap worth staring at. Without a scheme there is no authority to parse, so
`url_host` is `None` and the rule is false. It fails closed here, which is the right way round,
but a rule written the other way (`not (url_host(target) in BLOCKED)`) would fail **open** on
exactly that input. This package parses a URL and never fetches one: if the decision matters,
resolve and pin the address as well, which is a different problem than this library solves.

## Rules from a config file

The problem: the rules live in JSON or YAML next to the service, and a broken rule should fail the
config load rather than the request.

```python
import json

from safeexpr import Evaluator, SafeExprError, standard_registry

CONFIG = json.loads("""
{
  "rules": [
    {"name": "vip",       "when": "customer.lifetime_value > 10000"},
    {"name": "at-risk",   "when": "customer.days_since_order > 90"},
    {"name": "malformed", "when": "customer.plan == "}
  ]
}
""")

rules = Evaluator(registry=standard_registry())
sample = {"customer": {"lifetime_value": 250.0, "days_since_order": 400, "plan": "free"}}

loaded, rejected = [], []
for rule in CONFIG["rules"]:
    try:
        rules.evaluate(rule["when"], sample)  # a dry run at load time
    except SafeExprError as error:
        rejected.append((rule["name"], error.message))
    else:
        loaded.append(rule)

print("loaded:  ", [rule["name"] for rule in loaded])
print("rejected:", rejected)
# loaded:   ['vip', 'at-risk']
# rejected: [('malformed', 'could not parse expression: invalid syntax')]
```

**Evaluate against a sample at load time, not just parse.** A parse catches the syntax error
above; only an evaluation catches a rule referring to `customer.lifetme_value`, which is the
typo that will otherwise page you. Keep one representative record per rule set for exactly this,
and see [Embedding](embedding.md#validate-at-load-time) for the fuller version, including the
collision check.
