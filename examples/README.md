# Examples

One runnable program per topic. Every one of them runs with **no arguments**, needs no network,
no server and no files, and prints its own narrated output:

```console
python examples/quickstart.py
```

They are executed by the test suite, as subprocesses, exactly as you would run them. An example
that has drifted out of sync with the library is a confident, wrong answer somebody will copy, so
these are run rather than trusted.

## Start here

```console
python examples/quickstart.py               # one import, one call, and what it refuses
python examples/pipelines.py                # `|`, `_`, and the nine functions that take an expression
python examples/errors.py                   # every way an expression can fail, and who fixes each
```

## The jobs people use this for

```console
python examples/feature_flags.py            # a predicate per flag, changed without a deploy
python examples/alert_rules.py              # thresholds the on-call rota edits, and the rule that broke
python examples/access_control.py           # who may do what, in a file an auditor can read
python examples/data_validation.py          # rejecting bad rows, and the check that passes for the wrong reason
python examples/rollups.py                  # group, aggregate, sort, take: a saved report
python examples/rules_from_config.py        # rules in JSON, validated when the file loads
```

## The language, tier by tier

```console
python examples/strings_and_regex.py        # the string tier, no interpolation, and the pattern gate
python examples/dates_and_urls.py           # ISO dates with the clock passed in, and a URL with no scheme
python examples/types_and_defaults.py       # conversions that refuse, and nothing versus falsy
```

## Wiring it into something

```console
python examples/custom_functions.py         # your own functions, including a lazy one
python examples/reserved_names.py           # the one collision pipes create, and three ways out
python examples/budget.py                   # what a rule costs, and how to pick a budget
python examples/threads.py                  # one evaluator, eight threads, and why that is a contract
python examples/attributes.py               # the one door left closed, and what opening it costs
python examples/what_is_refused.py          # thirty-four escape attempts, run rather than described
```

## What each one shows

| Example | Shows |
| --- | --- |
| `quickstart.py` | `evaluate()` with no setup, dots and brackets reading the same key, the three things a bare evaluator refuses, and the one argument that adds forty-one functions |
| `pipelines.py` | `x \| f(a)` is `f(x, a)`, rewritten at parse time from the registry alone; `_` as the item and where it is not in scope; nesting with `_1` and `_2`; and the brackets a multi-line pipeline needs, which are Python's rule rather than ours |
| `errors.py` | Fourteen failures caught by one `except`, the class that says who has to fix each, what an error carries and what it deliberately does not, `annotated()` as a rule author sees it, and the single fact about your data a message gives up |
| `feature_flags.py` | A predicate per flag as data, `bool()` on the result because `and` returns an operand, a percentage rollout whose bucket is hashed in the host because the language has no clock and no randomness, and a broken flag that fails alone |
| `alert_rules.py` | Thresholds as strings, catching per rule so one stale metric does not silence the rest, `annotated()` for the author of the broken one, and the rule shape people get wrong first: `_` on the right of a pipe that takes no expression |
| `access_control.py` | Policy as a table an auditor can read, an unknown action denying, a broken rule denying, and `or` short-circuiting so the ownership check only runs for actors without the role |
| `data_validation.py` | Checks grouped so structural ones run first, the consistency check that **passes** on an empty basket because zero equals zero, a missing field failing loudly rather than falsely, and the regex gate refusing `^(a+)+$` |
| `rollups.py` | `group_by` returning a list of `{key, items}` in first-seen order, aggregation composed from `sum`, `pluck` and `len` rather than from a `sum_by` that does not exist, and how `where` before `group_by` decides whether a customer with no paid orders is absent or zero |
| `rules_from_config.py` | Rules in JSON, validated at load rather than at request time, four different failures found in one pass, the collision check against `function_names`, and the quadratic rule that only a **correctly sized** sample catches |
| `strings_and_regex.py` | The string tier, three ways to build a string without interpolation and why interpolation is refused, `slugify` dropping what it cannot render in ASCII rather than guessing, and six patterns at the gate including the two that reset it |
| `dates_and_urls.py` | ISO parsing and portable `strftime` directives only, a freshness rule with the clock passed in as context, and the URL with no scheme where an allowlist fails closed and a denylist fails open on the same input |
| `types_and_defaults.py` | Conversions that refuse rather than coerce, `str` refusing an object because converting one runs its own code, and `default` versus `or` on a `0` that means "do not retry" |
| `custom_functions.py` | Three functions added to the registry, what `arity`, `cost` and `lazy` each buy, a lazy predicate traced to show it is evaluated once per item and never re-parsed, renaming and removing entries, and the five rules a function you add has to hold to |
| `reserved_names.py` | The collision, every position where it is **not** a collision, the startup check, the forty-two reserved names, three ways out, and why `ReservedNameError` is deliberately not an `EvaluationError` |
| `budget.py` | What six shapes actually cost, measured by bisecting the budget they need; the O(n·m) rule the budget exists for; a table for choosing one; and three calls proving the budget is per call rather than per evaluator |
| `threads.py` | Eight threads on one evaluator agreeing with a serial run, three threads refusing on budget while three answer, the registry proven to be copied rather than held, `__slots__` refusing an attribute, and the pattern cache costing the same warm as cold |
| `attributes.py` | `attribute_types` opening two names on one type, everything else on that type still refused, and a `@property` that runs a query three times to show what "an attribute can run code" means. Ends with the conversion at the boundary that gives nothing up |
| `what_is_refused.py` | Thirty-four attempts across the failure classes in `THREAT-MODEL.md`, each evaluated with its real refusal printed, plus the one line that is allowed and why a callable in the context is still just a value |

## Reading these next to the docs

Each example has a written counterpart in [`docs/`](../docs/README.md), and they are meant to be
read together: the doc makes the argument, the example runs it.

| Example | Doc |
| --- | --- |
| `quickstart.py` | [Getting started](../docs/getting-started.md) |
| `pipelines.py`, `reserved_names.py` | [Pipes and `_`](../docs/pipes.md) |
| `strings_and_regex.py`, `dates_and_urls.py`, `types_and_defaults.py` | [Function reference](../docs/functions.md) |
| `feature_flags.py`, `alert_rules.py`, `access_control.py`, `data_validation.py`, `rollups.py` | [Recipes](../docs/recipes.md) |
| `custom_functions.py`, `attributes.py`, `threads.py`, `rules_from_config.py` | [Embedding](../docs/embedding.md) |
| `errors.py` | [Errors](../docs/errors.md) |
| `budget.py` | [Performance and limits](../docs/performance.md) |
| `what_is_refused.py` | [`THREAT-MODEL.md`](../THREAT-MODEL.md) |
