r"""The evaluator: a validated tree in, a value out.

Written from scratch rather than adapted from an existing sandbox, and the reason is that the
security properties here are structural rather than enforced. There is no list of forbidden
functions anywhere in this file, because there is no path by which a forbidden function could be
reached:

- **Only registry names occupy call position.** `_call` resolves `node.func.id` in the registry
  and *never* consults the context. A context value that happens to be a Python callable is
  still just a value, so the callback-smuggling class (F3, simpleeval CVE-2026-32640 and
  asteval's `reduce`) has nothing to smuggle through. The validator has already rejected
  `a.b(x)` and `f()(x)`, so a bare name is the only thing that can appear there at all.

- **Attribute access does not reach objects.** `_attribute` looks a key up in a mapping. It does
  not call `getattr` unless the value's exact type was registered by the host, with an explicit
  set of permitted attribute names. Without that registration, `user.plan` on a dict works and
  `user.plan` on an arbitrary object is an error, which is what keeps the `__class__` to
  `__mro__` to `__subclasses__` climb (F2) unreachable rather than merely blocked.

- **Nothing here imports a module the language could reach.** In particular there is no
  `import os`. simpleeval imports it at module scope purely to name `os.system` in a denylist,
  which leaves `os` sitting in the sandbox module's own globals; not having it is strictly
  better than naming it.

Per-evaluation state lives in `_Run`, never on the evaluator, so **no evaluation can observe
state left by another** and one `Evaluator` is safe to share between threads. The evaluator holds
one piece of mutable state, the compile cache below, and it is a memoisation cache: compiling is a
pure function of `(source, registry)`, the registry is fixed at construction, and the cache is
charged nothing by the budget, so a hit and a miss differ only in wall time and the language has
no clock.
"""

from __future__ import annotations

import ast
import difflib
import operator
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from ._errors import (
    BudgetExceededError,
    EvaluationError,
    ReservedNameError,
    SafeExprError,
    contained,
)
from ._guards import (
    HASHABLE_CONTAINERS,
    MAX_RESULT_SIZE,
    check_depth,
    concatenated_size,
    size_charge,
)
from ._parse import check_source, parse
from ._pipes import pipe_targets, transform
from ._registry import Function, FunctionError, as_function, describe_type
from ._validate import validate

# **A cap on the result size, not on the exponent, and the difference is measured.**
#
# simpleeval caps the exponent at 4,000,000. That misses the shape that actually hurts, because
# the cost of `a ** b` scales with `bit_length(a) * b` rather than with `b`:
#
#     2 ** 4_000_000        exponent at the cap,    0.5 MiB,  0.02s
#     (10**100) ** 100_000  exponent 40x under it,  4.0 MiB,  9.6s
#     (10**1000) ** 3_000_000                                 > 60s
#
# So the guard is on the estimated width of the result. 1 MiB of integer is far past anything a
# rules engine needs and stays in the low milliseconds.
#
# **Set by measured time rather than by observed need**, which is the honest basis for this one:
# no realistic rule raises anything to a large power at all, so ten times need would be a
# meaningless number near zero. The cap is where the operation stays in the low milliseconds, from
# the table above.
MAX_POWER_RESULT_BITS = 8_388_608

# **How much work one evaluation may do**, in nodes evaluated plus the declared cost of each
# function called.
#
# Six million rather than the design's original hundred thousand, and the difference is the whole
# argument. The canonical use cases measure at 4 to 6 steps per item, so a hundred thousand steps
# buys about 16,000 to 25,000 items on the simplest possible filter. A package documenting support
# for 10^5 items while raising on 25,000 of them is not bounding work, it is mis-stating its own
# capability. Applying the design's own ">= 10x observed need" rule to the 599,984 steps measured
# at 10^5 items gives six million, and 100,000 items runs in about 0.2 seconds there.
#
# The knob is per evaluator, and there is deliberately no way to switch it off. A host that needs
# more says how much more, which is a number a reader can see and a reviewer can question.
#
# **Re-measured after the per-call costs and the size charge existed**, which the decision record
# asked for, because both change the arithmetic. The heaviest canonical use case at 100,000 items
# is the pipeline at 538,000 steps in 155 ms, so six million is 11.1 times observed need and
# clears the ten-times rule. The measured cost is 4.0 to 5.8 steps per item across three orders of
# magnitude, which is what makes a budget expressible in items at all.
#
# `scripts/limits.py` prints this and `tests/test_limits.py` asserts the ratio.
DEFAULT_STEP_BUDGET = 6_000_000

# **How many compiled expressions one evaluator remembers.**
#
# Parsing, rewriting and validating a source depends only on `(source, registry)`, and the
# registry is fixed at construction, so all of it is work an evaluator can do once. For the flat
# shapes it is nearly the whole call: a feature flag's eleven steps cost 2.7 us and the call
# costs 33.1 us, so **91% of it was being redone per evaluation and thrown away**. Measured, and
# the collections tier is the other end of the same scale: at a thousand rows the fixed cost is
# under 5% and caching it buys nothing.
#
# Bounded rather than a plain dict, and the reason is a number rather than a principle. Measured
# by `scripts/limits.py`, which prints it beside every other cap:
#
#     a typical rule, 49 bytes             4.2 KiB per tree     0.52 MiB at this bound
#     a 2,046-byte flat literal, the
#     widest the source cap admits       262.6 KiB per tree    32.83 MiB at this bound
#
# The second row is why this is bounded at all. A host that accepts expression text from an
# untrusted source would otherwise hold an unbounded allocation keyed by attacker-chosen input,
# which is the denial of service this package exists not to have.
#
# **128 rather than `_regex._CACHE`'s 256**, because a compiled tree is up to three orders of
# magnitude larger than a compiled pattern, and here the ceiling is what sets the number rather
# than the hit rate. 128 distinct expressions per evaluator is about 25x the design's five
# canonical use cases; the ceiling it buys is published in `docs/performance.md` beside the other
# limits, because a bound nobody can read is not a bound anybody can review.
#
# **A byte budget over the cached sources was considered and not taken.** It would bound the
# memory directly rather than by proxy, and tree size does track source size closely enough for
# it to work (88x for the typical rule, 131x for the widest). It is rejected because it is a
# policy invented here rather than the one already argued for two modules away, and because the
# failure it prevents costs 33 MiB in a process that has already handed an attacker the
# expression text. Recorded so it is a decision rather than an omission.
#
# The worst case is worth reading plainly: it needs 128 *distinct* maximum-length flat literals,
# from a caller who controls the source. The bound is what makes that 33 MiB instead of unbounded.
#
# The eviction policy is copied from `_regex._CACHE` rather than invented, including its argument:
# the whole cache is dropped when it fills, which is cheap and needs no ordering bookkeeping, and
# the "if full, clear, then insert" sequence is not atomic so two threads can race it. The cost of
# losing that race is a recompile, which is indistinguishable from a cold cache. A lock there
# would be contended by every evaluation in the process to prevent an outcome that is already
# correct.
MAX_COMPILE_CACHE = 128

# A source compiled once: the validated tree, and the raw material for the per-call shadowed-pipe
# check. See `Evaluator._compile` for why the second half cannot simply be a decision.
_Compiled = tuple[ast.Expression, tuple[tuple[str, int, int], ...]]

# Always available, whatever registry a host supplies.
#
# `bitor` exists because `|` was borrowed for the pipe. Once a name is in the registry, `x | name`
# means "call it", so an author who genuinely wants bitwise-or on a value that shares a registry
# name needs a way to say so. It is a builtin rather than a registry entry because the rule that
# creates the need for it is always in force.
_BUILTINS: dict[str, Function] = {
    "bitor": Function("bitor", lambda a, b: a | b),
}

_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.BitOr: operator.or_,
}
_UNARYOPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_CMPOPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    # Argument order is reversed: `a in b` is `contains(b, a)`.
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_OP_SYMBOLS: dict[type[ast.AST], str] = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//",
    ast.Mod: "%", ast.Pow: "**", ast.BitOr: "|",
}  # fmt: skip


class _Run:
    """State for one evaluation.

    Separate from the evaluator so that an `Evaluator` holds nothing mutable and can be shared
    across threads. The step budget will live here too.

    `items` is the stack of `_` bindings, innermost last. A stack rather than a single value
    because lazy arguments nest: `customers | where(_.orders | any_(_.total > 100))` has two `_`
    in scope at once, and being able to name the outer one is what makes the cross-level case
    expressible at all.
    """

    __slots__ = ("budget", "context", "items", "source", "steps")

    def __init__(self, context: Mapping[str, Any], source: str, budget: int) -> None:
        self.context = context
        self.source = source
        self.items: list[Any] = []
        # **One counter, and the sharing is structural rather than remembered.** `LazyExpr` holds
        # this same `_Run` and hands it back to `_eval`, so a predicate running once per item
        # spends from the same pool as the expression that invoked it. There is no per-level
        # budget to reset and no place to forget to thread one through, which is what makes
        # `map(a, where(b, ...))` bounded at O(n*m) total rather than O(n) per level.
        self.budget = budget
        self.steps = budget


class LazyExpr:
    """An unevaluated expression, handed to a function that will run it per item.

    Holds an already-parsed, already-validated subtree of the original expression. **Parse once,
    evaluate N times**: filtering ten thousand items runs `ast.parse` zero extra times, which is
    the difference between usable and not on a real collection.

    The subtree is private and there is no path to it from the expression language. Attribute
    access reaches mapping keys only, so even if an instance of this reached a context it would
    be inert. That is the F8 property, and it holds because there is no side table keyed by a
    name a user could type.
    """

    __slots__ = ("_evaluator", "_node", "_run")

    def __init__(self, evaluator: Evaluator, node: ast.expr, run: _Run) -> None:
        self._evaluator = evaluator
        self._node = node
        self._run = run

    def evaluate(self, item: Any) -> Any:
        """Evaluate the expression with `item` bound to `_`.

        Args:
            item: The value `_` refers to for this call.

        Returns:
            The value of the expression.
        """
        self._run.items.append(item)
        try:
            return self._evaluator._eval(self._node, self._run)  # noqa: SLF001
        finally:
            # A `finally` rather than a plain pop, so an error inside one item does not leave the
            # stack deeper than it started and silently shift what `_` means afterwards.
            self._run.items.pop()

    def __repr__(self) -> str:
        # Deliberately says nothing about the tree. A repr that unparsed the subtree would put
        # expression internals into whatever logged it.
        return "<LazyExpr>"


def _error(run: _Run, node: ast.AST, message: str) -> EvaluationError:
    """Build a positioned `EvaluationError`.

    Returns rather than raises, so callers raise it outside any exception handler. That is the
    package-wide convention and the reason is in `_errors`: raising inside a handler leaves
    `__context__` pointing at the caught exception, which is a live handle on the caller's
    data.
    """
    return EvaluationError(
        message,
        source=run.source,
        lineno=getattr(node, "lineno", None),
        offset=(node.col_offset + 1 if hasattr(node, "col_offset") else None),
    )


def _checked_budget(budget: object) -> int:
    """Validate a budget at construction, where a host reads the message.

    Takes `object` rather than `int` deliberately. The annotation on `Evaluator.__init__` is a
    promise to a type-checked caller, and a host embedding this often is not one: a budget
    arriving as a float, a string or `True` should be refused here rather than doing something
    quietly strange several thousand steps into an evaluation. Typed as `int`, this function's
    own checks would be provably redundant and the type checker would say so, which is why the
    parameter widens instead of the checks being silenced.

    `True` is refused rather than read as 1. `bool` is an `int` in Python, and a budget of `True`
    is a mistake every time.

    Args:
        budget: The value the host passed.

    Returns:
        The budget, once it is known to be a positive integer.

    Raises:
        ValueError: If it is anything else.
    """
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        message = f"budget must be a positive integer, got {budget!r}"
        raise ValueError(message)
    return budget


def _spend(run: _Run, node: ast.AST, amount: int) -> None:
    """Take `amount` from the budget, or say it has run out.

    Args:
        run: The evaluation state.
        node: The node to point the error at.
        amount: How many steps to spend.

    Raises:
        BudgetExceededError: If the budget is exhausted.
    """
    run.steps -= amount
    if run.steps < 0:
        raise BudgetExceededError(
            run.budget,
            source=run.source,
            lineno=getattr(node, "lineno", None),
            offset=(node.col_offset + 1 if hasattr(node, "col_offset") else None),
        )


def _repetition_size(left: Any, right: Any) -> int | None:
    """How long `left * right` would be, if this is sequence repetition.

    Args:
        left: The left operand.
        right: The right operand.

    Returns:
        The resulting length, or `None` if `*` here is ordinary arithmetic.
    """
    for repeated, count in ((left, right), (right, left)):
        if isinstance(repeated, (str, bytes, bytearray, list, tuple)) and isinstance(count, int):
            return len(repeated) * count
    return None


def _checked_depth(run: _Run, node: ast.AST, value: Any) -> None:
    """Refuse a value too deeply nested to hash, positioned at the node that would hash it.

    Every place the language can reach `hash` on a value from the context goes through here:
    membership against a set or a mapping, a subscript into a mapping, and a key in a dict
    literal. Hashing is the one operation that does not raise on data too deep for it, so this is
    a check rather than a handler.

    Args:
        run: The evaluation state.
        node: The node to point the error at.
        value: The value about to be hashed.

    Raises:
        EvaluationError: If the value is too deeply nested.
    """
    if not isinstance(value, HASHABLE_CONTAINERS):
        # The same test the call sites make, repeated so this is safe to call from anywhere.
        return
    try:
        check_depth(value)
    except FunctionError as objection:
        failure = _error(run, node, f"this value {objection}")
    else:
        return
    raise failure


def _suggest(name: str, candidates: Sequence[str]) -> str:
    """Return a ', did you mean X?' clause, or an empty string.

    Only offers names the expression author could already see, so this reveals nothing they do
    not have.
    """
    close = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.7)
    return f", did you mean `{close[0]}`?" if close else ""


class Evaluator:
    """Evaluates expressions against a context.

    **Fixed after construction, and no evaluation can observe state left by another.** The
    registry, the attribute allowlist and the budget are frozen when the evaluator is built, and
    every piece of per-evaluation state lives in a call-scoped `_Run`. One `Evaluator` may be
    shared freely between threads.

    The one thing an evaluation writes is the compile cache, and it is a memoisation cache:
    compiling a source is a pure function of `(source, registry)`, the budget is charged the same
    number of steps on a hit as on a miss, and the language has no clock, so nothing inside an
    expression can tell a warm cache from a cold one. `tests/test_thread_safety.py` proves the
    budget half by bisection rather than by argument, the same way it does for the pattern cache.

    Args:
        registry: Name to `Function` (or to a bare callable, for the common case of one with no
            lazy arguments). The only things an expression can call. Empty by default, because
            the function registry is built separately; an evaluator with no registry is a
            perfectly usable expression language for comparisons and field access.
        attribute_types: Opt-in `getattr` access, as type to permitted attribute names. **Left
            empty unless a host deliberately opts in**, because attribute traversal on arbitrary
            objects is where essentially every Python sandbox escape has started.
        budget: How many steps one evaluation may spend, counted per node evaluated plus each
            function's declared cost. Every evaluation starts from this number, so it caps a
            single `evaluate` call rather than the lifetime of the evaluator. There is no value
            meaning "unlimited": a host needing more says how much more.

    Raises:
        ValueError: If `budget` is not a positive integer.
    """

    __slots__ = ("_attribute_types", "_budget", "_cache", "_registry")

    def __init__(
        self,
        registry: Mapping[str, Function | Callable[..., Any]] | None = None,
        attribute_types: Mapping[type, frozenset[str]] | None = None,
        budget: int = DEFAULT_STEP_BUDGET,
    ) -> None:
        self._budget = _checked_budget(budget)
        self._registry: dict[str, Function] = {
            **_BUILTINS,
            **{name: as_function(name, entry) for name, entry in (registry or {}).items()},
        }
        self._attribute_types: dict[type, frozenset[str]] = dict(attribute_types or {})
        # Per instance rather than module-level, which makes registry isolation structural: two
        # evaluators cannot be served each other's grammar because they do not share a dict.
        # `flags | first` is a call with a registry and bitwise-or without, so a source-keyed
        # shared cache would have to argue that it never happens instead of making it impossible.
        self._cache: dict[str, _Compiled] = {}

    @property
    def function_names(self) -> frozenset[str]:
        """The names an expression may call."""
        return frozenset(self._registry)

    @property
    def budget(self) -> int:
        """How many steps each evaluation may spend."""
        return self._budget

    @contained
    def evaluate(self, source: str, context: Mapping[str, Any] | None = None) -> Any:
        """Parse, validate and evaluate `source` against `context`.

        Args:
            source: The expression.
            context: Names available to the expression. Values are data; a callable among them
                is still only a value.

        Returns:
            The value of the expression.

        Raises:
            SafeExprError: For every failure. Nothing else escapes, which is what `contained`
                is for.
        """
        values = context or {}

        # **The source cap runs before the cache, for the same reason it runs before the parser.**
        # A cache keyed on the source would otherwise be the first thing to handle hostile input:
        # an unhashable argument would come back as a `TypeError` from a dict lookup, and a
        # multi-megabyte string would be hashed in full before anything decided it was too long.
        check_source(source)

        entry = self._cache.get(source)
        if entry is None:
            # Parse, note the pipe targets, refuse a shadowed pipe, rewrite, validate. Every line
            # here except the refusal depends only on `(source, registry)`, and the registry is
            # fixed at construction, so every line except the refusal is what gets kept.
            #
            # **The transform runs before validation**, so the tree which is validated is the tree
            # which is evaluated, with no window between the check and the use. It can only ever
            # produce a call to a name already in the registry, using subtrees that were already
            # there, so it cannot launder anything past the allowlist; and it preserves positions,
            # so errors still point at what the user wrote.
            #
            # **`pipe_targets` runs on the pre-transform tree**, because afterwards `x | first`
            # and `first(x)` are the same tree and the distinction it depends on is gone. It is
            # given the registry rather than a collision set: which registered names actually
            # collide with the data is not known here and must not be, or the entry would depend
            # on a context.
            #
            # **The refusal sits between them, and that placement is load-bearing.** It ran ahead
            # of validation before this cache existed, so a shadowed pipe is reported ahead of an
            # unrelated validation error in the same expression. Moving it after the compile would
            # reverse that on the first call and leave it right on every call after, which is a
            # difference nothing but a cold-cache test would ever see.
            tree = parse(source)
            targets = pipe_targets(tree, self._registry)
            self._refuse_shadowed_pipes(targets, values, source)
            tree = transform(tree, self._registry)
            validate(tree, source)

            # **Nothing is stored until every line above has returned.** A source that fails to
            # parse or fails validation is recompiled, and refused identically, on every call.
            # Caching the refusal would be sound and caching it as anything else would not, so the
            # simpler thing is the one that cannot be got wrong.
            #
            # If full, drop the lot and start again. Copied from `_regex._CACHE` rather than
            # invented, along with its argument for why no lock is needed: losing that race costs
            # a recompile, which is indistinguishable from a cold cache.
            entry = (tree, targets)
            if len(self._cache) >= MAX_COMPILE_CACHE:
                self._cache.clear()
            self._cache[source] = entry
        else:
            # **Per call, and it cannot be otherwise.** This reads the context, so it is a
            # decision about the data rather than about the source, and the entry holds nothing
            # that depends on the data. Memoising it would make `flags | first` succeed or refuse
            # according to which context happened to arrive first, which is the one defect this
            # cache is most likely to be built with.
            tree, targets = entry
            self._refuse_shadowed_pipes(targets, values, source)

        return self._run(tree, _Run(values, source, self._budget))

    def _refuse_shadowed_pipes(
        self, targets: tuple[tuple[str, int, int], ...], context: Mapping[str, Any], source: str
    ) -> None:
        """Refuse a `|` whose right-hand name is both a function and a key in the data.

        **Narrower than "reject any collision", and the difference is measured.** A bare name
        reads the context, so `metrics | where(_.value > min)` against `{"min": 10}` is correct
        and unambiguous; refusing it because `min` happens to be a function would break a
        realistic rule to prevent nothing. A written `first(x)` is unambiguous too, because a
        context value cannot be called at all. The right of a `|` is the one position where the
        author might reasonably have meant the other thing, since without the registry that `|`
        would be bitwise or.

        The walk that finds the candidates happened at compile time; what is left here is the set
        intersection that was always the cheap part, and it is the half that reads the context.
        `targets` arrives in source order, so the first match is the one to report.

        Args:
            targets: `(name, lineno, col_offset)` per right-of-pipe registry name, in source
                order, from `_compile`.
            context: The names available to the expression.
            source: The original source, for the error's position.

        Raises:
            ReservedNameError: On the first shadowed pipe, in source order.
        """
        if not targets or not context:
            return
        shadowed = self._registry.keys() & context.keys()
        if not shadowed:
            return
        for name, lineno, col_offset in targets:
            if name in shadowed:
                raise ReservedNameError(name, source=source, lineno=lineno, offset=col_offset + 1)

    def _run(self, tree: ast.Expression, run: _Run) -> Any:
        return self._eval(tree.body, run)

    # -- dispatch ----------------------------------------------------------
    def _eval(self, node: ast.AST, run: _Run) -> Any:
        # **The whole budget, charged in one place.** Every value this language produces comes
        # through here exactly once per node evaluated, including every re-evaluation of a lazy
        # subtree, so counting here needs no cooperation from anything else: a function added
        # later cannot forget to charge, and a new node type cannot arrive uncounted.
        # **This costs about 30ns per node, against roughly 80ns for the dispatch below it, and
        # no spelling makes it free.** A chained assignment, a countdown tested for truthiness and
        # a list cell were all measured against this one and came out the same within noise; the
        # irreducible part is reading and writing an attribute once per node. The price is paid
        # deliberately: without it there is no bound on work at all, and the plainest version of
        # the most security-critical line in the package is worth more than 15%.
        run.steps -= 1
        if run.steps < 0:
            _spend(run, node, 0)
        handler = self._DISPATCH.get(type(node))
        if handler is None:
            # Unreachable: validation runs first and rejects anything not in the allowlist. Kept
            # so that a future allowlist entry without a handler fails loudly rather than
            # silently evaluating to None.
            raise _error(run, node, f"cannot evaluate `{type(node).__name__}`")
        return handler(self, node, run)

    # -- leaves ------------------------------------------------------------
    def _constant(self, node: ast.Constant, run: _Run) -> Any:
        return node.value

    def _name(self, node: ast.Name, run: _Run) -> Any:
        if node.id.startswith("_"):
            return self._item(node, run)
        try:
            return run.context[node.id]
        except (KeyError, TypeError):
            pass
        # Raised out here rather than in the handler above. See `_error`.
        raise _error(
            run,
            node,
            f"`{node.id}` is not defined{_suggest(node.id, list(run.context))}",
        )

    def _item(self, node: ast.Name, run: _Run) -> Any:
        """Resolve `_`, `_1`, `_2`, ... against the stack of lazy bindings.

        `_` and `_1` are the innermost item; `_2` is one level out, and so on. Validation has
        already guaranteed the name is an underscore followed by digits, so no other underscore
        name reaches here.

        Reaching outward is not a convenience. Under innermost-only binding,
        `customers | where(_.orders | any_(_.total > _.threshold))`, meaning "orders above this
        customer's threshold", is unwriteable, and it is an ordinary rules-engine expression.
        """
        depth = 1 if node.id in {"_", "_1"} else int(node.id[1:])
        if not run.items:
            raise _error(
                run,
                node,
                f"`{node.id}` is only available inside a function argument that takes an "
                f"expression, such as `where(_.price > 10)`",
            )
        if depth > len(run.items):
            raise _error(
                run,
                node,
                f"`{node.id}` reaches {depth} levels out but only {len(run.items)} "
                f"{'is' if len(run.items) == 1 else 'are'} in scope here",
            )
        return run.items[-depth]

    # -- access ------------------------------------------------------------
    def _attribute(self, node: ast.Attribute, run: _Run) -> Any:
        value = self._eval(node.value, run)

        # Mappings first, and for a mapping there is no second option: `d.items` is the key
        # "items", never the dict method. Falling back to `getattr` here is how a sandbox ends up
        # exposing `.keys` and `.__class__` on data the host thought was inert.
        #
        # **The concrete type comes first in the tuple, and that ordering is the optimisation.**
        # `isinstance` walks a tuple left to right and stops at the first hit, so a plain `dict`
        # is answered by a C type check and never reaches `Mapping.__instancecheck__`, which is
        # Python-level and dispatches into `_abc._abc_instancecheck`. Measured on 3.13.15 over
        # two million calls: `isinstance(d, Mapping)` 99.9ns against `isinstance(d, dict)` 27.4ns.
        #
        # This line runs **once per attribute per item**, so a thousand-row `map(_.name)` pays it
        # a thousand times; `cProfile` over the canonical pipeline put `__instancecheck__` at
        # ~1,500 calls per evaluation. Worth **7 to 11%** on the collections tier, measured
        # 2026-08-24 by interleaving the arms within one process and taking medians over fifteen
        # rounds. A `timeit` of the check in isolation does **not** predict this number;
        # `tests/benchmarks/test_attribute_path_bench.py` carries the method and the table.
        #
        # Swapping the two entries gives all of it back and changes no behaviour, so nothing
        # would fail. `tests/test_eval.py::TestTheMappingFastPath` reads this line instead of
        # trusting the comment.
        if isinstance(value, (dict, Mapping)):
            try:
                return value[node.attr]
            except (KeyError, TypeError):
                pass
            raise _error(
                run,
                node,
                f"no field `{node.attr}`{_suggest(node.attr, [str(k) for k in value])}",
            )

        # The one opt-in path. A host that registers a type is stating that these specific
        # attributes on that specific type are safe to read; nothing is inferred.
        allowed = self._attribute_types.get(type(value))
        if allowed is not None and node.attr in allowed:
            return getattr(value, node.attr)

        raise _error(
            run,
            node,
            f"cannot read `.{node.attr}` on a value of type `{describe_type(value)}`; "
            f"attribute access works on mappings, and on other types only where the host has "
            f"registered them",
        )

    def _subscript(self, node: ast.Subscript, run: _Run) -> Any:
        value = self._eval(node.value, run)
        key = self._eval(node.slice, run)

        # **The dynamic half of the private-name block.** Validation catches `x["__class__"]`
        # because the key is a literal it can read. It cannot catch `x["__cl" + "ass__"]`, which
        # is the same attack written so a static check cannot see it. This is where that stops.
        if isinstance(key, str) and key.startswith("_"):
            raise _error(
                run,
                node,
                f'key "{key}" is not available: keys beginning with an underscore are blocked',
            )

        if isinstance(key, HASHABLE_CONTAINERS):
            _checked_depth(run, node, key)
        # Built inside the handler and raised outside it, like everywhere else here. A
        # `raise ... from None` in the handler clears `__cause__` and leaves `__context__`
        # pointing at the caught exception, and a `TypeError` from a host object's
        # `__getitem__` is a live handle on the caller's data. That is F9, and the corpus
        # checks every entry for it.
        failure: EvaluationError | None = None
        try:
            return value[key]
        except (KeyError, IndexError):
            pass
        except TypeError:
            failure = _error(
                run,
                node,
                f"cannot index a value of type `{describe_type(value)}` "
                f"with a `{describe_type(key)}`",
            )
        if failure is None:
            failure = _error(run, node, f"no entry for {key!r}")
        raise failure

    def _slice(self, node: ast.Slice, run: _Run) -> slice:
        return slice(
            None if node.lower is None else self._eval(node.lower, run),
            None if node.upper is None else self._eval(node.upper, run),
            None if node.step is None else self._eval(node.step, run),
        )

    # -- containers --------------------------------------------------------
    def _list(self, node: ast.List, run: _Run) -> list[Any]:
        return [self._eval(item, run) for item in node.elts]

    def _tuple(self, node: ast.Tuple, run: _Run) -> tuple[Any, ...]:
        return tuple(self._eval(item, run) for item in node.elts)

    def _dict(self, node: ast.Dict, run: _Run) -> dict[Any, Any]:
        # Validation has already rejected `{**a}`, which is the only way a key can be None.
        built: dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:  # pragma: no cover - validation rejects `{**a}` first
                continue
            evaluated = self._eval(key, run)
            if isinstance(evaluated, HASHABLE_CONTAINERS):
                _checked_depth(run, key, evaluated)
            built[evaluated] = self._eval(value, run)
        return built

    # -- operators ---------------------------------------------------------
    def _binop(self, node: ast.BinOp, run: _Run) -> Any:
        # A bare name on the right of `|` that is neither a function nor in the context is almost
        # always a mistyped pipe. Without this it degrades to bitwise-or and then reports "not
        # defined", which is true but hides what the author was reaching for.
        if (
            isinstance(node.op, ast.BitOr)
            and isinstance(node.right, ast.Name)
            and node.right.id not in self._registry
            and node.right.id not in run.context
        ):
            raise _error(
                run,
                node,
                f"`{node.right.id}` is not a function, so `|` here means bitwise or"
                f"{_suggest(node.right.id, sorted(self._registry))}",
            )

        left = self._eval(node.left, run)
        right = self._eval(node.right, run)
        if isinstance(node.op, ast.Pow):
            return self._power(node, left, right, run)

        # **`%` means modulo here, never string formatting**, and the distinction is only
        # visible at runtime, which is precisely the F1 lesson. A static allowlist sees
        # `BinOp(Mod)` and cannot tell `n % 3` from `"%(__class__)s" % d`. Measured against this
        # evaluator before the guard existed:
        #
        #     "%(__class__)s" % d   ->  'REACHED'   the underscore-key block is bypassed, because
        #                                           %-formatting does its own __getitem__ in C
        #                                           and never passes through `_subscript`
        #     "%s" % obj            ->  '<Host api_key=sk-live-SECRET>'
        #                                           a context object's repr handed to the
        #                                           expression author as a value
        #
        # The design bans `%` from the *registry*; it is not in the registry, it is an operator,
        # so that ban never applied. Rejecting it on string and bytes keeps integer modulo, which
        # is the only thing anybody wanted from `%` in a rules engine.
        if isinstance(node.op, ast.Mod) and isinstance(left, (str, bytes)):
            raise _error(
                run,
                node,
                "`%` on text means string formatting, which can read attributes and keys that "
                "this language does not allow; use `+` or `join` to build strings",
            )

        # **`*` on a sequence is repetition, and repetition had no cap at all.** R7 lists a
        # string length cap among the deterministic bounds and it had never been built, which
        # left a hole the step budget cannot see: the budget counts *nodes evaluated*, and
        # `"a" * 5000000` is three nodes. Measured against this evaluator before the guard
        # existed, that expression allocated five megabytes and the constant was free to be
        # larger; `[0] * 5000000` did the same with a list.
        #
        # Guarded on the predicted length rather than on the operands, for the same reason `**`
        # is guarded on the width of its result: an error after the allocation has already cost
        # the allocation.
        self._check_produced_size(node, left, right, run)

        op = _BINOPS[type(node.op)]
        symbol = _OP_SYMBOLS.get(type(node.op), "?")
        try:
            produced = op(left, right)
        except ZeroDivisionError:
            failure = _error(run, node, "division by zero")
        except TypeError:
            failure = _error(
                run,
                node,
                f"cannot apply `{symbol}` to `{describe_type(left)}` and `{describe_type(right)}`",
            )
        except (OverflowError, ValueError):
            failure = _error(run, node, f"`{symbol}` produced a result that cannot be represented")
        except RecursionError:
            # Reachable through the operands' own `__add__` and friends, which are host code.
            # List and tuple concatenation is shallow and cannot recurse, so this is about the
            # objects a host puts in a context rather than about nesting.
            failure = _error(
                run,
                node,
                f"cannot apply `{symbol}`: the operation recursed without end, which happens "
                f"when values nest too deeply or refer to themselves",
            )
        else:
            # **Producing a large value costs budget.** The per-result cap above bounds any one
            # allocation; this is what bounds the total, and the total is what actually hurts:
            # every string in `rows | map(t + t)` is well under the cap and the sum was 343 MB.
            charge = size_charge(produced)
            if charge:
                _spend(run, node, charge)
            return produced
        raise failure

    def _check_produced_size(self, node: ast.BinOp, left: Any, right: Any, run: _Run) -> None:
        """Refuse a repetition or concatenation whose result would be too large.

        **Both are guarded on the predicted size**, for the same reason `**` is: an error raised
        after the allocation has already cost the allocation. `*` was measured allocating five
        megabytes from fifteen characters, and `+` doubles, so `a + a + a + a` on a
        200,000-item list is 800,000 items from four nodes.

        Args:
            node: The operation.
            left: The left operand.
            right: The right operand.
            run: The evaluation state.

        Raises:
            EvaluationError: If the result would be over the cap.
        """
        if isinstance(node.op, ast.Mult):
            symbol, size = "*", _repetition_size(left, right)
        elif isinstance(node.op, ast.Add):
            symbol, size = "+", concatenated_size(left, right)
        else:
            return
        if size is not None and size > MAX_RESULT_SIZE:
            raise _error(
                run,
                node,
                f"`{symbol}` would produce {size:,} items, over the limit of {MAX_RESULT_SIZE:,}",
            )

    def _power(self, node: ast.BinOp, base: Any, exponent: Any, run: _Run) -> Any:
        """`**`, guarded on the size of the result rather than on the exponent."""
        if isinstance(base, int) and isinstance(exponent, int) and exponent > 0:
            estimate = max(base.bit_length(), 1) * exponent
            if estimate > MAX_POWER_RESULT_BITS:
                raise _error(
                    run,
                    node,
                    f"`**` would produce a number about {estimate // 8 // 1024} KiB wide, over "
                    f"the {MAX_POWER_RESULT_BITS // 8 // 1024} KiB limit",
                )
        try:
            return base**exponent
        except ZeroDivisionError:
            failure = _error(run, node, "division by zero")
        except (OverflowError, ValueError):
            failure = _error(run, node, "`**` produced a result that cannot be represented")
        except TypeError:
            failure = _error(
                run,
                node,
                f"cannot apply `**` to `{describe_type(base)}` and `{describe_type(exponent)}`",
            )
        raise failure

    def _unaryop(self, node: ast.UnaryOp, run: _Run) -> Any:
        value = self._eval(node.operand, run)
        try:
            return _UNARYOPS[type(node.op)](value)
        except TypeError:
            pass
        raise _error(run, node, f"cannot apply this operator to `{describe_type(value)}`")

    def _boolop(self, node: ast.BoolOp, run: _Run) -> Any:
        """`and` / `or`, short-circuiting and returning the deciding value, as Python does.

        `a or b` yields `b` when `a` is falsy, rather than `True`/`False`, which is what makes
        `name or "anonymous"` work.
        """
        wanted = isinstance(node.op, ast.Or)
        result: Any = not wanted
        for operand in node.values:
            result = self._eval(operand, run)
            if bool(result) is wanted:
                return result
        return result

    def _compare(self, node: ast.Compare, run: _Run) -> Any:
        """Chained comparison. `1 < a < 3` evaluates `a` once and short-circuits."""
        left = self._eval(node.left, run)
        # Both handlers build and `break`, so the raise below happens outside them. Leaving the
        # handler is what restores the thread's "currently handled exception", and it is the only
        # thing that keeps `__context__` clear: `raise ... from None` does not, and a `TypeError`
        # out of a host object's `__eq__` is a live handle on the caller's data. F9, and the
        # corpus checks every entry for it.
        failure: EvaluationError | None = None
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self._eval(comparator, run)
            # `a in b` hashes `a` when `b` is a set or a mapping, and hashing is the one
            # operation that crashes rather than raising on data too deep for it. The second
            # test is the cheap one and it is what keeps this off the hot path: nothing but a
            # tuple or a frozenset can carry the recursion.
            if isinstance(op, (ast.In, ast.NotIn)) and isinstance(left, HASHABLE_CONTAINERS):
                _checked_depth(run, node, left)
            try:
                outcome = _CMPOPS[type(op)](left, right)
            except TypeError:
                failure = _error(
                    run,
                    node,
                    f"cannot compare `{describe_type(left)}` with `{describe_type(right)}`",
                )
                break
            except RecursionError:
                # Comparison *does* raise, and CPython gives out between 5,000 and 10,000 levels
                # of nesting or on the first cycle. Unguarded this reached the boundary and was
                # reported as "this is a bug in safeexpr", which is the wrong answer to a
                # legitimate complaint about the host's data.
                #
                # The wording covers a second cause as well: comparing calls the values' own
                # `__eq__` and `__lt__`, which are host code, and host code that recurses without
                # end arrives here identically.
                failure = _error(
                    run,
                    node,
                    "cannot compare these values: the comparison recursed without end, which "
                    "happens when values nest too deeply or refer to themselves",
                )
                break
            if not outcome:
                return False
            left = right
        if failure is not None:
            raise failure
        return True

    def _ifexp(self, node: ast.IfExp, run: _Run) -> Any:
        branch = node.body if self._eval(node.test, run) else node.orelse
        return self._eval(branch, run)

    # -- calls -------------------------------------------------------------
    def _call(self, node: ast.Call, run: _Run) -> Any:
        """Only registry names are callable.

        The context is never consulted. That is the whole of the F3 defence: there is no path
        from a context value to call position, so a dangerous callable handed in as data cannot
        be invoked no matter what the expression says.

        Validation has already guaranteed `node.func` is a bare `Name` and that there are no
        keyword or starred arguments.
        """
        name = node.func.id  # type: ignore[attr-defined]  # validated: func is a Name
        function = self._registry.get(name)
        if function is None:
            raise _error(
                run,
                node,
                f"`{name}` is not a function{_suggest(name, sorted(self._registry))}"
                + ("; values from the context cannot be called" if name in run.context else ""),
            )
        # What the function costs beyond the nodes its arguments spend. A per-call figure: work
        # that scales with a collection is already charged per item, because a lazy argument is
        # re-evaluated through `_eval` once per item and each of those pays for itself.
        _spend(run, node, function.cost)
        # **Arity before arguments.** Checked here, not by letting the call raise `TypeError`,
        # because those two failures are indistinguishable once they arrive: a function handed
        # the wrong *number* of arguments and a function handed the wrong *kind* both raise
        # `TypeError`, and the handler below used to report both as a miscount. It also means a
        # miscounted call does not evaluate its arguments first.
        #
        # A bare callable declares no arity and is left unchecked, exactly as before.
        if not function.accepts(len(node.args)):
            raise _error(
                run,
                node,
                f"`{name}` takes {function.arity_text()}, got {len(node.args)}",
            )
        arguments = self._arguments(node, function, run)
        try:
            produced = function.call(*arguments)
        except SafeExprError:
            raise
        except FunctionError as objection:
            # A function saying what is wrong with the values it was given. It knows what; only
            # this layer knows where, so the message is positioned here. Nothing but the string
            # crosses over, which is why `FunctionError` is allowed to carry nothing else.
            failure = _error(run, node, f"`{name}`: {objection}")
        except TypeError:
            # **Two different failures wearing one exception type.** A function handed the wrong
            # number of arguments and a function handed the wrong kind of value both raise
            # `TypeError` here, and this handler used to report both as a miscount, which is a
            # false statement about a call whose argument count was fine.
            #
            # An informative arity that the call already satisfied settles it: a miscount is
            # ruled out, so the message can say what actually happened. Where nothing was
            # declared it still might be either, and the older wording is the honest one.
            failure = _error(
                run,
                node,
                f"`{name}` cannot work with the values it was given"
                if function.checks_arity
                else f"`{name}` cannot accept {len(arguments)} argument(s)",
            )
        except (ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError):
            # A registry function objecting to its input. The exception type is not reported:
            # these are our own functions and the useful part is which call failed.
            failure = _error(run, node, f"`{name}` could not process its arguments")
        else:
            # The other half of the size charge. `where`, `map`, `split`, `join`, `extend` and
            # `merge` all build a value whose size the caller chose, and the declared per-call
            # cost cannot see that.
            charge = size_charge(produced)
            if charge:
                _spend(run, node, charge)
            return produced
        raise failure

    def _arguments(self, node: ast.Call, function: Function, run: _Run) -> list[Any]:
        """Evaluate the eager arguments, skip the lazy ones, and charge for what was read.

        Args:
            node: The call.
            function: The registry entry being called.
            run: The evaluation state.

        Returns:
            The arguments to hand the function.
        """
        # **The lazy positions are simply not evaluated.** No side table, no synthetic names, no
        # rewritten tree: the function said which of its arguments are expressions, so those
        # arrive as a `LazyExpr` over the original subtree and everything else arrives as a value.
        arguments = [
            LazyExpr(self, argument, run) if index in function.lazy else self._eval(argument, run)
            for index, argument in enumerate(node.args)
        ]
        # **A call is charged for what it reads, and below for what it produces.** Without the
        # first half the counter is blind to a function that walks its input in C without
        # evaluating anything per item, and the blindness is not small: measured, `sum` over
        # 200,000 integers is charged **three steps for 1.7 milliseconds**, which is 2,000 times
        # less per unit of work than an expression evaluated per item. `rows | map(sum(nums))`
        # therefore bought about eighteen minutes of work from the default budget, which is
        # precisely the denial of service the budget exists to stop.
        #
        # Charging the eager arguments fixes it uniformly and needs nothing declared per
        # function. A `LazyExpr` has no length, so lazy positions contribute nothing. The
        # over-charge is on the constant-time accessors, `first` and `len` on a large list, and
        # it is 0.05% of the default budget: conservative in the safe direction and far cheaper
        # than a per-function exemption to get wrong.
        for argument in arguments:
            charge = size_charge(argument)
            if charge:
                _spend(run, node, charge)
        return arguments

    # Node type to handler, built inside the class body so the handlers are plain names here
    # rather than attribute lookups on a class that does not exist yet. The values are unbound
    # functions, so `_eval` passes `self` explicitly.
    _DISPATCH: ClassVar[dict[type[ast.AST], Callable[..., Any]]] = {
        ast.Constant: _constant,
        ast.Name: _name,
        ast.Attribute: _attribute,
        ast.Subscript: _subscript,
        ast.Slice: _slice,
        ast.List: _list,
        ast.Tuple: _tuple,
        ast.Dict: _dict,
        ast.BinOp: _binop,
        ast.UnaryOp: _unaryop,
        ast.BoolOp: _boolop,
        ast.Compare: _compare,
        ast.IfExp: _ifexp,
        ast.Call: _call,
    }


# The evaluator behind the module-level `evaluate`, built once and shared by every caller.
#
# Named `_SHARED` rather than `_DEFAULT` because `tests/test_limits.py` scans this package for
# module-level names containing `MAX`, `DEFAULT` or `UNIT` and requires each to have a
# published basis in `scripts/limits.py`. That scan is deliberately loose and it is right to
# be: it is the forcing function that stops a number arriving with no argument behind it. This
# is an object rather than a number, so the honest fix is a name that does not claim to be one.
#
# **A fresh `Evaluator` per call would leave the README's headline API permanently cold.** The
# construction itself is 0.21 us and would not be worth a line on its own; what it costs is the
# compile cache, which lives on the instance and would therefore be discarded on the way out of
# every call. That is the difference between 11x and 1x for the one entry point most readers try
# first.
#
# Sound for the same reasons the cache is. It has no registry, so its grammar is the fixed one
# every caller of this function already shares; it holds nothing mutable but the cache; and it is
# documented as safe to share between threads, which is what a module-level singleton is.
_SHARED = Evaluator()


def evaluate(source: str, context: Mapping[str, Any] | None = None) -> Any:
    """Evaluate `source` against `context` with no functions available.

    A convenience for the common case of a comparison over data. Build an `Evaluator` when you
    want a registry or a shared instance.

    Uses one module-level evaluator rather than building one per call, so a source evaluated twice
    is compiled once. The evaluator it shares has no registry, so every caller of this function
    sees the same language whoever else is using it.

    Args:
        source: The expression.
        context: Names available to the expression.

    Returns:
        The value of the expression.

    Raises:
        SafeExprError: For every failure.
    """
    return _SHARED.evaluate(source, context)
