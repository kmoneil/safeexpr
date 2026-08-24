"""Nothing escapes except a `SafeExprError`, and nothing rides out on one.

The second half is the part that is easy to get wrong. An error can be scrubbed of every string
that mentions the caller's data and still hand back the data itself, through `__context__`. These
tests pin the mechanism rather than the wording, so a future refactor that reintroduces
`raise ... from None` fails here rather than in somebody's incident review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from safeexpr import Evaluator, standard_registry
from safeexpr._errors import (
    InternalError,
    ParseError,
    SafeExprError,
    SourceTooLongError,
    ValidationError,
    contained,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "safeexpr"

# Not a credential. A marker string that must never appear in anything this package produces,
# placed on the probe objects below so a leak is visible rather than inferred.
SECRET = "sk-live-must-not-escape"  # noqa: S105


class Probe:
    """Stands in for a caller's context object. Anything reachable from it is a leak."""

    api_key = SECRET

    def __repr__(self) -> str:  # pragma: no cover - only runs if something prints it
        return f"<Probe api_key={SECRET}>"


class Hostile(Probe):
    """A `Probe` that refuses every operation, so every refusal is a handler holding it live.

    The point is coverage of *handlers*, not of operations. Each dunder below is a place the
    evaluator or a tier function catches something raised by the caller's own code, and each of
    those catches is a chance to build an error that still points at this object.
    """

    __hash__ = None  # type: ignore[assignment]

    def __eq__(self, other: object) -> bool:
        raise TypeError("refuses comparison")

    def __lt__(self, other: object) -> bool:
        raise TypeError("refuses ordering")

    def __gt__(self, other: object) -> bool:
        raise TypeError("refuses ordering")

    def __iter__(self) -> object:
        raise TypeError("refuses iteration")

    def __len__(self) -> int:
        raise TypeError("refuses length")

    def __getitem__(self, key: object) -> object:
        raise TypeError("refuses indexing")

    def __contains__(self, item: object) -> bool:
        raise TypeError("refuses membership")

    def __add__(self, other: object) -> object:
        raise TypeError("refuses addition")

    def __radd__(self, other: object) -> object:
        raise TypeError("refuses addition")


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


class TestNoRaiseSiteScrubsInsideItsHandler:
    """The forcing function, and the one this module's docstring already promised.

    Every test above proves a *reachable* path does not leak. That is worth having and it is not
    enough: a handler no test reaches is a handler nothing checks, and the corpus has the same
    blind spot, because it asserts `__context__` per entry and an entry can only reach what it can
    reach. `_regex._compiled` guarded `re.compile` against a warning the gate refuses first, so no
    corpus entry and no unit test ever ran that handler, and it scrubbed with
    `raise ... from None` for four cards.

    This reads the source instead. **Raising anything but a bare re-raise from inside an `except`
    block sets `__context__` on the new exception**, whatever the `from` clause says, so the rule
    is structural and needs no path to be reachable to enforce it. The convention it pins is the
    one `_errors` describes and `_validate._reject` states outright: build the error inside the
    handler, raise it once the handler has exited.
    """

    @staticmethod
    def _offenders() -> list[str]:
        found: list[str] = []
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                found.extend(
                    f"{path.name}:{inner.lineno}: {ast.unparse(inner)}"
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Raise) and inner.exc is not None
                )
        return found

    def test_every_shipped_module_builds_its_errors_and_raises_them_outside(self) -> None:
        assert self._offenders() == [], (
            "these raise inside the handler that caught something, so the new error carries "
            "`__context__` to whatever was caught, and from there to the caller's data. Build "
            "the error in the handler, assign it, and raise it after the handler exits."
        )

    def test_the_modules_are_actually_being_read(self) -> None:
        """A scan that silently finds no files would pass this suite for the wrong reason."""
        modules = sorted(path.name for path in SRC.glob("*.py"))
        assert len(modules) >= 10, f"only found {modules}"
        assert "_errors.py" in modules
        assert "_regex.py" in modules

    def test_a_bare_re_raise_is_allowed(self) -> None:
        """`except SafeExprError: raise` keeps the error that was already ours and already
        scrubbed, and re-wrapping it would only discard its position. The rule is about *new*
        exceptions, so the check has to let this through or the convention could not be written."""
        source = "try:\n    pass\nexcept ValueError:\n    raise\n"
        handlers = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler)]
        raises = [n for n in ast.walk(handlers[0]) if isinstance(n, ast.Raise)]
        assert len(raises) == 1
        assert raises[0].exc is None
        assert self._offenders() == []

    def test_the_check_catches_the_shape_it_exists_to_catch(self) -> None:
        """A checker that cannot fail is decoration. Both spellings must be caught: `from None`
        does not clear `__context__`, so it is no safer than an unadorned re-raise of a new
        error."""
        for spelling in ("raise Fresh('scrubbed')", "raise Fresh('scrubbed') from None"):
            source = f"try:\n    pass\nexcept ValueError:\n    {spelling}\n"
            handlers = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler)]
            caught = [
                n for n in ast.walk(handlers[0]) if isinstance(n, ast.Raise) and n.exc is not None
            ]
            assert caught, f"{spelling!r} was not flagged"

    def test_from_none_really_does_leave_the_context(self) -> None:
        """The premise, measured rather than cited.

        The rule above is only worth enforcing because `from None` does not do what its name
        suggests, so that is demonstrated here rather than taken on trust from the decision
        record. This is the one place in the package allowed to write the pattern, and the
        source scan reads `src/` only, so writing it here cannot make the scan pass.
        """

        class FreshError(Exception):
            pass

        def fails_holding_the_probe() -> None:
            raise ValueError(Probe())

        def scrubbed_the_wrong_way() -> None:
            try:
                fails_holding_the_probe()
            except ValueError:
                raise FreshError("scrubbed") from None

        with pytest.raises(FreshError) as caught:
            scrubbed_the_wrong_way()

        assert caught.value.__cause__ is None
        assert isinstance(caught.value.__context__, ValueError)
        assert caught.value.__context__.args[0].api_key == "sk-live-must-not-escape"


# Every registry function, called with the smallest argument count it declares, with a value that
# refuses everything in every non-lazy position. Built from the registry rather than listed, so a
# function added to a tier is swept the day it lands and nobody has to remember.
def _every_function_refused() -> list[str]:
    swept = []
    for name, function in sorted(standard_registry().items()):
        low = max(function.arity[0], 1)
        arguments = ["_" if index in function.lazy else "p" for index in range(low)]
        swept.append(f"{name}({', '.join(arguments)})")
    return swept


# The other half: the operators and syntax, which have their own handlers and no registry entry.
OPERATORS = (
    "p + 1", "1 + p", "p - 1", "p * 2", "p / 2", "p // 2", "p % 2", "p ** 2", "-p", "+p",
    "p == 1", "p != 1", "p < 1", "p > 1", "p <= 1", "1 < p < 3", "p in [1]", "1 in p",
    "not p", "p[0]", "p[0:1]", "p.field", "p._private", "{'k': p}['k'].x", "p if p else p",
    "p | first", "p | not_a_function", "no_such_function(p)", "no_such_name", "p['k']",
    "bitor(p, p)", "1 / 0",
)  # fmt: skip

SWEEP = (*_every_function_refused(), *OPERATORS)


def _refusal(evaluator: Evaluator, source: str) -> SafeExprError | None:
    """The error `source` produces against a value that refuses everything, or `None` if it ran.

    Only `SafeExprError` is caught. Anything else escaping is a failure of the wider promise and
    is left to propagate, so it fails the caller rather than being counted as a clean refusal.
    """
    try:
        evaluator.evaluate(source, {"p": Hostile(), "rows": [Hostile()]})
    except SafeExprError as error:
        return error
    return None


def _leaks(error: SafeExprError) -> list[str]:
    """What an error is carrying that it must not be.

    The type *name* is deliberately absent from this list. `_registry.describe_type` puts it in
    messages on purpose and argues the case there: a name is a string and cannot be climbed,
    where a `repr` would put the caller's data into text the expression author reads. It is a
    disclosure and it is documented as one in THREAT-MODEL's "What this does not bound", which is
    a different thing from a leak.
    """
    found = []
    if error.__cause__ is not None:
        found.append(f"__cause__ is {type(error.__cause__).__name__}")
    if error.__context__ is not None:
        found.append(f"__context__ is {type(error.__context__).__name__}")
    if getattr(error, "__notes__", None):
        found.append(f"__notes__ is {error.__notes__}")
    if SECRET in str(error):
        found.append("the secret is in the message")
    if "<Probe" in str(error) or "api_key=" in str(error):
        found.append("the repr is in the message")
    return found


@pytest.fixture(scope="module")
def evaluator() -> Evaluator:
    return Evaluator(registry=standard_registry())


class TestNothingRidesOutOnAnyRefusal:
    """The runtime half of the same property, across every refusal the language can produce.

    The corpus asserts `__context__` per entry, which makes it as broad as its entries. This is
    broad in a different direction: **one expression per registry function plus the operators**,
    each fed a value whose own dunders raise, so the handler that catches the refusal is holding
    the caller's object at the moment it builds an error. That is the F9 precondition, produced
    on purpose, seventy-odd times.

    It is generated from the registry, so it grows when a tier does.
    """

    @pytest.mark.parametrize("source", SWEEP)
    def test_the_refusal_carries_nothing(self, evaluator: Evaluator, source: str) -> None:
        """Anything that is not a `SafeExprError` propagates out of `_refusal` and fails here on
        its own, which is the other half of the promise and needs no assertion of its own."""
        error = _refusal(evaluator, source)
        if error is None:
            return
        assert _leaks(error) == [], f"{source!r}: {_leaks(error)}"

    def test_every_registry_function_is_in_the_sweep(self) -> None:
        """A sweep that quietly stopped covering a tier would pass every case it still had."""
        swept = {source.split("(", 1)[0] for source in SWEEP}
        missing = set(standard_registry()) - swept
        assert missing == set(), f"registry functions never swept: {sorted(missing)}"

    def test_most_of_the_sweep_actually_refuses(self, evaluator: Evaluator) -> None:
        """The sweep is worth what its refusals are worth.

        A handful of entries legitimately succeed: `default` and `is_none` are *about* awkward
        values, and `[p][0]` hands back what the host put in. If that number ever grows, the
        sweep has stopped producing the handlers it exists to produce.
        """
        refused = sum(_refusal(evaluator, source) is not None for source in SWEEP)
        assert refused >= len(SWEEP) - 4, f"only {refused} of {len(SWEEP)} were refused"


class TestTheLeakCheckFailsLoudly:
    """A leak check that cannot fail is decoration."""

    def test_a_carried_context_is_reported(self) -> None:
        error = ValidationError("scrubbed")
        error.__context__ = ValueError(Probe())
        assert "__context__ is ValueError" in _leaks(error)

    def test_a_carried_cause_is_reported(self) -> None:
        error = ValidationError("scrubbed")
        error.__cause__ = ValueError(Probe())
        assert "__cause__ is ValueError" in _leaks(error)

    def test_a_note_is_reported(self) -> None:
        error = ValidationError("scrubbed")
        error.add_note(SECRET)
        assert "__notes__" in " ".join(_leaks(error))

    def test_a_secret_in_the_message_is_reported(self) -> None:
        assert "the secret is in the message" in _leaks(ValidationError(f"saw {SECRET}"))

    def test_a_repr_in_the_message_is_reported(self) -> None:
        assert "the repr is in the message" in _leaks(ValidationError(f"saw {Probe()!r}"))

    def test_a_clean_error_reports_nothing(self) -> None:
        assert _leaks(ValidationError("cannot compare `Probe` with `int`")) == []
