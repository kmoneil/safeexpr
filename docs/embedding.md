# Embedding safeexpr

Everything a host has to decide, in the order it comes up.

## One evaluator, built at startup

`Evaluator` is fixed after construction and safe to share between threads, so the shape that works
is one instance at import time and a call per record. It is also the shape that is *fast*: an
evaluator remembers the compiled form of every source it has seen, so a rule evaluated twice is
parsed once, and building a fresh evaluator per call throws that away.

```python
from safeexpr import Evaluator, standard_registry

RULES = Evaluator(registry=standard_registry(), budget=6_000_000)


def matches(rule: str, record: dict) -> bool:
    return bool(RULES.evaluate(rule, {"record": record}))
```

The registry and the attribute allowlist are **copied** at construction rather than held, so
mutating the dictionary you passed in afterwards cannot change what an evaluator can do.
`__slots__` means nothing can be attached to an instance later either.

## The three knobs

`Evaluator` takes three arguments, and that is the whole configuration surface.

| Argument | Default | What it decides |
| --- | --- | --- |
| `registry` | empty | The only names an expression may call |
| `attribute_types` | empty | Opt-in `getattr`, as type to permitted attribute names |
| `budget` | 6,000,000 | Steps one `evaluate` call may spend |

Everything else is a module constant rather than configuration, on purpose: those constants bound
what an expression can do to your process, and a knob for that is a knob an over-eager caller
turns. See [Performance and limits](performance.md).

## Validate at load time

The two failures worth catching before a request arrives are a rule that does not parse and a
rule that parses against nothing real. Catch both by evaluating every rule against a
representative sample when you load the configuration:

```python
from safeexpr import Evaluator, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())

SAMPLE = {"customer": {"plan": "pro", "lifetime_value": 100.0, "orders": 3}}


def load(rules: dict[str, str]) -> dict[str, str]:
    """Return the rules that work, raising on the first that does not."""
    for name, source in rules.items():
        try:
            RULES.evaluate(source, SAMPLE)
        except SafeExprError as error:
            raise ValueError(f"rule {name!r} is broken: {error.annotated()}") from None
    return rules


load({"vip": "customer.lifetime_value > 1000"})
# {'vip': 'customer.lifetime_value > 1000'}
```

A sample record is doing real work here. Parsing catches `customer.plan ==` with nothing after
it; only an evaluation catches `customer.lifetime_vlaue`, which is the typo that would otherwise
reach production and quietly evaluate nothing.

Keep the sample beside the rules and treat it as part of the schema: when a field is renamed, the
sample changes, and every rule still referring to the old name fails the next deploy rather than
the next request.

## Check for name collisions

A registry name on the right of a `|` shadows a context key of the same name, and the evaluator
refuses rather than reading past it. That refusal is correct and it is nicer to know at startup:

```python
from safeexpr import Evaluator, standard_registry

RULES = Evaluator(registry=standard_registry())
CONTEXT_KEYS = {"customer", "order", "first", "region"}

sorted(CONTEXT_KEYS & RULES.function_names)
# ['first']
```

Three ways out, in order of preference: rename the context key, rename the function, or drop the
function from the registry. All three are one line, because the registry is a plain dictionary.

```python
registry = standard_registry()
registry["head"] = registry.pop("first")  # rename
del registry["slugify"]  # or drop what you do not need
```

See [Reserved names](pipes.md#reserved-names) for why the collision exists at all.

## Adding your own functions

A registry entry is a name and a `Function`. The simplest kind wraps a plain callable:

```python
from safeexpr import Evaluator, Function, FunctionError, standard_registry


def _round_to(value, places):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FunctionError(f"needs a number, got `{type(value).__name__}`")
    return round(value, places)


registry = standard_registry()
registry["round_to"] = Function("round_to", _round_to, arity=(2, 2), cost=1)

rules = Evaluator(registry=registry)
rules.evaluate("round_to(1.23456, 2)")
# 1.23
```

Four fields, and three of them earn their place:

- **`arity`** is `(minimum, maximum)`, with `None` as the maximum meaning variadic. Declaring it
  lets the evaluator check the count **before** your function runs, which is what makes a
  `TypeError` from inside your function mean "this value is wrong" rather than "this argument
  count is wrong".
- **`cost`** is what one call charges the step budget, as a per-call figure. Work that scales with
  the input is charged per item by the budget itself; this is the fixed overhead on top. One is
  right for almost everything. `matches` is ten, because an accepted pattern runs inside `re`
  where the step counter cannot follow it.
- **`lazy`** is the set of argument positions the evaluator must **not** evaluate. A function with
  a lazy position receives a `LazyExpr` and calls `.evaluate(item)` on it once per item, which is
  how `where(_.price > 10)` works without a lambda:

```python
from safeexpr import Evaluator, Function, standard_registry


def _count_where(items, predicate):
    return sum(1 for item in items if predicate.evaluate(item))


registry = standard_registry()
registry["count_where"] = Function(
    "count_where", _count_where, lazy=frozenset({1}), arity=(2, 2), cost=2
)

rules = Evaluator(registry=registry)
rules.evaluate("orders | count_where(_.total > 50)", {"orders": [{"total": 100}, {"total": 10}]})
# 1
```

### Rules for a function you add

The shipped tiers hold themselves to these, and a function you add is inside the same trust
boundary:

1. **Reject with `FunctionError`, carrying a string and nothing else.** No value, no caught
   exception, no `args` passed through. The evaluator catches your error inside an exception
   handler, so anything reachable from it is reachable through `__context__`.
2. **Do not name the function in the message.** The evaluator prefixes it.
3. **No reflection.** No `type()`, no `getattr`, no `__class__`, no `dir`. A class object is
   climbable and a type's *name* is not, which is why `describe_type` exists and returns a string.
4. **No I/O and no clock.** An expression is a pure function of its context, and a function that
   reads a file or the time breaks that for every rule in the system, not just the ones that call
   it.
5. **Bound your output.** A function that can return something much larger than its input is a
   memory amplifier the step budget prices only approximately.

## Attribute access on your own objects

By default, `.name` works on mappings and nothing else. To reach attributes on your own types,
register them:

```python
from dataclasses import dataclass

from safeexpr import Evaluator, standard_registry


@dataclass
class Customer:
    plan: str
    region: str
    api_key: str = "secret"


rules = Evaluator(registry=standard_registry(), attribute_types={Customer: {"plan", "region"}})
rules.evaluate('customer.plan == "pro"', {"customer": Customer("pro", "eu")})
# True
```

Anything not listed is refused, including on a registered type:

```python
from safeexpr import SafeExprError

try:
    rules.evaluate("customer.api_key", {"customer": Customer("pro", "eu")})
except SafeExprError as error:
    print(error.message)
# cannot read `.api_key` on a value of type `Customer`; attribute access works on mappings, and on other types only where the host has registered them
```

**This is the one argument that gives something up, and it is worth being blunt about.**
Attribute traversal on arbitrary objects is where essentially every published Python sandbox
escape has started. Registering a type opts that type back into it, limited to the names you list,
and what you list is yours to defend. A property that runs code, a lazy loader that issues a
query, a descriptor with a side effect: all of those are reachable through a name on this list.

The safer shape, when it is available, is to convert to plain data at the boundary:

```python
rules.evaluate('customer.plan == "pro"', {"customer": {"plan": "pro", "region": "eu"}})
# True
```

Then nothing is registered, nothing is reachable, and the expression cannot see an object at all.

## Threads

One `Evaluator` is safe to share between threads, and that is a contract rather than an
observation. Everything one evaluation needs, the step counter and the `_` scope stack included,
lives in a call-scoped object, so the budget is per call rather than per evaluator and two threads
never spend each other's. **No evaluation can observe state left by another**, which is the
substance of the contract and is a slightly narrower claim than "nothing on the instance ever
changes": what does change is a memoisation cache, and the paragraph after the example says why
that is the same promise.

```python
from concurrent.futures import ThreadPoolExecutor

from safeexpr import Evaluator, standard_registry

RULES = Evaluator(registry=standard_registry())
records = [{"n": i} for i in range(1000)]

with ThreadPoolExecutor(max_workers=8) as pool:
    hits = list(pool.map(lambda r: RULES.evaluate("record.n % 7 == 0", {"record": r}), records))

sum(hits)
# 143
```

Nothing here starts a thread, installs a signal handler, or sets a timeout, so there is no
interaction with whatever your host already does about any of those.

Two things are cached. Each evaluator remembers the compiled form of the sources it has seen, and a
bounded cache of compiled regular-expression patterns is shared process-wide. Both are memoisation
caches and **both are invisible to the budget**: compiling is a pure function of the source and the
registry, `matches` is charged its declared cost whether the pattern was cached or not, and the
budget reads the same on a warm cache as on a cold one. The suite proves that by bisecting the
smallest budget that evaluates in each state rather than by asserting it.

The compiled-expression cache holds 128 entries per evaluator and is dropped whole when it fills.
If you evaluate more than 128 distinct sources on one evaluator in rotation you will miss every
time, which costs what this package cost before the cache existed rather than anything worse. If
you are in that position, more evaluators, each with its own working set, is the shape that helps.

## Untrusted input

Read this before you point this package at anonymous internet users.

> Expressions come from semi-trusted config authors, not anonymous internet users. The sandbox is
> defense in depth for a config-authoring surface. If you must run genuinely hostile input, use
> process isolation. No in-interpreter CPython sandbox, this one included, should be your only
> boundary.

What that means in practice, if your expressions do come from somewhere you do not control:

- Put the evaluation in a subprocess with its own memory limit, and treat this package as the
  layer that makes the common case cheap rather than as the boundary.
- Lower the budget. Six million steps is sized for a hundred thousand items; a rule over a
  request-sized context needs orders of magnitude less. See
  [Performance](performance.md#choosing-a-budget).
- Keep `attribute_types` empty. Convert to plain data at the boundary instead.
- Do not put anything in the context you would not put in a log line, since a type mismatch names
  a type from it.

[The threat model](../THREAT-MODEL.md) is the catalogue behind that paragraph: nine classes of
published sandbox escape, one section each, with the mechanism, the advisories where it has broken
a real project, and the corpus entries proving it is unreachable here.
