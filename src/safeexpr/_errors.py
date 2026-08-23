r"""The error hierarchy, and the mechanism that keeps it from leaking.

Every error this package raises descends from `SafeExprError`, so a caller can wrap an
evaluation in one `except` and be sure nothing else escapes. That is a stronger promise than it
looks: the parse boundary alone can produce `SyntaxError`, `ValueError`, `UnicodeEncodeError`,
`MemoryError` and `RecursionError` from CPython, and which one you get varies by version.

**Errors are constructed, never wrapped, and the distinction is load-bearing.**

The obvious way to scrub an exception is `raise OurError(...) from None`. It does not work.
`from None` clears `__cause__` and suppresses the "During handling of the above exception"
display, but `__context__` keeps pointing at the original. On Python 3.10 and later an
`AttributeError` carries `.obj`, a live reference to the object whose attribute access failed,
so:

    raise OurError("fully scrubbed message") from None
    #  ->  err.__context__.obj  is the caller's object, reachable through a clean-looking error

That is F9, RestrictedPython CVE-2024-47532, surviving a re-wrap that looks correct. Assigning
`__context__ = None` inside the handler does not help either: CPython re-sets it as the raise
executes.

The only thing that works is **raising after the handler has exited**, because the thread's
"currently handled exception" is restored at that point. `contained` below is that pattern in
reusable form, and `tests/test_error_boundary.py` proves both halves: that the naive version
leaks, and that this one does not.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


class SafeExprError(Exception):
    """Base class for everything this package raises.

    Catching this catches all of it. Nothing here raises a bare built-in exception to the
    caller, and nothing raises a `BaseException` subclass outside this tree.

    Carries the user's own source and, where one is known, the position within it. `lineno` and
    `offset` are 1-based to match `SyntaxError`, and are `None` when the failure was not
    positional. **Nothing else is carried**: no reference to a causing exception, no `args`
    passed through from one, no `__notes__` copied from one.
    """

    def __init__(
        self,
        message: str,
        *,
        source: str = "",
        lineno: int | None = None,
        offset: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.source = source
        self.lineno = lineno
        self.offset = offset

    def annotated(self) -> str:
        """Render the message above the offending source with a caret under the position.

        R8 asks that errors report the user's original source with real offsets. This is that,
        in the form a human reads. Returns just the message when there is no position to point
        at.

        Returns:
            A multi-line string safe to print.
        """
        if not self.source or self.offset is None:
            return self.message
        line = self.source.splitlines()[(self.lineno or 1) - 1] if self.source else ""
        caret = " " * max(0, self.offset - 1) + "^"
        return f"{self.message}\n  {line}\n  {caret}"


class SourceTooLongError(SafeExprError):
    """The expression source exceeded the byte cap before it was parsed.

    Raised in place of letting CPython's parser meet the input. The cap exists because
    `ast.parse` does not fail gracefully on adversarial input: it gives out somewhere between
    2,989 and 5,975 levels of operator nesting depending on the interpreter.
    """

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(
            f"expression is {size} bytes, over the {limit} byte limit; "
            f"the limit is applied before parsing because CPython's parser does not fail "
            f"gracefully on very deeply nested input"
        )
        self.size = size
        self.limit = limit


class ParseError(SafeExprError):
    """The source could not be parsed as a single Python expression."""


class ValidationError(SafeExprError):
    """The source parsed, but uses a construct outside the supported language.

    This is the node allowlist talking. The message names the construct rather than describing
    the rule, because "lambda expressions are not supported" is actionable and "node type not in
    allowlist" is not.
    """


class EvaluationError(SafeExprError):
    """The expression was valid but could not be evaluated.

    An undefined name, a missing field, a type mismatch, a division by zero, or a result too
    large to compute. These are the user's mistakes rather than the language's limits, and the
    message says which one.
    """


class InternalError(SafeExprError):
    """Something raised that this package did not anticipate.

    Reaching this is a bug here, not a bug in the expression, and the message says so. It exists
    so that an unanticipated failure still arrives as a `SafeExprError` rather than as whatever
    CPython happened to raise, and so that it is visibly distinct from a user's mistake.
    """


def contained(fn: _F) -> _F:
    """Wrap a public entry point so nothing escapes except a `SafeExprError`.

    **`BaseException`, not `Exception`, and that is the point of this decorator.** At the eval
    boundary the threat is real: `SystemExit`, `KeyboardInterrupt` and `GeneratorExit` are not
    `Exception` subclasses, so a host's `except Exception` does not stop them, which is exactly
    the asteval CVE-2026-55244 class. They are reachable here without any `raise` in the
    language, because comparing against a context value calls that value's `__eq__`, and a
    caller may pass an object whose dunder methods misbehave.

    The trade-off is stated rather than hidden: a `KeyboardInterrupt` arriving *during* an
    evaluation is converted, so Ctrl-C will not interrupt one. The window is bounded by the step
    budget rather than by the size of the input, and the alternative is letting a sandboxed
    expression kill the host process.

    Note the structure. The error is built inside the handler and raised **after** it, which is
    what keeps `__context__` clear. A `raise ... from None` in the handler would leak; see this
    module's docstring.

    Args:
        fn: The entry point to wrap.

    Returns:
        The wrapped callable.
    """

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        error: SafeExprError | None = None
        try:
            return fn(*args, **kwargs)
        except SafeExprError:
            # Already ours, already scrubbed, already positioned. A bare re-raise keeps the
            # original traceback, and re-wrapping would only discard the position.
            raise
        except BaseException as exc:
            # Only the type name survives. The message may quote input, `args` are not ours to
            # hand on, and `__notes__` is a channel a caller's object could have written to.
            error = InternalError(
                f"internal error while evaluating ({type(exc).__name__}); "
                f"this is a bug in safeexpr, please report it"
            )
        raise error

    return wrapper  # type: ignore[return-value]
