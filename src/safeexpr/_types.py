r"""The types tier: conversion, and the null-safe chain.

Four converters, a test and a fallback. The interesting one is `str`, and it is interesting for a
security reason rather than a semantic one.

**`str` accepts primitives only, and that is the F1 lesson applied to a conversion function.**
`str(x)` on an arbitrary object calls that object's `__str__`, which is host code returning host
text, and a rules engine that hands the expression author `str(request.session)` has leaked
whatever that object's author chose to print. The corpus already carries this exact shape as
`"%s" % obj`, where a context object's `repr` came back as a value; the fact that the leak arrives
through a friendly-looking conversion rather than through `%` does not make it a different leak.
So the accepted types are listed rather than the refused ones, for the same reason the node
allowlist is an allowlist.

`default(x, fallback)` is the null-safe chain the design asks for. Both arguments are evaluated:
`or` already exists for short-circuiting, and a `default` whose fallback sometimes runs and
sometimes does not is harder to reason about than one that always does.
"""

from __future__ import annotations

import math
from typing import Any

from ._registry import Function, FunctionError, describe_type

# What `str` will convert. Listed rather than refused, because a denylist here would have to
# anticipate every type a host might put in a context, and the whole design principle is that it
# cannot.
#
# **Nothing is absent from this tuple twice over.** `None` is refused with its own message, and
# writing it here would have meant `type(None)`, which the tiers' reflection gate bans outright.
# The gate found that spelling in the first draft of this line, which is the argument for the ban
# having no exceptions in it: `type(None)` is entirely harmless and the rule is still better
# without a carve-out for it.
_STRINGABLE = (str, int, float, bool)


def _int(value: Any) -> int:
    """Convert to a whole number.

    Text is parsed, a float is truncated toward zero, and `True` is 1. Anything else is refused
    rather than guessed at.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise FunctionError("cannot convert a value that is not a finite number")
        return int(value)
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            # The message says what was wrong, not what the text was. CPython also caps integer
            # parsing at 4,300 digits, which arrives here as the same ValueError and is a
            # perfectly good reason to refuse.
            failure = FunctionError("needs text that reads as a whole number")
        else:
            return parsed
        raise failure
    raise FunctionError(f"cannot convert `{describe_type(value)}` to a whole number")


def _float(value: Any) -> float:
    """Convert to a decimal number.

    **Infinity and not-a-number are refused**, including the `inf` that a large literal overflows
    to. They compare in ways nobody means: a rule reading `float(_.x) > 100` against `"nan"` is
    silently false for every row, and a sort containing one is silently wrong. An error is a worse
    outcome for exactly one caller and a better one for everybody debugging.
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str)):
        try:
            parsed = float(value.strip() if isinstance(value, str) else value)
        except (ValueError, OverflowError):
            failure = FunctionError("needs something that reads as a number")
        else:
            if not math.isfinite(parsed):
                failure = FunctionError(
                    "would produce infinity or not-a-number, which compare in ways no rule means"
                )
            else:
                return parsed
        raise failure
    raise FunctionError(f"cannot convert `{describe_type(value)}` to a number")


def _str(value: Any) -> str:
    """Convert a primitive to text.

    **Primitives only.** See this module's docstring: `str` on an arbitrary object runs that
    object's `__str__` and hands the result to whoever wrote the expression. `None` is refused
    separately, because the answer Python gives is the word "None" and that is rarely what a rule
    meant.
    """
    if value is None:
        # Python answers `"None"`, and a rule that quietly renders a missing field as the word
        # "None" is worse than one that stops: the text looks deliberate everywhere it lands.
        raise FunctionError("cannot convert nothing to text; use `default` to supply a value")
    if not isinstance(value, _STRINGABLE):
        raise FunctionError(
            f"can convert text, numbers and true/false, but not `{describe_type(value)}`; "
            f"converting an arbitrary value would run that value's own code to produce the text"
        )
    return str(value)


def _bool(value: Any) -> bool:
    """Whether a value is truthy.

    Unrestricted, unlike `str`. Truthiness runs a host object's `__bool__` or `__len__` too, but
    what comes back is one bit rather than text the object chose, and the language already asks
    the same question of every value in an `and`, an `or` and an `if`.
    """
    return bool(value)


def _is_none(value: Any) -> bool:
    """Whether a value is nothing.

    The function form of `is None`. The `is` operator is deliberately absent from the language,
    because for a language whose values are data it is either meaningless or an accidental probe
    of CPython's interning, but asking about absence is an ordinary thing a rule needs.
    """
    return value is None


def _default(value: Any, fallback: Any) -> Any:
    """`value` unless it is nothing, in which case `fallback`.

    **Only `None` triggers the fallback**, not every falsy value. `default(_.count, 10)` where
    the count is genuinely 0 must give 0; `or` is the operator for "falsy or missing" and it is
    already in the language.
    """
    return fallback if value is None else value


TYPES: dict[str, Function] = {
    "int": Function("int", _int, arity=(1, 1)),
    "float": Function("float", _float, arity=(1, 1)),
    "str": Function("str", _str, arity=(1, 1)),
    "bool": Function("bool", _bool, arity=(1, 1)),
    "is_none": Function("is_none", _is_none, arity=(1, 1)),
    "default": Function("default", _default, arity=(2, 2)),
}
