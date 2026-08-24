# Errors

Every failure this package produces is a `SafeExprError`. One `except` catches all of them, and
nothing else escapes: not a `SyntaxError` from the parser, not a `RecursionError` from a deep
tree, not a `TypeError` from a comparison that made no sense.

```python
from safeexpr import Evaluator, SafeExprError, standard_registry

rules = Evaluator(registry=standard_registry())
try:
    rules.evaluate("orders | where(_.totl > 10)", {"orders": [{"total": 5}]})
except SafeExprError as error:
    print(error.annotated())
# no field `totl`, did you mean `total`?
#   orders | where(_.totl > 10)
#                  ^
```

## The taxonomy

Seven classes, and which one you get says **who has to fix it**.

| Error | Raised when | Whose problem |
| --- | --- | --- |
| `SourceTooLongError` | The source is over the byte cap, checked before parsing | The expression author's |
| `ParseError` | The source is not a single valid Python expression | The expression author's |
| `ValidationError` | It parsed, but uses a construct outside the language | The expression author's |
| `EvaluationError` | Undefined name, missing field, type mismatch, division by zero | Usually the author's, sometimes the data's |
| `BudgetExceededError` | The evaluation ran out of steps | The host's, mostly: see [Performance](performance.md) |
| `ReservedNameError` | A context key collides with a function on the right of a `\|` | The host's |
| `InternalError` | Something this package did not anticipate | Ours. Please report it |

`ReservedNameError` is deliberately **not** an `EvaluationError`. The expression is well formed
and the rule is right; what is wrong is that the host's data and the host's registry both claim
a name. Reporting that as "your rule is wrong" would blame the wrong person.

`SafeExprError` is the base of all seven, so:

```python
from safeexpr import BudgetExceededError, ReservedNameError, SafeExprError

issubclass(ReservedNameError, SafeExprError) and issubclass(BudgetExceededError, SafeExprError)
# True
```

## What an error carries

```python
from safeexpr import Evaluator, SafeExprError

try:
    Evaluator().evaluate("(a +\n unknown)", {"a": 1})
except SafeExprError as error:
    print(error.message)
    print(repr(error.source))
    print(error.lineno, error.offset)
# `unknown` is not defined
# '(a +\n unknown)'
# 2 2
```

- `message`: the sentence, with no position in it
- `source`: the expression as you passed it, unmodified
- `lineno` and `offset`: 1-based, matching `SyntaxError`, and `None` when the failure had no
  position
- `annotated()`: the message, the offending line, and a caret

**And nothing else.** No reference to a causing exception, no `args` carried through from one, no
notes copied from one. That is not tidiness: an `AttributeError` carries `.obj`, a live reference
to the object whose attribute lookup failed, and a `raise ... from None` leaves it reachable
through `__context__`. Errors here are constructed after the handler has exited rather than
wrapped inside it, which is the only spelling that actually drops the reference.

## What an error is allowed to say about your data

One thing, deliberately: **the type of a value it could not work with**.

```python
from safeexpr import Evaluator, SafeExprError, standard_registry


class Order:
    pass


rules = Evaluator(registry=standard_registry())
try:
    rules.evaluate("order > 10", {"order": Order()})
except SafeExprError as error:
    print(error.message)
# cannot compare `Order` with `int`
```

That tells an expression author a class name from your context. Never a value, never a `repr`,
and the name is a string rather than a class object, so there is nothing to climb from it. The
alternative, "cannot compare these two things", turns every type mismatch into a support ticket.
[The threat model](../THREAT-MODEL.md) records the trade.

## Showing an error to the person who wrote the expression

The author of a rule is usually not the operator of the process that runs it, and
`annotated()` is written for the author:

```python
from safeexpr import Evaluator, SafeExprError, standard_registry

rules = Evaluator(registry=standard_registry())


def check(source: str, sample: dict) -> str:
    try:
        rules.evaluate(source, sample)
    except SafeExprError as error:
        return error.annotated()
    return "ok"


print(
    check(
        'customer.tier == "gold" and customer.balence > 0',
        {"customer": {"tier": "gold", "balance": 5}},
    )
)
# no field `balence`, did you mean `balance`?
#   customer.tier == "gold" and customer.balence > 0
#                               ^
```

In a web form, the three fields to render are `message`, `lineno` and `offset`. In a log line,
prefer `message` plus the source as a separate field, so the caret does not have to survive your
log formatter.

## Suggestions

Misspellings get a suggestion when there is an unambiguous one, for names, fields and functions:

```python
from safeexpr import Evaluator, SafeExprError, standard_registry

rules = Evaluator(registry=standard_registry())
for source in ["frist([1])", "customer.nmae", "custmer"]:
    try:
        rules.evaluate(source, {"customer": {"name": "a"}})
    except SafeExprError as error:
        print(error.message)
# `frist` is not a function, did you mean `first`?
# no field `nmae`, did you mean `name`?
# `custmer` is not defined, did you mean `customer`?
```

## Refusals worth recognising

These are `ValidationError`s, raised before anything is evaluated, and each one names the
construct rather than the rule it broke:

```python
from safeexpr import Evaluator, SafeExprError, standard_registry

rules = Evaluator(registry=standard_registry())
for source in ['f"{x}"', "[i for i in x]", "lambda a: a", "x.upper()", "x.__class__"]:
    try:
        rules.evaluate(source, {"x": "text"})
    except SafeExprError as error:
        print(f"{type(error).__name__}: {error.message}")
# ValidationError: f-strings are not supported (use `join` or `+` to build strings)
# ValidationError: list comprehensions are not supported (use `map` or `where` instead)
# ValidationError: lambda expressions are not supported (pass the expression directly, as in `where(_.price > 10)`)
# ValidationError: method calls on values are not supported; only named functions can be called
# ValidationError: attribute `.__class__` is not available: attributes beginning with an underscore are blocked
```

Every one of those is a line in [the threat model](../THREAT-MODEL.md) with a published escape
behind it, which is why they are refusals rather than gaps.

## Errors from your own functions

A registry function rejects its input by raising `FunctionError`, and the evaluator turns that
into a positioned `EvaluationError` naming the function:

```python
from safeexpr import Evaluator, Function, FunctionError, SafeExprError


def _percent(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FunctionError("needs a number")
    return value * 100


rules = Evaluator(registry={"percent": Function("percent", _percent, arity=(1, 1))})
try:
    rules.evaluate('percent("x")')
except SafeExprError as error:
    print(error.message)
# `percent`: needs a number
```

Do not repeat the function's own name in the message: the evaluator prefixes it, and
`FunctionError("percent: needs a number")` prints it twice. `FunctionError` carries a string and
nothing else, for the same reason the built-in errors do.

## What to catch, and where

| Where | Catch | Do |
| --- | --- | --- |
| Config load, or a rule editor | `SafeExprError` | Reject the rule, show `annotated()` to its author |
| Per record, in a loop | `SafeExprError` | Log it with the rule name, and keep evaluating the other rules |
| A request path | `SafeExprError` | Fail the request closed, and never let an expression's message into a public response verbatim |

That last one is worth being deliberate about. Error messages here name context keys and type
names from your data, which is exactly what a rule author needs and not necessarily what an
unauthenticated caller should be told.
