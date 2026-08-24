# Changelog

All notable changes to this project are documented here.

## Unreleased

Pre-release. The evaluator, the pipe, the lazy-argument mechanism and the collections tier are
in; the remaining function tiers and the evaluation budget are not.

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

- **Escape corpus.** `corpus/escapes-v1.jsonl` holds 72 entries covering every failure class
  this package claims to close, each carrying its provenance and the stage at which it must be
  rejected. `python scripts/lanes.py corpus` runs it; CI runs it on every supported interpreter
  as a job of its own. Nine of the entries are controls that must still evaluate, because a
  corpus of nothing but rejections would pass against a sandbox that refuses everything.

### Fixed
- **Sequence repetition had no cap at all.** `"a" * 5000000` allocated a five-megabyte string and
  `[0] * 5000000` a five-million-item list, from fifteen characters of expression, and the
  constant was free to be larger. No existing limit saw it: the source cap bounds the expression,
  the step budget bounds nodes evaluated and that expression is three, and the power cap bounds
  the width of an integer rather than the length of a sequence. R7 had listed a string length cap
  among the deterministic bounds and it had never been built. Now guarded on the predicted size,
  because an error raised after the allocation has already cost the allocation, and shared with
  `replace`, `join` and `slugify` so text and repetition cannot drift apart.
- **A function given the wrong *kind* of value was told it could not accept that many
  arguments.** Both failures reach the evaluator as `TypeError` and it could not tell them
  apart, so it reported the one it could name, which was a false statement about a call whose
  argument count was fine. Declared arity is now checked before the call, and once a call has
  satisfied an informative arity a `TypeError` out of the function cannot be a miscount. A
  function that declared no arity keeps the older wording, because there the ambiguity is real.

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

- **The collections tier**, and with it the two canonical use cases that were still unwriteable:
  `where, map, extend, group_by, unique_by, sort_by, pluck, max_by, min_by, first, last, take,
  merge, len, sum, min, max, any_, all_`. Opt in with
  `Evaluator(registry=standard_registry())`; `Evaluator()` still starts empty, because a
  registered name is reserved on the right of a `|` and that cost belongs to a host who asked
  for the functions.
  - **`merge` is the relational join JMESPath cannot express.** It is shallow and right-biased:
    combining two objects at all is the whole of the gap, and going deeper would need the cycle
    detection and depth guard that are not built yet.
  - `group_by` returns a list of `{"key": ..., "items": [...]}` records rather than a mapping, so
    a group flows into the next pipe stage as an ordinary item. Groups come back in
    first-appearance order.
  - **Empty in, empty out**: `first`, `last`, `min`, `max`, `min_by` and `max_by` return `None`
    on an empty collection, `sum` returns `0`, and everything returning a collection returns an
    empty one. `metrics | where(...) | first` has to survive matching nothing.
  - A collection is a list or a tuple. Strings and mappings are refused with a message rather
    than iterated over characters or keys. `len` is the exception.
  - `pluck` takes the field name as a *value*, so it can come from the context, and it repeats
    the underscore-key block for exactly that reason: a name that never appears in the source is
    the case the validator structurally cannot see.
  - No entry performs runtime reflection. `tests/test_collections.py` parses the tier and
    asserts the absence of `format`, `getattr`, `type`, `reduce` and the rest, and asserts every
    registered callable is defined in a module that scan covers.
- **Registry entries declare arity and a step cost.** Arity is checked before the call. Cost is
  declared and not yet charged.
- `FunctionError`, for a registry function to say what is wrong with the values it was given. It
  carries a message and nothing else, and the evaluator adds the position.
- **Producing a large value costs budget**, which is what bounds memory. The step budget counts
  nodes evaluated, and a node that allocates is one node however much it allocates: measured,
  `rows | map(t + t)` over four thousand rows of 100,000 characters is a seventeen-character
  expression that allocated 343 MB and nothing saw it. No per-result cap could, because every one
  of those strings is comfortably under the cap and only the total hurt.
  - One step per 64 elements produced. **Integer division is the point**: anything under 64
    elements costs nothing, so an ordinary rule building short strings and small lists pays
    exactly what it paid before, and only bulk allocation is charged.
  - **One knob bounds both time and memory.** A host wanting a tighter memory bound lowers the
    budget, and the two scale together. At the default, total production is bounded at roughly
    384 million elements.
  - The charge is on what an operation *produces*, not on what it walks. `sum` over ten million
    integers still costs what `sum` over ten costs: it returns one integer, allocates nothing
    proportional to its input and holds nothing. Charging a scan as though it were an allocation
    would make a legitimate aggregate expensive for no reason.
  - Legitimate work is unaffected: all six canonical shapes at 100,000 items run in under a fifth
    of a second at the default budget.
- **`+` had no size cap, though `*` did**, so the same amplification was one character away.
  `a + a + a + a` on a 200,000-item list is 800,000 items from four nodes. Concatenation is now
  capped on its predicted size, and `extend` and `merge` are capped on theirs; `merge` is checked
  as it grows, because overlapping keys mean the sum of the inputs is an upper bound rather than
  an answer.
- **Guards on the host's data: how deeply it may nest, and what happens when it refers to
  itself.** F4. Nothing here recurses over data; comparing and hashing it recurses in C on our
  behalf, and the two fail differently.
  - **Hashing does not raise, it crashes.** `tuplehash` does not use `Py_EnterRecursiveCall`, so
    a deeply nested tuple exhausts the C stack and takes the interpreter with it: measured as an
    exit-139 segmentation fault, with and without this package. A `try` cannot catch a crash, so
    the depth is checked *before* the value reaches `hash`. Every path that hashes is covered:
    membership against a set or mapping, a subscript into a mapping, a key in a dict literal, and
    `group_by` and `unique_by`.
  - **Comparison does raise**, on every supported interpreter, and is now reported as an ordinary
    error. Before this, `a < b` on two self-referential lists said "internal error while
    evaluating (RecursionError); this is a bug in safeexpr, please report it", which tells the
    author to file a bug against a package that is working correctly.
  - The walk is bounded in **visits as well as depth**. Shared structure makes a graph rather
    than a tree, and forty levels of a value holding itself twice is a trillion values to reach
    with no path longer than forty. Removing that bound makes the suite hang rather than fail.
  - Data may nest 1,000 levels. Comparison gives out at 20,000 on 3.11 through 3.13 and at 60,000
    on 3.14, and the available stack is not ours alone.
- **Two error paths leaked a reference to the caller's data**, found by the corpus rather than by
  looking. Comparing and indexing both caught a `TypeError` out of a host object's own code and
  re-raised with `raise ... from None`, which clears `__cause__` and leaves `__context__` pointing
  at the caught exception, and from there at the object that raised it. That is F9, and the error
  module's own docstring warns against exactly this spelling. All three sites now build the error
  inside the handler and raise it after the handler has exited.
- **A shadowed pipe is refused rather than answered silently.** `values | min` against a context
  with its own `min` used to return the registry's answer with nothing said about the key it
  passed over; it now raises `ReservedNameError` naming the key, before anything is evaluated.
  - **Only the right of a `|`**, because that is the one position where the registry wins over
    the data. A bare `min` reads the context as it always did, so
    `metrics | where(_.value > min)` against `{"min": 10}` is correct and is not refused; with
    forty-one functions registered, `min`, `max`, `first`, `last`, `sum`, `len` and `default` are
    all realistic context keys and a blanket rule would break real expressions to prevent
    nothing. It would also refuse `bitor(flags, first)`, which is the escape hatch the design
    provides for exactly this.
  - `ReservedNameError` sits beside `EvaluationError` rather than under it, because this is not
    the expression author's mistake: the rule is correct and the expression is well-formed, and
    only the host can rename the key.
  - The reserved names are documented in the README as part of the language surface.
- **`matches`, with a static gate against catastrophic backtracking.** Patterns are parsed with
  the standard library's own regex parser and refused *before* they compile if they nest one
  backtrackable repeat inside another, or repeat an alternation whose branches describe the same
  language. Compiled patterns are cached.
  - **Input-length caps are not the mitigation, and that is measured.** `^(a+)+$` against a
    29-character input takes seven seconds on every supported interpreter, because the blowup is
    driven by the pattern's structure rather than by the size of the subject. There is no input
    cap short enough to help and long enough to be useful.
  - **The step budget cannot help either**, and this is the one place work happens outside it: a
    `matches` call is one node however long `re` spends inside it. `matches` is priced above
    every other function to reflect that, and the gate is what actually bounds it.
  - **Bounded repeats count as nesting.** `^(a{1,20}){1,20}$` has no unbounded quantifier
    anywhere and is still measurably slow. A rule written for unbounded repeats only, which is
    what the research proposed, would have let it straight through.
  - **Atomic groups and possessive quantifiers reset the nesting**, so `^(?>a+)+$` and `^(a++)+$`
    are accepted where `^(a+)+$` is refused. They were ruled out as a mitigation only because the
    old floor was 3.10 and they did not exist there; at the 3.11 floor they do, so an author who
    knows what they are doing can write a nested repeat and have it accepted.
  - Fails closed. `re._parser` is a private standard-library API, renamed from `sre_parse` in
    3.11; if it ever moves, every pattern is refused rather than compiled unchecked, and a canary
    test fails loudly in CI on every supported interpreter.
- **The types, strings, dates and URL tiers**, completing the registry at 40 functions:
  `int, float, str, bool, is_none, default`; `lower, upper, strip, split, join, replace,
  starts_with, ends_with, contains, slugify`; `parse_iso, format_date`; `url_host, url_path,
  url_query`. Still stdlib-only, and no core function needs an extra.
  - **`str` converts primitives and refuses everything else.** `str(x)` on an arbitrary object
    runs that object's `__str__`, which is host code returning host text, so a rules engine that
    allowed it would publish whatever that object's author chose to print. The corpus already
    carried the same leak arriving as `"%s" % obj`; arriving through a friendly conversion does
    not make it a different leak.
  - **`format_date` formats through `strftime` and nothing that interprets a template**, which
    is the card's own requirement and F1's most-repeated shape. Its directives are an allowlist
    rather than whatever the platform's C library accepts, so `%c`, `%x`, `%s` and `%-d` are
    refused and output does not vary with libc across the interpreter matrix.
  - Nothing coerces. `lower(user.age)` is a mistake in the rule, and answering it with `"30"`
    hides the mistake until the field arrives missing rather than numeric.
  - `float` refuses infinity and not-a-number, including the infinity a large literal overflows
    to. They compare in ways nobody means: `float(_.x) > 100` against `"nan"` is silently false
    for every row.
  - `slugify` is ASCII in core and lossy about it. Accented Latin keeps its base letter, `café`
    slugging to `cafe`, and a script with no ASCII form is dropped: a title written entirely in
    Greek or Japanese slugs to nothing. Word boundaries around dropped characters survive, so
    `a日b` is `a-b`. The `unicode` extra is the upgrade.
  - `url_host` gives the hostname rather than the network location, so a port or credentials do
    not silently make `url_host(u) == "example.com"` false. `url_query` gives one value per name,
    first occurrence winning, so the common comparison is not against a list.
- **A shared step budget**, the only limit here that bounds *work* rather than shape. One counter
  per evaluation, decremented on every node evaluated plus each function's declared cost, raising
  `BudgetExceededError` when it runs out. Default 6,000,000 steps, set per evaluator with
  `Evaluator(budget=...)`; there is no value meaning unlimited.
  - **Shared across nested lazy evaluations rather than per level**, which is the whole point:
    a per-level counter bounds each level to N and lets two levels do N*N work, and
    `map(a, where(b, _ == _2))` is that shape from a thirty-character source. `LazyExpr` hands
    the same run state back to the evaluator, so the sharing is structural and there is nothing
    to remember to thread through.
  - **A counter, never a timer.** No `signal`, no `threading`, no `concurrent.futures`, asserted
    by a test that reads the imports of every shipped module. `signal.alarm` is main-thread-only
    and POSIX-only and an executor timeout leaks the thread that is still running; a counter
    gives the same answer on the same input on every platform and inside any thread.
  - Every evaluation starts from the full budget, so an evaluator does not degrade over its
    lifetime and threads sharing one cannot spend each other's allowance.
  - The cost is real and measured: roughly 10 to 17% on evaluation-heavy expressions, about 30ns
    per node against roughly 80ns for the dispatch beside it. `tests/benchmarks/` records the
    before and after side by side.
- Benchmarks and allocation ceilings for the tier's hot paths, in `tests/benchmarks/`. They need
  `uv sync --frozen --group measure` and are skipped when those tools are absent, so the
  interpreter matrix stays green with nothing but pytest and hypothesis installed.

### Fixed
- **A too-deep expression reported "this is a bug in safeexpr, please report it".** The evaluator
  walks the tree recursively and gave out at about 497 nested operators, while the source cap
  allowed 1023, so legal input produced an internal-error message telling the author to file a
  bug. It is now a validation error naming the depth and the limit.

### Known limitations
- **The regex gate is conservative and refuses some safe patterns.** `(.*)*$` and `(a?)*$` are
  fast on CPython because `re` optimises them, and both are refused: the optimisation is an
  implementation detail, and a gate that refuses a pattern which happens to be fast today is
  worth more than one that accepts a pattern which is slow tomorrow. Write the inner repeat as
  atomic or possessive to have it accepted.
- **`matches` is the one function whose work is not bounded by the step budget.** The counter
  sees one call and `re` does the rest in C. The pattern gate is what bounds it instead.
- **`slugify` is ASCII and lossy.** Text in a script with no ASCII form slugs to nothing at all.
  Transliteration is the `unicode` extra's job and the extra is declared but empty.
- **Reading a field off a parsed timestamp needs the host to opt in.** `parse_iso` returns a
  `datetime`, which compares and formats; `.year` needs `attribute_types`, because attribute
  access reaching arbitrary objects is the thing this package most deliberately does not do.
- **The budget's element is not a byte.** Producing a value costs one step per 64 elements, and
  an element is a character for text and an item for a collection, so a list of pointers costs
  more memory per element than a string does. The bound is real and the units are approximate.
- **`merge` is shallow.** Nested mappings are replaced rather than combined. A deep merge needs a
  depth guard and cycle detection over host data, which is not built.
- **Data functions are guarded where Python raises, not where it crashes.** Sorting, comparing
  and hashing walk nested values in C on our behalf; where those raise `RecursionError` the tier
  reports it as an ordinary error, but a sufficiently deep structure can exhaust the C stack
  below the level any Python code can see. Measured: hashing a tuple nested about 400,000 deep
  segfaults CPython, with or without this package. A general depth cap over host data is the
  remaining work.
- A `KeyboardInterrupt` arriving during an evaluation is converted into a `SafeExprError` rather
  than propagating. That is deliberate, because the same containment is what stops a hostile
  `__eq__` raising `SystemExit` past a host's `except Exception`, but it does mean Ctrl-C will
  not interrupt an evaluation in progress.
