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

Every evaluation runs under a **step budget**: one counter, decremented per node evaluated,
shared across nested evaluation, raising `BudgetExceededError` rather than running on. It is a
counter and not a timer, so it needs no `signal`, no thread and no executor, gives the same
answer on every platform and inside any thread, and bounds a filter over a context of any size.
Set it with `Evaluator(budget=...)`; the default is six million steps.

Forty functions across five tiers: collections, types, strings, dates and URL. `matches` is the
one still to come, because regular expressions need a static ReDoS gate rather than an
input-length cap. Two things worth knowing before you reach for them: `str` converts primitives
and refuses arbitrary objects, because converting one would run that object's own code to produce
the text, and `slugify` is ASCII in core, so a script with no ASCII form is dropped rather than
transliterated.

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
