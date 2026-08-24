# Getting started

Five minutes, from nothing to a rule engine your config authors can write against.

## Install

```console
pip install safeexpr
```

One pure-Python wheel, `py3-none-any`, and nothing else. There is no compiled artifact to build,
no wheel to miss for your platform, and no transitive dependency to audit. Python 3.11 through
3.14.

Not published yet, so until the first release:

```console
pip install git+https://github.com/kmoneil/safeexpr
```

## Your first expression

```python
from safeexpr import evaluate

evaluate("2 + 2")
# 4
```

That is the whole entry point for a one-off. It parses the source, checks every node against an
allowlist, evaluates it, and returns the value.

## The context is your data

The second argument is a mapping of names the expression may read:

```python
from safeexpr import evaluate

evaluate(
    'user.plan == "pro" and user.region in ["us", "eu"]',
    {"user": {"plan": "pro", "region": "eu"}},
)
# True
```

Dots read dictionary keys. `user.plan` and `user["plan"]` are the same thing, and the dotted form
is there because config authors write it without being taught. Nothing else about the value is
reachable: `user.__class__` is refused by name, before evaluation starts.

An expression is a **question about data**, not a program. There is no assignment, no loop, no
function definition and no import, so an expression always terminates and always produces exactly
one value.

## Functions are opt-in

`evaluate` above is a convenience wrapper around an evaluator with **no functions at all**. That
is a usable language already: comparison, arithmetic, boolean logic, field access and indexing.

To call functions, build an `Evaluator` and hand it a registry:

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())

rules.evaluate("len(items) > 2", {"items": [1, 2, 3]})
# True
```

`standard_registry()` is the forty-one shipped functions, across six tiers: collections, types,
strings, regex, dates and URLs. See [the function reference](functions.md).

**Why opting in rather than defaulting?** Because a registered name is reserved on the right of a
`|`. Adding forty-one names to an evaluator that only wanted `a == b` would change the meaning of
expressions to pay for functions nobody asked for. [Reserved names](pipes.md#reserved-names) has
the full argument, and the one error it can produce.

## Build one evaluator, evaluate many times

`Evaluator` is immutable after construction and safe to share between threads, so the shape that
scales is one evaluator at startup and a call per row:

```python
from safeexpr import Evaluator, standard_registry

RULES = Evaluator(registry=standard_registry())  # once, at import time
RULE = 'customer.plan == "pro" and customer.seats >= 5'  # from your config store


def is_eligible(customer: dict) -> bool:
    return bool(RULES.evaluate(RULE, {"customer": customer}))


is_eligible({"plan": "pro", "seats": 12})
# True
```

Each `evaluate` call parses its source: there is no expression cache. On any collection worth
filtering the parse is a small fraction of the work, and the one thing this package does cache,
compiled regular-expression patterns, is bounded and shared process-wide rather than per
evaluator.

## Pipes, in thirty seconds

With the standard registry, `|` chains a value through functions. The value on the left becomes
the **first argument** of the call on the right:

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())

rules.evaluate(
    'orders | where(_.status == "paid") | map(_.total) | sum',
    {"orders": [{"status": "paid", "total": 120}, {"status": "open", "total": 40}]},
)
# 120
```

`_` is the item under consideration. `where(_.status == "paid")` is not a string that gets
re-parsed per item and it is not a lambda: the function receives the unevaluated comparison and
runs it once per item. That is why this language needs no lambda syntax, and
[Pipes and `_`](pipes.md) is the longer version.

## When an expression is wrong

Every failure is a `SafeExprError`, and every one carries a position:

```python
from safeexpr import Evaluator, SafeExprError, standard_registry

rules = Evaluator(registry=standard_registry())

try:
    rules.evaluate("orders | where(_.total > 10) | frist", {"orders": []})
except SafeExprError as error:
    print(error.annotated())
```

```text
`frist` is not a function, so `|` here means bitwise or, did you mean `first`?
  orders | where(_.total > 10) | frist
  ^
```

That output is meant for the person who wrote the expression, which is usually not the person
running the process. [Errors](errors.md) covers the taxonomy, what an error is allowed to say
about your data, and how to surface one in a config-editing UI.

## Where to go next

| If you want | Read |
| --- | --- |
| The whole syntax, and what is deliberately absent | [The language](language.md) |
| Every shipped function, with a worked example each | [Function reference](functions.md) |
| Pipes, `_`, nesting, and the one name collision | [Pipes and `_`](pipes.md) |
| Rules for feature flags, alerts, access control, validation | [Recipes](recipes.md) |
| Wiring this into a service, and what to check at startup | [Embedding safeexpr](embedding.md) |
| What each error means and how to show it to an author | [Errors](errors.md) |
| The step budget, and what a rule costs | [Performance and limits](performance.md) |

Or run the code: [`examples/`](../examples/README.md) has one runnable program per topic, each of
which prints its own output and takes no arguments.
