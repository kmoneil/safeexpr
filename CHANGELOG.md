# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**The headings are parsed, not just read.** `release.yml` refuses a tag whose version has no
`## <version>` section here, and builds the GitHub release notes from that section, so renaming
`## Unreleased` and dating it is part of cutting a release rather than a courtesy.

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

- **Escape corpus.** `corpus/escapes-v1.jsonl` holds 100 entries covering every failure class
  this package claims to close, each carrying its provenance and the stage at which it must be
  rejected. `python scripts/lanes.py corpus` runs it; CI runs it on every supported interpreter
  as a job of its own. Seventeen of the entries are controls that must still evaluate, because a
  corpus of nothing but rejections would pass against a sandbox that refuses everything.

- **`.github/workflows/release.yml`**, adapted from `ssrfguard`, which has already been through
  a PyPI release. Tag-triggered, and the shape is the point:

  - **No long-lived credential.** Trusted publishing mints a short-lived OIDC token for this
    repository, this workflow and this environment. `id-token: write` is granted to the publish
    job alone, and `tests/test_lanes.py` fails if a repository secret ever appears.
  - **Gate before build.** Publishing is the one irreversible step here, since a version number
    on PyPI cannot be reused even after a delete, so seven of the eight lanes run against the
    tagged tree first. `zero-deps` and `sdist` matter most at that moment: they are the only ones
    that read the artifact a user downloads rather than the tree it came from.
  - **Three refusals that name the actual mistake**: a tag disagreeing with `__version__`, a
    changelog with no section for the version, and a security policy still saying nothing has
    been released. Each would otherwise surface as an upload failure after everything passed,
    which reads as an infrastructure problem.
  - **Build once, publish that artifact**, with `--no-build-isolation` over a synced `build`
    group so the backend that produces the artifact comes from `uv.lock` like everything else.
    That group has existed since the scaffold and nothing used it until now.
  - **A CycloneDX SBOM, nearly empty, which is the whole point:** the zero-dependency claim in a
    form a procurement process can read without taking our word for it. Generated from the built
    wheel rather than from `pyproject.toml`, so it describes the artifact.
  - **A GitHub release after the upload rather than before it**, with notes taken from the
    changelog section rather than written a second time.

  Five tests pin the parts that a default would otherwise keep true until the day it did not:
  actions SHA-pinned across both workflows, `attestations: true`, `digest-mismatch: error`,
  `--no-build-isolation`, no repository secret, and exactly one job holding each write
  permission. They strip comments before searching, because the file argues at length for the
  very strings they look for.

- **A `Provenance` section in `SECURITY.md`**, saying what a downloader can verify: a signed
  PEP 740 attestation naming the workflow that built the artifact, and the SBOM. Both are
  asserted rather than promised, so the paragraph cannot quietly stop being true.

- **The changelog format is now a contract rather than a habit.** `release.yml` refuses a tag
  whose version has no `## <version>` section and builds the release notes from it, so the header
  says so and `tests/test_packaging.py` accepts either a heading for the packaged version or
  `## Unreleased`, which is the honest answer before the first tag.

- **The version and the security policy's support window are tied together.** `SECURITY.md` says
  there is no released version, which is true and will stop being true, and a support window
  describing a world that no longer exists is worse than none: it tells a reporter their version
  is unsupported when it is the only supported one.

  The sentence is now keyed to `__version__` rather than to somebody remembering. Bumping to a
  final release **fails the suite** until the policy is rewritten, which is what makes cutting a
  release mechanical instead of a checklist. Verified by bumping to `0.1.0` and watching it fail.
  The same test asserts the version is one PyPI will accept, because a version rejected at upload
  is found in the most expensive place.

- **An `sdist` lane, which proves the distribution rather than the package.** `pyproject.toml`
  ships `tests/` and `corpus/` on purpose, and says why: downstream packagers rebuild from the
  sdist and run this suite to validate the build, and for this package that matters more than
  usual, because the corpus is the security argument.

  That was a promise about an artifact nothing here ever ran. `python scripts/lanes.py sdist`
  builds the sdist, builds the wheel **from** it, installs that wheel into an interpreter holding
  only a test runner, and runs the shipped suite from the unpacked tree. **The isolation is the
  lane**, the same reason `zero-deps` has one: a checkout has `.github/`, `.git`, `uv.lock` and a
  synced environment, and a distribution has none of them, so passing here says nothing about
  passing there.

  It earned itself on the first run. See the fix below.

- **Thread safety is a contract, not an observation.** One `Evaluator` is safe to share between
  threads: immutable after construction, with the registry and permitted attribute types **copied**
  rather than held, `__slots__` so nothing can be attached later, and every piece of
  per-evaluation state (the step counter and the `_` scope stack included) in a call-scoped
  object. The budget is therefore per call, so two threads never spend each other's.

  Asserted rather than asserted-to: five concurrency tests on one shared evaluator, including the
  one the contract exists for, where a thread is run to budget exhaustion beside others that must
  all still succeed. **No mutable module-level state**, enforced structurally by a scan that reads
  every shipped module for a module-level container something writes to after import.

  The scan found exactly one, and it is allowlisted with a measured argument rather than removed:
  the bounded cache of compiled regular-expression patterns. Compiling is a pure function of the
  pattern string, and `matches` is charged its declared cost whether the pattern was compiled or
  fetched, **so the cache is invisible to the budget**: 13 steps on a cold cache and 13 on a warm
  one. The language has no clock, so nothing inside an expression can observe it at all.

- **No string interpolation in v1, and the argument is measured.** f-strings, and t-strings on
  3.14, stay off the node allowlist. The reason is sharper than "the same shape as `str.format`":
  `f"{obj}"` calls that object's `__format__`, `f"{obj!r}"` its `__repr__`, `f"{obj!s}"` its
  `__str__`, and `f"{obj:{spec}}"` hands its `__format__` a spec **computed at runtime**. Four
  ways to run a context object's own code, on values `str()` already refuses to convert for
  exactly that reason, in a syntax a static check reads as one node.

  All four parse to the same `JoinedStr` and `FormattedValue`, so one allowlist entry closes every
  one of them. Two corpus entries record that the variants were considered. If interpolation is
  ever allowed, the entries proving each conversion unreachable land **in the same change**.

- **The closed allowlist is now proven by construction rather than by example.** F7's claim is
  that grammar nobody has reviewed is rejected, and the evidence was two hand-written t-string
  corpus entries: one case, the one that already happened. A new test enumerates **every
  `ast.expr` subclass the running interpreter has**, asserts the allowed set is exactly the
  fourteen the language is made of, and constructs an instance of every other one to assert it is
  rejected. On 3.14 that covers `TemplateStr` and `Interpolation` without naming either, and it
  fails on the day a future Python adds an expression node. Proven non-vacuous by mutation: adding
  `ast.Lambda` to the allowlist fails two of its tests.

- **README pass.** An install section, a **non-goals** section (not Turing-complete, no I/O ever,
  not CEL and not CEL-compatible, no custom grammar ever), an **alternatives** table with the
  figures checked against PyPI metadata on 2026-08-23, **what happens past every limit** with the
  error type for each, and **the supported scale stated plainly**: ten times a hundred thousand
  items on the heaviest of the five canonical use cases, past which you get
  `BudgetExceededError` rather than a slow answer.

  The configuration surface is three constructor arguments and the README now says so, with
  `attribute_types` given its own paragraph rather than a table row: it opts a type back into
  `getattr`, which is where essentially every published Python sandbox escape has started, and
  listing it beside `budget` without saying so would have been the most expensive omission in the
  file. `tests/test_readme.py` reads the three names off `Evaluator.__init__`'s own signature.

- **A disclosure two documents disagreed about, now written down.** `RESEARCH-FINDINGS` said a
  type name in an error message is something R8 forbids; `_registry.describe_type` returns exactly
  that and argues the case in its docstring. Both shipped. Settled in favour of the code and
  documented: **an error names the type of a value it could not work with**, never a value and
  never a `repr`. A name is a string rather than a class object, so nothing about it is climbable,
  and the alternative is an error that cannot say what went wrong. It is now a bullet in
  `THREAT-MODEL.md`'s "What this does not bound" and a paragraph in the README.

- **A poisoned-value sweep across every refusal the language can produce.**
  `TestNothingRidesOutOnAnyRefusal` builds one expression per registry function at its declared
  minimum arity, plus thirty-two operator and syntax cases, and evaluates each against a value
  whose every dunder raises. Each refusal is therefore produced by a handler holding the caller's
  object live, which is the F9 precondition on purpose, seventy-four times over. Every one is
  asserted to carry no `__cause__`, no `__context__`, no `__notes__`, no secret and no `repr`.

  **Zero leaks.** The sweep is generated from the registry rather than listed, so a tier added
  later is swept the day it lands, and it is broad where the corpus is deep: the corpus asserts
  `__context__` per entry and is exactly as wide as its entries, and no corpus entry calls
  `slugify` with an object that refuses conversion.

- **`SECURITY.md`**, the disclosure policy, published before it is needed rather than improvised
  afterwards. Private reporting through GitHub's private vulnerability reporting or by email, and
  **no public issue for a suspected escape**, because a public report on a sandbox is a working
  exploit published at the one moment no upgrade exists. What is in scope, what is triaged on the
  facts, and what is documented rather than accidental are all listed, and every accepted escape
  ships as a new release with a CVE requested, the reporter credited, and a corpus entry added in
  the same change.

  Two things it says that a template would not. **Which sentence wins:** the threat-model
  statement and the standing commitment read as contradicting each other, and "semi-trusted" being
  handed back to a reporter as a reason to close their report is the exact failure this policy
  exists to prevent, so the policy says outright that it is not a reason to downgrade an escape.
  And **an honest support window:** there is no released version, so a version table would be
  fiction.

  `tests/test_security_policy.py` holds it to the parts a test can hold: the four workflow steps
  by name, a private channel that points at *this* repository, and the three files that
  `pyproject.toml`'s own comment says "move together" about the release status. That comment is a
  test now.

- **`tests/test_readme.py`**, which checks the README's published facts against the code that
  makes them true. Every value in the limits table against the constant in force, the
  reserved-names block against `Evaluator.function_names`, the function and tier counts computed
  from the registry rather than typed, the budget and size-charge numbers, the supported-version
  range against the classifiers, and the three front-page expressions evaluated rather than
  admired. It also runs the doctests in the shipped modules, which `testpaths = ["tests"]` meant
  nothing ever did.

  The reserved-names check earned itself immediately by pinning a distinction that looks like a
  defect and is not: the block lists **42** names against a registry of **41**, because `bitor` is
  a builtin rather than a tier entry.

- **`THREAT-MODEL.md`**, the failure-mode catalog the corpus was built to support. One section
  per class F1 to F9, each with the mechanism, the advisories where it has broken a real
  project, why it is unreachable here, and **the corpus entries proving it**. A reviewer can
  trace all nine classes to passing tests without reading the source, which is the document's
  whole job.

  **It is checked against the corpus rather than trusted.** `tests/test_threat_model.py` asserts
  the entry list in each section equals the corpus's entries for that class, in both directions:
  a citation with no entry behind it is a dead reference, and an entry with no citation is
  coverage the catalog does not claim. The per-class counts in its summary table are asserted
  too, and every advisory identifier in the body must appear in the document's own sources
  table. A corpus entry renamed and not re-cited fails the suite.

  Three things the document states plainly rather than rounding up: the step budget does not
  bound regular-expression time, memory amplification is mitigated and not eliminated, and F4 is
  bounded and not eliminated. An often-repeated claim that simpleeval fixed a sandbox escape via
  generators and `_frame` methods could not be verified against OSV, so it is not cited and F6
  rests on RestrictedPython's CVE-2023-37271 alone.

- **`docs/`, eight task-shaped guides**, indexed by `docs/README.md`: getting started, the
  language, a reference for every shipped function, pipes and `_`, nine worked recipes, embedding
  the package in a host, the error taxonomy, and performance and limits.

  **They are executed rather than read.** `tests/test_docs.py` evaluates every
  `expression => result` line in the reference documents against the standard registry and
  compares the `repr`, runs every Python block in every document and compares its output to the
  one the document claims, and resolves every link and cross-document anchor. It also holds the
  documents to the code: the function reference must carry a section for every registry entry and
  none for anything else, the reserved-name list must be exactly what is reserved, and the
  embedding guide's configuration table must name exactly the constructor's parameters. Writing
  the guides found four claims that were wrong, including a regex note saying a `{m,n}` bound
  clears the backtracking gate when only an exact `{m}` does.

- **`examples/`, eighteen runnable programs**, one per topic, each taking no arguments, needing
  no network and printing its own narrated output. `tests/test_examples.py` runs every one of
  them as a subprocess, exactly as a reader would, and pins the **claim** each one exists to make
  rather than only its exit status: that `attributes.py` shows a property running host code three
  times from inside an expression, that `threads.py` shows three threads refusing on budget while
  three answer from the same evaluator, that `rules_from_config.py` catches a quadratic rule at
  load time with a correctly sized sample. Both directions of the index are resolved against the
  directory, so an example with no row and a row with no example both fail.

- **A front page written to be read by somebody deciding whether to use this**: a banner, badges,
  a "what you get" table, a jump-to bar, a quick start, and a documentation table, with the
  reference material it used to open with moved into `docs/` and linked. Every claim the old
  README made is still there and still checked by `tests/test_readme.py`, which gains checks that
  the banner exists, that every guide is linked, and that every example command on the page names
  a file that is there.

- `docs/` and `examples/` ship in the source distribution, because `tests/` does. Both new test
  modules read those directories, so an sdist carrying the suite without them would carry tests
  that fail on a downstream packager's machine for a reason that has nothing to do with their
  build. `tests/test_packaging.py` asserts the include list names them, and its private-reference
  scan now covers them too.

### Fixed
- **The shipped test suite did not pass from the shipped source distribution.**
  `tests/test_lanes.py` reads `.github/workflows/ci.yml` to assert every lane is wired into CI,
  and `.github/` is deliberately not in the sdist, because CI plumbing is not the product.
  So a downstream packager doing exactly what `pyproject.toml` invites them to do got a
  `FileNotFoundError` that had nothing to do with the package.

  Fixed the way round that keeps both promises: the check skips outside a checkout and says so in
  its skip reason, rather than the workflow being added to the distribution to keep a test green.
  A second test asserts the workflow **is** present in a checkout, identified by `.git`, so the
  skip cannot quietly cover somebody deleting it. Found by the new `sdist` lane, on its first run.

- **Four planning-card ids had leaked into `tests/`, which ships.** Working notes live outside the
  distribution and always have; what escaped was references to them, written into test docstrings
  while working from a card. In a published sdist that is a pointer to a document the reader
  cannot open. Rewritten to say what the test checks, and `tests/test_packaging.py` now scans
  every shipped file for a reference to a private directory or a card id.

  Two details that took a second attempt. The scan assembles its own patterns from parts, so
  **this file is covered by its own check** rather than exempted, which is the blind spot a check
  like this reliably grows. And a plain substring search for one of the private names finds it
  inside `test_a_clean_error_reports_nothing`, so the pattern requires word boundaries: the first
  version reported four test names and nothing real.

- **A timing measurement took the median where the rest of the suite takes the minimum**, and it
  flaked one run in five. `scripts/limits.py`'s `seconds_for` was the one wall-clock measurement
  the previous hardening sweep did not reach, because it lives in `scripts/` rather than `tests/`,
  and `test_the_aggregates_are_within_a_small_factor` failed intermittently with nothing in
  `src/` changed. Interference can only add time and never remove it, so it is the minimum of
  five samples now: **zero failures in fifteen runs**, against one in five before. The published
  reference timing is unchanged at 152.6 ms for the pipeline at 100,000 items, so the number in
  the README still holds.
- **A fourth instance of F9, in the one handler no corpus entry can reach.**
  `_regex._compiled` guards `re.compile` against a warning the pattern gate did not already
  refuse, and it scrubbed with `raise failure from None`, which is the exact spelling `_errors`
  exists to warn against. Measured: the refusal carried `__context__` to the warning, a warning's
  `args` quote the pattern, and the pattern can come from the host's data.

  **Three defences and the union of them still had a hole.** The gate refuses those patterns
  first, so no test and no corpus entry ever ran that handler. `_eval._call` re-wraps a
  `FunctionError` into a fresh error raised outside its own handler, so nothing reached a host.
  And the corpus asserts `__context__` on every *entry*, which is why it found the first three
  instances of this class and structurally could not find this one. Relying on a downstream layer
  to scrub is what the decision record forbids, which is why this is a defect rather than a
  tidy-up.

  Fixed to the package's own convention, and pinned two ways. A regression test forces the branch
  with a compile that warns. And `TestNoRaiseSiteScrubsInsideItsHandler` **reads the source rather
  than running it**: every `raise` lexically inside an `except` block in `src/` must be a bare
  re-raise, which needs no path to be reachable to enforce. Both were confirmed failing against
  the pre-fix source. `tests/test_error_boundary.py` opened by promising that a refactor
  reintroducing `raise ... from None` "fails here rather than in somebody's incident review", and
  nothing made that true until now.
- **Every wall-clock assertion in the suite now times the minimum of several samples** rather
  than one. Interference only ever adds time, so the smallest observation is the closest thing to
  an operation's own cost and cannot be inflated by a busy machine, while a genuinely slow
  operation is slow in the minimum too. Prompted by a single unreproducible failure on 3.14 while
  the machine was building another environment: thirteen further runs were clean and the test was
  never identified, so the class of assertion most likely responsible was hardened rather than a
  guess being presented as a fix.
- **A regular expression the engine warns about was reported as a bug in this package.** Under
  `-W error`, which is ordinary in CI and is this project's own pytest setting, a pattern like
  `[a--b]` raised `FutureWarning` from inside the pattern gate's parse, reaching the boundary as
  "internal error while evaluating (FutureWarning); this is a bug in safeexpr, please report it".
  It is now an ordinary refusal naming the cause, without repeating the pattern back.

  **Found by the audit-hook fuzzer, and not by anything looking for it**: printing the warning
  made CPython open this package's source to show the offending line, and the hook saw an `open`
  during evaluation.

  One asymmetry remains and is documented: a pattern that only warns compiles fine, so under
  ordinary filters it works and under `-W error` it is refused. Removing that would need
  `warnings.catch_warnings`, which mutates a process-global filter list and would quietly break
  the promise that one evaluator is safe to share between threads.
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
- **Property tests for the transform layer**, over a generator that produces pipes inside lazy
  arguments, calls inside calls, and rewrites inside containers and subscripts, rather than the
  flat chains the pipe work shipped with. Idempotence, position preservation, byte-identity when
  nothing is registered, and independence from the context all hold over those shapes.
  - **The tree that is validated is the tree that is evaluated**, asserted by watching both calls
    rather than by checking each function returns its argument. `validate(tree) is tree` and
    `transform(tree) is tree` are each true on their own and neither says this. Making `evaluate`
    validate a deep copy leaves the existing F8 test passing and fails only the new property,
    which is the gap it was written for.
  - `_` scoping is checked at generated nesting depths up to eight: the outermost item stays
    reachable, reaching one level past the nesting is refused, and `_` means the innermost
    binding whatever the depth.
  - A coverage assertion keeps the generator honest, as with the differential and fuzzing work.
- **An audit-hook tripwire.** `scripts/audit_fuzz.py` fuzzes the evaluator with
  `sys.addaudithook` watching and fails if any audit event fires during evaluation beyond this
  package parsing its own source. Every other test here checks something somebody thought of;
  this is the one that watches for what nobody did.
  - It runs in its own process, because a hook cannot be removed once installed and fires for
    every audited operation.
  - It reports what it reached, not only what it found: 60,000 expressions, 48,737 past the
    parser, 5,539 evaluated. A fuzzer whose inputs all die at the parser proves nothing, and the
    suite asserts floors on those counts.
  - **A tripwire, not a defence layer**, and nothing installs a hook at runtime. Hooks observe
    rather than block, fire process-wide, and cannot be uninstalled. The README says so.
- **Differential testing against CPython itself**, over generated expressions inside the safe
  subset. The property is that this package and `eval` either agree on a value or both refuse,
  which keeps division by zero, mismatched comparisons and out-of-range indexing in the
  generator's reach rather than steering it away from exactly the cases where disagreement would
  hide.
  - **Coverage of the allowlist is asserted, not hoped for.** A generator that drifts toward
    atoms still produces thousands of examples and still reports zero divergence; shrinking the
    generator to atoms and arithmetic leaves all 61 agreement tests passing and fails only the
    coverage assertion. The allowlist is read from the validator rather than copied, so a node
    type added to the language with no way to generate one fails here.
  - Every refusal where Python succeeds is named: `%` on text, the size and power caps, and
    attribute access on anything that is not a mapping.
- **Every limit is now set from a measurement**, at ten times observed need or more, with
  `scripts/limits.py` in the repository to reproduce it and `tests/test_limits.py` asserting the
  ratios so they cannot drift back out.
  - **`MAX_EXPRESSION_DEPTH` was 100 against a measured need of 12**, which is 8.3 times and
    fails this package's own rule. It is 125: the smallest value clearing the floor, chosen at
    that end of the window because the evaluator gives out at 497 and the remaining stack belongs
    to whoever called us.
  - **A call is now charged for what it reads**, not only for what it evaluates and produces.
    Measured, `sum` over 200,000 integers was charged three steps for 1.7 milliseconds, two
    thousand times less per unit of work than an expression evaluated per item, and
    `rows | map(sum(nums))` bought about eighteen minutes from the default budget. That is the
    denial of service the budget exists to prevent, arriving through the one door it was not
    watching. Bounded at 2.6 seconds now.
  - **Per-function step costs are all 1 except `matches`.** They were 1, 2 and 5 by eye; measured,
    the whole tier lands between 0.85 and 1.5 times a bare `map` per charged step, with no room
    for the differences the numbers claimed. `matches` keeps 10, and that one is a deliberate
    conservatism rather than a measurement: its work happens inside `re`, where the counter
    cannot follow.
  - The step budget is confirmed rather than revised: 6,000,000 is 11.1 times the heaviest
    canonical use case at 100,000 items, which measures 538,433 steps in 153 ms at a stable 4.0
    to 5.4 steps per item.
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
