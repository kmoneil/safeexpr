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

Per-evaluation state lives in `_Run`, never on the evaluator, so an `Evaluator` is immutable
after construction and safe to share between threads.
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
from ._guards import MAX_RESULT_SIZE
from ._parse import parse
from ._pipes import shadowed_pipes, transform
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
# Provisional: the limits work sets every default empirically, and this one is derived from the
# measurements above rather than from a benchmark of real expressions.
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
# Provisional in the same sense as the other caps here: the empirical limits work owns the final
# value.
DEFAULT_STEP_BUDGET = 6_000_000

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


def _suggest(name: str, candidates: Sequence[str]) -> str:
    """Return a ', did you mean X?' clause, or an empty string.

    Only offers names the expression author could already see, so this reveals nothing they do
    not have.
    """
    close = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.7)
    return f", did you mean `{close[0]}`?" if close else ""


class Evaluator:
    """Evaluates expressions against a context.

    Immutable after construction: the registry and the attribute allowlist are fixed, and no
    evaluation writes to the instance. One `Evaluator` may be shared freely between threads.

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

    __slots__ = ("_attribute_types", "_budget", "_registry")

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
        # Parse, check for a shadowed pipe, rewrite pipes, validate, evaluate. The transform runs
        # **before** validation so that the tree which is validated is the tree which is
        # evaluated, with no window between the check and the use. It can only ever produce a
        # call to a name already in the registry, using subtrees that were already there, so it
        # cannot launder anything past the allowlist; and it preserves positions, so errors still
        # point at what the user wrote.
        values = context or {}
        tree = parse(source)
        self._refuse_shadowed_pipes(tree, values, source)
        tree = transform(tree, self._registry)
        validate(tree, source)
        return self._run(tree, _Run(values, source, self._budget))

    def _refuse_shadowed_pipes(
        self, tree: ast.Expression, context: Mapping[str, Any], source: str
    ) -> None:
        """Refuse a `|` whose right-hand name is both a function and a key in the data.

        **Narrower than "reject any collision", and the difference is measured.** A bare name
        reads the context, so `metrics | where(_.value > min)` against `{"min": 10}` is correct
        and unambiguous; refusing it because `min` happens to be a function would break a
        realistic rule to prevent nothing. A written `first(x)` is unambiguous too, because a
        context value cannot be called at all. The right of a `|` is the one position where the
        author might reasonably have meant the other thing, since without the registry that `|`
        would be bitwise or.

        Checked before the rewrite, because afterwards `x | first` and `first(x)` are the same
        tree, and guarded by a set intersection so the common case costs one cheap operation
        rather than a walk.

        Args:
            tree: The parsed expression, before the rewrite.
            context: The names available to the expression.
            source: The original source, for the error's position.

        Raises:
            ReservedNameError: On the first shadowed pipe, in source order.
        """
        if not context:
            return
        shadowed = self._registry.keys() & context.keys()
        if not shadowed:
            return
        offenders = shadowed_pipes(tree, shadowed)
        if not offenders:
            return
        # **Ordered and positioned by the right-hand name, not by the `BinOp`.** `x | first | last`
        # parses as `(x | first) | last`, and both of those start at column zero, so sorting on
        # the operator's own position is a tie that reports whichever the walk happened to find
        # first. The colliding name is what differs between them, and it is also what the caret
        # should be pointing at.
        right = min(
            (found.right for found in offenders),
            key=lambda found: (found.lineno, found.col_offset),
        )
        name = right.id if isinstance(right, ast.Name) else right.func.id  # type: ignore[attr-defined]
        raise ReservedNameError(
            name, source=source, lineno=right.lineno, offset=right.col_offset + 1
        )

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
            raise BudgetExceededError(
                run.budget,
                source=run.source,
                lineno=getattr(node, "lineno", None),
                offset=(node.col_offset + 1 if hasattr(node, "col_offset") else None),
            )
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
        if isinstance(value, Mapping):
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

        try:
            return value[key]
        except (KeyError, IndexError):
            pass
        except TypeError:
            raise _error(
                run,
                node,
                f"cannot index a value of type `{describe_type(value)}` "
                f"with a `{describe_type(key)}`",
            ) from None
        raise _error(run, node, f"no entry for {key!r}")

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
        return {
            self._eval(key, run): self._eval(value, run)
            for key, value in zip(node.keys, node.values, strict=True)
            if key is not None
        }

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
        if isinstance(node.op, ast.Mult):
            size = _repetition_size(left, right)
            if size is not None and size > MAX_RESULT_SIZE:
                raise _error(
                    run,
                    node,
                    f"`*` would produce {size:,} items, over the limit of {MAX_RESULT_SIZE:,}",
                )

        op = _BINOPS[type(node.op)]
        symbol = _OP_SYMBOLS.get(type(node.op), "?")
        try:
            return op(left, right)
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
        raise failure

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
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self._eval(comparator, run)
            try:
                outcome = _CMPOPS[type(op)](left, right)
            except TypeError:
                raise _error(
                    run,
                    node,
                    f"cannot compare `{describe_type(left)}` with `{describe_type(right)}`",
                ) from None
            if not outcome:
                return False
            left = right
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
        run.steps -= function.cost
        if run.steps < 0:
            raise BudgetExceededError(
                run.budget,
                source=run.source,
                lineno=node.lineno,
                offset=node.col_offset + 1,
            )
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
        # **The lazy positions are simply not evaluated.** No side table, no synthetic names, no
        # rewritten tree: the function said which of its arguments are expressions, so those
        # arrive as a `LazyExpr` over the original subtree and everything else arrives as a value.
        arguments = [
            LazyExpr(self, argument, run) if index in function.lazy else self._eval(argument, run)
            for index, argument in enumerate(node.args)
        ]
        try:
            return function.call(*arguments)
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
        raise failure

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


def evaluate(source: str, context: Mapping[str, Any] | None = None) -> Any:
    """Evaluate `source` against `context` with no functions available.

    A convenience for the common case of a comparison over data. Build an `Evaluator` when you
    want a registry or a shared instance.

    Args:
        source: The expression.
        context: Names available to the expression.

    Returns:
        The value of the expression.

    Raises:
        SafeExprError: For every failure.
    """
    return Evaluator().evaluate(source, context)
