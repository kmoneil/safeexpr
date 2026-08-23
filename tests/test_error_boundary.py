"""Nothing escapes except a `SafeExprError`, and nothing rides out on one.

The second half is the part that is easy to get wrong. An error can be scrubbed of every string
that mentions the caller's data and still hand back the data itself, through `__context__`. These
tests pin the mechanism rather than the wording, so a future refactor that reintroduces
`raise ... from None` fails here rather than in somebody's incident review.
"""

from __future__ import annotations

import sys

import pytest

from safeexpr._errors import (
    InternalError,
    ParseError,
    SafeExprError,
    SourceTooLongError,
    ValidationError,
    contained,
)


class Probe:
    """Stands in for a caller's context object. Anything reachable from it is a leak."""

    api_key = "sk-live-must-not-escape"

    def __repr__(self) -> str:  # pragma: no cover - only runs if something prints it
        return "<Probe api_key=sk-live-must-not-escape>"


class TestTheF9LeakIsClosed:
    def test_the_naive_pattern_leaks_a_live_object(self) -> None:
        """The control. If this ever stops leaking, CPython changed and `contained` can simplify.

        This is RestrictedPython CVE-2024-47532 in miniature: `AttributeError.obj` holds the
        object whose attribute access failed, and `from None` does not detach it.
        """

        def naive() -> None:
            try:
                Probe().nope  # type: ignore[attr-defined]  # noqa: B018
            except AttributeError:
                raise ValidationError("scrubbed") from None

        with pytest.raises(ValidationError) as caught:
            naive()

        leaked = caught.value.__context__
        assert leaked is not None, "from None no longer leaves __context__ set"
        assert isinstance(getattr(leaked, "obj", None), Probe)

    def test_contained_does_not_leak(self) -> None:
        """The same failure, through the real boundary."""

        @contained
        def boundary() -> None:
            Probe().nope  # type: ignore[attr-defined]  # noqa: B018

        with pytest.raises(InternalError) as caught:
            boundary()
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None

    def test_no_reachable_attribute_holds_the_object(self) -> None:
        """Belt and braces: walk what the error actually exposes and look for the probe."""

        @contained
        def boundary() -> None:
            Probe().nope  # type: ignore[attr-defined]  # noqa: B018

        with pytest.raises(InternalError) as caught:
            boundary()
        error = caught.value
        reachable = [
            error.__context__,
            error.__cause__,
            *error.args,
            getattr(error, "obj", None),
            *getattr(error, "__notes__", []),
        ]
        assert not any(isinstance(item, Probe) for item in reachable)

    def test_the_message_names_neither_the_object_nor_its_secret(self) -> None:
        @contained
        def boundary() -> None:
            Probe().nope  # type: ignore[attr-defined]  # noqa: B018

        with pytest.raises(InternalError) as caught:
            boundary()
        text = str(caught.value)
        assert "Probe" not in text
        assert "api_key" not in text
        assert "sk-live" not in text
        # The exception *type* is named, because a bug report needs it and a type name is not
        # the caller's data.
        assert "AttributeError" in text

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="add_note is 3.11+")
    def test_notes_are_not_carried_across(self) -> None:
        """`__notes__` is a writable channel on the causing exception, so it is a way for a
        caller's object to attach a string to an error we then hand back."""

        @contained
        def boundary() -> None:
            exc = ValueError("inner")
            exc.add_note("note-from-the-caller")
            raise exc

        with pytest.raises(InternalError) as caught:
            boundary()
        assert "note-from-the-caller" not in str(caught.value)
        assert "note-from-the-caller" not in getattr(caught.value, "__notes__", [])


class TestBaseExceptionContainment:
    """F5, asteval CVE-2026-55244: these three are not `Exception` subclasses, so a host's
    `except Exception` does not stop them.

    They are reachable without any `raise` in the language, because comparing against a context
    value calls that value's `__eq__` and a caller may pass an object whose dunders misbehave.
    """

    @pytest.mark.parametrize(
        "exc",
        [SystemExit(1), KeyboardInterrupt(), GeneratorExit()],
        ids=["SystemExit", "KeyboardInterrupt", "GeneratorExit"],
    )
    def test_non_exception_baseexceptions_are_contained(self, exc: BaseException) -> None:
        @contained
        def boundary() -> None:
            raise exc

        with pytest.raises(InternalError) as caught:
            boundary()
        assert type(exc).__name__ in str(caught.value)
        assert caught.value.__context__ is None

    def test_a_hosts_except_exception_would_have_missed_these(self) -> None:
        """Spells out why the containment is needed rather than asserting it abstractly."""
        for exc in (SystemExit(1), KeyboardInterrupt(), GeneratorExit()):
            assert not isinstance(exc, Exception)
            assert isinstance(exc, BaseException)


class TestOurOwnErrorsPassThrough:
    def test_a_safeexpr_error_is_not_rewrapped(self) -> None:
        """Re-wrapping would discard the position, which is the useful part."""
        original = ValidationError(
            "lambdas are not supported", source="lambda x: x", lineno=1, offset=1
        )

        @contained
        def boundary() -> None:
            raise original

        with pytest.raises(ValidationError) as caught:
            boundary()
        assert caught.value is original
        assert caught.value.lineno == 1
        assert caught.value.offset == 1

    @pytest.mark.parametrize(
        "error",
        [
            ParseError("bad", source="1 +"),
            SourceTooLongError(9000, 2048),
            ValidationError("nope", source="x"),
            InternalError("boom"),
        ],
    )
    def test_every_error_type_is_a_safeexpr_error(self, error: SafeExprError) -> None:
        assert isinstance(error, SafeExprError)

    def test_a_successful_call_is_untouched(self) -> None:
        @contained
        def boundary(a: int, b: int) -> int:
            return a + b

        assert boundary(2, 3) == 5

    def test_the_wrapper_keeps_the_functions_identity(self) -> None:
        @contained
        def named(x: int) -> int:
            """Docstring survives."""
            return x

        assert named.__name__ == "named"
        assert named.__doc__ == "Docstring survives."


class TestPositionReporting:
    """R8: errors report the user's original source with real offsets."""

    def test_annotated_points_at_the_offending_column(self) -> None:
        error = ValidationError(
            "the & operator is not supported", source="a & b", lineno=1, offset=1
        )
        assert error.annotated() == "the & operator is not supported\n  a & b\n  ^"

    def test_annotated_degrades_to_the_message_without_a_position(self) -> None:
        error = SourceTooLongError(9000, 2048)
        assert error.annotated() == error.message
        assert "9000" in error.annotated()

    def test_annotated_handles_an_offset_past_the_end(self) -> None:
        """CPython reports an offset one past the last character for a truncated expression, so
        this must render rather than raise."""
        error = ParseError("could not parse", source="1 +", lineno=1, offset=4)
        assert "1 +" in error.annotated()

    def test_the_source_carried_is_the_users_own(self) -> None:
        source = "user.plan == 'pro'"
        error = ValidationError("x", source=source, lineno=1, offset=1)
        assert error.source == source
