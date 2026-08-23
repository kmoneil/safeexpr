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

Pipes, lazy arguments and the function registry are not built yet, so the `|` examples above do
not work today. The `CHANGELOG.md` "Known limitations" section is kept current with exactly what
does and does not exist.

The `Development Status :: 3 - Alpha` classifier stays until the escape corpus ships and passes
on every supported interpreter, because that corpus is the security claim.

## Threat model

> Expressions come from semi-trusted config authors, not anonymous internet users. The sandbox is
> defense in depth for a config-authoring surface. If you must run genuinely hostile input, use
> process isolation. No in-interpreter CPython sandbox, this one included, should be your only
> boundary.

## Requirements

Python 3.10 through 3.14. No runtime dependencies, and that is asserted by
`tests/test_zero_deps.py` and by the `zero-deps` lane, which imports a built wheel in an
interpreter that has nothing else installed.

## Prior art

`simpleeval` (MIT, Daniel Fairhead) is the closest thing in this space and was studied closely
while designing this package. Its regression tests for CVE-2026-32640 are a source for our escape
corpus. It is **not** vendored and this package does not depend on it.

## Licence

Apache-2.0. See `LICENSE`.
