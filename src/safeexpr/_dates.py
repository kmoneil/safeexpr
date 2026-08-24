r"""The dates tier: parse an ISO timestamp, format one back out.

Two functions, and the second one is where the card puts its emphasis. **`format_date` must not
route a user's string through `str.format`**, because that is F1 exactly: a format string is
interpreted at runtime, `"{0.__class__}".format(x)` performs an attribute lookup no AST check can
read, and it is the single most-repeated escape in the competitive scan. So formatting goes
through `strftime`, whose directives name calendar fields and nothing else. There is no path from
a `strftime` directive to an attribute, a key or a method.

That leaves two problems `strftime` has of its own, and both are closed here rather than left to
the platform:

- **Directives vary by C library.** `%-d`, `%e` and `%s` exist on some platforms and not others,
  and an unknown directive may be an error on one and passed through literally on another. A
  package that matrices across four interpreters cannot have its output depend on which libc
  built them, so the accepted directives are an allowlist and anything else is refused with a
  message naming it.
- **Format strings multiply.** `format_date(d, "%Y" * 100000)` is a short expression that asks
  for a large string, and the step budget counts nodes rather than bytes, so it cannot see it.
  Both the format and the result are bounded.

`parse_iso` returns a `datetime`, which compares and formats. Reading a field off one, `.year`
and the like, needs the host to register the type through `attribute_types`, because attribute
access reaching arbitrary objects is the thing this package most deliberately does not do.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ._guards import MAX_RESULT_SIZE, text, within_size
from ._registry import Function, FunctionError, describe_type

# **An allowlist, so output does not depend on which C library built the interpreter.**
#
# Everything here is documented by CPython as available on all platforms. Deliberately absent:
# `%c`, `%x` and `%X`, whose whole output is locale-defined and whose length is therefore
# unpredictable; `%s`, which is a libc extension rather than a Python guarantee; and the `%-d`
# and `%e` width modifiers, which are glibc-only.
#
# `%a`, `%A`, `%b`, `%B` and `%p` are locale-dependent in their *content* rather than their
# presence, and are kept because naming a month is an ordinary thing a rule does. A host running
# under a non-English locale gets that locale's names, which is its own choice.
_DIRECTIVES = frozenset("YymdHIMSfjpaAbBzZUWwGuV%")

# A format longer than this is not a date format. Kept far below the result cap because the
# result is what actually gets allocated and each directive expands.
_MAX_FORMAT_LENGTH = 1024


def _parse_iso(value: Any) -> datetime:
    """Read an ISO 8601 timestamp.

    A date on its own is accepted and lands at midnight, which is what a rule comparing
    `_.due > parse_iso("2026-01-01")` means.
    """
    subject = text(value, "an ISO 8601 timestamp as text")
    try:
        parsed = datetime.fromisoformat(subject)
    except ValueError:
        # The text is not repeated back. It is the host's data, and an error message is read by
        # whoever wrote the expression rather than by whoever owns the data.
        failure = FunctionError("needs text in ISO 8601 form, such as `2026-08-24T13:45:00`")
    else:
        return parsed
    raise failure


def _format_date(value: Any, pattern: Any) -> str:
    """Render a date or timestamp using `strftime` directives.

    Not `str.format`, and not `%`-formatting. See this module's docstring: both interpret their
    argument at runtime in ways that reach attributes, and neither is reachable from here.
    """
    if not isinstance(value, date):
        raise FunctionError(
            f"needs a date or timestamp, got `{describe_type(value)}`; `parse_iso` makes one"
        )
    layout = text(pattern, "a format as text")
    if len(layout) > _MAX_FORMAT_LENGTH:
        raise FunctionError(
            f"format is {len(layout):,} characters, over the limit of {_MAX_FORMAT_LENGTH:,}"
        )
    _check_directives(layout)
    formatted = value.strftime(layout)
    within_size(len(formatted), "text")
    return formatted


def _check_directives(layout: str) -> None:
    """Refuse any directive outside the portable set.

    Args:
        layout: The format string.

    Raises:
        FunctionError: On an unknown directive, or a `%` with nothing after it.
    """
    index = 0
    while True:
        found = layout.find("%", index)
        if found == -1:
            return
        if found + 1 >= len(layout):
            raise FunctionError("format ends with a `%` that names no field")
        directive = layout[found + 1]
        if directive not in _DIRECTIVES:
            raise FunctionError(
                f"`%{directive}` is not a date field this supports; the supported ones are "
                f"{' '.join('%' + character for character in sorted(_DIRECTIVES))}"
            )
        index = found + 2


DATES: dict[str, Function] = {
    "parse_iso": Function("parse_iso", _parse_iso, arity=(1, 1)),
    "format_date": Function("format_date", _format_date, arity=(2, 2)),
}

__all__ = ["DATES", "MAX_RESULT_SIZE"]
