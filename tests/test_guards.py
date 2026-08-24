"""Guards on the host's data: how deeply it may nest, and what happens when it refers to itself.

F4, straight from expr-lang's advisory: it shipped a denial of service because builtins recursed
over user data with no depth cap. The exposure here is the same and the mechanism is CPython's,
not ours. Nothing in this package recurses over data; comparing and hashing it recurses in C on
our behalf, and the two behave differently in a way that decides the whole design:

- **Comparison raises**, on every supported interpreter, and never crashes. CPython guards it,
  so `RecursionError` arrives on the first cycle or at a depth that depends on the version:
  20,000 on 3.11, 3.12 and 3.13, and 60,000 on 3.14, which raised its limits. That difference is
  why the deep-comparison test asserts what this package promises rather than where the
  interpreter happens to give out.
- **Hashing does not raise. It crashes.** `tuplehash` does not use `Py_EnterRecursiveCall`, so a
  deeply nested tuple exhausts the C stack and takes the interpreter with it. Measured: hashing a
  tuple nested 200,000 deep is an exit-139 segmentation fault, with and without this package in
  the picture. **A `try` cannot catch a crash**, which is why the depth is checked before the
  value ever reaches `hash`.

Measured before this guard existed, four expressions reported "internal error while evaluating
(RecursionError); this is a bug in safeexpr, please report it" for data that was merely deep, and
three crashed the interpreter outright.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, SafeExprError, standard_registry
from safeexpr._eval import _checked_depth, _Run
from safeexpr._guards import (
    MAX_DATA_NESTING,
    MAX_RESULT_SIZE,
    SIZE_CHARGE_UNIT,
    check_depth,
    concatenated_size,
    size_charge,
)
from safeexpr._registry import FunctionError

EV = Evaluator(registry=standard_registry())


def nest_tuple(depth: int) -> Any:
    value: Any = ()
    for _ in range(depth):
        value = (value,)
    return value


def nest_list(depth: int) -> Any:
    value: Any = []
    for _ in range(depth):
        value = [value]
    return value


def cyclic_pair() -> tuple[Any, Any]:
    first: list[Any] = []
    second: list[Any] = []
    first.append(first)
    second.append(second)
    return first, second


@pytest.fixture
def ev() -> Evaluator:
    return EV


class TestEveryPathThatHashes:
    """Hashing is the one operation that crashes rather than raising, so every way the language
    can reach it needs the check in front rather than a handler around it."""

    def test_membership_against_a_set(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a in s", {"a": nest_tuple(5000), "s": {1, 2}})
        assert "nests more than" in str(caught.value)

    def test_membership_against_a_mapping(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError):
            ev.evaluate("a in d", {"a": nest_tuple(5000), "d": {1: "x"}})

    def test_a_subscript_into_a_mapping(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("d[k]", {"d": {1: "x"}, "k": nest_tuple(5000)})
        assert "nests more than" in str(caught.value)

    def test_a_key_in_a_dict_literal(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("{k: 1}", {"k": nest_tuple(5000)})
        assert "nests more than" in str(caught.value)

    @pytest.mark.parametrize("source", ["rows | group_by(_)", "rows | unique_by(_)"])
    def test_a_grouping_key_in_the_collections_tier(self, ev: Evaluator, source: str) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"rows": [nest_tuple(5000)]})
        assert "nests more than" in str(caught.value)

    def test_a_frozenset_nests_the_same_way_a_tuple_does(self, ev: Evaluator) -> None:
        value: Any = frozenset()
        for _ in range(2000):
            value = frozenset({value})
        with pytest.raises(EvaluationError):
            ev.evaluate("a in s", {"a": value, "s": {1}})


class TestEveryPathThatCompares:
    """Comparison raises rather than crashing, so these are handlers. Before them, all four
    reported an internal bug in this package for data that was merely deep."""

    @pytest.mark.parametrize("source", ["a < b", "a == b", "a != b", "a >= b"])
    def test_deeply_nested_values(self, ev: Evaluator, source: str) -> None:
        """**Asserts the promise, not a threshold, and that is not hedging.**

        Where comparison gives out is the interpreter's business and it moves: 3.11, 3.12 and
        3.13 raise at 20,000 levels and **3.14 handles 20,000 and raises at 60,000**, measured.
        A test naming a depth passes on three interpreters and fails on the fourth without
        anything being wrong, which is what the first version of this did.

        What this package promises is narrower and does not move: deep data either compares or
        produces our error. It never reports an internal bug and never takes the interpreter with
        it. No supported interpreter crashes on comparison; all four raise.
        """
        context = {"a": nest_list(20_000), "b": nest_list(20_000)}
        outcome: Any = None
        refused: EvaluationError | None = None
        try:
            outcome = ev.evaluate(source, context)
        except EvaluationError as caught:
            refused = caught
        if refused is not None:
            assert "recursed without end" in str(refused)
            assert "bug in safeexpr" not in str(refused)
        else:
            assert isinstance(outcome, bool)

    @pytest.mark.parametrize("source", ["a < b", "a == b"])
    def test_nesting_past_every_interpreter_s_limit_is_always_refused(
        self, ev: Evaluator, source: str
    ) -> None:
        """Above the highest measured threshold, so every supported interpreter reaches the
        handler. 3.14's is 60,000, the others' is 20,000."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"a": nest_list(120_000), "b": nest_list(120_000)})
        assert "recursed without end" in str(caught.value)
        assert "bug in safeexpr" not in str(caught.value)

    def test_self_referential_values(self, ev: Evaluator) -> None:
        first, second = cyclic_pair()
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a < b", {"a": first, "b": second})
        assert "bug in safeexpr" not in str(caught.value)

    def test_membership_in_a_list_compares_rather_than_hashes(self, ev: Evaluator) -> None:
        """A list has no hashing to do, so this arrives through the comparison path."""
        first, second = cyclic_pair()
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a in items", {"a": first, "items": [second]})
        assert "bug in safeexpr" not in str(caught.value)

    def test_adding_two_self_referential_lists_is_shallow_and_simply_works(
        self, ev: Evaluator
    ) -> None:
        """Worth pinning rather than assuming. Concatenation copies references and never looks
        inside, so a cycle is not a problem for it and refusing one would be a false alarm."""
        first, second = cyclic_pair()
        assert len(ev.evaluate("a + b", {"a": first, "b": second})) == 2

    def test_an_operator_whose_own_code_recurses_without_end(self, ev: Evaluator) -> None:
        """The reachable way an arithmetic operator gives out. Concatenation cannot recurse, so
        this is about the objects a host puts in a context: the evaluator calls their `__add__`,
        and that is host code."""

        class Recurses:
            def __add__(self, other: object) -> object:
                return self + other

        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a + b", {"a": Recurses(), "b": 1})
        assert "recursed without end" in str(caught.value)
        assert "bug in safeexpr" not in str(caught.value)

    def test_a_comparison_whose_own_code_recurses_without_end(self, ev: Evaluator) -> None:
        """Same shape on the comparison side: `__eq__` is host code too."""

        class Recurses:
            def __eq__(self, other: object) -> bool:
                return self == other

            __hash__ = None  # type: ignore[assignment]

        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a == b", {"a": Recurses(), "b": 1})
        assert "recursed without end" in str(caught.value)
        assert "bug in safeexpr" not in str(caught.value)

    @pytest.mark.parametrize(
        "source", ["rows | sort_by(_)", "rows | max", "rows | min", "rows | max_by(_)"]
    )
    def test_the_collections_tier_reports_the_same_way(self, ev: Evaluator, source: str) -> None:
        first, second = cyclic_pair()
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"rows": [first, second]})
        assert "bug in safeexpr" not in str(caught.value)


class TestTheCardsTwoCriteria:
    def test_depth_ten_thousand_raises_our_error_and_never_a_recursion_error(
        self, ev: Evaluator
    ) -> None:
        """The card's first criterion, at the depth it names."""
        with pytest.raises(SafeExprError) as caught:
            ev.evaluate("a in s", {"a": nest_tuple(10_000), "s": {1}})
        assert not isinstance(caught.value, RecursionError)
        assert "bug in safeexpr" not in str(caught.value)

    def test_a_self_referential_mapping_terminates_with_a_clear_error(self, ev: Evaluator) -> None:
        """The card's second criterion, written as it writes it: `d["self"] = d`."""
        record: dict[str, Any] = {"name": "a"}
        record["self"] = record
        other: dict[str, Any] = {"name": "a"}
        other["self"] = other
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a == b", {"a": record, "b": other})
        assert "nest too deeply or refer to themselves" in str(caught.value)

    def test_a_self_referential_mapping_can_still_be_read_from(self, ev: Evaluator) -> None:
        """Cycles are only a problem for operations that walk. Reading a field is not one."""
        record: dict[str, Any] = {"name": "a"}
        record["self"] = record
        assert ev.evaluate("d.self.self.name", {"d": record}) == "a"


class TestItNeverCrashesTheInterpreter:
    """The claim a `try/except` cannot make, so it is made by running the crash in a subprocess
    and checking the interpreter came back.

    Without the guard this is an exit-139 segmentation fault. There is no way to assert that from
    inside the process that would be dying.
    """

    @pytest.mark.parametrize(
        "expression",
        [
            "a in s",
            "d[a]",
            "{a: 1}",
            "rows | unique_by(_)",
        ],
    )
    def test_the_paths_that_used_to_segfault(self, expression: str) -> None:
        program = (
            "import sys\n"
            "from safeexpr import Evaluator, standard_registry, SafeExprError\n"
            "deep = ()\n"
            "for _ in range(200_000): deep = (deep,)\n"
            "ev = Evaluator(registry=standard_registry())\n"
            "ctx = {'a': deep, 's': {1}, 'd': {1: 2}, 'rows': [deep]}\n"
            "try:\n"
            f"    ev.evaluate({expression!r}, ctx)\n"
            "except SafeExprError as exc:\n"
            "    sys.stdout.write('REFUSED')\n"
        )
        finished = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert finished.returncode == 0, (
            f"interpreter exited {finished.returncode}; -11 is a segmentation fault, which is "
            f"what this guard exists to prevent"
        )
        assert finished.stdout == "REFUSED"


class TestTheCheapPathStaysCheap:
    """The guard runs on every membership test, subscript and dict key in the language, so it has
    to cost nothing for the values a rule actually uses."""

    @pytest.mark.parametrize(
        "value", [1, "text", 1.5, True, None, b"bytes", (1, 2, 3), frozenset({1, 2})]
    )
    def test_a_scalar_or_shallow_value_passes_straight_through(self, value: Any) -> None:
        check_depth(value)

    @pytest.mark.parametrize("value", [[1, [2, [3]]], {"a": {"b": {"c": 1}}}, {1, 2}])
    def test_a_container_hash_cannot_recurse_through_is_not_walked(self, value: Any) -> None:
        """A list or a dict inside a tuple makes the whole tuple unhashable, so `hash` stops
        there and never goes deeper. Walking them would be work for nothing."""
        check_depth(value)

    def test_the_evaluators_wrapper_repeats_the_test_so_it_is_safe_to_call_anywhere(self) -> None:
        """Every call site tests `HASHABLE_CONTAINERS` before calling, so this backstop never
        fires today. It stays, and is tested directly, because a guard that only works when the
        caller remembers to guard it is not a guard."""
        run = _Run({}, "x", 100)
        node = ast.parse("x", mode="eval").body
        for value in (1, "text", None, [1, 2]):
            _checked_depth(run, node, value)

    def test_ordinary_expressions_are_unaffected(self, ev: Evaluator) -> None:
        assert ev.evaluate("k in s", {"k": 2, "s": {1, 2}}) is True
        assert ev.evaluate("d[k]", {"d": {"a": 1}, "k": "a"}) == 1
        assert ev.evaluate('{"a": 1}.a', {}) == 1
        assert ev.evaluate("rows | group_by(_.g) | len", {"rows": [{"g": "x"}, {"g": "x"}]}) == 1


class TestTheCapItself:
    def test_exactly_at_the_cap_is_allowed(self) -> None:
        check_depth(nest_tuple(MAX_DATA_NESTING))

    def test_one_past_the_cap_is_refused(self) -> None:
        with pytest.raises(FunctionError, match="nests more than"):
            check_depth(nest_tuple(MAX_DATA_NESTING + 1))

    def test_the_cap_is_far_below_where_comparison_gives_out(self) -> None:
        """Measured: comparison is fine at 5,000 and raises at 10,000. The cap is a fifth of the
        first of those, because the available stack is not ours alone; it depends on how deep the
        host's own stack already is when it calls us."""
        assert MAX_DATA_NESTING == 1_000

    def test_a_wide_graph_of_shared_values_is_refused_before_it_explodes(self) -> None:
        """**The case the depth cap alone does not see.** Shared structure makes a graph rather
        than a tree, and a diamond doubles the node count per level: thirty levels of `(x, x)` is
        a billion values to visit from a structure holding thirty objects, and no path in it is
        deeper than thirty."""
        value: Any = ()
        for _ in range(40):
            value = (value, value)
        with pytest.raises(FunctionError, match="too large to check"):
            check_depth(value)

    def test_sharing_that_does_not_explode_is_allowed(self) -> None:
        """A value appearing twice is not a cycle and must not be treated as one."""
        shared = (1, 2, 3)
        check_depth((shared, shared, shared))


class TestProperties:
    @given(depth=st.integers(min_value=0, max_value=MAX_DATA_NESTING))
    @settings(max_examples=50, deadline=None)
    def test_anything_within_the_cap_is_allowed(self, depth: int) -> None:
        check_depth(nest_tuple(depth))

    @given(depth=st.integers(min_value=MAX_DATA_NESTING + 1, max_value=MAX_DATA_NESTING * 8))
    @settings(max_examples=25, deadline=None)
    def test_anything_past_the_cap_is_refused(self, depth: int) -> None:
        with pytest.raises(FunctionError):
            check_depth(nest_tuple(depth))

    @given(
        value=st.recursive(
            st.one_of(st.integers(), st.text(max_size=5), st.booleans(), st.none()),
            lambda inner: st.tuples(inner, inner),
            max_leaves=30,
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_a_value_the_guard_allows_can_always_be_hashed(self, value: Any) -> None:
        """The guard's whole purpose: what it lets through must survive `hash`."""
        check_depth(value)
        hash(value)


class TestRegressions:
    @pytest.mark.parametrize(
        ("source", "context_key"),
        [("a < b", "pair"), ("a == b", "pair"), ("a in items", "member")],
    )
    def test_regression_guards_deep_data_was_reported_as_a_bug_in_this_package(
        self, source: str, context_key: str
    ) -> None:
        """Measured before the guard: all three produced "internal error while evaluating
        (RecursionError); this is a bug in safeexpr, please report it".

        That is the wrong answer twice over. It tells the expression author to file a bug against
        a package that is working correctly, and it tells them nothing about the data that
        actually caused it.
        """
        first, second = cyclic_pair()
        context = (
            {"a": first, "b": second} if context_key == "pair" else {"a": first, "items": [second]}
        )
        with pytest.raises(EvaluationError) as caught:
            EV.evaluate(source, context)
        assert "bug in safeexpr" not in str(caught.value)
        assert "report it" not in str(caught.value)

    def test_regression_guards_hashing_is_checked_before_it_happens_not_after(self) -> None:
        """A handler around `hash` cannot help, because the failure is a crash rather than an
        exception. Asserted by handing the guard a value whose depth is past the cap and checking
        it is refused without anything hashing it."""

        class Counted(tuple):  # type: ignore[type-arg]
            def __hash__(self) -> int:  # pragma: no cover - must never be called
                raise AssertionError("the value was hashed before its depth was checked")

        value: Any = Counted()
        for _ in range(MAX_DATA_NESTING + 5):
            value = (value,)
        with pytest.raises(EvaluationError):
            EV.evaluate("a in s", {"a": value, "s": {1}})


class TestWhatProducingAValueCosts:
    """The memory-amplification policy's arithmetic, on its own.

    The step budget counts nodes evaluated, and a node that allocates is one node however much it
    allocates. Charging by the size of what a node produced is what folds memory into the budget,
    and the unit is chosen so ordinary values cost nothing at all.
    """

    @pytest.mark.parametrize("value", [1, 1.5, True, None, object()])
    def test_something_with_no_length_costs_nothing(self, value: Any) -> None:
        assert size_charge(value) == 0

    @pytest.mark.parametrize(
        "value", ["", "x", "x" * (SIZE_CHARGE_UNIT - 1), [], [1, 2, 3], {"a": 1}, (1,), {1, 2}]
    )
    def test_anything_under_the_unit_costs_nothing(self, value: Any) -> None:
        """**Integer division is the point.** An ordinary rule building short strings and small
        lists pays exactly what it paid before the policy existed."""
        assert size_charge(value) == 0

    @pytest.mark.parametrize("multiple", [1, 2, 10, 1000])
    def test_the_charge_is_one_step_per_unit(self, multiple: int) -> None:
        assert size_charge("x" * (SIZE_CHARGE_UNIT * multiple)) == multiple
        assert size_charge([0] * (SIZE_CHARGE_UNIT * multiple)) == multiple

    def test_the_unit_is_the_documented_one(self) -> None:
        assert SIZE_CHARGE_UNIT == 64


class TestPredictingAConcatenation:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            ("ab", "cde", 5),
            ([1, 2], [3], 3),
            ((1,), (2, 3), 3),
            (b"ab", b"c", 3),
            (bytearray(b"ab"), bytearray(b"c"), 3),
        ],
    )
    def test_it_predicts_the_length(self, left: Any, right: Any, expected: int) -> None:
        assert concatenated_size(left, right) == expected

    @pytest.mark.parametrize(
        ("left", "right"), [(1, 2), ("a", 1), ("a", [1]), ([1], (2,)), ({"a": 1}, {"b": 2})]
    )
    def test_anything_that_is_not_concatenation_predicts_nothing(
        self, left: Any, right: Any
    ) -> None:
        """`+` on two mappings is not concatenation and does not work at all, and `+` on two
        numbers is arithmetic. Neither has a length to predict."""
        assert concatenated_size(left, right) is None

    def test_a_subclass_of_text_still_concatenates(self) -> None:
        """**Written as a walk over families rather than as `type(left) is type(right)`.** An
        identity check on types says a `str` subclass does not concatenate with a `str`, and it
        does. The tiers' reflection gate refuses `type` anyway, which is how this was found."""

        class Label(str):
            __slots__ = ()

        assert concatenated_size(Label("ab"), "c") == 3


class TestTheSingleResultCaps:
    """The per-result half of the policy. It bounds any one allocation; the budget charge bounds
    the total, and neither is enough on its own."""

    @pytest.mark.parametrize(
        "source",
        [
            "a + a",
            "a + a + a + a",
            "a * 2",
            "2 * a",
        ],
    )
    def test_a_result_over_the_cap_is_refused(self, ev: Evaluator, source: str) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"a": [0] * (MAX_RESULT_SIZE // 2 + 1)})
        assert "over the limit" in str(caught.value)

    def test_text_concatenation_is_capped_the_same_way(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a + a", {"a": "x" * (MAX_RESULT_SIZE // 2 + 1)})
        assert "`+` would produce" in str(caught.value)

    def test_ordinary_concatenation_is_untouched(self, ev: Evaluator) -> None:
        assert ev.evaluate("a + b", {"a": "ab", "b": "c"}) == "abc"
        assert ev.evaluate("a + b", {"a": [1], "b": [2]}) == [1, 2]
        assert ev.evaluate("a + b", {"a": 1, "b": 2}) == 3

    def test_extend_is_capped(self, ev: Evaluator) -> None:
        big = [0] * (MAX_RESULT_SIZE // 2 + 1)
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a | extend(a)", {"a": big})
        assert "over the limit" in str(caught.value)

    def test_merge_is_capped(self, ev: Evaluator) -> None:
        first = {n: n for n in range(MAX_RESULT_SIZE // 2 + 1)}
        second = {n + 10**8: n for n in range(MAX_RESULT_SIZE // 2 + 1)}
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("merge(a, b)", {"a": first, "b": second})
        assert "over the limit" in str(caught.value)

    def test_merge_is_checked_as_it_grows_because_keys_overlap(self, ev: Evaluator) -> None:
        """The sum of the inputs is an upper bound, not an answer: merging a mapping with itself
        produces the same size, not twice it."""
        same = {n: n for n in range(MAX_RESULT_SIZE // 2 + 1)}
        assert len(ev.evaluate("merge(a, a)", {"a": same})) == len(same)


class TestAmplificationRegressions:
    def test_regression_memory_concatenation_had_no_cap(self, ev: Evaluator) -> None:
        """`*` was capped and `+` was not, so the same amplification was one character away.
        Measured: `a + a + a + a` on a 200,000-item list produced 800,000 items from four nodes,
        and doubling the input doubled it again with no limit anywhere."""
        big = [0] * 400_000
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("a + a + a + a", {"a": big})
        assert "`+` would produce" in str(caught.value)

    def test_regression_memory_the_aggregate_case_no_cap_could_see(self, ev: Evaluator) -> None:
        """Every string in this is far under the per-result cap and the total was 343 MB.

        A per-result cap is structurally unable to catch it, which is why the budget charges by
        size: the cost accumulates across items where a cap resets on each one.
        """
        text = "x" * 100_000
        assert size_charge(text + text) * 1 < MAX_RESULT_SIZE, "each item is well under the cap"
        with pytest.raises(SafeExprError) as caught:
            ev.evaluate("rows | map(t + t)", {"rows": list(range(4000)), "t": text})
        assert "budget" in str(caught.value)
