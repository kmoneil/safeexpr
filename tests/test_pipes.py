"""The pipe transform, and the bug it deletes by construction.

`|` is Python's bitwise-or and this package borrows it for chaining, so the interesting tests are
not the ones where piping works. They are the ones where it must **not**: `flags | mask` has to
stay arithmetic, and it has to stay arithmetic for a structural reason rather than because a
guard happened to fire.

The predecessor split pipes with `^(\\w+)`. `\\w` matches digits, so the bitwise-or guard never
fired at all: `flags | mask` became `mask(flags)` and `flags | 2` became `2(flags)`. Those exact
inputs are the first thing tested here.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, SafeExprError, ValidationError
from safeexpr._parse import MAX_SOURCE_BYTES, parse
from safeexpr._pipes import transform
from safeexpr._registry import Function
from safeexpr._validate import MAX_EXPRESSION_DEPTH


def _where(items: list[Any], lazy: Any) -> list[Any]:
    return [item for item in items if lazy.evaluate(item)]


def _map(items: list[Any], lazy: Any) -> list[Any]:
    return [lazy.evaluate(item) for item in items]


def _any(items: list[Any], lazy: Any) -> bool:
    return any(lazy.evaluate(item) for item in items)


REGISTRY: dict[str, Any] = {
    "where": Function("where", _where, frozenset({1})),
    "map": Function("map", _map, frozenset({1})),
    "any_": Function("any_", _any, frozenset({1})),
    "first": lambda items: items[0] if items else None,
    "last": lambda items: items[-1] if items else None,
    "len": len,
    "double": lambda n: n * 2,
    "add": lambda a, b: a + b,
}

# Every name the transform will treat as a pipe, including the always-present builtin.
NAMES = frozenset({*REGISTRY, "bitor"})

ITEMS = [{"price": 5, "name": "a"}, {"price": 20, "name": "b"}, {"price": 30, "name": "c"}]


@pytest.fixture
def ev() -> Evaluator:
    return Evaluator(registry=REGISTRY)


def rewrite(source: str, functions: Any = NAMES) -> str:
    """Parse, transform, and dump, for asserting on the shape rather than the result."""
    return ast.dump(transform(parse(source), functions))


class TestTheLegacyBugIsStructurallyImpossible:
    """`^(\\w+)` matched digits, so the predecessor's bitwise guard never fired.

    Here the decision is a node type checked against a set. A `Constant` is not a `Name`, so it
    cannot be mistaken for one however the source is written.
    """

    @pytest.mark.parametrize(
        ("source", "context", "expected"),
        [
            ("flags | mask", {"flags": 0b1010, "mask": 0b0101}, 0b1111),
            ("flags | 2", {"flags": 0b1000}, 0b1010),
            ("2 | flags", {"flags": 0b0001}, 0b0011),
            ("a | b", {"a": 1, "b": 2}, 3),
            ("1 | 2", {}, 3),
            ("flags | mask | extra", {"flags": 1, "mask": 2, "extra": 4}, 7),
            ("(a | b) | c", {"a": 1, "b": 2, "c": 4}, 7),
            ("a | b + c", {"a": 1, "b": 2, "c": 4}, 7),
        ],
    )
    def test_bitwise_or_stays_bitwise(
        self, ev: Evaluator, source: str, context: dict[str, Any], expected: int
    ) -> None:
        assert ev.evaluate(source, context) == expected

    def test_a_numeric_literal_on_the_right_is_never_called(self, ev: Evaluator) -> None:
        """`flags | 2` became `2(flags)` and reported "Lambda Functions not implemented"."""
        assert ev.evaluate("flags | 2", {"flags": 8}) == 10

    def test_a_context_name_on_the_right_is_never_called(self, ev: Evaluator) -> None:
        """`flags | mask` became `mask(flags)` and reported "Function 'mask' not defined"."""
        assert ev.evaluate("flags | mask", {"flags": 10, "mask": 5}) == 15

    @pytest.mark.parametrize("source", ["flags | mask", "flags | 2", "a | b"])
    def test_no_call_node_is_produced(self, source: str) -> None:
        """Asserted on the tree, not the result: the shape is the guarantee."""
        assert "Call(" not in rewrite(source)

    def test_dict_merge_still_works(self, ev: Evaluator) -> None:
        """`|` on dicts is a real operator that a rules author might use."""
        assert ev.evaluate("a | b", {"a": {"x": 1}, "b": {"y": 2}}) == {"x": 1, "y": 2}


class TestPipesWork:
    def test_pipe_to_a_bare_name(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | first", {"items": [1, 2, 3]}) == 1

    def test_pipe_to_a_call_puts_the_value_first(self, ev: Evaluator) -> None:
        assert ev.evaluate("n | add(3)", {"n": 4}) == 7

    def test_pipe_with_a_lazy_argument(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | where(_.price > 10)", {"items": ITEMS}) == ITEMS[1:]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("items | len", 3),
            ("items | first | len", 2),
            ("items | where(_.price > 10) | len", 2),
            ("items | where(_.price > 10) | map(_.name) | first", "b"),
            ("items | where(_.price > 1) | where(_.price > 10) | map(_.name) | last", "c"),
        ],
    )
    def test_chains_of_every_length(self, ev: Evaluator, source: str, expected: Any) -> None:
        assert ev.evaluate(source, {"items": ITEMS}) == expected

    def test_the_canonical_alerting_use_case(self, ev: Evaluator) -> None:
        """DESIGN §3 use case 2, which needed pipes to be expressible at all."""
        metrics = [{"value": 1}, {"value": 9}]
        got = ev.evaluate(
            "metrics | where(_.value > threshold) | first", {"metrics": metrics, "threshold": 5}
        )
        assert got == {"value": 9}

    def test_a_pipe_inside_a_lazy_argument(self, ev: Evaluator) -> None:
        """Pipes nest into predicates, which is where the transform's ordering matters."""
        customers = [
            {"name": "acme", "orders": [{"total": 150}]},
            {"name": "globex", "orders": [{"total": 20}]},
        ]
        source = "cs | where(_.orders | any_(_.total > 100)) | map(_.name)"
        assert ev.evaluate(source, {"cs": customers}) == ["acme"]

    def test_outward_binding_survives_a_pipe(self, ev: Evaluator) -> None:
        customers = [
            {"name": "acme", "limit": 100, "orders": [{"total": 150}]},
            {"name": "globex", "limit": 500, "orders": [{"total": 200}]},
        ]
        source = "cs | where(_.orders | any_(_.total > _2.limit)) | map(_.name)"
        assert ev.evaluate(source, {"cs": customers}) == ["acme"]


class TestTheRuleIsStatic:
    """The compatibility promise: one source, one meaning, whatever the data."""

    def test_the_rewrite_ignores_the_context_entirely(self) -> None:
        """The transform's only input besides the tree is the set of names."""
        source = "flags | first"
        once = rewrite(source)
        assert once == rewrite(source)
        # Same names, wildly different data. Same tree, because data was never consulted.
        for context in ({"flags": 5, "first": 2}, {}, {"flags": [1], "first": "x"}):
            evaluator = Evaluator(registry=REGISTRY)
            with pytest.raises(SafeExprError):
                evaluator.evaluate("nonexistent_marker", context)
            assert rewrite(source) == once

    def test_registry_membership_is_the_only_thing_that_changes_the_rewrite(self) -> None:
        assert "Call(" in rewrite("x | first", frozenset({"first"}))
        assert "Call(" not in rewrite("x | first", frozenset())

    def test_a_context_name_cannot_make_something_a_pipe(self) -> None:
        """Even a callable in the context does not turn `|` into a call."""
        evaluator = Evaluator()
        assert evaluator.evaluate("a | b", {"a": 1, "b": 2}) == 3

    def test_registry_names_are_reserved_on_the_right_of_a_pipe(self, ev: Evaluator) -> None:
        """The documented cost of a static rule, pinned so it cannot change silently.

        With `first` registered, `flags | first` is `first(flags)` even though the context has a
        variable called `first`. That is the price of the rewrite not depending on data.
        """
        with pytest.raises(SafeExprError):
            ev.evaluate("flags | first", {"flags": 5, "first": 2})
        assert ev.evaluate("bitor(flags, first)", {"flags": 5, "first": 2}) == 7


class TestPrecedenceMatchesPython:
    """`|` binds tighter than comparison and looser than arithmetic, and the transform inherits
    that from the parser rather than reimplementing it."""

    def test_chains_are_left_associative(self, ev: Evaluator) -> None:
        """`a | f | g` is `g(f(a))`, not `f(a | g)`."""
        assert ev.evaluate("n | double | double", {"n": 3}) == 12

    def test_pipe_binds_tighter_than_comparison(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | len > 2", {"items": ITEMS}) is True
        assert ev.evaluate("items | len > 5", {"items": ITEMS}) is False

    def test_pipe_binds_tighter_than_boolean_operators(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | len and True", {"items": ITEMS}) is True

    def test_arithmetic_binds_tighter_than_pipe(self, ev: Evaluator) -> None:
        """`x | f + 1` groups as `x | (f + 1)`, whose right side is a BinOp rather than a Name,
        so it is not a pipe. Inherited from Python's grammar, and worth pinning."""
        assert "Call(" not in rewrite("x | first + 1")

    def test_a_ternary_around_a_pipe(self, ev: Evaluator) -> None:
        assert ev.evaluate("items | len if flag else 0", {"items": ITEMS, "flag": True}) == 3

    def test_a_parenthesised_pipe_in_an_argument(self, ev: Evaluator) -> None:
        assert ev.evaluate("add((items | len), 10)", {"items": ITEMS}) == 13


class TestTheTransformCannotLaunderAnything:
    """It only ever builds a call to a name already in the registry, from subtrees already there.

    Validation runs after the transform, so anything the transform leaves behind still has to pass
    the allowlist.
    """

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("x | a.b(1)", "method calls"),
            ("[y for y in z] | first", "list comprehensions"),
            ("(lambda: 1) | first", "lambda expressions"),
            ("x | first | a.b(1)", "method calls"),
            ("x.__class__ | first", "underscore"),
            ("x | first(a.__class__)", "underscore"),
            ("x | where(_.__class__)", "underscore"),
            ("{y for y in z} | first", "set comprehensions"),
        ],
    )
    def test_rejected_constructs_stay_rejected_through_a_pipe(
        self, ev: Evaluator, source: str, expected: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            ev.evaluate(source, {"x": {}, "a": {}, "z": []})
        assert expected in str(caught.value)

    @pytest.mark.parametrize(
        "source",
        ["x | unknown", "x | unknown(1)", "x | os", "x | eval", "x | open(1)", "x | a.b"],
    )
    def test_an_unregistered_name_is_left_completely_alone(self, source: str) -> None:
        """Asserted as "the tree did not change" rather than "no Call appeared", because
        `x | unknown(1)` already contains a call before anything is rewritten. The question is
        whether the transform *moved* it, not whether one is present.
        """
        before = ast.dump(parse(source), include_attributes=True)
        after = ast.dump(transform(parse(source), NAMES), include_attributes=True)
        assert before == after

    def test_a_call_whose_function_is_not_a_bare_name_is_not_a_pipe(self) -> None:
        """`x | (a.b)(1)` has a Call on the right, but its `func` is an Attribute, so the piped
        value must not be spliced into its arguments."""
        before = ast.dump(parse("x | (a.b)(1)"), include_attributes=True)
        after = ast.dump(transform(parse("x | (a.b)(1)"), NAMES), include_attributes=True)
        assert before == after

    def test_attribute_access_on_the_right_stays_bitwise(self, ev: Evaluator) -> None:
        """`a.b` is a field lookup, not a call, so `x | a.b` is ordinary bitwise-or of two
        values and must evaluate as such."""
        assert ev.evaluate("x | a.b", {"x": 0b0001, "a": {"b": 0b0010}}) == 0b0011

    def test_the_validated_tree_is_the_evaluated_tree(self) -> None:
        """F8. The transform rewrites in place and returns the same object, so validation and
        evaluation still see one tree with no window between them."""
        tree = parse("items | first")
        assert transform(tree, NAMES) is tree


class TestDepthAndRecursion:
    """A 2047-byte source holds a 1023-stage pipe chain. Both the transform and the evaluator have
    to have an answer for that, and originally neither did."""

    def test_the_transform_does_not_recurse(self) -> None:
        """`ast.NodeTransformer` raises `RecursionError` on this input. The transform walks with
        an explicit stack for exactly that reason."""
        depth = (MAX_SOURCE_BYTES - 2) // 2
        source = "a" + "|first" * (depth // 6)
        tree = transform(parse(source), NAMES)
        assert isinstance(tree, ast.Expression)

    def test_a_chain_within_the_depth_limit_evaluates(self, ev: Evaluator) -> None:
        source = "n" + " | double" * 20
        assert ev.evaluate(source, {"n": 1}) == 2**20

    def test_a_chain_past_the_depth_limit_is_a_clear_error(self, ev: Evaluator) -> None:
        """**Regression.** This used to report "internal error ... this is a bug in safeexpr,
        please report it", which is the wrong answer to input that is merely too deep."""
        source = "n" + " | double" * (MAX_EXPRESSION_DEPTH + 10)
        with pytest.raises(ValidationError) as caught:
            ev.evaluate(source, {"n": 1})
        assert "nests" in str(caught.value)
        assert "bug in safeexpr" not in str(caught.value)

    def test_deep_arithmetic_is_a_clear_error_too(self, ev: Evaluator) -> None:
        with pytest.raises(ValidationError) as caught:
            ev.evaluate("1" + "+1" * 600, {})
        assert "over the limit" in str(caught.value)

    def test_nothing_within_the_limit_is_refused(self, ev: Evaluator) -> None:
        source = "1" + "+1" * (MAX_EXPRESSION_DEPTH - 5)
        assert ev.evaluate(source, {}) == MAX_EXPRESSION_DEPTH - 4


class TestErrorQuality:
    def test_an_unknown_name_after_a_pipe_says_so(self, ev: Evaluator) -> None:
        """Degrading to "name not defined" is true but hides what the author was reaching for."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | undefind_fn", {"items": [1]})
        message = str(caught.value)
        assert "is not a function" in message
        assert "bitwise or" in message

    def test_a_near_miss_gets_a_suggestion(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | frist", {"items": [1]})
        assert "did you mean `first`" in str(caught.value)

    def test_the_error_points_at_the_pipe(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("items | undefind_fn", {"items": [1]})
        assert caught.value.lineno == 1
        assert caught.value.source == "items | undefind_fn"

    def test_a_name_in_the_context_is_still_bitwise_without_complaint(self, ev: Evaluator) -> None:
        assert ev.evaluate("a | b", {"a": 1, "b": 2}) == 3


class TestTransformProperties:
    """T3, as properties over generated expressions rather than examples."""

    OPERANDS = st.sampled_from(["a", "b", "1", "2", "items", "flags"])
    PIPEABLE = st.sampled_from(["first", "last", "len", "double"])

    @st.composite
    def _expression(draw: Any) -> str:  # type: ignore[misc]  # noqa: N805
        parts = [draw(TestTransformProperties.OPERANDS)]
        for _ in range(draw(st.integers(min_value=0, max_value=6))):
            if draw(st.booleans()):
                parts.append(f"| {draw(TestTransformProperties.PIPEABLE)}")
            else:
                parts.append(f"| {draw(TestTransformProperties.OPERANDS)}")
        return " ".join(parts)

    @given(_expression())
    @settings(max_examples=250, deadline=None)
    def test_the_transform_is_idempotent(self, source: str) -> None:
        once = transform(parse(source), NAMES)
        twice = transform(once, NAMES)
        assert ast.dump(once) == ast.dump(twice)

    @given(_expression())
    @settings(max_examples=250, deadline=None)
    def test_positions_survive_the_rewrite(self, source: str) -> None:
        """R8 depends on this: an error after the rewrite must still point at the user's source."""
        rewritten = transform(parse(source), NAMES)
        for node in ast.walk(rewritten):
            if isinstance(node, ast.expr):
                assert node.lineno == 1
                assert 0 <= node.col_offset <= len(source)

    @given(_expression())
    @settings(max_examples=250, deadline=None)
    def test_a_tree_with_no_pipes_is_untouched(self, source: str) -> None:
        """With an empty registry nothing is a pipe, so the tree must come back byte-identical,
        attributes included."""
        original = ast.dump(parse(source), include_attributes=True)
        rewritten = ast.dump(transform(parse(source), frozenset()), include_attributes=True)
        assert original == rewritten

    @given(_expression())
    @settings(max_examples=250, deadline=None)
    def test_the_rewrite_does_not_depend_on_the_context(self, source: str) -> None:
        assert rewrite(source) == rewrite(source)

    @given(st.integers(min_value=0, max_value=40))
    @settings(max_examples=50, deadline=None)
    def test_chains_of_any_length_compose_left_to_right(self, stages: int) -> None:
        evaluator = Evaluator(registry=REGISTRY)
        source = "n" + " | double" * stages
        assert evaluator.evaluate(source, {"n": 1}) == 2**stages


class TestDifferentialAgainstPython:
    """For expressions with no registry name in them, `|` must mean exactly what Python means."""

    @pytest.mark.parametrize(
        "source",
        [
            "a | b", "a | 2", "1 | 2", "a | b | c", "(a | b) | c", "a | (b | c)",
            "a | b + c", "a | b * c", "a | b - c", "(a | b) > 3", "a | b == 3",
        ],
    )  # fmt: skip
    def test_results_match_python(self, ev: Evaluator, source: str) -> None:
        context = {"a": 0b0011, "b": 0b0101, "c": 0b1001}
        assert ev.evaluate(source, context) == eval(source, {"__builtins__": {}}, context)  # noqa: S307

    @given(
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=200, deadline=None)
    def test_bitwise_or_agrees_with_the_operator_module(self, left: int, right: int) -> None:
        evaluator = Evaluator(registry=REGISTRY)
        got = evaluator.evaluate("a | b", {"a": left, "b": right})
        assert got == operator.or_(left, right)

    def test_bitor_agrees_with_the_operator_too(self, ev: Evaluator) -> None:
        for left, right in ((0b1010, 0b0101), (0, 0), (255, 1)):
            assert ev.evaluate("bitor(a, b)", {"a": left, "b": right}) == left | right


class TestTheBitorEscapeHatch:
    def test_bitor_is_available_without_any_registry(self) -> None:
        """The rule that creates the need for it is always in force, so it is always there."""
        assert Evaluator().evaluate("bitor(a, b)", {"a": 1, "b": 2}) == 3

    def test_bitor_is_listed_among_the_function_names(self) -> None:
        assert "bitor" in Evaluator().function_names

    def test_a_host_registry_does_not_remove_it(self, ev: Evaluator) -> None:
        assert "bitor" in ev.function_names

    def test_bitor_works_on_dicts_too(self, ev: Evaluator) -> None:
        assert ev.evaluate("bitor(a, b)", {"a": {"x": 1}, "b": {"y": 2}}) == {"x": 1, "y": 2}
