# Performance and limits

What a rule costs, how the bound works, and how to pick the one number you get to set.

## The step budget

Every evaluation runs under a **step budget**: one counter, decremented per node evaluated plus
each function's declared cost, shared across nested evaluation. When it reaches zero the
evaluation raises `BudgetExceededError` rather than running on.

```python
from safeexpr import BudgetExceededError, Evaluator, standard_registry

rules = Evaluator(registry=standard_registry(), budget=100)
try:
    rules.evaluate("rows | map(_.v * 2)", {"rows": [{"v": i} for i in range(200)]})
except BudgetExceededError as error:
    print(error.budget)
# 100
```

**A counter, not a timer**, and the difference is the whole design:

- No `signal`, so it works off the main thread and off POSIX.
- No thread and no executor, so nothing is left running after the refusal.
- The same input gives the same answer on every platform and in every thread, so the bound is
  something you can reason about rather than something you hope for.
- It bounds a filter over a context of any size, because the cost is charged per item processed
  rather than per node in the source.

The budget is **per `evaluate` call**, not per evaluator, so two threads never spend each other's.

## Why a budget is needed at all

The language has no loops, no recursion and no user-defined functions, so every expression
terminates. Termination is structural here; the budget is the backstop behind it, and what it
actually bounds is **work**, which shape alone cannot:

```text
map(a, where(b, _ == _2))
```

That is short, shallow, and O(n·m). Source length, expression depth and the power cap all bound
what an expression *is*. Only the budget bounds what it *does*.

## What things cost

Reproduce all of this on your own machine:

```console
python scripts/limits.py
```

The five canonical use cases, measured at three sizes. Step counts are exact and portable; the
milliseconds are from one machine and are there for the ratio between rows, not to be quoted.

| Use case | Steps at 10³ | Steps at 10⁴ | Steps at 10⁵ | Steps per item |
| --- | --- | --- | --- | --- |
| feature flag | 11 | 11 | 11 | flat |
| authorization | 10 | 10 | 10 | flat |
| workflow condition | 9 | 9 | 9 | flat |
| alerting rule | 4,034 | 40,317 | 403,129 | 4.03 |
| pipeline | 5,824 | 54,253 | 538,433 | 5.4 |

Two things to take from that table.

**A rule that does not touch a collection is free.** A feature flag is eleven steps whether your
context holds ten records or a hundred thousand, because it reads three fields and compares them.
Most rules in most systems are this shape.

**A rule that does touch one costs 4 to 5.4 steps per item**, and that rate is stable across three
orders of magnitude. That is what makes a budget expressible in items rather than in nodes.

## Choosing a budget

Start from the biggest collection a rule will see:

```text
budget ≈ items × 6 × 10
```

Six steps per item is the rate the five canonical shapes measure at, and the ten is headroom,
which is this project's own rule applied to a measurement rather than to a guess. The shipped
default, 6,000,000, is that formula at a hundred thousand items.

Heavier shapes exist: a three-stage `where | map | sum` measures about 8 steps per item, which the
headroom absorbs. `python examples/budget.py` prints the rate for a handful of shapes, measured by
bisecting the budget they need, so you can check the one you are about to deploy rather than
trusting a table.

| Your biggest collection | A reasonable budget |
| --- | --- |
| A request-sized context, tens of fields | 10,000 |
| 1,000 records | 60,000 |
| 10,000 records | 600,000 |
| 100,000 records | 6,000,000 (the default) |

Lower is better when you can. The budget is the one bound between a rule an author got wrong and
a process that stops answering, and a rule over a request-sized context needs three orders of
magnitude less than the default.

```python
from safeexpr import Evaluator, standard_registry

per_request = Evaluator(registry=standard_registry(), budget=10_000)
bulk = Evaluator(registry=standard_registry(), budget=6_000_000)
```

There is deliberately **no value meaning unlimited**. A bound you cannot express is a bound you
cannot review.

## The budget bounds memory too

Producing a value costs steps in proportion to its size, so the counter that bounds time also
bounds allocation. That closes the gap where

```text
rows | map(t + t)
```

allocated hundreds of megabytes from a seventeen-character expression while costing almost
nothing to evaluate. Anything under 64 elements is charged nothing, so ordinary rules are
unaffected, and lowering the budget tightens the time bound and the memory bound together.

## Every other limit

These are module constants rather than configuration, and every one is set at **ten times
observed need or more**:

| Limit | Value | Measured need | Ratio | Past it |
| --- | --- | --- | --- | --- |
| Source length | 2,048 bytes | set by 3.11's parser | | `SourceTooLongError` |
| Expression nesting | 125 | 12 | 10.4x | `ValidationError` |
| Data nesting | 1,000 | 7 | 143x | `EvaluationError` |
| Result size | 1,048,576 elements | 100,000 | 10.5x | `EvaluationError` |
| Step budget | 6,000,000 | 538,433 | 11.1x | `BudgetExceededError` |
| Power result | 1 MiB of integer | set by measured time | | `EvaluationError` |

**Every limit refuses rather than degrades.** Nothing is truncated, nothing is silently
approximated, and no limit is a warning. Each refusal names what was exceeded and by how much.

The source-length cap is the one not set by need: `ast.parse` gives out somewhere between 2,989
and 5,975 levels of operator nesting depending on the interpreter, and does not fail gracefully
when it does, so the cap is applied **before** CPython's parser ever sees the input.

Changing one of these means changing the package, which is the intended difficulty. They are
readable if you need to check one:

```python
from safeexpr._guards import MAX_DATA_NESTING, MAX_RESULT_SIZE
from safeexpr._parse import MAX_SOURCE_BYTES
from safeexpr._validate import MAX_EXPRESSION_DEPTH

MAX_SOURCE_BYTES, MAX_EXPRESSION_DEPTH, MAX_DATA_NESTING, MAX_RESULT_SIZE
# (2048, 125, 1000, 1048576)
```

## What the budget cannot see

Time per charged step is roughly constant across the collections tier, which is what makes the
counter a proxy for time at all. `scripts/limits.py` prints the ratios so an outlier is visible
rather than assumed, and two functions sit well above the reference: `join` at about six times
and `pluck` at about fifteen. Both do real per-item work that costs more than a node evaluation,
and both are bounded by the size of their input, so the budget still stops them. It stops them
later than the ratio would suggest, which is what the measurement is for.

The other blind spot is named rather than hidden: an accepted regular expression runs inside `re`,
where the counter cannot follow it. That is why `matches` costs ten steps rather than one, and why
patterns that can backtrack catastrophically are refused before they compile rather than timed
out afterwards. See [`matches`](functions.md#matches).

## Benchmarks

The repository carries a benchmark suite for the hot paths, with a saved baseline. A mean-time
regression over 10% fails:

```console
pytest tests/benchmarks --benchmark-compare --benchmark-compare-fail=mean:10%
```

Allocation-sensitive paths carry a `pytest-memray` ceiling as well, so a change that trades
memory for speed shows up as a failure rather than as a number nobody reads.

Both need the optional measurement tools, which are deliberately not part of the default
development environment:

```console
uv sync --frozen --group measure
```

## Making a rule cheaper

In rough order of how much they tend to matter:

1. **Filter before you map.** `where(...) | map(...)` charges the map only for the rows that
   survived; the other order charges it for all of them.
2. **`take` early** when a rule only needs the first few.
3. **Pass less context.** The budget is charged for what is evaluated, not for what is in scope,
   but a rule that reaches into a huge structure usually ends up evaluating over it.
4. **Split a four-step pipeline into two rules** with something the host can name in between.
   That is usually a readability win that happens to be a performance one.
5. **Prefer `any_` to `len(where(...)) > 0`.** Both are bounded; only one stops at the first hit.
