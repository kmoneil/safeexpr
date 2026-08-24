r"""What every tier checks before it does anything, and the ceilings it works under.

Two jobs, both shared because five tiers doing them five ways is how one of them ends up doing
them wrong.

**Argument validation.** A function given the wrong kind of value must say so in a sentence
naming the type it got, and must never name the *value*: a `repr` in an error message is the
caller's data handed to whoever reads the error, which is the leak R8 exists to prevent. These
helpers are the one spelling of that.

**A ceiling on how deeply host data may nest**, because comparing and hashing it recurses in C.
Comparison raises `RecursionError` and can be handled; hashing does not raise at all, it crashes
the interpreter, so the depth has to be known before the value reaches `hash`.

**A ceiling on how large a value one step may produce.** R7 lists a string length cap among the
deterministic bounds and it had never been built, which left a hole the step budget cannot see:
the budget counts *nodes evaluated*, and `"a" * 5000000` is three nodes. Measured against this
evaluator before this module existed, that expression allocated five megabytes, and the constant
is free to be larger. Repetition, `replace` and `join` are the three ways a rules engine turns a
short expression into a large value, so all three are bounded here rather than each somewhere
else.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._registry import FunctionError, describe_type

# **The largest value one operation may produce**, counted in characters for text and in items
# for a list or tuple.
#
# A megabyte is far past anything a rule needs: the largest realistic case is joining a few
# thousand short strings, which is tens of kilobytes. It is small enough that the failure is a
# message rather than a machine swapping, and large enough that no honest expression meets it.
#
# Provisional, in the same sense as every other cap here: the empirical limits work owns the
# final value.
MAX_RESULT_SIZE = 1_048_576

# **How deeply host data may nest before it is refused, and why there is a number here at all.**
#
# Comparing or hashing nested data recurses in C on our behalf. Comparison is guarded by CPython
# and raises `RecursionError`, which can be caught and reported. **Hashing is not.** `tuplehash`
# does not use `Py_EnterRecursiveCall`, so a deeply nested tuple does not raise: it exhausts the C
# stack and takes the interpreter with it. Measured on this interpreter, hashing a tuple nested
# 200,000 deep is an exit-139 segmentation fault, with or without this package in the picture, and
# a `try` cannot catch a crash. The depth has to be known *before* the value reaches `hash`, which
# is the whole reason this cap exists rather than another exception handler.
#
# 1,000 rather than something near the measured failure. Comparison gives out between 5,000 and
# 10,000 on a bare call, and the available stack is not ours alone: it depends on how deep the
# host's own stack already is when it calls us, so a cap set near the ceiling would hold in a test
# and fail from inside a framework. For scale, JSON from an ordinary API nests under twenty.
#
# Provisional, like every other cap here: the empirical limits work owns the final value.
MAX_DATA_NESTING = 1_000

# A ceiling on the *walk*, not on the data. Shared structure makes a graph rather than a tree, and
# a diamond of shared lists doubles the node count per level: thirty levels of `[x, x]` is a
# billion visits from a structure holding thirty objects. The depth cap alone does not see that,
# because no path is deep. Cycles are caught by the depth cap, since a cycle makes every path
# unbounded.
_MAX_WALK = 100_000

# What `hash` actually recurses into. A list or a dict inside a tuple makes the whole tuple
# unhashable, so `hash` stops there with a `TypeError` and never goes deeper; only these two
# containers can carry the recursion, which is what makes the walk below complete rather than
# merely careful.
HASHABLE_CONTAINERS = (tuple, frozenset)


def sequence(value: Any) -> Sequence[Any]:
    """Return `value` as a list or tuple, or object to it.

    Not a string, whose elements are characters, and not a mapping, whose elements are keys. Both
    iterate perfectly well in Python and both are almost always a mistake in a rule.

    Args:
        value: The value to check.

    Returns:
        The same object, once it is known to be a list or a tuple.

    Raises:
        FunctionError: If it is anything else.
    """
    if isinstance(value, (list, tuple)):
        return value
    raise FunctionError(f"needs a list, got `{describe_type(value)}`")


def text(value: Any, what: str = "text") -> str:
    """Return `value` as a string, or object to it.

    **Deliberately does not coerce.** `lower(user.age)` is a mistake in the rule, and answering
    it with `"30"` hides the mistake until the day the field is missing instead of numeric.

    Args:
        value: The value to check.
        what: What the argument is, for the message: "text", "a separator", and so on.

    Returns:
        The same string.

    Raises:
        FunctionError: If it is not a string.
    """
    if isinstance(value, str):
        return value
    raise FunctionError(f"needs {what}, got `{describe_type(value)}`")


def check_depth(value: Any) -> None:
    """Refuse a value too deeply nested to hash without crashing the interpreter.

    **Costs one `isinstance` for anything that is not a tuple or a frozenset**, which is every
    grouping key, dictionary key and membership test a rule realistically performs. The walk only
    runs for the two containers `hash` can recurse through, so the common case pays almost
    nothing and the uncommon case pays in proportion to the value it was handed.

    Walked with an explicit stack. A recursive depth check would be the thing it is checking for.

    Args:
        value: The value about to be hashed.

    Raises:
        FunctionError: If it nests past the cap, refers to itself, or is too large to check.
    """
    if not isinstance(value, HASHABLE_CONTAINERS):
        return
    # Callers on a hot path test `HASHABLE_CONTAINERS` themselves before calling, so the line
    # above is a backstop for everyone else rather than the fast path. It stays because a guard
    # that only works when the caller remembers to guard it is not a guard.
    visited = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        visited += 1
        if depth > MAX_DATA_NESTING:
            raise FunctionError(
                f"nests more than {MAX_DATA_NESTING:,} levels deep, or refers to itself; "
                f"a value that deep cannot be used as a key"
            )
        if visited > _MAX_WALK:
            raise FunctionError(
                f"is too large to check for safe nesting; more than {_MAX_WALK:,} values were "
                f"reached, which happens when the same value appears inside itself many times"
            )
        if isinstance(current, HASHABLE_CONTAINERS):
            stack.extend((item, depth + 1) for item in current)


def within_size(size: int, what: str) -> None:
    """Refuse a result that would be larger than the cap.

    Checked on the *predicted* size wherever the size can be predicted, so the allocation never
    happens rather than happening and then being complained about. That distinction is the whole
    value: an error after allocating a gigabyte has already cost the gigabyte.

    Args:
        size: How many characters or items the result would hold.
        what: What is being produced, for the message.

    Raises:
        FunctionError: If it is over the cap.
    """
    if size > MAX_RESULT_SIZE:
        raise FunctionError(
            f"would produce {what} of {size:,}, over the limit of {MAX_RESULT_SIZE:,}"
        )
