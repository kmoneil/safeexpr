"""The collections tier.

Two things are being tested here and they are not the same thing. Most of the file is ordinary
behaviour: `where` filters, `merge` combines, `take` takes. The rest is the card's review gate,
which is a *structural* claim rather than a behavioural one: no entry in this tier performs
runtime reflection, and that is asserted by parsing the module rather than by trusting the review
that put it there.

The gate matters more than it looks. F1 is the single most important lesson from the competitive
scan: a static AST allowlist cannot see an attribute lookup a *function* performs at runtime, so
one convenient `format` or `getattr` in a registry entry reopens the climb the whole validator
exists to close. A test that reads the source is the only kind that keeps holding once somebody
adds a tier without reading this comment.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, SafeExprError, standard_registry
from safeexpr._collections import COLLECTIONS

# A module-level evaluator for the generated tests. Hypothesis refuses a function-scoped fixture,
# and rightly: the fixture would be rebuilt per example and the cost would swamp the test.
EV = Evaluator(registry=standard_registry())

ROWS = [
    {"name": "a", "team": "red", "score": 3},
    {"name": "b", "team": "blue", "score": 1},
    {"name": "c", "team": "red", "score": 2},
]


@pytest.fixture
def ev() -> Evaluator:
    return Evaluator(registry=standard_registry())


@pytest.fixture
def counted() -> tuple[Evaluator, list[Any]]:
    """An evaluator with a `tick` function that records every value it sees.

    The only way to observe *how many times* an expression ran, which is what the laziness and
    short-circuit claims are actually about.
    """
    seen: list[Any] = []

    def tick(value: Any) -> Any:
        seen.append(value)
        return value

    registry = standard_registry()
    registry["tick"] = tick
    return Evaluator(registry=registry), seen


class TestWhereAndMap:
    def test_where_keeps_the_matching_items(self, ev: Evaluator) -> None:
        assert ev.evaluate("rows | where(_.score > 1)", {"rows": ROWS}) == [ROWS[0], ROWS[2]]

    def test_where_keeps_nothing_when_nothing_matches(self, ev: Evaluator) -> None:
        assert ev.evaluate("rows | where(_.score > 99)", {"rows": ROWS}) == []

    def test_map_evaluates_the_expression_per_item(self, ev: Evaluator) -> None:
        assert ev.evaluate("rows | map(_.name)", {"rows": ROWS}) == ["a", "b", "c"]

    def test_map_can_build_new_records(self, ev: Evaluator) -> None:
        result = ev.evaluate('rows | map({"n": _.name})', {"rows": ROWS})
        assert result == [{"n": "a"}, {"n": "b"}, {"n": "c"}]

    def test_the_predicate_runs_once_per_item_and_no_more(
        self, counted: tuple[Evaluator, list[Any]]
    ) -> None:
        evaluator, seen = counted
        evaluator.evaluate("items | where(tick(_) > 1)", {"items": [1, 2, 3]})
        assert seen == [1, 2, 3]

    def test_where_does_not_modify_the_list_it_was_given(self, ev: Evaluator) -> None:
        rows = list(ROWS)
        ev.evaluate("rows | where(_.score > 1)", {"rows": rows})
        assert rows == ROWS


class TestExtend:
    def test_it_concatenates(self, ev: Evaluator) -> None:
        assert ev.evaluate("a | extend(b)", {"a": [1, 2], "b": [3]}) == [1, 2, 3]

    def test_it_leaves_both_inputs_alone(self, ev: Evaluator) -> None:
        left, right = [1, 2], [3]
        ev.evaluate("a | extend(b)", {"a": left, "b": right})
        assert (left, right) == ([1, 2], [3])

    def test_a_tuple_is_a_collection_too(self, ev: Evaluator) -> None:
        assert ev.evaluate("a | extend(b)", {"a": (1,), "b": [2]}) == [1, 2]


class TestGroupBy:
    def test_it_returns_group_records_rather_than_a_mapping(self, ev: Evaluator) -> None:
        """The shape canonical use case 4 needs: `map` over the result must see `.key` and
        `.items`, which a mapping of key to items cannot provide."""
        result = ev.evaluate("rows | group_by(_.team)", {"rows": ROWS})
        assert result == [
            {"key": "red", "items": [ROWS[0], ROWS[2]]},
            {"key": "blue", "items": [ROWS[1]]},
        ]

    def test_groups_come_back_in_first_appearance_order(self, ev: Evaluator) -> None:
        result = ev.evaluate("rows | group_by(_.team) | pluck('key')", {"rows": ROWS})
        assert result == ["red", "blue"]

    def test_items_keep_their_order_within_a_group(self, ev: Evaluator) -> None:
        result = ev.evaluate("rows | group_by(_.team) | first", {"rows": ROWS})
        assert result["items"] == [ROWS[0], ROWS[2]]

    def test_a_key_that_cannot_be_hashed_is_a_clear_error(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("rows | group_by(_)", {"rows": ROWS})
        assert "cannot be one" in str(caught.value)

    def test_grouping_an_empty_list_gives_no_groups(self, ev: Evaluator) -> None:
        assert ev.evaluate("rows | group_by(_.team)", {"rows": []}) == []


class TestUniqueBy:
    def test_the_first_item_for_each_key_wins(self, ev: Evaluator) -> None:
        result = ev.evaluate("rows | unique_by(_.team) | map(_.name)", {"rows": ROWS})
        assert result == ["a", "b"]

    def test_order_of_first_appearance_is_kept(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | unique_by(_)", {"items": [3, 1, 3, 2, 1]}) == [3, 1, 2]


class TestSortBy:
    def test_ascending_by_default(self, ev: Evaluator) -> None:
        assert ev.evaluate("rows | sort_by(_.score) | map(_.name)", {"rows": ROWS}) == [
            "b",
            "c",
            "a",
        ]

    def test_descending_when_asked(self, ev: Evaluator) -> None:
        result = ev.evaluate("rows | sort_by(_.score, True) | map(_.name)", {"rows": ROWS})
        assert result == ["a", "c", "b"]

    @pytest.mark.parametrize("descending", ["False", "True"])
    def test_equal_keys_keep_their_original_order_in_both_directions(
        self, ev: Evaluator, descending: str
    ) -> None:
        """`sort_by(_.score, True) | take(3)` has to give the same three rows every run, and it
        only does if reversing preserves stability rather than reversing ties as well."""
        rows = [{"n": name, "k": 0} for name in "abcd"]
        result = ev.evaluate(f"rows | sort_by(_.k, {descending}) | map(_.n)", {"rows": rows})
        assert result == ["a", "b", "c", "d"]

    def test_the_key_is_evaluated_once_per_item_not_once_per_comparison(
        self, counted: tuple[Evaluator, list[Any]]
    ) -> None:
        evaluator, seen = counted
        evaluator.evaluate("items | sort_by(tick(_))", {"items": [3, 1, 2]})
        assert seen == [3, 1, 2]

    def test_keys_that_cannot_be_ordered_are_a_clear_error(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | sort_by(_)", {"items": [1, "a"]})
        assert "not all comparable" in str(caught.value)

    def test_it_does_not_reorder_the_list_it_was_given(self, ev: Evaluator) -> None:
        items = [3, 1, 2]
        ev.evaluate("items | sort_by(_)", {"items": items})
        assert items == [3, 1, 2]


class TestPluck:
    def test_it_reads_one_field_from_every_item(self, ev: Evaluator) -> None:
        assert ev.evaluate('rows | pluck("name")', {"rows": ROWS}) == ["a", "b", "c"]

    def test_the_field_name_can_come_from_the_context(self, ev: Evaluator) -> None:
        """What `pluck` has that `map(_.name)` does not: the column is a value, so a host can
        drive the same rule over a configured field."""
        assert ev.evaluate("rows | pluck(field)", {"rows": ROWS, "field": "team"}) == [
            "red",
            "blue",
            "red",
        ]

    @pytest.mark.parametrize("field", ["__class__", "_private", "__mro__"])
    def test_an_underscore_field_is_blocked_even_when_written_as_a_literal(
        self, ev: Evaluator, field: str
    ) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(f'rows | pluck("{field}")', {"rows": [{field: "REACHED"}]})
        assert "underscore" in str(caught.value)
        assert "REACHED" not in str(caught.value)

    def test_an_underscore_field_arriving_as_a_value_is_blocked_too(self, ev: Evaluator) -> None:
        """The case the validator structurally cannot catch: it never sees this string."""
        context = {"rows": [{"__class__": "REACHED"}], "field": "__class__"}
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("rows | pluck(field)", context)
        assert "underscore" in str(caught.value)

    def test_a_field_name_that_is_not_text_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("rows | pluck(1)", {"rows": ROWS})
        assert "field name as text" in str(caught.value)

    def test_a_missing_field_is_named(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('rows | pluck("nope")', {"rows": ROWS})
        assert '"nope"' in str(caught.value)

    def test_items_that_are_not_mappings_are_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('rows | pluck("name")', {"rows": [1, 2]})
        assert "list of mappings" in str(caught.value)


class TestMaxByAndMinBy:
    def test_they_return_the_item_not_the_key(self, ev: Evaluator) -> None:
        assert ev.evaluate("rows | max_by(_.score)", {"rows": ROWS}) == ROWS[0]
        assert ev.evaluate("rows | min_by(_.score)", {"rows": ROWS}) == ROWS[1]

    def test_a_tie_goes_to_the_first_item(self, ev: Evaluator) -> None:
        rows = [{"n": "a", "k": 1}, {"n": "b", "k": 1}]
        assert ev.evaluate("rows | max_by(_.k)", {"rows": rows}) is rows[0]
        assert ev.evaluate("rows | min_by(_.k)", {"rows": rows}) is rows[0]

    def test_keys_that_cannot_be_compared_are_a_clear_error(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | max_by(_)", {"items": [1, "a"]})
        assert "not all comparable" in str(caught.value)


class TestFirstLastAndTake:
    def test_first_and_last(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | first", {"items": [1, 2, 3]}) == 1
        assert ev.evaluate("items | last", {"items": [1, 2, 3]}) == 3

    def test_take_takes_a_prefix(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | take(2)", {"items": [1, 2, 3]}) == [1, 2]

    def test_take_more_than_there_is_takes_what_there_is(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | take(9)", {"items": [1, 2]}) == [1, 2]

    def test_take_zero(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | take(0)", {"items": [1, 2]}) == []

    def test_a_negative_count_is_refused_rather_than_slicing_from_the_end(
        self, ev: Evaluator
    ) -> None:
        """Python would read `[:-1]` as "all but the last", which is not what anybody typing
        `take(-1)` meant."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | take(-1)", {"items": [1, 2]})
        assert "0 or more" in str(caught.value)

    def test_true_is_not_a_count(self, ev: Evaluator) -> None:
        """`bool` is an `int` in Python, so `take(rows, True)` would silently mean one row."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | take(True)", {"items": [1, 2]})
        assert "whole number" in str(caught.value)


class TestMerge:
    def test_later_keys_win(self, ev: Evaluator) -> None:
        assert ev.evaluate('merge(a, {"x": 2})', {"a": {"x": 1, "y": 1}}) == {"x": 2, "y": 1}

    def test_it_takes_more_than_two(self, ev: Evaluator) -> None:
        result = ev.evaluate('merge({"a": 1}, {"b": 2}, {"c": 3})', {})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_it_does_not_modify_its_inputs(self, ev: Evaluator) -> None:
        """An expression that merges a host's record must not edit the host's record."""
        original = {"x": 1}
        ev.evaluate('merge(a, {"x": 2})', {"a": original})
        assert original == {"x": 1}

    def test_it_is_shallow_and_says_so_by_replacing_a_nested_value(self, ev: Evaluator) -> None:
        """The documented boundary. One level is the whole of what JMESPath cannot express, and
        going deeper would need cycle detection over host data, which is not built."""
        result = ev.evaluate('merge(a, {"inner": {"q": 2}})', {"a": {"inner": {"p": 1}}})
        assert result == {"inner": {"q": 2}}

    def test_something_that_is_not_a_mapping_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("merge(a, b)", {"a": {"x": 1}, "b": [1]})
        assert "needs mappings" in str(caught.value)


class TestLenSumMinAndMax:
    @pytest.mark.parametrize(
        ("source", "context", "expected"),
        [
            ("len(items)", {"items": [1, 2, 3]}, 3),
            ("len(text)", {"text": "abcd"}, 4),
            ("len(record)", {"record": {"a": 1}}, 1),
            ("sum(items)", {"items": [1, 2, 3]}, 6),
            ("min(items)", {"items": [3, 1, 2]}, 1),
            ("max(items)", {"items": [3, 1, 2]}, 3),
        ],
    )
    def test_the_aggregates(
        self, ev: Evaluator, source: str, context: dict[str, Any], expected: Any
    ) -> None:
        assert ev.evaluate(source, context) == expected

    def test_len_of_something_with_no_length(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("len(n)", {"n": 5})
        assert "something with a length" in str(caught.value)

    def test_summing_things_that_are_not_numbers(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("sum(items)", {"items": ["a", "b"]})
        assert "list of numbers" in str(caught.value)

    def test_values_that_cannot_be_compared(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("max(items)", {"items": [1, "a"]})
        assert "not all comparable" in str(caught.value)


class TestAnyAndAll:
    def test_with_a_predicate(self, ev: Evaluator) -> None:
        assert ev.evaluate("rows | any_(_.score > 2)", {"rows": ROWS}) is True
        assert ev.evaluate("rows | all_(_.score > 2)", {"rows": ROWS}) is False

    def test_without_a_predicate_they_test_truthiness(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | any_", {"items": [0, 1]}) is True
        assert ev.evaluate("items | any_", {"items": [0, 0]}) is False
        assert ev.evaluate("items | all_", {"items": [1, 2]}) is True

    def test_an_empty_list_is_vacuously_true_for_all_and_false_for_any(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | all_(_ > 1)", {"items": []}) is True
        assert ev.evaluate("items | any_(_ > 1)", {"items": []}) is False

    def test_any_stops_at_the_first_match(self, counted: tuple[Evaluator, list[Any]]) -> None:
        evaluator, seen = counted
        assert evaluator.evaluate("items | any_(tick(_) > 1)", {"items": [5, 1, 2]}) is True
        assert seen == [5]

    def test_all_stops_at_the_first_failure(self, counted: tuple[Evaluator, list[Any]]) -> None:
        evaluator, seen = counted
        assert evaluator.evaluate("items | all_(tick(_) > 1)", {"items": [5, 0, 2]}) is False
        assert seen == [5, 0]


class TestTheEmptyCollectionRule:
    """One rule across the tier, so an author learns it once.

    Canonical use case 2 is `metrics | where(...) | first`, which has to survive matching
    nothing. If an empty collection raised, the ordinary case would be the error case.
    """

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("items | first", None),
            ("items | last", None),
            ("items | min", None),
            ("items | max", None),
            ("items | min_by(_)", None),
            ("items | max_by(_)", None),
            ("items | sum", 0),
            ("items | where(_ > 1)", []),
            ("items | map(_)", []),
            ("items | sort_by(_)", []),
            ("items | unique_by(_)", []),
            ("items | group_by(_)", []),
            ("items | take(3)", []),
            ("items | extend(items)", []),
            ("items | pluck('x')", []),
        ],
    )
    def test_an_empty_list_in(self, ev: Evaluator, source: str, expected: Any) -> None:
        assert ev.evaluate(source, {"items": []}) == expected


class TestOnlyListsAndTuplesAreCollections:
    """Strings iterate over characters and mappings over keys. Both are almost always a mistake
    in a rule, so both are refused with a sentence rather than answered surprisingly."""

    @pytest.mark.parametrize(
        "source",
        [
            "items | where(_ > 1)",
            "items | map(_)",
            "items | first",
            "items | last",
            "items | take(1)",
            "items | sum",
            "items | max",
            "items | any_",
            "items | sort_by(_)",
            "items | group_by(_)",
            "items | unique_by(_)",
            "items | max_by(_)",
            "items | pluck('x')",
            "items | extend(items)",
        ],
    )
    @pytest.mark.parametrize("value", ["abc", {"a": 1}, 7, None])
    def test_it_is_refused_with_a_message(self, ev: Evaluator, source: str, value: Any) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"items": value})
        assert "needs a list" in str(caught.value)

    def test_len_is_the_exception_because_asking_a_string_its_length_is_not_a_mistake(
        self, ev: Evaluator
    ) -> None:
        assert ev.evaluate("len(items)", {"items": "abc"}) == 3


class TestGuardsAgainstHostileData:
    """F4, expr-lang's advisory: builtins that walk user data with no cap.

    Nothing in this tier recurses, but sorting and comparing walk nested values in C on our
    behalf and both give out on input a host can genuinely hold. Unguarded these arrive as
    "internal error ... this is a bug in safeexpr", which is the wrong answer to a legitimate
    complaint about the data. A general guard over host data is still to be written.
    """

    @staticmethod
    def _two_knots() -> list[Any]:
        first: list[Any] = []
        second: list[Any] = []
        first.append(first)
        second.append(second)
        return [first, second]

    @pytest.mark.parametrize("source", ["items | sort_by(_)", "items | max", "items | max_by(_)"])
    def test_self_referential_data_is_reported_not_crashed_into(
        self, ev: Evaluator, source: str
    ) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"items": self._two_knots()})
        assert "refer to themselves" in str(caught.value)
        assert "bug in safeexpr" not in str(caught.value)

    def test_a_key_whose_hash_recurses_is_reported(self, ev: Evaluator) -> None:
        """Host data is arbitrary objects, and an object's `__hash__` is host code."""

        class Knot:
            def __init__(self) -> None:
                self.other: Knot | None = None

            def __hash__(self) -> int:
                return hash(self.other)

            def __eq__(self, other: object) -> bool:
                return self is other

        left, right = Knot(), Knot()
        left.other, right.other = right, left
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | unique_by(_)", {"items": [left]})
        assert "refer to themselves" in str(caught.value)

    def test_an_unhashable_key_is_reported_by_name_of_type_only(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | unique_by(_)", {"items": [{"secret": "sk-live"}]})
        assert "`dict`" in str(caught.value)
        assert "sk-live" not in str(caught.value)


class TestErrorsFromTheTierAreScrubbedAndPositioned:
    """R8 and F9 together. A function knows *what* is wrong; only the evaluator knows *where*,
    and neither may hand the caller a live reference to the data."""

    def test_the_error_points_at_the_call(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("x + len(n)", {"x": 1, "n": 5})
        assert caught.value.offset == 5
        assert "^" in caught.value.annotated()

    @pytest.mark.parametrize(
        ("source", "context"),
        [
            ("items | take(-1)", {"items": [1]}),
            ("items | sort_by(_)", {"items": [1, "a"]}),
            ("items | pluck(1)", {"items": [{"a": 1}]}),
            ("merge(a, b)", {"a": {}, "b": 1}),
        ],
    )
    def test_no_error_carries_a_cause_or_a_context(
        self, ev: Evaluator, source: str, context: dict[str, Any]
    ) -> None:
        with pytest.raises(SafeExprError) as caught:
            ev.evaluate(source, context)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    def test_the_function_name_is_in_the_message(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | take(-1)", {"items": [1]})
        assert "`take`" in str(caught.value)


class TestTheTierIsDeclaredConsistently:
    def test_every_function_the_card_lists_is_registered(self) -> None:
        expected = {
            "where", "map", "extend", "group_by", "unique_by", "sort_by", "pluck", "max_by",
            "min_by", "first", "last", "take", "merge", "len", "sum", "min", "max", "any_",
            "all_",
        }  # fmt: skip
        assert set(COLLECTIONS) == expected

    def test_each_entry_is_registered_under_its_own_name(self) -> None:
        for name, function in COLLECTIONS.items():
            assert function.name == name

    def test_lazy_positions_are_declared_only_where_an_expression_is_taken(self) -> None:
        """The evaluator consumes this declaration, so a wrong one is not a cosmetic error: it
        decides whether an argument is evaluated or handed over as a subtree."""
        takes_an_expression = {
            "where", "map", "group_by", "unique_by", "sort_by", "max_by", "min_by", "any_", "all_",
        }  # fmt: skip
        declared = {name for name, f in COLLECTIONS.items() if f.lazy}
        assert declared == takes_an_expression
        for name in declared:
            assert COLLECTIONS[name].lazy == frozenset({1}), name

    def test_the_dearer_functions_are_priced_above_a_plain_scan(self) -> None:
        """Absolute values are the step budget's to calibrate; the ordering carries the
        meaning."""
        assert COLLECTIONS["sort_by"].cost > COLLECTIONS["group_by"].cost
        assert COLLECTIONS["group_by"].cost > COLLECTIONS["where"].cost
        assert all(function.cost >= 1 for function in COLLECTIONS.values())


# ---------------------------------------------------------------------------
# Generated data, and what the tier must agree with.
# ---------------------------------------------------------------------------

# Small ranges on purpose. Ties, repeats and empty lists are where the interesting disagreements
# live, and wide integers would generate almost none of them.
_ROW = st.fixed_dictionaries(
    {
        "k": st.integers(min_value=-3, max_value=3),
        "g": st.sampled_from(["a", "b", "c"]),
    }
)
_ROWS = st.lists(_ROW, max_size=25)


def _python_unique_by(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    kept = []
    for row in rows:
        if row["g"] not in seen:
            seen.add(row["g"])
            kept.append(row)
    return kept


# Each entry is an expression and the plain Python that must produce the same answer. Written as
# the *obvious* Python rather than as a transcription of the implementation, so a disagreement
# means the tier drifted from what a reader would expect rather than from itself.
EQUIVALENTS: list[tuple[str, Callable[[list[dict[str, Any]]], Any]]] = [
    ("rows | where(_.k > 0)", lambda rows: [r for r in rows if r["k"] > 0]),
    ("rows | where(_.g == 'a')", lambda rows: [r for r in rows if r["g"] == "a"]),
    ("rows | map(_.g)", lambda rows: [r["g"] for r in rows]),
    ("rows | map(_.k + 1)", lambda rows: [r["k"] + 1 for r in rows]),
    ("rows | pluck('g')", lambda rows: [r["g"] for r in rows]),
    ("rows | sort_by(_.k)", lambda rows: sorted(rows, key=lambda r: r["k"])),
    ("rows | sort_by(_.k, True)", lambda rows: sorted(rows, key=lambda r: r["k"], reverse=True)),
    ("rows | unique_by(_.g)", _python_unique_by),
    ("rows | first", lambda rows: rows[0] if rows else None),
    ("rows | last", lambda rows: rows[-1] if rows else None),
    ("rows | take(0)", lambda rows: rows[:0]),
    ("rows | take(3)", lambda rows: rows[:3]),
    ("rows | take(99)", lambda rows: rows[:99]),
    ("rows | extend(rows)", lambda rows: rows + rows),
    ("rows | len", len),
    ("rows | map(_.k) | sum", lambda rows: sum(r["k"] for r in rows)),
    ("rows | map(_.k) | min", lambda rows: min((r["k"] for r in rows), default=None)),
    ("rows | map(_.k) | max", lambda rows: max((r["k"] for r in rows), default=None)),
    ("rows | min_by(_.k)", lambda rows: min(rows, key=lambda r: r["k"], default=None)),
    ("rows | max_by(_.k)", lambda rows: max(rows, key=lambda r: r["k"], default=None)),
    ("rows | any_(_.k > 0)", lambda rows: any(r["k"] > 0 for r in rows)),
    ("rows | all_(_.k > 0)", lambda rows: all(r["k"] > 0 for r in rows)),
    ("rows | map(_.k) | any_", lambda rows: any(r["k"] for r in rows)),
    ("rows | map(_.k) | all_", lambda rows: all(r["k"] for r in rows)),
    (
        "rows | where(_.k > 0) | map(_.g) | unique_by(_)",
        lambda rows: list(dict.fromkeys(r["g"] for r in rows if r["k"] > 0)),
    ),
]


class TestDifferentialAgainstPython:
    """Every function against the plain Python that means the same thing.

    Example-based tests pin the cases somebody thought of. This pins the definition: ties,
    repeats, empty lists and the boundary between "no items" and "one item" are generated rather
    than chosen, and those are where a collection function actually goes wrong.

    `max_by` is the one worth naming. Python's `max` returns the *first* maximal element and so
    does this tier, which is invisible until a generated list produces a tie; without this test
    the tie-breaking rule would be an accident of implementation rather than a decision.
    """

    @pytest.mark.parametrize(
        ("source", "equivalent"), EQUIVALENTS, ids=[source for source, _ in EQUIVALENTS]
    )
    @given(rows=_ROWS)
    @settings(max_examples=150, deadline=None)
    def test_it_agrees_with_python(
        self, source: str, equivalent: Callable[[list[dict[str, Any]]], Any], rows: Any
    ) -> None:
        assert EV.evaluate(source, {"rows": rows}) == equivalent(rows)


class TestPropertiesThatHoldForAnyInput:
    """Invariants, for the functions whose Python equivalent would just restate the code.

    Writing `group_by`'s equivalent as a `setdefault` loop tests that the implementation is the
    implementation. These say what grouping *means* instead: a partition, in a defined order,
    with every item under the right key.
    """

    @given(rows=_ROWS)
    @settings(max_examples=150, deadline=None)
    def test_group_by_partitions_its_input(self, rows: Any) -> None:
        groups = EV.evaluate("rows | group_by(_.g)", {"rows": rows})
        flattened = [item for group in groups for item in group["items"]]
        assert flattened == [row for group in groups for row in rows if row["g"] == group["key"]]
        assert len(flattened) == len(rows)

    @given(rows=_ROWS)
    @settings(max_examples=150, deadline=None)
    def test_every_group_key_is_distinct_and_in_first_appearance_order(self, rows: Any) -> None:
        groups = EV.evaluate("rows | group_by(_.g)", {"rows": rows})
        keys = [group["key"] for group in groups]
        assert len(keys) == len(set(keys))
        assert keys == list(dict.fromkeys(row["g"] for row in rows))

    @given(rows=_ROWS)
    @settings(max_examples=150, deadline=None)
    def test_every_item_in_a_group_carries_that_group_key(self, rows: Any) -> None:
        for group in EV.evaluate("rows | group_by(_.g)", {"rows": rows}):
            assert all(item["g"] == group["key"] for item in group["items"])

    @given(rows=_ROWS)
    @settings(max_examples=150, deadline=None)
    def test_sorting_is_a_permutation_and_is_ordered(self, rows: Any) -> None:
        sorted_rows = EV.evaluate("rows | sort_by(_.k)", {"rows": rows})
        keys = [row["k"] for row in sorted_rows]
        assert keys == sorted(keys)
        assert len(sorted_rows) == len(rows)
        assert sorted(row["k"] for row in sorted_rows) == sorted(row["k"] for row in rows)

    @pytest.mark.parametrize(
        "source",
        ["rows | sort_by(_.k)", "rows | unique_by(_.g)", "rows | take(3)", "rows | where(_.k > 0)"],
    )
    @given(rows=_ROWS)
    @settings(max_examples=100, deadline=None)
    def test_applying_it_twice_is_the_same_as_applying_it_once(
        self, source: str, rows: Any
    ) -> None:
        once = EV.evaluate(source, {"rows": rows})
        twice = EV.evaluate(source, {"rows": once})
        assert once == twice

    @given(rows=_ROWS)
    @settings(max_examples=150, deadline=None)
    def test_a_filter_and_its_negation_partition_the_input(self, rows: Any) -> None:
        kept = EV.evaluate("rows | where(_.k > 0)", {"rows": rows})
        dropped = EV.evaluate("rows | where(not _.k > 0)", {"rows": rows})
        assert len(kept) + len(dropped) == len(rows)

    @given(rows=_ROWS, count=st.integers(min_value=0, max_value=30))
    @settings(max_examples=150, deadline=None)
    def test_take_never_returns_more_than_it_was_asked_for_or_than_exists(
        self, rows: Any, count: int
    ) -> None:
        taken = EV.evaluate("rows | take(n)", {"rows": rows, "n": count})
        assert len(taken) == min(count, len(rows))
        assert taken == rows[:count]

    @given(left=_ROWS, right=_ROWS)
    @settings(max_examples=150, deadline=None)
    def test_merging_two_mappings_gives_the_union_of_their_keys(
        self, left: Any, right: Any
    ) -> None:
        first = {f"k{n}": row["k"] for n, row in enumerate(left)}
        second = {f"k{n}": row["g"] for n, row in enumerate(right)}
        merged = EV.evaluate("merge(a, b)", {"a": first, "b": second})
        assert set(merged) == set(first) | set(second)
        assert all(merged[key] == second[key] for key in second)
        assert all(merged[key] == first[key] for key in first if key not in second)


class TestNothingInTheTierModifiesWhatItWasGiven:
    """A rule that evaluates over a host's data must not edit it.

    Checked by deep-copying the context, evaluating, and comparing: it catches a function that
    sorts in place or writes into a row, which no single behavioural test would notice because
    the returned value would still be right.
    """

    @pytest.mark.parametrize(
        ("source", "equivalent"), EQUIVALENTS, ids=[source for source, _ in EQUIVALENTS]
    )
    @given(rows=_ROWS)
    @settings(max_examples=100, deadline=None)
    def test_the_context_is_unchanged_afterwards(
        self, source: str, equivalent: Callable[[list[dict[str, Any]]], Any], rows: Any
    ) -> None:
        before = copy.deepcopy(rows)
        EV.evaluate(source, {"rows": rows})
        assert rows == before
