"""The parse boundary holds against everything CPython's parser can do to us.

The point of these tests is not that bad syntax is rejected. It is that the *resource* failures,
which are not `SyntaxError` and which differ by interpreter version, never reach the caller as
themselves. A handler written as `except SyntaxError` passes every syntax test in this file and
still leaks `MemoryError`, `ValueError` and `UnicodeEncodeError`.
"""

from __future__ import annotations

import ast
import sys

import pytest

from safeexpr._errors import ParseError, SafeExprError, SourceTooLongError
from safeexpr._parse import MAX_SOURCE_BYTES, parse

# The lowest parser cliff across the supported interpreters, measured by bisection at the default
# recursion limit: 2,989 on **3.11**, against ~5,975 on 3.12 through 3.14. 3.11 is the
# outlier in all three respects: lowest bound, `RecursionError` rather than `MemoryError`, and the
# only one that moves with `sys.setrecursionlimit`.
LOWEST_PARSER_CLIFF = 2989

# Past the cliff on every supported interpreter, so the resource-failure tests below trigger
# everywhere rather than only on 3.11.
OVER_THE_PARSER_CLIFF = 6000


class TestTheLengthGateRunsFirst:
    def test_over_length_source_is_rejected(self) -> None:
        with pytest.raises(SourceTooLongError) as caught:
            parse("1" * (MAX_SOURCE_BYTES + 1))
        assert caught.value.limit == MAX_SOURCE_BYTES
        assert caught.value.size == MAX_SOURCE_BYTES + 1

    def test_the_cliff_input_never_reaches_the_parser(self, monkeypatch) -> None:
        """The gate is only worth anything if it runs *before* `ast.parse`.

        Asserted by making `ast.parse` explode if it is called at all, rather than by trusting
        the ordering of two statements.
        """

        def explode(*args: object, **kwargs: object) -> object:
            raise AssertionError("ast.parse was reached; the length gate did not run first")

        monkeypatch.setattr(ast, "parse", explode)
        with pytest.raises(SourceTooLongError):
            parse("-" * OVER_THE_PARSER_CLIFF + "1")

    def test_the_limit_leaves_headroom_under_the_measured_cliff(self) -> None:
        """One byte cannot express more than one nesting level, so the cap bounds depth."""
        assert MAX_SOURCE_BYTES < LOWEST_PARSER_CLIFF, (
            f"MAX_SOURCE_BYTES is {MAX_SOURCE_BYTES}, at or above the lowest measured parser "
            f"cliff of {LOWEST_PARSER_CLIFF} (Python 3.11). An all-unary expression at that "
            f"length would reach the parser and exhaust it"
        )

    def test_length_is_measured_in_bytes_not_characters(self) -> None:
        """A multi-byte character costs what it costs on the wire."""
        # Each snowman is 3 UTF-8 bytes, so this is over the cap in bytes and under it in
        # characters. Measuring characters would let ~3x the intended input through.
        source = '"' + ("☃" * ((MAX_SOURCE_BYTES // 3) + 1)) + '"'
        assert len(source) < MAX_SOURCE_BYTES
        with pytest.raises(SourceTooLongError):
            parse(source)


class TestNonSyntaxErrorFailuresAreContained:
    """The tests that a naive `except SyntaxError` boundary would fail."""

    def test_memory_error_from_a_deeply_nested_expression(self, monkeypatch) -> None:
        """Bypass the gate to prove the inner guard is real and not just unreachable.

        Raising `MAX_SOURCE_BYTES` for the duration is what a caller would do if we exposed the
        limit as a knob, so this is also the regression test for someone doing that.
        """
        monkeypatch.setattr("safeexpr._parse.MAX_SOURCE_BYTES", OVER_THE_PARSER_CLIFF * 2)
        with pytest.raises(ParseError) as caught:
            parse("-" * OVER_THE_PARSER_CLIFF + "1")
        # 3.11 raises RecursionError here where the others raise MemoryError, so this asserts
        # containment rather than which exception CPython happened to choose.
        assert "MemoryError" in str(caught.value) or "RecursionError" in str(caught.value)

    def test_a_null_byte_is_contained_on_every_version(self) -> None:
        """**Version-divergent**: `ast.parse` raises `ValueError` for this on some supported
        versions and `SyntaxError` on others. Both must surface identically."""
        with pytest.raises(ParseError):
            parse("1\x00+2")

    def test_an_unpaired_surrogate_is_contained(self) -> None:
        """`str.encode` raises `UnicodeEncodeError`, so the *length check itself* can fail."""
        with pytest.raises(ParseError) as caught:
            parse("'\ud800'")
        assert "surrogate" in str(caught.value)

    @pytest.mark.parametrize(
        "exc",
        [MemoryError(), RecursionError(), ValueError("boom"), OverflowError()],
        ids=["MemoryError", "RecursionError", "ValueError", "OverflowError"],
    )
    def test_any_parser_exception_becomes_our_error(self, monkeypatch, exc: Exception) -> None:
        """Whatever a future CPython decides to raise, the caller sees one type."""

        def raiser(*args: object, **kwargs: object) -> object:
            raise exc

        monkeypatch.setattr(ast, "parse", raiser)
        with pytest.raises(ParseError) as caught:
            parse("1 + 1")
        assert type(exc).__name__ in str(caught.value)

    def test_a_keyboard_interrupt_is_not_swallowed(self, monkeypatch) -> None:
        """Containment belongs at the eval boundary, not here.

        Nothing user-controlled runs during a parse, and every failure mode CPython's parser has
        was measured to be an `Exception` subclass. Widening this guard to `BaseException` would
        catch nothing extra and would make a program that parses in a loop resist Ctrl-C.
        """

        def interrupt(*args: object, **kwargs: object) -> object:
            raise KeyboardInterrupt

        monkeypatch.setattr(ast, "parse", interrupt)
        with pytest.raises(KeyboardInterrupt):
            parse("1 + 1")


class TestEverythingRaisedIsOurs:
    @pytest.mark.parametrize(
        "source",
        [
            "",
            "   ",
            "\n",
            "1 +",
            "x = 1",
            "1; 2",
            "# nothing",
            "\t1+1",
            "a\u202eb",  # bidi override, escaped rather than embedded
            "'\ud800'",
            "1\x00+2",
            "1" * (MAX_SOURCE_BYTES + 1),
            "9" * 4400,
        ],
        ids=[
            "empty",
            "whitespace",
            "newline",
            "incomplete",
            "statement",
            "two-statements",
            "comment-only",
            "leading-tab",
            "bidi-override",
            "lone-surrogate",
            "null-byte",
            "over-length",
            "huge-int-literal",
        ],
    )
    def test_bad_input_raises_only_safeexpr_errors(self, source: str) -> None:
        """One `except SafeExprError` has to be enough for a caller."""
        with pytest.raises(SafeExprError):
            parse(source)

    def test_bytes_are_rejected_rather_than_quietly_parsed(self) -> None:
        """`ast.parse(b"1+1")` succeeds, so without an explicit check a caller passing the wrong
        type would get a working evaluation instead of an error."""
        with pytest.raises(ParseError) as caught:
            parse(b"1+1")  # type: ignore[arg-type]
        assert "bytes" in str(caught.value)

    def test_the_empty_expression_gets_its_own_message(self) -> None:
        """CPython reports `invalid syntax (<unknown>, line 0)`, and line 0 does not exist."""
        with pytest.raises(ParseError) as caught:
            parse("")
        assert "empty" in str(caught.value)


class TestErrorsCarryTheUsersSourceAndNothingElse:
    def test_a_syntax_error_reports_a_real_position(self) -> None:
        with pytest.raises(ParseError) as caught:
            parse("1 +")
        error = caught.value
        assert error.source == "1 +"
        assert error.lineno == 1
        # SyntaxError.offset is nominally 1-based but CPython reports 0 for a truncated
        # expression, so this pins it as a real position within the source rather than assuming
        # a floor CPython does not actually honour.
        assert error.offset is not None
        assert 0 <= error.offset <= len(error.source) + 1

    def test_nothing_is_chained(self) -> None:
        """F9, and the subtle half of it.

        `raise ... from None` is **not** enough. It clears `__cause__` and suppresses the
        traceback display, but `__context__` keeps pointing at the original exception, and on
        every supported version an `AttributeError` carries `.obj`, a reference to the object
        whose access failed. Measured: an error raised `from None` with a fully scrubbed message
        still yields a live object through `err.__context__.obj`.

        Assigning `__context__ = None` inside the handler does not fix it either, because CPython
        re-sets it as the raise executes. The error has to be raised after the handler exits, and
        that is why `_parse.parse` is shaped the way it is.

        `__suppress_context__` is deliberately not asserted: it governs display only, and with
        `__context__` already None there is nothing left for it to suppress.
        """
        with pytest.raises(ParseError) as caught:
            parse("1 +")
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    def test_nothing_is_chained_on_the_resource_path_either(self, monkeypatch) -> None:
        monkeypatch.setattr("safeexpr._parse.MAX_SOURCE_BYTES", OVER_THE_PARSER_CLIFF * 2)
        with pytest.raises(ParseError) as caught:
            parse("-" * OVER_THE_PARSER_CLIFF + "1")
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


class TestValidInputStillParses:
    @pytest.mark.parametrize(
        "source",
        [
            "1 + 1",
            'user.plan == "pro" and user.region in ["us", "eu"]',
            "metrics | where(_.value > threshold) | first",
            "a if b else c",
            "1 < a < 3",
            '{"a": 1}["a"]',
            "-1",
            "items[1:2]",
        ],
    )
    def test_supported_shapes_parse(self, source: str) -> None:
        tree = parse(source)
        assert isinstance(tree, ast.Expression)

    def test_the_returned_tree_is_not_yet_validated(self) -> None:
        """This boundary promises the source is *a* Python expression, not a safe one.

        Rejecting `__class__` is the allowlist's job, and asserting that here keeps the two
        responsibilities from blurring: a reader who assumes `parse` is a security boundary would
        be wrong, and this is where they find out.
        """
        assert isinstance(parse("x.__class__"), ast.Expression)

    def test_source_at_exactly_the_limit_is_accepted(self) -> None:
        """Off-by-one on a security limit is worth a test in both directions."""
        source = "1" + "+1" * ((MAX_SOURCE_BYTES - 1) // 2)
        assert len(source.encode()) <= MAX_SOURCE_BYTES
        assert isinstance(parse(source), ast.Expression)


def test_the_parser_cliff_is_where_we_measured_it() -> None:
    """A canary on the constant this module's limit is derived from.

    If a future CPython moves the parser's bound downward, `MAX_SOURCE_BYTES` stops being
    conservative and this fails on that interpreter rather than in somebody's production.
    """
    depth = MAX_SOURCE_BYTES - 1
    try:
        ast.parse("-" * depth + "1", mode="eval")
    except (MemoryError, RecursionError) as exc:  # pragma: no cover - only on a tightened bound
        # Bisect the real cliff so the failure message says what to set, not just that it broke.
        lo, hi = 1, depth
        while hi - lo > 1:
            mid = (lo + hi) // 2
            try:
                ast.parse("-" * mid + "1", mode="eval")
                lo = mid
            except (MemoryError, RecursionError):
                hi = mid
        pytest.fail(
            f"ast.parse raises {type(exc).__name__} at {depth} nesting levels on Python "
            f"{sys.version_info.major}.{sys.version_info.minor} "
            f"(recursionlimit={sys.getrecursionlimit()}). The real cliff here is {lo}, so "
            f"MAX_SOURCE_BYTES={MAX_SOURCE_BYTES} is no longer below it and must come down."
        )


def test_the_naive_from_none_pattern_would_leak() -> None:
    """Proof that the structure in `_parse.parse` is necessary, not fussy.

    This reproduces the pattern a reviewer would naturally write, and shows it hands back a live
    object anyway. Without this test, "raise ... from None" looks obviously sufficient and the
    real structure looks like something worth tidying away.

    This is F9 (RestrictedPython CVE-2024-47532) reduced to nine lines.
    """

    class Probe:
        payload = "reachable-through-context"

    def naive() -> None:
        try:
            Probe().nope  # type: ignore[attr-defined]  # noqa: B018
        except AttributeError:
            raise ParseError("scrubbed message", source="") from None

    with pytest.raises(ParseError) as caught:
        naive()

    leaked = caught.value.__context__
    assert leaked is not None, "if this fails, CPython changed and _parse can be simplified"
    assert isinstance(getattr(leaked, "obj", None), Probe), (
        "the leaked context should still expose the live object; that is the whole point"
    )
    # And the contrast: our real boundary does not do this.
    with pytest.raises(ParseError) as ours:
        parse("1 +")
    assert ours.value.__context__ is None
