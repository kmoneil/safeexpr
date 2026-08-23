r"""The node allowlist: the primary security guarantee.

Everything else in this package is defence in depth. This module is the part that decides what
the language *is*, and it decides by allowlist: a node type absent from `_ALLOWED_NODES` is
rejected, and that is what makes new CPython grammar safe by default rather than safe once
somebody notices it.

That is not hypothetical. Python 3.14 added `TemplateStr` and `Interpolation` for t-strings
(PEP 750), and they are **expression** nodes, so `t"{x}"` parses in `mode="eval"` and would have
been evaluated by a denylist that had never heard of them. A closed allowlist rejected them on
the day the interpreter shipped. RestrictedPython's CVE-2025-22153 is the same story with
`try/except*`, and asteval's escape count against simpleeval's is largely the denylist/allowlist
difference.

Three things this module deliberately does *not* do:

- **It does not decide whether an expression is meaningful**, only whether it is expressible.
  Undefined names and wrong argument counts are the evaluator's business.
- **It does not recurse.** The source cap allows 2048 bytes, which is up to ~2,040 levels of
  operator nesting, and a recursive walk of a tree that deep raises `RecursionError` at the
  default limit of 1000. Measured, not feared. The walk below uses an explicit stack, which is
  the same rule the design imposes on the data functions.
- **It does not re-expose the tree.** Validation happens once, on the tree that is then
  evaluated, so there is no window between the check and the use. That is the F8 property
  (asteval GHSA-vp47-9734-prjw) and `tests/test_validate.py` asserts the identity directly.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from typing import Any

from ._errors import ValidationError

# Names the language reserves for lazy-argument bindings: `_` for the innermost item, `_1` as an
# explicit synonym, `_2` and beyond reaching outward one nesting level per index.
#
# The pattern is anchored and digits-only on purpose. Everything else beginning with an
# underscore is rejected, which is what stops a user naming an internal (`__lazy_0` was a real
# leak in the prototype that preceded this design) and what stops `__class__` arriving as a bare
# name rather than as an attribute.
_LAZY_NAME = re.compile(r"^_\d*$")

# **How deeply an expression may nest.** Measured: the evaluator walks the tree recursively and
# gives out between 497 and 498 nested operators at the default recursion limit of 1000, and the
# source cap allows 1023. Without this, a legal 2 KB expression reported
# "internal error ... this is a bug in safeexpr, please report it", which is the wrong answer to
# input that is merely too deep.
#
# 100 rather than 400. The available depth is not ours alone: it depends on how deep the host's
# own stack already is when it calls us, so a limit set near the measured ceiling would hold on a
# bare call and fail from inside a framework. For scale, the deepest canonical use case nests
# about 10, so 100 is roughly 10x anything realistic while leaving 4x headroom against a shallow
# caller's ceiling.
#
# Provisional: the empirical limits work owns the final value.
MAX_EXPRESSION_DEPTH = 100

# Operators the language has. `|` is here because it carries the pipe; ordinary bitwise algebra
# is not a goal, so `&`, `^`, `<<`, `>>` and `@` are absent and `~` is absent from the unary set.
# `bitor(a, b)` is the escape hatch for the one case where `|` means what it looks like.
_ALLOWED_BINOPS: frozenset[type[ast.operator]] = frozenset(
    {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.BitOr}
)
_ALLOWED_UNARYOPS: frozenset[type[ast.unaryop]] = frozenset({ast.Not, ast.USub, ast.UAdd})
# `is` and `is not` are absent. They ask about object identity, which for a language whose values
# are data is either meaningless or an accidental probe of CPython's interning.
_ALLOWED_CMPOPS: frozenset[type[ast.cmpop]] = frozenset(
    {ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn}
)

_ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.Compare,
        ast.Call,
        ast.Name,
        ast.Constant,
        ast.Attribute,
        ast.Subscript,
        ast.Slice,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.IfExp,
        ast.Load,
        *_ALLOWED_BINOPS,
        *_ALLOWED_UNARYOPS,
        *_ALLOWED_CMPOPS,
    }
)

# What to call a rejected construct when talking to the person who wrote it. A reader who typed a
# lambda wants to be told lambdas are unsupported, not that a node type failed a set membership
# test. Anything missing from here falls back to the node's class name, which is why the map can
# be incomplete without the allowlist being incomplete.
#
# The whole predicate is stored, verb included, rather than a bare noun with the verb bolted on
# at the call site. Otherwise one template has to agree with both "lambda expressions" and "the &
# operator", and it ends up saying "the & operator are not supported".
_CONSTRUCT_NAMES: dict[str, str] = {
    "Lambda": "lambda expressions are not supported",
    "ListComp": "list comprehensions are not supported",
    "SetComp": "set comprehensions are not supported",
    "DictComp": "dict comprehensions are not supported",
    "GeneratorExp": "generator expressions are not supported",
    "comprehension": "comprehensions are not supported",
    "NamedExpr": "the walrus operator is not supported",
    "Starred": "star unpacking is not supported",
    "JoinedStr": "f-strings are not supported",
    "FormattedValue": "f-strings are not supported",
    "TemplateStr": "t-strings are not supported",
    "Interpolation": "t-strings are not supported",
    "Await": "await is not supported",
    "Yield": "yield is not supported",
    "YieldFrom": "yield from is not supported",
    "Set": "set literals are not supported",
    "keyword": "keyword arguments are not supported",
    "arguments": "lambda expressions are not supported",
    "arg": "lambda expressions are not supported",
    "Assign": "assignment is not supported",
    "AugAssign": "augmented assignment is not supported",
    "AnnAssign": "annotated assignment is not supported",
    "Delete": "del is not supported",
    "Import": "import is not supported",
    "ImportFrom": "import is not supported",
    "Try": "try/except is not supported",
    "TryStar": "try/except* is not supported",
    "Store": "assignment is not supported",
    "Del": "del is not supported",
    "MatMult": "the @ operator is not supported",
    "BitAnd": "the & operator is not supported",
    "BitXor": "the ^ operator is not supported",
    "LShift": "the << operator is not supported",
    "RShift": "the >> operator is not supported",
    "Invert": "the ~ operator is not supported",
    "Is": "the `is` operator is not supported",
    "IsNot": "the `is not` operator is not supported",
}

# Advice worth giving, for the rejections a reasonable person will hit and be able to work
# around. Absent means "no suggestion", which is better than a generic one.
_SUGGESTIONS: dict[str, str] = {
    "ListComp": "use `map` or `where` instead",
    "SetComp": "use `map` or `where` instead",
    "DictComp": "use `map` or `where` instead",
    "GeneratorExp": "use `map` or `where` instead",
    "Lambda": "pass the expression directly, as in `where(_.price > 10)`",
    "JoinedStr": "use `join` or `+` to build strings",
    "TemplateStr": "use `join` or `+` to build strings",
    "BitAnd": "use `and` for logic",
    "BitXor": "use `and`/`or` for logic",
    "Is": "use `==`",
    "IsNot": "use `!=`",
    "Set": "use a list",
    "keyword": "pass arguments positionally",
}


def _describe(node: ast.AST) -> str:
    """State what is unsupported, the way the person who wrote it would say it."""
    name = type(node).__name__
    return _CONSTRUCT_NAMES.get(name, f"`{name}` nodes are not supported")


def _reject(
    node: ast.AST, source: str, reason: str, anchor: ast.AST | None = None
) -> ValidationError:
    """Build a positioned `ValidationError`.

    Returns rather than raises, so the caller raises it outside any exception handler. Nothing
    here is in a handler today, but the convention is uniform across the package so that the one
    place it matters cannot be the odd one out.

    **`anchor` exists because operators carry no position.** `ast.BitAnd`, `ast.Is`,
    `ast.arguments` and `ast.keyword` have no `lineno` at all, so rejecting `a & b` on the
    `BitAnd` node alone produces a correct message with nothing to point at. The anchor is the
    nearest ancestor that does have a position, which for `a & b` is the `BinOp` covering the
    whole expression.

    Args:
        node: The offending node, which decides what the message says.
        source: The user's original source.
        reason: The sentence naming what is wrong.
        anchor: Node to take the position from. Defaults to `node`.

    Returns:
        The error to raise.
    """
    name = type(node).__name__
    suggestion = _SUGGESTIONS.get(name)
    message = reason if suggestion is None else f"{reason} ({suggestion})"
    at = anchor if anchor is not None else node
    return ValidationError(
        message,
        source=source,
        lineno=getattr(at, "lineno", None),
        # `col_offset` is 0-based and `SyntaxError.offset` is 1-based. Converting here keeps
        # every error in this package on one convention, which is what `annotated()` relies on.
        offset=(at.col_offset + 1 if hasattr(at, "col_offset") else None),
    )


def _check_name(node: ast.Name, source: str) -> ValidationError | None:
    """Reject private names, allowing only the reserved lazy bindings."""
    if not node.id.startswith("_"):
        return None
    if _LAZY_NAME.match(node.id):
        return None
    return _reject(
        node,
        source,
        f"`{node.id}` is not available: names beginning with an underscore are reserved, "
        f"and only `_`, `_1`, `_2` and so on are valid",
    )


def _check_attribute(node: ast.Attribute, source: str) -> ValidationError | None:
    """Reject private and dunder attribute access.

    This is the static half of the F2 defence, the `__class__` to `__mro__` to `__subclasses__`
    climb that has broken essentially every Python sandbox. The dynamic half lives in the
    evaluator, for keys that are computed rather than written.
    """
    if not node.attr.startswith("_"):
        return None
    return _reject(
        node,
        source,
        f"attribute `.{node.attr}` is not available: attributes beginning with an underscore "
        f"are blocked",
    )


def _check_subscript(node: ast.Subscript, source: str) -> ValidationError | None:
    """Reject `x["__class__"]`, the spelling that walks past an attribute-only check.

    Verified in the prototype: a validator checking only `Attribute` rejects `x.__class__` and
    lets `x["__class__"]` straight through. Only constant keys can be caught here; a computed
    key such as `x["__cl" + "ass__"]` is the evaluator's problem, which is why both layers exist.
    """
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.startswith("_"):
        return _reject(
            node,
            source,
            f'subscript key "{key.value}" is not available: keys beginning with an underscore '
            f"are blocked",
        )
    return None


def _check_call(node: ast.Call, source: str) -> ValidationError | None:
    """Only a bare name may occupy call position.

    `f(x)` is a call to a registry function. `a.b(x)` and `f()(x)` are method and higher-order
    calls, and neither exists in this language. Rejecting them here is what makes "only registry
    functions are callable" (F3, the callback-smuggling class) a property of the grammar rather
    than a check the evaluator has to remember to perform.
    """
    if isinstance(node.func, ast.Name):
        return None
    what = (
        "method calls on values"
        if isinstance(node.func, ast.Attribute)
        else "calling the result of an expression"
    )
    return _reject(node, source, f"{what} are not supported; only named functions can be called")


def _check_dict(node: ast.Dict, source: str) -> ValidationError | None:
    """Reject `{**a}`, which the grammar encodes as a `None` key rather than as a node."""
    if any(key is None for key in node.keys):
        return _reject(node, source, "dict unpacking with `**` is not supported")
    return None


# Node type to the extra rule that applies to it, beyond being on the allowlist. A dispatch table
# rather than a chain of `isinstance`, so adding a rule is one line next to the others.
_CHECKS: dict[type[ast.AST], Callable[[Any, str], ValidationError | None]] = {
    ast.Name: _check_name,
    ast.Attribute: _check_attribute,
    ast.Subscript: _check_subscript,
    ast.Call: _check_call,
    ast.Dict: _check_dict,
}


def _problem_with(node: ast.AST, source: str, anchor: ast.AST | None) -> ValidationError | None:
    """Return what is wrong with one node, or `None` if nothing is.

    The allowlist check comes first: a node type that is not permitted is not worth inspecting
    further, and reporting "not supported" beats reporting something about its contents.
    """
    if type(node) not in _ALLOWED_NODES:
        return _reject(node, source, _describe(node), anchor)
    check = _CHECKS.get(type(node))
    return None if check is None else check(node, source)


def validate(tree: ast.Expression, source: str = "") -> ast.Expression:
    """Check every node against the allowlist, returning the same tree.

    The tree is returned rather than copied, deliberately: the object validated is the object
    evaluated, so there is no window in which one could be swapped for the other.

    Args:
        tree: A parsed expression, from `_parse.parse`.
        source: The original source, used to position error messages.

    Returns:
        `tree`, unchanged and object-identical to the argument.

    Raises:
        ValidationError: On the first construct outside the language, in source order.
    """
    # (error, node-had-its-own-position). The flag is a tiebreak: when a container and something
    # inside it are both rejected at the same coordinates, the container is the better message.
    # `lambda x: x` rejects both `Lambda` and its `arguments`, and the user typed a lambda.
    problems: list[tuple[ValidationError, bool]] = []

    # An explicit stack rather than recursion. A 2048-byte source can nest ~2,040 deep and a
    # recursive walk raises `RecursionError` well before that at the default limit.
    #
    # Each entry carries the nearest positioned ancestor, so an unpositioned node can still be
    # pointed at somewhere real.
    deepest = 0
    stack: list[tuple[ast.AST, ast.AST | None, int]] = [(tree, None, 0)]
    while stack:
        node, ancestor, depth = stack.pop()
        deepest = max(deepest, depth)
        own = hasattr(node, "lineno")
        anchor = node if own else ancestor

        problem = _problem_with(node, source, anchor)
        if problem is not None:
            problems.append((problem, own))

        stack.extend((child, anchor, depth + 1) for child in ast.iter_child_nodes(node))

    if deepest > MAX_EXPRESSION_DEPTH:
        # Reported before any other problem: an expression this deep is unusable whatever else is
        # wrong with it, and the alternative was a RecursionError from the evaluator dressed up as
        # an internal bug report.
        raise ValidationError(
            f"expression nests {deepest} levels deep, over the limit of {MAX_EXPRESSION_DEPTH}",
            source=source,
        )

    if problems:
        # Report the earliest problem in source order. A stack-based walk finds them in an order
        # that is deterministic but not the reading order, and being told about the second
        # mistake in a line first is a small, avoidable confusion.
        problems.sort(key=lambda p: (p[0].lineno or 0, p[0].offset or 0, not p[1]))
        raise problems[0][0]

    return tree
