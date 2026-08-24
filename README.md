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

The budget also bounds **memory**, because producing a value costs steps in proportion to its
size. That closes the gap where `rows | map(t + t)` allocated hundreds of megabytes from a
seventeen-character expression while costing almost nothing to evaluate. Anything under 64
elements is charged nothing, so ordinary rules are unaffected, and lowering the budget tightens
the time bound and the memory bound together.

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

## Limits

Every limit is set from a measurement at **ten times observed need or more**, and
`python scripts/limits.py` reproduces the table on your own machine. `tests/test_limits.py`
re-checks the ratios, so a number that drifts out of its own rule fails the suite rather than
sitting in a comment.

| Limit | Value | Measured need | Ratio |
| --- | --- | --- | --- |
| Source length | 2,048 bytes | set by 3.11's parser, not by need | |
| Expression nesting | 125 | 12, a tangled but real rule | 10.4x |
| Data nesting | 1,000 | 7, a nested configuration tree | 143x |
| Result size | 1,048,576 elements | 100,000, a map over 10⁵ items | 10.5x |
| Step budget | 6,000,000 | 538,433, the heaviest use case at 10⁵ items | 11.1x |
| Power result | 1 MiB of integer | set by measured time; no rule needs large powers | |

**Reference timing.** The five canonical use cases at 100,000 items: the heaviest is the pipeline
at 538,433 steps in 153 ms, and the rate is **4.0 to 5.4 steps per item**, stable across 10³, 10⁴
and 10⁵. That rate is what makes a budget expressible in items rather than in nodes: at the
originally proposed 100,000 steps it would have covered about twenty thousand items and raised on
a hundred thousand.

## How this is tested

Beyond the escape corpus, three things run on every supported interpreter:

- **Differential testing against CPython.** Generated expressions inside the safe subset must
  give the answer `eval` gives, or both must refuse. Coverage of the node allowlist is asserted,
  so a generator that drifts toward easy cases fails rather than passing quietly.
- **An audit-hook tripwire.** `python scripts/audit_fuzz.py` fuzzes the evaluator with
  `sys.addaudithook` watching, and fails if **any** audit event fires during evaluation beyond
  this package parsing its own source. `exec`, `import`, `open`, `os.system` and the subprocess
  events are all observed process-wide, so an escape trips it whether or not anybody wrote a test
  for that escape.
- **A published limits measurement.** `python scripts/limits.py` reproduces the table above.

> **Audit hooks are a test tripwire here, not a defence layer, and nothing installs one at
> runtime.** They observe rather than block; they fire process-wide, so a host would pay for
> every audited operation in its process; and a hook cannot be uninstalled once added, which
> makes it a target rather than a shield if a sandbox is already broken. If you want one in your
> own process, that is your decision to make on its own merits.

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
