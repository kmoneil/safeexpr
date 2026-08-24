r"""What every tier checks before it does anything, and the ceilings it works under.

Two jobs, both shared because five tiers doing them five ways is how one of them ends up doing
them wrong.

**Argument validation.** A function given the wrong kind of value must say so in a sentence
naming the type it got, and must never name the *value*: a `repr` in an error message is the
caller's data handed to whoever reads the error, which is the leak R8 exists to prevent. These
helpers are the one spelling of that.

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
