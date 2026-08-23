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

from ._errors import EvaluationError, SafeExprError, contained
from ._parse import parse
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
    """

    __slots__ = ("context", "source")

    def __init__(self, context: Mapping[str, Any], source: str) -> None:
        self.context = context
        self.source = source


def _error(run: _Run, node: ast.AST, message: str) -> EvaluationError:
    """Build a positioned `EvaluationError`.

    Returns rather than raises, so callers raise it outside any exception handler. That is the
    package-wide convention and the reason is in `_errors`: raising inside a handler leaves
    `__context__` pointing at the caught exception, which on 3.10+ is a live handle on the
    caller's data.
    """
    return EvaluationError(
        message,
        source=run.source,
        lineno=getattr(node, "lineno", None),
        offset=(node.col_offset + 1 if hasattr(node, "col_offset") else None),
    )


def _suggest(name: str, candidates: Sequence[str]) -> str:
    """Return a ', did you mean X?' clause, or an empty string.

    Only offers names the expression author could already see, so this reveals nothing they do
    not have.
    """
    close = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.7)
    return f", did you mean `{close[0]}`?" if close else ""


def _describe_type(value: object) -> str:
    """Name a value's type for an error message.

    The type name only. A `repr` of the value would put the caller's data into a string the
    expression author reads, which is the leak R8 exists to prevent.
    """
    return type(value).__name__


class Evaluator:
    """Evaluates expressions against a context.

    Immutable after construction: the registry and the attribute allowlist are fixed, and no
    evaluation writes to the instance. One `Evaluator` may be shared freely between threads.

    Args:
        registry: Name to callable, the only things an expression can call. Empty by default,
            because the function registry is built separately; an evaluator with no registry is
            a perfectly usable expression language for comparisons and field access.
        attribute_types: Opt-in `getattr` access, as type to permitted attribute names. **Left
            empty unless a host deliberately opts in**, because attribute traversal on arbitrary
            objects is where essentially every Python sandbox escape has started.
    """

    __slots__ = ("_attribute_types", "_registry")

    def __init__(
        self,
        registry: Mapping[str, Callable[..., Any]] | None = None,
        attribute_types: Mapping[type, frozenset[str]] | None = None,
    ) -> None:
        self._registry: dict[str, Callable[..., Any]] = dict(registry or {})
        self._attribute_types: dict[type, frozenset[str]] = dict(attribute_types or {})

    @property
    def function_names(self) -> frozenset[str]:
        """The names an expression may call."""
        return frozenset(self._registry)

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
        tree = validate(parse(source), source)
        return self._run(tree, _Run(context or {}, source))

    def _run(self, tree: ast.Expression, run: _Run) -> Any:
        return self._eval(tree.body, run)

    # -- dispatch ----------------------------------------------------------
    def _eval(self, node: ast.AST, run: _Run) -> Any:
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
            f"cannot read `.{node.attr}` on a value of type `{_describe_type(value)}`; "
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
                f"cannot index a value of type `{_describe_type(value)}` "
                f"with a `{_describe_type(key)}`",
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
                f"cannot apply `{symbol}` to `{_describe_type(left)}` "
                f"and `{_describe_type(right)}`",
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
                f"cannot apply `**` to `{_describe_type(base)}` and `{_describe_type(exponent)}`",
            )
        raise failure

    def _unaryop(self, node: ast.UnaryOp, run: _Run) -> Any:
        value = self._eval(node.operand, run)
        try:
            return _UNARYOPS[type(node.op)](value)
        except TypeError:
            pass
        raise _error(run, node, f"cannot apply this operator to `{_describe_type(value)}`")

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
                    f"cannot compare `{_describe_type(left)}` with `{_describe_type(right)}`",
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
        arguments = [self._eval(argument, run) for argument in node.args]
        try:
            return function(*arguments)
        except SafeExprError:
            raise
        except TypeError:
            failure = _error(run, node, f"`{name}` cannot accept {len(arguments)} argument(s)")
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
