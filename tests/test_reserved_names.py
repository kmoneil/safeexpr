"""Registry names are reserved on the right of a pipe, and the collision is reported.

`|` is Python's bitwise-or, borrowed for chaining, and the rule for telling the two apart is that
the right-hand side is a pipe if and only if the name is in the registry. That decision never
consults the context, deliberately: a rule that did would make an expression mean different
things on different data. The cost is that `flags | first` calls the function whatever `first`
means in the data, and the data's `first` is silently unreachable there.

**Silently was the problem.** Measured before this check existed: `values | min` against
`{"values": [3, 1], "min": 99}` returned 1, with no error and no hint that a context key had been
passed over.

**The check is narrower than "reject any collision", and that is measured too.** With forty-one
functions in the standard registry, `min`, `max`, `first`, `last`, `sum`, `len` and `default` are
all realistic context keys, and `metrics | where(_.value > min)` against `{"min": 10}` is a
correct, unambiguous rule: a bare name reads the context. Refusing it because `min` is also a
function would break real expressions to prevent nothing. Only the right of a `|` is ambiguous,
because that is the one position where the author might have meant bitwise or instead.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import (
    EvaluationError,
    Evaluator,
    ReservedNameError,
    SafeExprError,
    standard_registry,
)

EV = Evaluator(registry=standard_registry())
NAMES = sorted(standard_registry())


@pytest.fixture
def ev() -> Evaluator:
    return EV


def _sample_for(name: str) -> Any:
    """A context value that would work if the key were reachable."""
    return 7


class TestTheCollisionIsRefused:
    def test_the_scenario_the_card_describes(self, ev: Evaluator) -> None:
        with pytest.raises(ReservedNameError) as caught:
            ev.evaluate("flags | first", {"flags": 5, "first": 2})
        assert caught.value.name == "first"

    def test_the_silent_answer_that_used_to_come_back(self, ev: Evaluator) -> None:
        """Measured before the check existed: this returned 1, the registry's answer, with the
        context's `min` passed over and nothing said about it."""
        with pytest.raises(ReservedNameError):
            ev.evaluate("values | min", {"values": [3, 1], "min": 99})

    @pytest.mark.parametrize("name", NAMES)
    def test_every_registry_name_collides(self, ev: Evaluator, name: str) -> None:
        """The card asks for a collision with every registry name, so every one is generated
        rather than a sample being chosen."""
        with pytest.raises(ReservedNameError) as caught:
            ev.evaluate(f"x | {name}", {"x": [1], name: _sample_for(name)})
        assert caught.value.name == name

    @pytest.mark.parametrize("name", NAMES)
    def test_it_collides_in_call_form_on_the_right_of_a_pipe_too(
        self, ev: Evaluator, name: str
    ) -> None:
        """`x | take(2)` is a pipe as much as `x | first` is."""
        with pytest.raises(ReservedNameError) as caught:
            ev.evaluate(f"x | {name}(1)", {"x": [1], name: _sample_for(name)})
        assert caught.value.name == name

    def test_a_collision_further_along_a_chain_is_found(self, ev: Evaluator) -> None:
        with pytest.raises(ReservedNameError) as caught:
            ev.evaluate("x | where(_ > 0) | last", {"x": [1, 2], "last": 9})
        assert caught.value.name == "last"

    def test_a_collision_inside_a_predicate_is_found(self, ev: Evaluator) -> None:
        context = {"rows": [{"n": [1, 2]}], "first": 9}
        with pytest.raises(ReservedNameError) as caught:
            ev.evaluate("rows | where(_.n | first)", context)
        assert caught.value.name == "first"

    def test_the_first_collision_in_source_order_is_the_one_reported(self, ev: Evaluator) -> None:
        """A stack-based walk finds them in an order that is deterministic but not the reading
        order, and being told about the second mistake first is a small, avoidable confusion."""
        context = {"x": [[1, 2]], "first": 9, "last": 8}
        with pytest.raises(ReservedNameError) as caught:
            ev.evaluate("x | first | last", context)
        assert caught.value.name == "first"


class TestNothingElseIsRefused:
    """The false alarms a blanket rule would raise, each one a realistic expression."""

    def test_a_bare_name_reads_the_context_and_always_did(self, ev: Evaluator) -> None:
        assert ev.evaluate("first", {"first": 2}) == 2
        assert ev.evaluate("min + max", {"min": 1, "max": 2}) == 3

    def test_a_colliding_name_inside_a_predicate_is_fine(self, ev: Evaluator) -> None:
        """**The expression a blanket rule would break.** Thresholds called `min` and `max` are
        ordinary, and reading them by name is unambiguous."""
        context = {"metrics": [{"value": 5}, {"value": 20}], "min": 10}
        assert ev.evaluate("metrics | where(_.value > min) | len", context) == 1

    def test_a_written_call_is_unambiguous_because_context_values_cannot_be_called(
        self, ev: Evaluator
    ) -> None:
        """There is no second reading to be confused with: F3 means a callable in the context is
        a value and nothing more, so `first(x)` can only ever mean the function."""
        assert ev.evaluate("first(items)", {"items": [9], "first": 2}) == 9

    def test_a_collision_the_expression_never_uses_is_not_an_error(self, ev: Evaluator) -> None:
        """`default` is a very ordinary configuration key. An expression that never pipes onto it
        has nothing wrong with it."""
        assert ev.evaluate("a == b", {"a": 1, "b": 1, "default": "x", "min": 0}) is True

    def test_ordinary_bitwise_or_between_two_context_values(self, ev: Evaluator) -> None:
        assert ev.evaluate("a | b", {"a": 4, "b": 1}) == 5

    def test_bitor_is_the_way_out(self, ev: Evaluator) -> None:
        """The escape hatch the message points at, for the author who genuinely meant bitwise
        or on a value that shares a function's name."""
        assert ev.evaluate("bitor(flags, first)", {"flags": 5, "first": 2}) == 7

    def test_an_evaluator_with_no_registry_has_nothing_to_collide_with(self) -> None:
        assert Evaluator().evaluate("flags | first", {"flags": 5, "first": 2}) == 7

    def test_a_host_registry_that_avoids_the_name_is_unaffected(self) -> None:
        registry = standard_registry()
        del registry["first"]
        assert Evaluator(registry=registry).evaluate("flags | first", {"flags": 5, "first": 2}) == 7

    def test_no_context_at_all(self, ev: Evaluator) -> None:
        assert ev.evaluate('"a" | upper', {}) == "A"


class TestTheError:
    def test_it_is_a_safeexpr_error_but_deliberately_not_an_evaluation_error(self) -> None:
        """It is not the expression author's mistake. The rule is correct and the expression is
        well-formed; the host's data and the host's registry both claim a name, and only the host
        can fix it. A caller catching `EvaluationError` to report "your rule is wrong" would be
        blaming the wrong person."""
        with pytest.raises(SafeExprError) as caught:
            EV.evaluate("flags | first", {"flags": 5, "first": 2})
        assert isinstance(caught.value, ReservedNameError)
        assert not isinstance(caught.value, EvaluationError)

    def test_the_message_names_the_key_and_the_way_out(self) -> None:
        with pytest.raises(ReservedNameError) as caught:
            EV.evaluate("flags | first", {"flags": 5, "first": 2})
        message = str(caught.value)
        assert "`first`" in message
        assert "rename" in message
        assert "bitor" in message

    def test_it_points_at_the_colliding_name(self) -> None:
        """At the name rather than at the operator, because `x | first | last` nests as
        `(x | first) | last` and both of those start at the same column, so the operator's
        position cannot tell them apart."""
        source = "1 + (flags | first)"
        with pytest.raises(ReservedNameError) as caught:
            EV.evaluate(source, {"flags": 5, "first": 2})
        assert caught.value.lineno == 1
        assert source[caught.value.offset - 1 :].startswith("first")
        assert "^" in caught.value.annotated()

    def test_it_carries_no_cause_and_no_context_and_no_data(self) -> None:
        """F9 applies to this error like every other one."""
        with pytest.raises(ReservedNameError) as caught:
            EV.evaluate("flags | first", {"flags": 5, "first": "sk-live-SECRET"})
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "sk-live" not in str(caught.value)


class TestItHappensBeforeAnythingRuns:
    def test_nothing_is_evaluated_first(self) -> None:
        """ "Before any evaluation" is the card's wording, and it matters: a shadowed pipe must
        not run half a pipeline and then complain."""
        seen: list[Any] = []
        registry = standard_registry()
        registry["tick"] = lambda value: (seen.append(value), value)[1]
        evaluator = Evaluator(registry=registry)
        with pytest.raises(ReservedNameError):
            evaluator.evaluate("tick(x) | first", {"x": [1], "first": 2})
        assert seen == []

    def test_it_is_reported_ahead_of_an_unrelated_validation_error(self) -> None:
        """The check runs before validation, because after the rewrite `x | first` and
        `first(x)` are the same tree and the collision is no longer visible."""
        with pytest.raises(ReservedNameError):
            EV.evaluate("(x | first) and lambda_is_invalid.__class__", {"x": [1], "first": 2})

    def test_a_budget_of_one_does_not_hide_it(self) -> None:
        """The check is not paid for out of the step budget, so an exhausted budget cannot mask
        a configuration problem."""
        evaluator = Evaluator(registry=standard_registry(), budget=1)
        with pytest.raises(ReservedNameError):
            evaluator.evaluate("flags | first", {"flags": 5, "first": 2})


class TestProperties:
    @given(name=st.sampled_from(NAMES), other=st.sampled_from(["a", "b", "value", "rows"]))
    @settings(max_examples=200, deadline=None)
    def test_a_pipe_onto_a_colliding_name_always_refuses(self, name: str, other: str) -> None:
        with pytest.raises(ReservedNameError):
            EV.evaluate(f"{other} | {name}", {other: [1], name: 1})

    @given(name=st.sampled_from(NAMES))
    @settings(max_examples=200, deadline=None)
    def test_the_same_expression_without_the_colliding_key_never_refuses_for_this_reason(
        self, name: str
    ) -> None:
        try:
            EV.evaluate(f"x | {name}", {"x": [1]})
        except ReservedNameError:  # pragma: no cover - would be the failure
            pytest.fail(f"{name} refused with no colliding key in the context")
        except SafeExprError:
            pass

    @given(name=st.sampled_from(NAMES))
    @settings(max_examples=200, deadline=None)
    def test_reading_a_colliding_key_by_name_always_works(self, name: str) -> None:
        assert EV.evaluate(name, {name: 42}) == 42


class TestRegressions:
    def test_regression_reserved_a_shadowed_pipe_no_longer_answers_silently(self) -> None:
        """The whole card. `values | min` against a context with its own `min` returned the
        registry's answer and said nothing, so a host could ship a rule that read a key it never
        actually read."""
        with pytest.raises(ReservedNameError):
            EV.evaluate("values | min", {"values": [3, 1], "min": 99})

    def test_regression_reserved_the_check_does_not_consult_the_context_to_decide_the_rewrite(
        self,
    ) -> None:
        """The property the pipe rule rests on: registry membership decides pipe from bitwise-or,
        and the context never changes that decision. Reporting a collision is not the same as
        deciding differently because of one, and this pins the difference: with `first` out of the
        registry the same source is bitwise or under every context."""
        registry = standard_registry()
        del registry["first"]
        evaluator = Evaluator(registry=registry)
        assert evaluator.evaluate("flags | first", {"flags": 5, "first": 2}) == 7
        assert evaluator.evaluate("flags | first", {"flags": 5, "first": 99}) == 103

    def test_regression_reserved_a_realistic_threshold_context_is_not_broken(self) -> None:
        """A blanket "reject any collision" rule would refuse this, and it is correct. `min` and
        `max` as threshold keys are ordinary, and reading them by name is unambiguous."""
        context = {"metrics": [{"v": 5}, {"v": 20}], "min": 10, "max": 30}
        assert EV.evaluate("metrics | where(_.v > min) | where(_.v < max) | len", context) == 1
