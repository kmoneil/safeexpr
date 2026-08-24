# Threat model

Nine classes of published sandbox escape, one section each, with the corpus entries that prove
each one is unreachable in this package.

The catalog was written before any code was, from the CVE and issue history of every Python (and
the reference Go) safe evaluator in this space. The finding that shaped the design: **every
mature competitor has shipped at least one sandbox escape, and the escapes cluster into a small
number of recurring patterns.** The value of studying them is not that we are smarter. It is that
the specific holes are known, so each one can be made structurally unreachable rather than patched
after disclosure.

The rule the catalog exists to enforce is that **every class must be answerable "no, structurally",
never "no, we check for it".** A check is a patch. Structure is a guarantee. One class, F4, does
not meet that bar, and this document says so rather than rounding it up.

## Scope

> Expressions come from semi-trusted config authors, not anonymous internet users. The sandbox is
> defense in depth for a config-authoring surface. If you must run genuinely hostile input, use
> process isolation. No in-interpreter CPython sandbox, this one included, should be your only
> boundary.

That last clause is not throat-clearing. Many experts consider in-process CPython sandboxing
impossible in the general case, and `simpleeval`'s own README says so. A package whose pitch is
safety has to be the first to say where its boundary ends.

## How to run any claim in this document

Every claim below is a corpus entry, and every corpus entry id is also a pytest node id:

```
pytest tests/test_corpus.py -k F2-mro-climb -v      # one entry
python scripts/lanes.py corpus                      # all of them, verbosely
```

The corpus is [`corpus/escapes-v1.jsonl`](corpus/escapes-v1.jsonl), one JSON object per line,
each carrying its provenance and **the stage at which it must be rejected**. That stage field is
what gives an entry its value: "the expression was rejected" is also true of a typo, so the
harness fails an entry rejected at the wrong stage even though it was still rejected, and for
anything expected past the parser it separately asserts the expression is valid Python. CI runs
the corpus as a job of its own on Python 3.11, 3.12, 3.13 and 3.14.

Two properties are checked on **every** entry rather than being entries of their own: no error
may carry `__cause__` or `__context__` (that is F9, checked corpus-wide), and every rejection must
be a `SafeExprError`.

`tests/test_threat_model.py` asserts that the entry lists in this document agree exactly with the
corpus, class by class. A renamed or deleted entry fails the suite rather than leaving a dead
citation here.

## Summary

| # | Escape class | Where it has broken | Reachable here? | Entries |
| --- | --- | --- | --- | --- |
| [F1](#f1-strformat-and-format_map-runtime-reflection) | `str.format` / `format_map` runtime reflection | Jinja2, RestrictedPython, asteval | No, structurally | 16 |
| [F2](#f2-object-attribute-traversal-to-__subclasses__) | Object attribute traversal to `__subclasses__` | simpleeval, asteval, the classic pyjail | No, structurally | 18 |
| [F3](#f3-a-dangerous-callable-smuggled-as-a-callback) | A dangerous callable smuggled as a callback | simpleeval, asteval | No, structurally | 7 |
| [F4](#f4-resource-exhaustion) | Resource exhaustion | expr-lang | **Bounded, not eliminated** | 22 |
| [F5](#f5-non-exception-baseexception-leakage) | Non-`Exception` `BaseException` leakage | asteval | No, structurally | 2 |
| [F6](#f6-stack-frame-walking-in-generators) | Stack-frame walking in generators | RestrictedPython | No, structurally | 4 |
| [F7](#f7-new-syntax-as-new-attack-surface) | New syntax as new attack surface | RestrictedPython | No, structurally | 4 |
| [F8](#f8-ast-mutation-toctou) | AST mutation TOCTOU | asteval | No, structurally | 7 |
| [F9](#f9-exception-carried-object-leakage) | Exception-carried object leakage | RestrictedPython | No, structurally | 6 |

Plus 16 [controls](#controls-the-language-still-works), which matter as much as the rejections: a
corpus of nothing but rejections would pass against a sandbox that refuses everything, and that is
not a sandbox anybody can use.

---

## F1. `str.format` and `format_map` runtime reflection

**Mechanism.** A static AST allowlist constrains *syntax*, but `"{0.__class__}".format(obj)`
performs attribute access and subscripting **at runtime**, driven by the value of a format string
the AST never inspects. From there the climb is the classic one: `__class__` to `__mro__` to
`__subclasses__()` to a class whose `__init__.__globals__` yields `os`.

**Where it has broken.** Jinja2 twice, in 2016 via `str.format` (CVE-2016-10516) and in 2019 via
`format_map` (CVE-2019-10906). RestrictedPython in 2023 (CVE-2023-41039). asteval in 2025
(CVE-2025-24359).

**Why it is unreachable here.**

- **There is no method-call-on-value syntax at all.** `_validate._check_call` permits only a bare
  name in call position, so `"abc".format(x)` is rejected as a construct rather than as a name on
  a list. Reaching the bound method without calling it fails a step earlier still: `"abc".format`
  is attribute access, which reads mapping keys, and a `str` is not a mapping and not a registered
  type. `format`, `format_map`, `getattr`, `vars`, `type` and `reduce` are not in the registry
  either, and there is no denylist naming them anywhere in the package.
- **`%` is an operator, not a registry entry, so a registry ban never applied to it.** This was a
  live hole found while writing the corpus: `"%(__class__)s" % d` returned `REACHED`, because
  `%`-formatting does its own `__getitem__` in C and never passes through the evaluator's
  subscript path, and `"%s" % obj` handed back a context object's `repr`. `%` on `str` and `bytes`
  is now refused at evaluation, leaving integer modulo, which is the only thing anybody wanted
  from `%` in a rules engine.
- **f-strings and t-strings are off the node allowlist**, so no interpolation syntax reaches the
  evaluator on any supported version. This is the F1 shape rather than a tidiness rule, and it is
  worth stating because the syntax looks inert. Measured against a host object: `f"{obj}"` calls
  that object's `__format__`, `f"{obj!r}"` its `__repr__`, `f"{obj!s}"` its `__str__`, and
  `f"{obj:{spec}}"` hands its `__format__` a spec **computed at runtime**, which is a value
  deciding what host code does. Four ways to run a context object's own code, on values `str()`
  already refuses to convert, in a syntax a static check reads as one node. All four spellings
  parse to the same `JoinedStr` and `FormattedValue`, so one allowlist entry closes all of them.
- **`str()` converts primitives and refuses arbitrary objects**, because asking an object for its
  text form runs that object's own code.
- **`format_date` goes through `strftime`**, whose directives name calendar fields and cannot
  reach an attribute, a key or a method. A brace template comes back as literal characters.

This is the single most important lesson from the scan, and it is the reason principle 2 below
exists.

**Proven by** `F1-str-format-attribute`, `F1-str-format-map`, `F1-restrictedpython-format`,
`F1-asteval-format`, `F1-str-attribute-on-literal`, `F1-percent-mapping-key`,
`F1-percent-repr-leak`, `F1-percent-str-leak`, `F1-fstring`, `F1-fstring-conversion`,
`F1-fstring-nested-format-spec`, `F1-tstring-314`, `F1-tstring-pre314`,
`F1-str-of-a-host-object`, `F1-format-date-brace-template`,
`F1-format-date-percent-mapping-key`.

## F2. Object attribute traversal to `__subclasses__`

**Mechanism.** Reach any object's `__class__`, then walk `__mro__`, `__bases__` and
`__subclasses__()` until you find a class whose `__init__.__globals__` contains something worth
having.

**Where it has broken.** simpleeval (CVE-2026-32640, CWE-915 uncontrolled attribute access),
asteval, and it is the shape of essentially every published pyjail. The climb itself is described
in the OSIRIS Lab / NYU Tandon writeup "Escaping Python Sandboxes".

**Why it is unreachable here.**

- **Attribute access reads mapping keys.** `_eval._attribute` performs a lookup in a mapping. It
  does not call `getattr` unless the host has registered that exact type together with an
  explicit set of permitted attribute names. `user.plan` on a dict works; `user.plan` on an
  arbitrary object is an error, not a traversal.
- **The underscore rule is static and dynamic, and both halves are load-bearing.** The validator
  rejects `x.__class__` and constant-key `x["__class__"]`; the evaluator rejects computed keys
  such as `x["__cl" + "ass__"]` and keys arriving from the context, which a static check cannot
  see by construction.
- **The rule covers the whole private namespace, not only dunders.** `x._private` is refused on
  the same rule.
- **`pluck` repeats the rule inside the function.** A field name passed to a function is a
  constant argument, not an `Attribute` or a `Subscript`, so no validator rule applies to it, and
  in the computed case the name never appears in the source at all. That is the one shape a
  static allowlist structurally cannot read, which is why the check lives where the name is used.
- **Validation runs after the pipe rewrite**, so nothing the transform leaves behind escapes the
  allowlist.

**Proven by** `F2-class-attribute`, `F2-mro-climb`, `F2-subclasses-call`, `F2-mro-index`,
`F2-globals`, `F2-code`, `F2-builtins`, `F2-subscript-literal`, `F2-subscript-computed`,
`F2-subscript-from-variable`, `F2-subscript-from-list`, `F2-object-attribute`,
`F2-object-secret`, `F2-private-attribute`, `F2-pipe-preserves-dunder-block`,
`F2-pluck-literal-dunder-field`, `F2-pluck-computed-dunder-field`,
`F2-pluck-underscore-prefixed-field`.

## F3. A dangerous callable smuggled as a callback

**Mechanism.** Pass an attacker-reachable dangerous function into a "safe" higher-order function,
so the sandbox calls it for you.

**Where it has broken.** simpleeval (CVE-2026-32640), asteval via `reduce` and `reduce_ex`
(SNYK-PYTHON-ASTEVAL-1073629).

**Why it is unreachable here.**

- **Call position resolves in the registry and never in the context.** `_eval._call` looks up
  `node.func.id` in the registry. A context value that happens to be a Python callable is still
  only a value, so there is no path to block rather than a path that is blocked.
- **The validator has already reduced call position to a bare name**, so `obj.method(1)` and
  `f()(2)` never reach the evaluator at all.
- **Higher-order functions take unevaluated expressions, not callables.** `where(items, _.x > 1)`
  hands the function an AST subtree, so there is no argument position anywhere in the language
  whose value is a function.
- **The pipe does not create a new path.** A name on the right of `|` that is not in the registry
  leaves `|` as ordinary bitwise or, which fails on types rather than degenerating into a call.

The corpus context for these entries contains `os.system` itself, under the name `system`. If any
path from a context value to call position existed, that is what would come through it.

**Proven by** `F3-context-callable`, `F3-os-system`, `F3-method-call`, `F3-call-result`,
`F3-pipe-to-context-callable`, `F3-pipe-to-os-system`, `F3-pipe-to-method-call`.

## F4. Resource exhaustion

**Bounded, not eliminated.** This is the one row that does not read "no, structurally", and
rounding it up would be dishonest: some resource consumption is inherent to evaluating anything at
all. What follows is what is bounded, by what, and to what value.

**Mechanism.** Deeply nested or cyclic data, huge input strings, expressions that allocate far
more than they cost to evaluate, nested lazy arguments with no shared work bound, and regular
expressions that backtrack catastrophically.

**Where it has broken.** expr-lang, which is zero-dependency and shipped two denial-of-service
advisories anyway: CVE-2025-29786 (parser memory exhaustion on huge input) and CVE-2025-68156
(unbounded recursion in builtins). It added compile-time node and memory budgets after release.
That is direct evidence that resource bounding is mandatory rather than gold-plating, and it is
principle 3 below.

**What bounds what.**

| Bound | Value | What it stops |
| --- | --- | --- |
| Source length, enforced before `ast.parse` | 2,048 bytes | Parser exhaustion, and the failure is not a `SyntaxError`: 3.11 gives out at 2,989 nesting levels with a `RecursionError`, and 3.12 through 3.14 at about 5,974 with a `MemoryError`. 3.11 is the binding constraint and the outlier |
| Expression nesting | 125 | A legal but over-deep tree reaching the evaluator's own recursion limit |
| Step budget, shared across nested lazy evaluation | 6,000,000 | Unbounded evaluation work. One counter, not a timer, so no `signal`, no thread, no executor |
| Result-size charge | 1 step per 64 elements produced | The gap where `rows \| map(t + t)` allocated 343 MB from seventeen characters while costing almost nothing to evaluate |
| Result-size cap on repetition and concatenation | 1,048,576 elements | `"a" * 5000000` and `[0] * 5000000`, which are three nodes each |
| Power result width | 1 MiB of integer | `(10 ** 100) ** 100000`, whose exponent is small and whose result is not. Capping the exponent, which is what simpleeval does, misses it |
| Data nesting, checked before `hash` | 1,000 levels | Recursion and worse over host data. See below |
| Reachable values in one guard walk | 100,000 visits | Shared structure that multiplies: forty levels of a value holding itself twice is shallow, acyclic, and reaches a trillion values |
| Static ReDoS pattern gate | Compile time | Catastrophic backtracking, which no input-length cap can bound |

Every one of those numbers is set from a measurement at ten times observed need or more,
`python scripts/limits.py` reproduces the table, and `tests/test_limits.py` asserts the ratios so
that a number drifting out of its own rule fails the suite.

**Two findings worth carrying forward.** First, **hashing is the one operation in CPython that
crashes rather than raising**: a sufficiently deeply nested tuple segmentation-faults the
interpreter with no catchable error, so the depth cap runs *before* the value reaches `hash`
rather than as a `try`/`except` around it. Second, **a per-result cap cannot see an aggregate**:
bounding one allocation and bounding the total are different problems and need different
mechanisms, which is why both the cap and the size charge exist.

**Proven by** `F4-parser-oom`, `F4-long-source`, `F4-deep-unary`, `F4-deep-unary-under-cap`,
`F4-deep-pipe-chain`, `F4-power-large-exponent`, `F4-power-large-base`,
`F4-nested-lazy-quadratic-work`, `F4-nested-lazy-three-deep`,
`F4-repeated-predicate-over-one-collection`, `F4-sequence-repetition-allocation`,
`F4-list-repetition-allocation`, `F4-aggregate-allocation-inside-map`, `F4-concatenation-doubles`,
`F4-merge-of-large-mappings`, `F4-redos-nested-quantifier`, `F4-redos-ambiguous-alternation`,
`F4-redos-bounded-nesting`, `F4-hash-of-deeply-nested-data`,
`F4-hash-of-deeply-nested-data-as-a-key`, `F4-comparison-of-self-referential-data`,
`F4-shared-structure-that-multiplies`.

## F5. Non-`Exception` `BaseException` leakage

**Mechanism.** The sandbox raises a `BaseException` subclass that slips past the host's
`except Exception`, killing the process or disrupting signal and cleanup handling.

**Where it has broken.** asteval (CVE-2026-55244), via `SystemExit`, `KeyboardInterrupt` and
`GeneratorExit`.

**Why it is unreachable here.** No syntax in this language raises: there is no `raise`, no user
exceptions, no `try`. That is not sufficient on its own, and the corpus entries show why. **A host
object's `__eq__` is host code, and it runs during a comparison.** A context value whose `__eq__`
raises `SystemExit` reaches the boundary without the expression containing anything unusual, so
the `x == 1` in those two entries is the whole attack.

The boundary therefore catches `BaseException`, not `Exception`, and constructs a fresh
`InternalError` carrying only the caught type's name. The message may quote input, `args` are not
ours to pass on, and `__notes__` is a channel a caller's object could have written to, so none of
the three survives.

The trade-off is stated rather than hidden: **a `KeyboardInterrupt` arriving during an evaluation
is converted, so Ctrl-C will not interrupt one.** The window is bounded by the step budget rather
than by the size of the input, and the alternative is letting a sandboxed expression kill the host
process.

**Proven by** `F5-systemexit`, `F5-keyboardinterrupt`.

## F6. Stack-frame walking in generators

**Mechanism.** Generators and generator expressions expose `gi_frame`; walk `f_back` from there
past the sandbox boundary into the caller's globals.

**Where it has broken.** RestrictedPython (CVE-2023-37271).

**Why it is unreachable here.** Generator expressions, list, set and dict comprehensions, `lambda`
and `yield` are all absent from the node allowlist, so no user-defined iteration exists to hold a
frame. **This is why "no comprehensions" is a security decision here and not an ergonomic one**,
and why the answer to "could you add them" is no rather than not yet.

**Proven by** `F6-genexp`, `F6-listcomp`, `F6-dictcomp`, `F6-gi-frame`.

## F7. New syntax as new attack surface

**Mechanism.** New CPython grammar arrives, a sandbox written against the old grammar has never
heard of it, and the new nodes evaluate. RestrictedPython's case was `try`/`except*` meeting a
CPython type-confusion bug.

**Where it has broken.** RestrictedPython (CVE-2025-22153, on CPython 3.11 and later).

**Why it is unreachable here.** Two independent reasons, and the second is the general one.

- `try` and `except*` are **statements**, so they cannot appear in `mode="eval"` at all.
- **The node allowlist is closed.** A node type absent from it is rejected, so grammar added by a
  future Python is refused until somebody reviews it and adds it deliberately. This is the
  allowlist-versus-denylist difference stated as a version policy rather than as a preference.

That is not a hypothetical. **Python 3.14 added `TemplateStr` and `Interpolation` for t-strings
(PEP 750), and they are expression nodes**, so `t"{x}"` parses in `mode="eval"` and would have
been evaluated by a denylist that had never heard of them. The closed allowlist rejected them on
the day the interpreter shipped, which is what `F1-tstring-314` records; before 3.14 the same
source is a syntax error, which is `F1-tstring-pre314`, and the pair is why the corpus carries
version bounds at all.

The entries below are the same property tested against constructs that parse cleanly today. Note
`F7-await` in particular: **`await z` is valid Python in `mode="eval"`** and parses without
complaint, so the allowlist is what stops it rather than the parser, and the intuition runs the
other way round.

**Proven by** `F7-walrus`, `F7-lambda`, `F7-starred`, `F7-await`.

## F8. AST mutation TOCTOU

**Mechanism.** The attacker mutates exposed or attached AST node attributes in the window between
the safety check and the use.

**Where it has broken.** asteval (GHSA-vp47-9734-prjw).

**Why it is unreachable here.**

- **Validation returns the same tree object it was given**, and the evaluator runs on that object.
  There is no copy, so there is no window in which one tree could be swapped for another.
- **There are no synthetic names.** The design this package was built from specified hoisting lazy
  arguments into a side table keyed by names like `__lazy_0`. Built as specified and attacked, it
  handed back a live AST subtree: `items | where(_ > 1) and __lazy_0` returned a `LazyExpr` whose
  `.node` was the `Compare`. The side table is gone; the registry declares which argument
  positions are lazy and the evaluator simply does not evaluate those, which is both simpler and
  strictly safer.
- **A `LazyExpr` is inert as a value.** The last four entries put one directly into the context,
  which is a stronger test than the design's own scenario: the side table would have made one
  reachable by *naming* it, and these hand one over outright. `_node` and `_evaluator` are refused
  by the underscore rule, and even an ordinary attribute name has nothing to reach, because
  attribute access reads mapping keys and a `LazyExpr` is neither a mapping nor a registered type.

The identity property is also asserted directly, and it needed a property test rather than a unit
test to state properly: `validate(tree) is tree` and `transform(tree) is tree` were both true and
both tested, and **neither of them says the tree `evaluate` validated is the tree it evaluated.**
Making `evaluate` validate a deep copy leaves the older assertions green and fails only the
property. See `tests/test_transform_properties.py`.

**Proven by** `F8-lazy-name`, `F8-lazy-name-in-expression`, `F8-dunder-name`, `F8-lazyexpr-node`,
`F8-lazyexpr-evaluator`, `F8-lazyexpr-subscript`, `F8-lazyexpr-not-a-mapping`.

## F9. Exception-carried object leakage

**Mechanism.** A failed operation produces an exception carrying a **live reference** to the
object it failed on. On every supported version, `AttributeError` carries `.obj` and `NameError`
carries `.name`; on 3.12 and later, `add_note` is a channel a caller's object can write to.
Catching and re-wrapping is not sufficient: chaining with `raise ... from e`, retaining the
original, or passing `e.args` through all hand the caller that reference. `str(e)` additionally
leaks the context object's type name.

**Where it has broken.** RestrictedPython (CVE-2024-47532, "information leakage via
`AttributeError.obj` and the `string` module"). This class was **absent from the F1 to F8 catalog**
and was added during the research spike because it is directly reachable in this design.

**Why it is unreachable here.** Errors are **constructed, never wrapped**, from scrubbed data only:
a message template, the user's own source, and an offset.

The obvious implementation does not work, and this is the part worth reading twice.
`raise OurError(...) from None` clears `__cause__` and suppresses the "During handling of the
above exception" display, **but leaves `__context__` pointing at the original**, and from there at
the object that raised it. Assigning `__context__ = None` inside the handler does not help either:
CPython re-sets it as the raise executes. The only pattern that works is **building the error
inside the handler and raising it after the handler has exited**, because the thread's currently
handled exception is restored at that point. `_errors.contained` is that pattern in reusable form,
and every raise site in the package follows the same convention.

**The corpus found three live instances of this by itself**, and nothing was looking for them.
The harness asserts `__cause__` and `__context__` are `None` on **every** entry rather than in one
dedicated test, so adding an unrelated F4 entry for the recursion guards failed that check. Two of
the three were `raise ... from None` sites that predated the card, each holding a live handle on
the caller's data through the exception it had caught. The third was in the recursion guard being
added by that same work, written the same way in its first draft, and the corpus refused it before
it was committed. The contexts for these entries put the secret where it would show:
`_Prickly.api_key` is reachable through `__context__` if the leak is present.

**Proven by** `F9-attribute-error-obj`, `F9-missing-field`, `F9-name-error`,
`F9-comparison-typeerror-context`, `F9-subscript-typeerror-context`,
`F9-comparison-recursion-context`.

---

## Controls: the language still works

A corpus of nothing but rejections would pass against a sandbox that refuses everything, and a
gate that refuses every pattern is not a gate anybody can use. Each cluster of rejections above
has a control beside it asserting the ordinary case still evaluates, and the controls are run by
the same harness with the same strictness.

**Proven by** `control-feature-flag`, `control-modulo`, `control-chained-comparison`,
`control-field-access`, `control-dict-key-named-items`, `control-power`, `control-subscript`,
`control-pluck-ordinary-field`, `control-collections-pipeline`,
`control-work-within-the-budget-still-runs`, `control-str-of-a-primitive-still-works`,
`control-repetition-within-the-cap`, `control-redos-an-ordinary-pattern-runs`,
`control-redos-atomic-nesting-is-allowed`, `control-ordinary-nesting-still-works`,
`control-bulk-work-within-the-policy-runs`.

One of them is worth singling out. `control-dict-key-named-items` evaluates `x.items` against a
mapping that has an `items` key, and asserts the value comes back rather than the bound method.
**Falling back to `getattr` when a key is missing is how a sandbox leaks the object model**, and
this control is what stops that fallback being added later as a convenience.

## Three cross-cutting principles

1. **Allowlist, never denylist.** asteval's escape count against simpleeval's is largely this
   difference, and a denylist is whack-a-mole by construction. Node handling here is
   allowlist-only, and this is a stated invariant rather than an implementation detail. It is also
   why this package was written from scratch rather than vendoring simpleeval, whose core is three
   denylists (`DISALLOW_PREFIXES`, `DISALLOW_METHODS`, `DISALLOW_FUNCTIONS`) and which imports
   `os` at module scope purely to name `os.popen` and `os.system` in one of them. There is no list
   of forbidden functions anywhere in this package, because there is no path by which a forbidden
   function could be reached.
2. **A static AST check is necessary but not sufficient, and F1 is the proof.** Anything that
   performs *runtime* attribute access or reflection is banned from the registry regardless of how
   convenient it is, because a static allowlist cannot see it: `BinOp(Mod)` looks the same whether
   it is `n % 3` or `"%(__class__)s" % d`. New registry additions pass a mandatory "does this do
   runtime reflection?" review gate. The same reasoning is why `pluck` repeats the underscore rule
   internally: a field name arriving as a value is invisible to every static check by
   construction.
3. **Zero-dep does not mean DoS-free.** expr-lang is zero-dependency and shipped two
   denial-of-service advisories anyway, then added compile-time node and memory budgets after
   release. Having no dependencies removes supply-chain surface. It removes no resource-exhaustion
   surface at all.

## What this does not bound

Stated plainly, because a threat model that lists only its wins is marketing.

- **The step budget does not bound regular-expression time.** One `matches()` call is one node
  however long the engine spends inside it. This is why the ReDoS mitigation is a *static pattern
  gate* that refuses at compile time rather than an input-length cap: `^(a+)+$` against a
  29-character input takes seven seconds, identically on every supported version, so no useful
  input cap exists. A pattern is refused if it nests one backtrackable repeat inside another, or
  repeats an alternation whose branches match the same text. Atomic groups and possessive
  quantifiers reset that, so `^(?>a+)+$` is accepted where `^(a+)+$` is refused. The gate is
  deliberately conservative and refuses some patterns that happen to be fast.
- **The step budget bounds evaluation, not every kind of work.** A registry function that loops in
  C rather than re-evaluating an expression is charged once however many items it walks. That
  policy is pinned by a test, so it fails loudly if it ever changes rather than drifting quietly.
- **Memory amplification is mitigated, not eliminated.** Producing a value costs budget in
  proportion to its size, and repetition and concatenation are capped on the predicted result
  size, so one knob bounds time and memory together. Neither mechanism bounds the memory the host
  already handed in, and a host that puts a very large object in the context has already spent it.
- **F4 is bounded, not eliminated.** See that section for what each limit covers. Some resource
  consumption is inherent to evaluating anything.
- **A host that registers a type opts back into attribute traversal for that type**, limited to
  the attribute names it lists. That is a deliberate escape hatch, and the host owns the
  consequences of what it registers.
- **Ctrl-C does not interrupt an evaluation in progress.** See F5.
- **An error names the type of a value it could not work with.** "cannot compare `Order` with
  `int`" tells an expression author the class name of something in the context, and that is the
  one thing about the host's data an error here discloses. It is deliberate:
  `_registry.describe_type` returns `type(value).__name__` and never a `repr`, so a name crosses
  over and a value does not, and a name is a string rather than a class object, so nothing about
  it is climbable. The M0 research flagged type names as a disclosure and it was right to; the
  trade was taken knowingly, because the alternative is an error that cannot say what went wrong.
  If a class name is itself sensitive in your deployment, keep those values out of the context.
  `tests/test_error_boundary.py` sweeps every registry function and operator with a value that
  refuses everything, and asserts that the type name is the *only* thing that crosses.

## What is not a defence here

**Audit hooks are a CI tripwire in this project, not a runtime defence, and nothing installs one
at runtime.** `python scripts/audit_fuzz.py` fuzzes the evaluator with `sys.addaudithook`
watching, and fails if any audit event fires during evaluation beyond this package parsing its own
source. `exec`, `import`, `open`, `os.system` and the subprocess events are all observed
process-wide, so an escape trips it whether or not anybody wrote a test for that escape.

That is a genuinely different kind of assurance from the corpus. **Tests that assert an outcome
can only find what somebody imagined; a tripwire below the code finds what nobody did.** It has
already earned its place: it found a defect by watching `open`, in code that has nothing to do
with files, because a warning about a user's regular expression made CPython read this package's
source to print the offending line.

It is not a defence layer, for three reasons. Hooks observe rather than block. They fire
process-wide, so a host would pay for every audited operation in its process. And a hook cannot be
uninstalled once added, which makes it a target rather than a shield if a sandbox is already
broken. If you want one in your own process, that is your decision to make on its own merits.

## Sourcing

Every advisory identifier cited above was verified against a public registry during the M0
research spike, against OSV.dev except where the table says otherwise. That survey produced two
corrections and one addition, all three of which are reflected in this document:

- RestrictedPython's format-string escape is **CVE-2023-41039**. The design draft cited the GHSA
  without the CVE.
- **F9 was added** as a result. RestrictedPython's CVE-2024-47532 was absent from the original F1
  to F8 catalog and is directly reachable in this design.
- An often-repeated claim that simpleeval fixed a **sandbox escape via generators and `_frame`
  methods** could not be verified. simpleeval has exactly two advisories in OSV, both for
  CVE-2026-32640. The claim is therefore **not cited here**, and F6 rests on RestrictedPython's
  CVE-2023-37271 alone. If a commit or changelog entry surfaces, it belongs in the corpus first
  and in this document second.

| Identifier | Project | What it is |
| --- | --- | --- |
| CVE-2016-10516 | Jinja2 | Sandbox escape via `str.format` |
| CVE-2019-10906 | Jinja2 | Sandbox escape via `str.format_map` |
| CVE-2023-37271 | RestrictedPython | Stack-frame walking via generators |
| CVE-2023-41039 | RestrictedPython | Format-string escape |
| CVE-2024-47532 | RestrictedPython | Information leakage via `AttributeError.obj` and the `string` module |
| CVE-2025-22153 | RestrictedPython | `try`/`except*` type confusion, CPython 3.11 and later |
| CVE-2025-24359 | asteval | Format-string escape |
| CVE-2025-29786 | expr-lang | Parser memory exhaustion on unbounded input |
| CVE-2025-68156 | expr-lang | Unbounded recursion in builtins |
| CVE-2026-32640 | simpleeval | Module leakage through passed-in object attributes, and dangerous callables smuggled as callbacks. Fixed in 1.0.5 |
| CVE-2026-55244 | asteval | `SystemExit` / `KeyboardInterrupt` / `GeneratorExit` bypass `except Exception` |
| GHSA-vp47-9734-prjw | asteval | AST mutation TOCTOU |
| SNYK-PYTHON-ASTEVAL-1073629 | asteval | `reduce` / `reduce_ex` callback smuggling. Verified in Snyk's database rather than in OSV |

Thirty-five of the hundred and two corpus entries name `safeexpr` as their source library. Those
were found by building this package rather than ported from a disclosure, and each says what it
found in its `note` field: seven under F1, six under F2, twelve under F4, one under F9, and nine
of the controls. The `%`-formatting hole is the one worth reading, because it was a live hole in a
shipped defence that the design's own rule had never covered.

Provenance and source library are not the same field, and two of the F9 entries show why: they
cite CVE-2024-47532 and name RestrictedPython, because the *class* is RestrictedPython's, while
their provenance records that the instance was ours and that this corpus is what found it.

## Reporting an escape

A sandbox escape is always a critical bug here. Do not open a public issue for one.
`SECURITY.md` carries the private contact and the disclosure process: every accepted escape
ships as a new release rather than a silent push, with a CVE requested, the researcher
credited, and a corpus entry added in the same change.
