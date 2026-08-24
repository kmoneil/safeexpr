r"""The parse boundary: the only place user source reaches CPython's parser.

`ast.parse` is not a safe front door on its own, and the reasons are measured rather than
assumed. Probed on every supported version:

===========================  ==================================================================
input                        what `ast.parse(..., mode="eval")` does
===========================  ==================================================================
``"-" * n + "1"``            fails past a per-version cliff, measured by bisection at the
                             default recursion limit:

                             ====== ========= ==================================
                             3.11   **2,989** `RecursionError`
                             3.12   **5,974** `MemoryError`
                             3.13   **5,974** `MemoryError`
                             3.14   **5,974** `MemoryError`
                             ====== ========= ==================================

                             **3.11 is the outlier and it is the binding constraint**: its cliff
                             is roughly half the others', it fails with a different exception,
                             and unlike the others it *moves with* `sys.setrecursionlimit`
                             (raising the limit to 3000 lifts it to 5,975). On every other
                             version the bound is the PEG parser's own and does not move at all
                             (checked at 1000, 5000 and 20000).
``"1\x00+2"``                **`ValueError` on some versions, `SyntaxError` on others.** The
                             same input, two exception types, split across the matrix.
``"'\ud800'"``               `UnicodeEncodeError`, on every version.
``b"1+1"``                   **succeeds.** Bytes parse, so a caller passing the wrong type gets
                             a working evaluation rather than an error.
===========================  ==================================================================

So the obvious guard, ``try: ast.parse(...) except SyntaxError:``, leaks `MemoryError`,
`RecursionError`, `ValueError` and `UnicodeEncodeError` straight to the host. This module catches
`Exception` and raises `ParseError` instead. `Exception` rather than `BaseException` is
deliberate: every failure mode above is already an `Exception` subclass, so widening would catch
nothing extra and would swallow a real Ctrl-C. `BaseException` containment belongs at the eval
boundary, where a sandboxed expression could raise `SystemExit`.

Two things follow from the `MemoryError` row and are worth stating, because they are easy to get
backwards:

- **The cap runs before the parser, not after.** Once `ast.parse` has been called on hostile
  input the damage is already done, so the length check cannot be a validation step.
- **Byte length is a poor proxy, and it is the only one available.** A 20 KB ``1+1+1+...``
  parses fine while a 6 KB ``-----...1`` does not, because what the parser minds is nesting
  depth rather than size. `MAX_SOURCE_BYTES` is therefore set low enough that the worst case
  (one nesting level per byte) stays under the measured threshold with room to spare.
"""

from __future__ import annotations

import ast

from ._errors import ParseError, SourceTooLongError

# **Set by Python 3.11, not by the majority.** The densest possible input is one operator per
# byte (`-` or `~`), so N bytes cannot express more than N levels of nesting. The cliff is ~5,975
# on 3.12 through 3.14, but only **2,989 on 3.11**, so a limit chosen from the majority would be
# unsafe on exactly one supported interpreter.
#
# 2048 leaves ~1.45x headroom on 3.11 and ~2.9x elsewhere. For scale, the longest expression in
# the design's canonical use cases is under 100 bytes, so this is roughly 20x any realistic input
# and the headroom costs nothing.
#
# Raising this is not a free knob, and the 3.11 row is why: its bound follows
# `sys.setrecursionlimit`, so a host that *lowers* the recursion limit lowers this cliff too.
# The guard inside `parse` is what makes that survivable rather than exploitable, but the cap is
# what keeps hostile input away from the parser in the first place.
MAX_SOURCE_BYTES = 2048


def _length_of(source: str) -> int:
    """Return the UTF-8 byte length of `source`.

    Encoding is not infallible: a lone surrogate raises `UnicodeEncodeError`, so the length check
    itself can fail on input the caller believes is a string. That is reported as a parse failure
    rather than allowed to propagate, because from the caller's side it is one.

    Args:
        source: The expression text.

    Returns:
        Length in UTF-8 bytes.

    Raises:
        ParseError: If `source` cannot be encoded.
    """
    failed = False
    try:
        return len(source.encode("utf-8"))
    except UnicodeEncodeError:
        failed = True
    # Raised out here rather than in the handler. See `parse` for why that matters.
    if failed:
        raise ParseError(
            "expression contains characters that are not valid text "
            "(an unpaired surrogate), so it cannot be parsed",
            source=source,
        )
    raise AssertionError("unreachable")  # pragma: no cover


def check_source(source: str) -> None:
    """Refuse a source that must not reach anything else, before anything else sees it.

    Separate from `parse` because it has to run **before the compile cache is touched**, not only
    before the parser. A cache keyed on the source text would otherwise be the first thing to
    handle hostile input: an unhashable argument would raise `TypeError` out of a dict lookup
    rather than the package's own error, and a multi-megabyte string would be hashed in full
    before anything had decided it was too long. Both are the caller's mistake and both should be
    reported as such, at the boundary, in this package's own vocabulary.

    Args:
        source: The expression text. Must be `str`; `bytes` is rejected explicitly, because
            `ast.parse` accepts it and would otherwise let a type confusion through silently.

    Raises:
        ParseError: If `source` is not `str`, or cannot be encoded.
        SourceTooLongError: If the source exceeds `MAX_SOURCE_BYTES`.
    """
    if not isinstance(source, str):
        # No `from None` here: nothing is being handled at this point, so there is no context
        # to suppress. Writing one would imply `from None` is what protects the other paths,
        # and the block in `parse` explains at length why it is not.
        raise ParseError(
            f"expression must be str, not {type(source).__name__}",
            source="",
        )

    size = _length_of(source)
    if size > MAX_SOURCE_BYTES:
        raise SourceTooLongError(size, MAX_SOURCE_BYTES)


def parse(source: str) -> ast.Expression:
    """Parse `source` into a single Python expression, or raise `ParseError`.

    This is the only place in the package that calls `ast.parse`.

    Args:
        source: The expression text. Must be `str`; `bytes` is rejected explicitly, because
            `ast.parse` accepts it and would otherwise let a type confusion through silently.

    Returns:
        The parsed `ast.Expression`. Nothing has been validated yet: this guarantees the source
        is *a* Python expression, not that it is one this package will evaluate.

    Raises:
        SourceTooLongError: If the source exceeds `MAX_SOURCE_BYTES`.
        ParseError: For every other failure, including the resource exhaustion cases that are
            not `SyntaxError`.
    """
    # Repeated on the cached path rather than skipped there, so that "the cap runs before the
    # parser" stays a property of this function and not of whoever remembered to call the other
    # one. It costs one `str.encode` on an input already capped at 2 KB.
    check_source(source)

    if not source.strip():
        # `ast.parse("")` reports `invalid syntax (<unknown>, line 0)`, and line 0 does not
        # exist. Worth its own message rather than passing that on.
        raise ParseError("expression is empty", source=source)

    # **The error is built in the handler and raised after it, and that is not a style choice.**
    #
    # `raise ... from None` sets `__cause__` to None and suppresses the "During handling of the
    # above exception" display, but it does **not** clear `__context__`. The original exception
    # stays attached and reachable. Measured:
    #
    #     raise OurError("scrubbed") from None   ->   err.__context__.obj is the live object
    #
    # which is F9 (RestrictedPython CVE-2024-47532) straight through an error that looks clean.
    # Assigning `__context__ = None` inside the handler does not help either: CPython re-sets it
    # as the raise executes. The only thing that works is raising once the handler has exited,
    # because the "currently handled exception" is restored at that point.
    #
    # `tests/test_parse_boundary.py` asserts `__context__ is None` on every path here, so this
    # structure cannot be tidied back into a `raise ... from None` without a test failing.
    error: ParseError | None = None
    tree: ast.Expression | None = None
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        # The only branch with a position to report. `msg`, `lineno` and `offset` are a string
        # and two ints. None of them reference a host object, so surfacing them satisfies R8
        # without reopening the leak. `exc.text` is deliberately not used: we already hold the
        # user's source and do not need CPython's copy of it.
        error = ParseError(
            f"could not parse expression: {exc.msg}",
            source=source,
            lineno=exc.lineno,
            offset=exc.offset,
        )
    except Exception as exc:
        # Everything else the parser can do: `MemoryError`, `RecursionError`, `ValueError`,
        # `UnicodeEncodeError`.
        #
        # **`Exception` rather than `BaseException`, deliberately.** The design calls for
        # `BaseException` containment, and that is right at the *eval* boundary, where a
        # sandboxed expression raising `SystemExit` would slip past a host's `except Exception`
        # (the asteval CVE-2026-55244 class). It is wrong here. Nothing user-controlled runs
        # during a parse; the only thing raising is CPython's parser, and every failure mode it
        # has was measured to be an `Exception` subclass already, `MemoryError` and
        # `RecursionError` included. Widening to `BaseException` would therefore catch nothing
        # extra and would swallow a real `KeyboardInterrupt`, making a program that parses in a
        # loop resistant to Ctrl-C. Containment belongs where the threat is.
        #
        # Only the exception's *type name* is reported. Its message can embed the input CPython
        # was looking at, and its `args` are not ours to hand on.
        error = ParseError(
            f"could not parse expression ({type(exc).__name__})",
            source=source,
        )

    if error is not None:
        raise error
    assert tree is not None  # noqa: S101 - narrowing for the type checker; unreachable otherwise
    return tree
