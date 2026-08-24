# safeexpr documentation

Start with **[Getting started](getting-started.md)**: install, your first expression, and the one
decision you have to make before the second one.

The rest is shaped by task rather than by module.

| Guide | What is in it |
| --- | --- |
| [Getting started](getting-started.md) | Install, a first expression, the context, why functions are opt-in, and pipes in thirty seconds |
| [The language](language.md) | Every piece of syntax: values, names, field access, indexing, arithmetic, comparison, boolean logic, conditionals, calls, and what is deliberately absent |
| [Function reference](functions.md) | All forty-one shipped functions plus `bitor`, grouped by tier, each with a worked example and its refusals |
| [Pipes and `_`](pipes.md) | What `\|` rewrites to, the item variable, nesting with `_1` and `_2`, the one name collision this creates, and how to check for it |
| [Recipes](recipes.md) | Nine complete programs: feature flags, alert rules, access control, validation, routing, pricing, rollups, URL allowlists, and rules loaded from a config file |
| [Embedding safeexpr](embedding.md) | One evaluator at startup, validating rules at load time, adding your own functions, attribute access on your own objects, threads, and what to do about untrusted input |
| [Errors](errors.md) | The seven error classes and who has to fix each, what an error carries, what it is allowed to say about your data, and how to show one to a rule author |
| [Performance and limits](performance.md) | The step budget, what a rule costs, choosing a budget, every other limit and what happens past it |

Two documents live at the repository root because they are read by people who are not evaluating
the API:

- [`THREAT-MODEL.md`](../THREAT-MODEL.md), the catalogue of published sandbox escape classes, with
  the mechanism, the advisories, and the corpus entry proving each one is unreachable here
- [`SECURITY.md`](../SECURITY.md), the disclosure process and the support window

## The other half is runnable

[`examples/`](../examples/README.md) is documentation that executes. One example per topic, each
runs with **no arguments** and prints its own output, and every one of them is run by the test
suite, so an example that has drifted out of step with the library fails a build rather than
misleading a reader.

```console
python examples/quickstart.py
python examples/feature_flags.py
python examples/pipelines.py
```

## Two conventions worth knowing before you read anything else

**Functions are opt-in, and the reason is a name collision.** `Evaluator()` starts with an empty
registry, which is already a usable language for comparisons and field access.
`Evaluator(registry=standard_registry())` adds forty-one functions and, with them, pipes. Every
name in the registry becomes reserved on the right of a `|`, which is what lets the meaning of an
expression be decided without looking at your data. [Reserved names](pipes.md#reserved-names) is
the argument in full.

**Nothing degrades quietly.** Every limit refuses, every type mismatch raises, every missing field
raises, and every one of those errors is a `SafeExprError` that names what happened and where.
There is no truncation, no coercion and no `None` returned to mean "that did not work". A rule
that is wrong is loud, on the grounds that the person who wrote it is not the person who will be
paged.
