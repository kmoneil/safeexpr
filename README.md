# safeexpr

CEL-class expression evaluation for Python at simpleeval's dependency cost: **zero runtime
dependencies**, pure stdlib, no compiled wheels.

```
user.plan == "pro" and user.region in ["us", "eu"]
metrics | where(_.value > threshold) | first
orders | where(_.status == "paid") | group_by(_.customer_id)
```

## Status

**Alpha.** The evaluator works for comparison, arithmetic, field access and indexing:

```python
from safeexpr import evaluate

evaluate(
    'user.plan == "pro" and user.region in ["us", "eu"]', {"user": {"plan": "pro", "region": "eu"}}
)
# True
```

Pipes and the collections tier work, and are opt-in:

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())
rules.evaluate(
    'orders | where(_.status == "paid") | group_by(_.customer_id)'
    ' | map(merge(_, {"n": len(_.items)}))',
    {"orders": [{"customer_id": "c1", "status": "paid", "items": [1, 2]}]},
)
# [{'key': 'c1', 'items': [{'customer_id': 'c1', 'status': 'paid', 'items': [1, 2]}], 'n': 1}]
```

`Evaluator()` starts with no functions, and adding them is one argument rather than a default,
because a registered name is reserved on the right of a `|`: with `first` registered,
`flags | first` calls it whatever the context says `first` is.

### Reserved names

Registry membership is what tells the pipe transform that a `|` is a pipe rather than bitwise-or,
and that decision never looks at your data, so an expression means the same thing whatever it is
evaluated against. The price is that **a function name on the right of a `|` always wins**: with
`first` registered, `flags | first` calls the function even if your context has a `first`.

That collision is refused with a `ReservedNameError` naming the key, rather than quietly reading
past it. Only the right of a `|` is affected. A bare `min` reads your data as it always did, so
`metrics | where(_.value > min)` against `{"min": 10}` is correct and is not refused, and
`first(x)` is unambiguous because a value from the context can never be called. `bitor(a, b)` is
the way to say bitwise-or on a value that shares a function's name.

The reserved names are exactly the registry's, so `Evaluator.function_names` is the list, and

```python
sorted(set(my_context) & rules.function_names)  # empty means no collisions
```

is the check to run at startup if you would rather know early. With `standard_registry()` they
are:

```
all_ any_ bitor bool contains default ends_with extend first float format_date group_by int
is_none join last len lower map matches max max_by merge min min_by parse_iso pluck replace
slugify sort_by split starts_with str strip sum take unique_by upper url_host url_path url_query
where
```

Every evaluation runs under a **step budget**: one counter, decremented per node evaluated,
shared across nested evaluation, raising `BudgetExceededError` rather than running on. It is a
counter and not a timer, so it needs no `signal`, no thread and no executor, gives the same
answer on every platform and inside any thread, and bounds a filter over a context of any size.
Set it with `Evaluator(budget=...)`; the default is six million steps.

Forty-one functions across six tiers: collections, types, strings, regex, dates and URL. Three
things worth knowing before you reach for them: `str` converts primitives and refuses arbitrary
objects, because converting one would run that object's own code to produce the text; `slugify`
is ASCII in core, so a script with no ASCII form is dropped rather than transliterated; and
`matches` refuses patterns that can backtrack catastrophically.

That last one is a real restriction, so it is worth stating plainly. `^(a+)+$` against a
29-character input takes seven seconds, so no input-length cap helps and the pattern itself is
refused before it compiles. A pattern is refused if it nests one backtrackable repeat inside
another, or repeats an alternation whose branches match the same text. **Atomic groups and
possessive quantifiers reset that**, so `^(?>a+)+$` and `^(a++)+$` are accepted where `^(a+)+$`
is not; both are available on every supported version, which is 3.11 and later. The gate is
deliberately conservative and refuses a few patterns that happen to be fast.

The `CHANGELOG.md` "Known limitations" section is kept current with exactly what does and does
not exist.

The `Development Status :: 3 - Alpha` classifier stays until the escape corpus ships and passes
on every supported interpreter, because that corpus is the security claim.

## Threat model

> Expressions come from semi-trusted config authors, not anonymous internet users. The sandbox is
> defense in depth for a config-authoring surface. If you must run genuinely hostile input, use
> process isolation. No in-interpreter CPython sandbox, this one included, should be your only
> boundary.

## Requirements

Python 3.11 through 3.14. No runtime dependencies, and that is asserted by
`tests/test_zero_deps.py` and by the `zero-deps` lane, which imports a built wheel in an
interpreter that has nothing else installed.

## Prior art

`simpleeval` (MIT, Daniel Fairhead) is the closest thing in this space and was studied closely
while designing this package. Its regression tests for CVE-2026-32640 are a source for our escape
corpus. It is **not** vendored and this package does not depend on it.

## Licence

Apache-2.0. See `LICENSE`.
