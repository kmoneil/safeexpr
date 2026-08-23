# Changelog

All notable changes to this project are documented here.

## Unreleased

Scaffold only. Nothing in this package evaluates an expression yet.

### Added
- Project scaffold: `pyproject.toml`, `src/safeexpr/` layout, Apache-2.0 licence text, CI matrix
  over Python 3.11 through 3.14.
- Zero-dependency enforcement: `tests/test_zero_deps.py` reads declared metadata, and
  `scripts/check_zero_deps.py` imports a built wheel in an interpreter with nothing else in it.
- `scripts/lanes.py`, the single spelling of how this project runs its checks, with
  `tests/test_lanes.py` asserting every lane is wired into CI.
- Parse boundary: source is capped at 2048 bytes *before* it reaches `ast.parse`, and every
  parser failure surfaces as a `SafeExprError`. The cap is set by Python 3.11, whose parser
  gives out at 2,989 levels of operator nesting against roughly 5,975 on every other supported
  version.
- **Lazy arguments.** A registry function declares which of its argument positions are
  expressions, and the evaluator does not evaluate those, handing over the unevaluated subtree
  instead. This is what lets `where(items, _.price > 10)` work without a lambda. The expression
  is parsed once and evaluated per item: filtering ten thousand items calls `ast.parse` exactly
  once.
- **`_` binds the innermost item, and `_2`, `_3` reach outward** one nesting level per index.
  Reaching outward is not a convenience: under innermost-only binding, "orders above this
  customer's threshold" is unwriteable, and it is an ordinary rules-engine expression.
- The supported floor is **Python 3.11**, chosen so that every version in the matrix stays in
  upstream security support. 3.10 reaches end of life on 2026-10-31.
- **Public error hierarchy**, rooted at `SafeExprError` and exported from the package:
  `ParseError`, `ValidationError`, `SourceTooLongError`, `InternalError`. Every failure this
  package produces is one of these, and no error carries a reference to the data that caused it.
  Errors are constructed from scrubbed parts and raised outside the handler that caught the
  cause, because `raise ... from None` leaves `__context__` live, and that is a reachable
  handle on the caller's object.
- `SafeExprError.annotated()` renders a message above the offending source with a caret under
  the position.
- **Node allowlist.** The supported language is defined by what is listed rather than by what is
  forbidden, so syntax added by a future Python is rejected until it is reviewed. Rejections name
  the construct ("list comprehensions are not supported") and point at it. Attribute and
  constant-subscript access to underscore-prefixed names is blocked, and only `_`, `_1`, `_2` and
  so on are accepted as names beginning with an underscore.

- **Evaluator**, with `evaluate(source, context)` and an `Evaluator` class. Comparison,
  arithmetic, boolean logic with Python's short-circuit semantics, chained comparison, field
  access on mappings, indexing, slicing, and list/tuple/dict literals.
  - Only registry functions can be called. A callable in the context is a value and nothing
    more, so a dangerous function handed in as data cannot be invoked.
  - Attribute access reads mapping keys. It does not reach into arbitrary objects unless the
    host registers a type together with the attribute names it permits.
  - Underscore-prefixed subscript keys are blocked at evaluation as well as at validation, which
    covers computed keys such as `x["__cl" + "ass__"]`.
  - `**` is capped on the estimated size of its result rather than on its exponent. Capping the
    exponent misses a large base: `(10**100) ** 100000` takes about 10 seconds with an exponent
    well under any exponent-only limit.
  - An `Evaluator` holds nothing mutable and can be shared between threads.
  - Field access needs no wrapper type, so a large context costs nothing at evaluation entry and
    a self-referential one is harmless.

- **Escape corpus.** `corpus/escapes-v1.jsonl` holds 57 entries covering every failure class
  this package claims to close, each carrying its provenance and the stage at which it must be
  rejected. `python scripts/lanes.py corpus` runs it; CI runs it on every supported interpreter
  as a job of its own. Seven of the entries are controls that must still evaluate, because a
  corpus of nothing but rejections would pass against a sandbox that refuses everything.

### Fixed
- **`%` on text no longer performs string formatting.** Found while writing the corpus. `%` is an
  operator rather than a registry function, so the rule banning string formatting had never
  applied to it, and two things got through: `"%(__class__)s" % d` read a key that the
  underscore-key block should have stopped (%-formatting does its own lookup in C and never
  passes through the evaluator), and `"%s" % obj` handed back a context object's full `repr`.
  Integer and float modulo are unaffected.

- **Pipes.** `items | where(...)` becomes `where(items, ...)`, and chains compose left to right.
  The rewrite happens if and only if the right-hand side names a registered function, decided
  without consulting the context, so an expression cannot mean different things on different data.
  `bitor(a, b)` is always available for the case where a value shares a function's name.
- **An expression depth limit** of 100 nested nodes, reported as a plain validation error.

### Fixed
- **A too-deep expression reported "this is a bug in safeexpr, please report it".** The evaluator
  walks the tree recursively and gave out at about 497 nested operators, while the source cap
  allowed 1023, so legal input produced an internal-error message telling the author to file a
  bug. It is now a validation error naming the depth and the limit.

### Known limitations
- **No pipes, lazy arguments or function registry yet**, so `where`, `map` and the rest of the
  data functions do not exist and `items | where(...)` does not parse as a pipe. What works today
  is comparison, arithmetic, field access and indexing over ordinary data.
- **No evaluation budget.** A deeply nested expression over a large context is bounded only by
  the source-length cap and the power cap, so evaluation time is not yet bounded by anything
  proportional to the work done.
- A `KeyboardInterrupt` arriving during an evaluation is converted into a `SafeExprError` rather
  than propagating. That is deliberate, because the same containment is what stops a hostile
  `__eq__` raising `SystemExit` past a host's `except Exception`, but it does mean Ctrl-C will
  not interrupt an evaluation in progress.
- The escape corpus is an empty directory with a schema sketch. Until it ships and passes, the
  package's central security claim is unproven, which is what the `Development Status :: 3 - Alpha`
  classifier is saying.
