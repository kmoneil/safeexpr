"""What a registry entry declares, and what the evaluator does with the declaration.

Three fields, three different jobs. `lazy` decides whether an argument is evaluated or handed
over as a subtree. `arity` decides whether the call happens at all, and exists because a
function given the wrong *number* of arguments and one given the wrong *kind* both raise
`TypeError`: indistinguishable once caught, so the message used to guess. `cost` is declared here
and charged by a step budget that is not built yet, so a tier can be written once and priced
without being rewritten.
"""

from __future__ import annotations

from typing import Any

import pytest

from safeexpr import EvaluationError, Evaluator, Function, FunctionError
from safeexpr._registry import as_function, describe_type


class TestAnEntryRefusesToBeBuiltWrong:
    """Caught at construction, where a host reads the message, rather than at the first call in
    production."""

    def test_a_negative_lazy_position(self) -> None:
        with pytest.raises(ValueError, match="zero-based"):
            Function("f", len, lazy=frozenset({-1}))

    def test_an_arity_whose_maximum_is_below_its_minimum(self) -> None:
        with pytest.raises(ValueError, match="minimum, maximum"):
            Function("f", len, arity=(3, 1))

    def test_a_negative_minimum_arity(self) -> None:
        with pytest.raises(ValueError, match="minimum, maximum"):
            Function("f", len, arity=(-1, 2))

    def test_a_lazy_position_past_the_last_argument_the_arity_allows(self) -> None:
        """A declaration that can never fire: the evaluator would evaluate the argument the
        function believes it is receiving unevaluated."""
        with pytest.raises(ValueError, match="past the last argument"):
            Function("f", len, lazy=frozenset({4}), arity=(1, 2))

    def test_a_cost_below_one(self) -> None:
        with pytest.raises(ValueError, match="cost must be 1 or more"):
            Function("f", len, cost=0)


class TestArity:
    @pytest.mark.parametrize(
        ("arity", "count", "accepted"),
        [
            ((2, 2), 1, False),
            ((2, 2), 2, True),
            ((2, 2), 3, False),
            ((1, 2), 1, True),
            ((1, 2), 2, True),
            ((2, None), 9, True),
            ((2, None), 1, False),
            ((0, None), 0, True),
        ],
    )
    def test_what_a_declaration_accepts(
        self, arity: tuple[int, int | None], count: int, accepted: bool
    ) -> None:
        assert Function("f", len, arity=arity).accepts(count) is accepted

    @pytest.mark.parametrize(
        ("arity", "text"),
        [
            ((1, 1), "1 argument"),
            ((2, 2), "2 arguments"),
            ((1, 2), "1 or 2 arguments"),
            ((1, 3), "1 to 3 arguments"),
            ((2, None), "at least 2 arguments"),
            ((1, None), "at least 1 argument"),
        ],
    )
    def test_how_a_declaration_reads(self, arity: tuple[int, int | None], text: str) -> None:
        assert Function("f", len, arity=arity).arity_text() == text


class TestArityIsCheckedBeforeTheCall:
    def test_the_message_names_what_was_expected_and_what_arrived(self) -> None:
        ev = Evaluator(registry={"pair": Function("pair", lambda a, b: (a, b), arity=(2, 2))})
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("pair(1)", {})
        assert "`pair` takes 2 arguments, got 1" in str(caught.value)

    def test_arguments_are_not_evaluated_when_the_count_is_already_wrong(self) -> None:
        """Nothing should run on the way to reporting a miscount, least of all something that
        raises an error of its own and buries the real complaint."""
        calls: list[int] = []

        def tick() -> int:
            calls.append(1)
            return 1

        registry: dict[str, Any] = {
            "pair": Function("pair", lambda a, b: (a, b), arity=(2, 2)),
            "tick": Function("tick", tick, arity=(0, 0)),
        }
        with pytest.raises(EvaluationError):
            Evaluator(registry=registry).evaluate("pair(tick())", {})
        assert calls == []

    def test_a_bare_callable_declares_no_arity_and_is_left_unchecked(self) -> None:
        """Guessing one from a signature would be reflection, and the old message is still the
        honest thing to say when nothing was declared."""
        ev = Evaluator(registry={"double": lambda n: n * 2})
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("double(1, 2)", {})
        assert "cannot accept 2 argument(s)" in str(caught.value)

    @pytest.mark.parametrize(
        ("arity", "informative"),
        [((1, 1), True), ((1, 2), True), ((2, None), True), ((0, None), False)],
    )
    def test_only_a_narrower_declaration_counts_as_one(
        self, arity: tuple[int, int | None], informative: bool
    ) -> None:
        """`(0, None)` rules nothing out, so it is treated as never having said anything."""
        assert Function("f", len, arity=arity).checks_arity is informative


class TestRegressions:
    def test_regression_arity_a_wrong_value_was_reported_as_a_wrong_argument_count(self) -> None:
        """A function given the right number of arguments and the wrong kind of value was told
        it could not accept that many arguments.

        Both failures reach the evaluator as `TypeError` and the handler could not tell them
        apart, so it reported the one it could name. The statement was simply false: the call
        below passes exactly the one argument the function declares, and the old message said it
        could not accept one argument.

        Fixed by checking the declared arity before the call. Once a call has satisfied an
        informative arity, a `TypeError` out of the function cannot be a miscount.
        """

        def needs_a_number(value: Any) -> Any:
            return value + 1  # TypeError on anything that will not add to an int

        ev = Evaluator(
            registry={"needs_a_number": Function("needs_a_number", needs_a_number, arity=(1, 1))}
        )
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("needs_a_number(text)", {"text": "not a number"})
        message = str(caught.value)
        assert "cannot work with the values it was given" in message
        assert "argument(s)" not in message, (
            "reported a wrong argument count for a call that passed exactly the right number"
        )

    def test_regression_arity_an_undeclared_function_keeps_the_older_wording(self) -> None:
        """The other half of the same fix. Where nothing was declared the ambiguity is real, so
        the message must not start claiming a certainty it does not have."""
        ev = Evaluator(registry={"add_one": lambda value: value + 1})
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("add_one(text)", {"text": "not a number"})
        assert "cannot accept 1 argument(s)" in str(caught.value)


class TestAFunctionCanObjectToItsValues:
    def test_the_objection_is_reported_with_the_function_name_and_a_position(self) -> None:
        def picky(value: Any) -> Any:
            raise FunctionError("needs something else entirely")

        ev = Evaluator(registry={"picky": Function("picky", picky, arity=(1, 1))})
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("1 + picky(2)", {})
        assert "`picky`: needs something else entirely" in str(caught.value)
        assert caught.value.offset == 5

    def test_the_objection_does_not_bring_the_caught_exception_with_it(self) -> None:
        """F9. The evaluator catches `FunctionError` inside a handler, so anything reachable from
        one would be reachable through the `__context__` of the error it builds."""

        def picky(value: Any) -> Any:
            try:
                int("not a number at all")
            except ValueError:
                failure = FunctionError("cannot use this")
            raise failure

        ev = Evaluator(registry={"picky": Function("picky", picky, arity=(1, 1))})
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("picky(2)", {})
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None

    def test_a_function_error_carries_nothing_but_its_message(self) -> None:
        objection = FunctionError("just words")
        assert objection.args == ("just words",)
        assert not hasattr(objection, "value")


class TestDescribeType:
    def test_it_names_the_type_and_not_the_value(self) -> None:
        class Host:
            def __repr__(self) -> str:  # pragma: no cover - must never be called
                return "sk-live-SECRET"

        assert describe_type(Host()) == "Host"
        assert describe_type({"k": "sk-live"}) == "dict"
        assert describe_type(1) == "int"


class TestAsFunction:
    def test_a_bare_callable_becomes_a_function_with_no_lazy_positions(self) -> None:
        function = as_function("double", lambda n: n * 2)
        assert function.name == "double"
        assert function.lazy == frozenset()
        assert function.arity == (0, None)
        assert function.cost == 1

    def test_a_function_is_passed_through_unchanged(self) -> None:
        original = Function("f", len, arity=(1, 1), cost=4)
        assert as_function("ignored", original) is original
