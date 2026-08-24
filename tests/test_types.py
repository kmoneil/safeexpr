"""The types tier: conversion, and the null-safe chain.

Most of this is ordinary. The part worth reading is `str`, which refuses anything that is not a
primitive, and refuses it for a security reason rather than a semantic one: `str(x)` on an
arbitrary object runs that object's `__str__`, and a rules engine that hands the expression
author `str(request.session)` has published whatever that object's author chose to print. The
corpus already carries the same leak arriving through `"%s" % obj`.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, standard_registry

EV = Evaluator(registry=standard_registry())


@pytest.fixture
def ev() -> Evaluator:
    return EV


class TestInt:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("42", 42), ("  7 ", 7), ("-3", -3), (2.9, 2), (-2.9, -2), (True, 1), (False, 0), (5, 5)],
    )
    def test_it_converts(self, ev: Evaluator, value: Any, expected: int) -> None:
        assert ev.evaluate("int(x)", {"x": value}) == expected

    @pytest.mark.parametrize("value", ["abc", "", "1.5", "0x10", "1e3", " ", "+-1"])
    def test_text_that_is_not_a_whole_number_is_refused(self, ev: Evaluator, value: str) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("int(x)", {"x": value})
        assert "whole number" in str(caught.value)

    def test_underscores_separate_digits_exactly_as_they_do_in_python(self, ev: Evaluator) -> None:
        """`int("1_000")` is 1000 in Python (PEP 515) and is 1000 here. Worth pinning rather than
        discovering: the tier's rule is "reads as a whole number", and what Python reads that way
        is the definition being borrowed."""
        assert ev.evaluate("int(x)", {"x": "1_000_000"}) == 1_000_000

    def test_a_value_it_cannot_convert_names_the_type_and_not_the_value(
        self, ev: Evaluator
    ) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("int(x)", {"x": {"secret": "sk-live"}})
        assert "`dict`" in str(caught.value)
        assert "sk-live" not in str(caught.value)

    def test_a_float_that_is_not_finite_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("int(x)", {"x": float("inf")})
        assert "finite" in str(caught.value)

    def test_cpython_s_own_digit_limit_arrives_as_an_ordinary_refusal(self, ev: Evaluator) -> None:
        """CPython caps integer parsing at 4,300 digits, which is a perfectly good reason to
        refuse and must not surface as an internal error."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("int(x)", {"x": "9" * 5000})
        assert "whole number" in str(caught.value)


class TestFloat:
    @pytest.mark.parametrize(
        ("value", "expected"), [("1.5", 1.5), ("  2 ", 2.0), (3, 3.0), (True, 1.0), (-0.5, -0.5)]
    )
    def test_it_converts(self, ev: Evaluator, value: Any, expected: float) -> None:
        assert ev.evaluate("float(x)", {"x": value}) == expected

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity", "1e400"])
    def test_infinity_and_not_a_number_are_refused(self, ev: Evaluator, value: str) -> None:
        """They compare in ways nobody means: `float(_.x) > 100` against `"nan"` is silently
        false for every row, and one in a sort makes the sort silently wrong."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("float(x)", {"x": value})
        assert "infinity or not-a-number" in str(caught.value)

    def test_text_that_is_not_a_number_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("float(x)", {"x": "abc"})
        assert "reads as a number" in str(caught.value)

    @pytest.mark.parametrize("value", [{"a": 1}, [1], (1,), None])
    def test_a_type_it_cannot_convert_names_the_type(self, ev: Evaluator, value: Any) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("float(x)", {"x": value})
        assert "to a number" in str(caught.value)


class TestStr:
    @pytest.mark.parametrize(
        ("value", "expected"), [(1, "1"), (1.5, "1.5"), (True, "True"), ("a", "a")]
    )
    def test_it_converts_primitives(self, ev: Evaluator, value: Any, expected: str) -> None:
        assert ev.evaluate("str(x)", {"x": value}) == expected

    def test_an_arbitrary_object_is_refused_rather_than_printed(self, ev: Evaluator) -> None:
        """**The F1 shape wearing a friendly name.** Converting an arbitrary value runs that
        value's own code to produce the text, which is the same leak the corpus records for
        `"%s" % obj`."""

        class Host:
            def __repr__(self) -> str:  # pragma: no cover - must never be called
                return "<Host api_key=sk-live-SECRET>"

            def __str__(self) -> str:  # pragma: no cover - must never be called
                return "<Host api_key=sk-live-SECRET>"

        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("str(x)", {"x": Host()})
        assert "sk-live" not in str(caught.value)
        assert "`Host`" in str(caught.value)

    @pytest.mark.parametrize("value", [[1, 2], {"a": 1}, (1,)])
    def test_containers_are_refused_because_their_contents_might_not_be_primitives(
        self, ev: Evaluator, value: Any
    ) -> None:
        with pytest.raises(EvaluationError):
            ev.evaluate("str(x)", {"x": value})

    def test_nothing_is_refused_with_advice_rather_than_rendered_as_the_word_none(
        self, ev: Evaluator
    ) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("str(x)", {"x": None})
        assert "default" in str(caught.value)


class TestBoolIsNoneAndDefault:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, False), (1, True), ("", False), ("a", True), ([], False), ([1], True), (None, False)],
    )
    def test_bool_is_truthiness(self, ev: Evaluator, value: Any, expected: bool) -> None:
        assert ev.evaluate("bool(x)", {"x": value}) is expected

    def test_bool_is_unrestricted_because_one_bit_is_not_a_leak(self, ev: Evaluator) -> None:
        """Unlike `str`. Truthiness runs a host object's `__bool__` too, but what comes back is
        one bit rather than text the object chose."""

        class Host:
            def __bool__(self) -> bool:
                return True

        assert ev.evaluate("bool(x)", {"x": Host()}) is True

    @pytest.mark.parametrize(("value", "expected"), [(None, True), (0, False), ("", False)])
    def test_is_none_asks_only_about_absence(
        self, ev: Evaluator, value: Any, expected: bool
    ) -> None:
        assert ev.evaluate("is_none(x)", {"x": value}) is expected

    def test_default_replaces_only_nothing(self, ev: Evaluator) -> None:
        assert ev.evaluate("default(x, 10)", {"x": None}) == 10
        assert ev.evaluate("default(x, 10)", {"x": 3}) == 3

    @pytest.mark.parametrize("falsy", [0, "", [], False])
    def test_default_leaves_falsy_values_alone(self, ev: Evaluator, falsy: Any) -> None:
        """`default(_.count, 10)` where the count is genuinely 0 must give 0. `or` is the
        operator for "falsy or missing" and it is already in the language."""
        assert ev.evaluate("default(x, 10)", {"x": falsy}) == falsy

    def test_default_chains_for_a_null_safe_read(self, ev: Evaluator) -> None:
        context = {"row": {"nickname": None, "name": "ada"}}
        assert ev.evaluate("default(row.nickname, row.name)", context) == "ada"


class TestDifferentialAgainstPython:
    """Where the tier claims to mean what Python means, generated values check that it does."""

    @given(value=st.integers(min_value=-(10**12), max_value=10**12))
    def test_int_of_an_integer_is_the_integer(self, value: int) -> None:
        assert EV.evaluate("int(x)", {"x": value}) == int(value)

    @given(value=st.floats(allow_nan=False, allow_infinity=False, width=32))
    def test_int_of_a_float_truncates_toward_zero_as_python_does(self, value: float) -> None:
        assert EV.evaluate("int(x)", {"x": value}) == int(value)

    @given(value=st.floats(allow_nan=False, allow_infinity=False, width=32))
    def test_float_of_a_finite_float_is_unchanged(self, value: float) -> None:
        assert EV.evaluate("float(x)", {"x": value}) == value

    @given(value=st.integers(min_value=-(10**9), max_value=10**9))
    def test_int_round_trips_through_str(self, value: int) -> None:
        assert EV.evaluate("int(str(x))", {"x": value}) == value

    @given(
        value=st.one_of(
            st.integers(), st.text(max_size=20), st.booleans(), st.none(), st.lists(st.integers())
        )
    )
    def test_bool_and_is_none_agree_with_python(self, value: Any) -> None:
        assert EV.evaluate("bool(x)", {"x": value}) is bool(value)
        assert EV.evaluate("is_none(x)", {"x": value}) is (value is None)

    @given(value=st.one_of(st.integers(), st.text(max_size=20), st.none()), fallback=st.integers())
    def test_default_agrees_with_the_conditional_it_replaces(
        self, value: Any, fallback: int
    ) -> None:
        expected = fallback if value is None else value
        assert EV.evaluate("default(x, y)", {"x": value, "y": fallback}) == expected

    @given(value=st.floats(allow_nan=True, allow_infinity=True, width=32))
    def test_float_accepts_exactly_the_finite_values(self, value: float) -> None:
        if math.isfinite(value):
            assert EV.evaluate("float(x)", {"x": value}) == value
        else:
            with pytest.raises(EvaluationError):
                EV.evaluate("float(x)", {"x": value})
