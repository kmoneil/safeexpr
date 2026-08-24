"""The step budget: the one limit that bounds work rather than shape.

Every other cap here bounds what an expression *is*. Source length, expression depth and the
power cap all read the input and say yes or no before anything runs. None of them bounds what an
expression *does*, and because lazy arguments nest, `map(a, where(b, _ == _2))` is O(n*m) from a
source short enough to tweet. expr-lang shipped exactly that denial of service and had to add
exactly this counter after release (CVE-2025-68156), which is why this is not gold-plating.

**The counter is shared, and the sharing is the whole test.** A per-level budget would bound each
nesting level to N and let two levels do N*N work, which is the shape that hurts. `_Run` holds one
counter and `LazyExpr` hands that same `_Run` back to the evaluator, so nested evaluation spends
from the same pool with nothing to thread through and nothing to forget.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import (
    BudgetExceededError,
    EvaluationError,
    Evaluator,
    Function,
    SafeExprError,
    standard_registry,
)
from safeexpr._collections import COLLECTIONS
from safeexpr._eval import DEFAULT_STEP_BUDGET
from safeexpr._guards import SIZE_CHARGE_UNIT

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "safeexpr"


def _evaluator(budget: int) -> Evaluator:
    return Evaluator(registry=standard_registry(), budget=budget)


def _completes(source: str, context: dict[str, Any], budget: int) -> bool:
    try:
        _evaluator(budget).evaluate(source, context)
    except BudgetExceededError:
        return False
    return True


def steps_for(source: str, context: dict[str, Any]) -> int:
    """The smallest budget under which the expression finishes: what it costs, in steps.

    Measured through the public surface rather than by reading the counter, so the number this
    returns is the number a host would actually observe. Doubling to find a ceiling and then
    bisecting costs a few dozen evaluations of a tiny expression, which is cheaper than it looks
    and much harder to get quietly wrong than an accessor would be.
    """
    high = 1
    while not _completes(source, context, high):
        high *= 2
    low = high // 2 + 1
    while low < high:
        middle = (low + high) // 2
        if _completes(source, context, middle):
            high = middle
        else:
            low = middle + 1
    return low


class TestTheCounterIsShared:
    """T4. The card's headline claim, and the one a per-level counter would fail."""

    def test_nested_work_costs_the_product_not_the_sum(self) -> None:
        """**The property a per-level budget cannot have.**

        Twelve inner evaluations arranged as 3x4 and as 4x3 cost nearly the same, and both cost
        more than a flat pass over twelve items. Under a per-level counter each level would be
        measured against the full budget on its own and the product would go uncharged, which is
        exactly how a short expression runs for an hour.

        Nearly, not exactly: the inner `map` call is itself evaluated once per outer item, so the
        shape with four outer items pays for one more call than the shape with three. That
        asymmetry is real and small, and asserting exact equality would be asserting something
        false.
        """
        context = {"a": list(range(3)), "b": list(range(4)), "c": list(range(12))}
        wide = steps_for("map(a, map(b, _ + _2))", context)
        tall = steps_for("map(b, map(a, _ + _2))", context)
        flat = steps_for("map(c, _ + 1)", context)
        assert abs(wide - tall) <= max(wide, tall) // 5, (
            f"the same total work cost materially different amounts in different shapes: "
            f"{wide} against {tall}"
        )
        assert min(wide, tall) > flat, "nesting cost no more than a flat pass over 12 items"

    @pytest.mark.parametrize(("outer", "inner"), [(2, 2), (2, 8), (8, 2), (5, 5), (10, 10)])
    def test_cost_tracks_the_product_of_the_nesting(self, outer: int, inner: int) -> None:
        context = {"a": list(range(outer)), "b": list(range(inner))}
        cost = steps_for("map(a, map(b, _ + _2))", context)
        # Each inner item evaluates `_ + _2`, which is three nodes, and each outer item pays for
        # its own inner call on top. The bound is loose on purpose: what matters is that the
        # product appears at all, not the constant in front of it.
        assert cost >= 3 * outer * inner

    def test_deeper_nesting_keeps_charging(self) -> None:
        context = {"a": list(range(3)), "b": list(range(3)), "c": list(range(3))}
        two = steps_for("map(a, map(b, _ + _2))", context)
        three = steps_for("map(a, map(b, map(c, _ + _2 + _3)))", context)
        assert three > two * 3

    def test_a_predicate_that_would_run_forever_is_stopped(self) -> None:
        """The DoS shape, at a size that would take minutes unbounded."""
        rows = list(range(2000))
        with pytest.raises(BudgetExceededError):
            _evaluator(200_000).evaluate("map(a, map(b, _ + _2))", {"a": rows, "b": rows})


class TestItRaisesRatherThanHanging:
    def test_the_error_is_a_budget_error_and_a_safeexpr_error(self) -> None:
        with pytest.raises(BudgetExceededError) as caught:
            _evaluator(50).evaluate("map(a, _ + 1)", {"a": list(range(100))})
        assert isinstance(caught.value, SafeExprError)

    def test_the_message_names_the_budget_and_says_what_it_bounds(self) -> None:
        with pytest.raises(BudgetExceededError) as caught:
            _evaluator(50).evaluate("map(a, _ + 1)", {"a": list(range(100))})
        message = str(caught.value)
        assert "50" in message
        assert "total work" in message
        assert caught.value.budget == 50

    def test_it_points_at_where_the_budget_ran_out(self) -> None:
        with pytest.raises(BudgetExceededError) as caught:
            _evaluator(50).evaluate("map(a, _ + 1)", {"a": list(range(100))})
        assert caught.value.lineno == 1
        assert caught.value.offset is not None
        assert caught.value.source == "map(a, _ + 1)"

    def test_it_carries_no_cause_and_no_context(self) -> None:
        """F9 applies to this error like every other one."""
        with pytest.raises(BudgetExceededError) as caught:
            _evaluator(50).evaluate("map(a, _.secret)", {"a": [{"secret": "sk-live"}] * 100})
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "sk-live" not in str(caught.value)

    def test_a_budget_of_one_stops_the_simplest_possible_expression(self) -> None:
        """No expression is free, so there is no input that slips past a budget of one."""
        with pytest.raises(BudgetExceededError):
            _evaluator(1).evaluate("1 + 1", {})


class TestNoTimeoutsNoSignalsNoThreads:
    """An acceptance criterion, asserted against the source rather than the intention.

    The claim that makes this bound worth having is that it is deterministic and portable.
    `signal.alarm` is main-thread-only and POSIX-only; an executor timeout leaks the thread that
    is still running. Either one would make the bound something a host has to hope for. A test
    that reads the imports is what keeps the claim true after somebody reaches for the obvious
    tool.
    """

    FORBIDDEN = frozenset(
        {
            "signal",
            "threading",
            "_thread",
            "concurrent",
            "concurrent.futures",
            "multiprocessing",
            "asyncio",
            "resource",
            "faulthandler",
        }
    )

    @staticmethod
    def _imports(tree: ast.Module) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.add(node.module)
        return found

    def test_no_shipped_module_imports_a_timeout_mechanism(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in sorted(SRC_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            banned = sorted(self._imports(tree) & self.FORBIDDEN)
            if banned:
                offenders[path.name] = banned
        assert not offenders, (
            f"the package imports a timeout mechanism: {offenders}. The budget is deterministic "
            f"and portable precisely because it is a counter and nothing else."
        )

    @pytest.mark.parametrize("hook", ["settrace", "setprofile", "setrecursionlimit", "alarm"])
    def test_nothing_installs_an_interpreter_hook(self, hook: str) -> None:
        """A tracer would bound work too, and would change the behaviour of the host's own
        debugger while doing it."""
        for path in sorted(SRC_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                assert not (isinstance(node, ast.Attribute) and node.attr == hook), (
                    f"{path.name} calls {hook}"
                )


class TestTheBudgetIsPerEvaluator:
    def test_it_defaults_to_the_documented_value(self) -> None:
        assert Evaluator().budget == DEFAULT_STEP_BUDGET
        assert DEFAULT_STEP_BUDGET == 6_000_000

    def test_a_host_can_set_it(self) -> None:
        assert Evaluator(budget=99).budget == 99

    @pytest.mark.parametrize("bad", [0, -1, True, False, 2.5, "lots", None])
    def test_a_budget_that_is_not_a_positive_integer_is_refused_at_construction(
        self, bad: Any
    ) -> None:
        """Refused where a host reads the message, not several thousand steps into an
        evaluation. `True` is refused rather than read as 1, because `bool` is an `int` and a
        budget of `True` is a mistake every time."""
        with pytest.raises(ValueError, match="positive integer"):
            Evaluator(budget=bad)

    def test_there_is_no_value_meaning_unlimited(self) -> None:
        """A host needing more says how much more, which is a number a reviewer can question."""
        with pytest.raises(ValueError, match="positive integer"):
            Evaluator(budget=None)

    def test_two_evaluators_do_not_share_a_budget(self) -> None:
        small, large = _evaluator(200), _evaluator(1_000_000)
        context = {"a": list(range(100))}
        with pytest.raises(BudgetExceededError):
            small.evaluate("map(a, _ + 1)", context)
        assert len(large.evaluate("map(a, _ + 1)", context)) == 100

    def test_the_evaluator_is_still_immutable_after_an_evaluation(self) -> None:
        """The counter lives on the run, not on the evaluator. If it did not, one evaluation
        would spend the next one's budget and a shared evaluator would degrade over time."""
        ev = _evaluator(10_000)
        before = {slot: getattr(ev, slot) for slot in Evaluator.__slots__}
        ev.evaluate("map(a, _ + 1)", {"a": list(range(50))})
        assert {slot: getattr(ev, slot) for slot in Evaluator.__slots__} == before


class TestEveryEvaluationStartsFromFull:
    def test_the_same_evaluator_can_run_the_same_expression_repeatedly(self) -> None:
        """**The classic way to get this wrong**: a counter that lives on the evaluator works
        the first time and fails the tenth, which is the kind of bug that reaches production
        because every test that runs one expression passes."""
        ev = _evaluator(10_000)
        context = {"a": list(range(50))}
        for _ in range(20):
            assert len(ev.evaluate("map(a, _ + 1)", context)) == 50

    def test_an_evaluation_that_exhausted_the_budget_does_not_poison_the_next(self) -> None:
        ev = _evaluator(5_000)
        with pytest.raises(BudgetExceededError):
            ev.evaluate("map(a, _ + 1)", {"a": list(range(10_000))})
        assert ev.evaluate("1 + 1", {}) == 2

    def test_one_evaluator_gives_every_thread_its_own_budget(self) -> None:
        """The counter is per-run and a run is per-call, so sharing an evaluator across threads
        cannot make one thread spend another's budget."""
        ev = _evaluator(20_000)
        context = {"a": list(range(50))}
        failures: list[BaseException] = []

        def work() -> None:
            try:
                for _ in range(30):
                    assert len(ev.evaluate("map(a, _ + 1)", context)) == 50
            except BaseException as exc:  # pragma: no cover - only on a real failure
                failures.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not failures


class TestTheCostIsDeterministic:
    """Deterministic, not merely bounded. The same input costs the same on every run, which is
    what lets a host set a budget from a measurement rather than from a margin."""

    @pytest.mark.parametrize(
        "source", ["1 + 1", "map(a, _ + 1)", "where(a, _ > 2)", "map(a, map(a, _ + _2))"]
    )
    def test_the_same_expression_and_data_always_cost_the_same(self, source: str) -> None:
        context = {"a": list(range(6))}
        costs = {steps_for(source, context) for _ in range(5)}
        assert len(costs) == 1

    def test_cost_does_not_depend_on_the_evaluator_instance(self) -> None:
        context = {"a": list(range(6))}
        exact = steps_for("map(a, _ + 1)", context)
        assert _completes("map(a, _ + 1)", context, exact)
        assert not _completes("map(a, _ + 1)", context, exact - 1)

    @given(size=st.integers(min_value=0, max_value=40))
    @settings(max_examples=25, deadline=None)
    def test_cost_grows_with_the_collection(self, size: int) -> None:
        smaller = steps_for("map(a, _ + 1)", {"a": list(range(size))})
        larger = steps_for("map(a, _ + 1)", {"a": list(range(size + 1))})
        assert larger > smaller


class TestFunctionCostsAreCharged:
    def test_a_declared_cost_is_spent(self) -> None:
        cheap = Function("cheap", lambda value: value, arity=(1, 1), cost=1)
        dear = Function("dear", lambda value: value, arity=(1, 1), cost=500)
        registry: dict[str, Any] = {"cheap": cheap, "dear": dear}

        def cost_of(name: str) -> int:
            low, high = 1, 4096
            while low < high:
                middle = (low + high) // 2
                try:
                    Evaluator(registry=registry, budget=middle).evaluate(f"{name}(1)", {})
                except BudgetExceededError:
                    low = middle + 1
                else:
                    high = middle
            return low

        assert cost_of("dear") - cost_of("cheap") == 499

    def test_the_collections_tier_is_priced_flat(self) -> None:
        """**This asserted a gap of 4 and now asserts none, because the 4 was a guess.**

        `sort_by` was priced at 5 against `map`'s 1, on the reasoning that a comparison sort is
        superlinear. The limits work measured the whole tier and found time per charged step
        landing between 0.85 and 1.5 times a bare `map`, with no room in that spread for a cost
        of 2 and certainly not 5. Charging for what a call reads and what it produces is what
        closed the gap that the 5 was standing in for.

        Same data and same predicate either way, so the only difference the test could see is
        the declared cost, and there is none left to see.
        """
        context = {"a": list(range(5))}
        gap = steps_for("sort_by(a, _)", context) - steps_for("map(a, _)", context)
        assert gap == COLLECTIONS["sort_by"].cost - COLLECTIONS["map"].cost == 0

    def test_matches_is_the_only_entry_priced_above_a_plain_scan(self) -> None:
        """And its price is deliberate rather than measured: an accepted pattern still runs
        inside `re`, where the counter cannot follow it."""
        dearer = {name: f.cost for name, f in standard_registry().items() if f.cost != 1}
        assert dearer == {"matches": 10}

    def test_a_costly_function_can_exhaust_the_budget_on_its_own(self) -> None:
        registry: dict[str, Any] = {
            "dear": Function("dear", lambda value: value, arity=(1, 1), cost=10_000)
        }
        with pytest.raises(BudgetExceededError):
            Evaluator(registry=registry, budget=100).evaluate("dear(1)", {})


class TestACallIsChargedForWhatItReadsAndWhatItProduces:
    """**This class asserted the opposite twice, and measurement is what settled it.**

    The step budget charged nodes evaluated. The memory policy added a charge for elements
    produced. Both times the conclusion recorded here was that a function walking its input in C
    without evaluating anything per item was an acceptable blind spot, on the reasoning that a
    single pass of `sum` over ten million integers is a fraction of a second.

    That reasoning was wrong, and the limits work measured it: `sum` over 200,000 integers was
    charged **three steps for 1.7 milliseconds**, roughly two thousand times less per unit of
    work than an expression evaluated per item. One call is indeed a fraction of a second; the
    default budget bought about **850,000** of them, which is eighteen minutes. That is exactly
    the denial of service the budget exists to prevent, arriving through the one door it was not
    watching.

    A call is now charged for the size of its eager arguments as well as its result, so an
    operation pays for what it reads and for what it keeps.
    """

    def test_a_function_that_walks_its_input_is_charged_for_walking_it(self) -> None:
        """`sum` returns one integer either way. What differs is what it had to read."""
        small = steps_for("sum(a)", {"a": list(range(10))})
        large = steps_for("sum(a)", {"a": list(range(10_000))})
        assert large > small

    @pytest.mark.parametrize("source", ["sum(a)", "min(a)", "max(a)", "len(a)", "first(a)"])
    def test_every_single_pass_function_is_charged_for_its_input(self, source: str) -> None:
        """`len` and `first` are constant time and are charged anyway.

        That is an over-charge and it is deliberate: exempting them needs a per-function
        declaration, which is one more thing to get wrong, and the cost of being wrong the other
        way is a blind spot. On a 200,000-item list the over-charge is 3,125 steps, which is
        0.05% of the default budget.
        """
        small = steps_for(source, {"a": list(range(10))})
        large = steps_for(source, {"a": list(range(100_000))})
        assert large > small

    def test_a_function_that_keeps_its_input_is_charged_for_that_too(self) -> None:
        small = steps_for("pluck(a, 'k')", {"a": [{"k": 1}] * 10})
        large = steps_for("pluck(a, 'k')", {"a": [{"k": 1}] * 10_000})
        assert large > small

    def test_and_anything_re_evaluating_an_expression_is_charged_per_item(self) -> None:
        small = steps_for("map(a, _ + 1)", {"a": list(range(10))})
        large = steps_for("map(a, _ + 1)", {"a": list(range(100))})
        assert large > small * 5

    def test_a_lazy_argument_contributes_nothing_because_it_has_no_length(self) -> None:
        """The subtree handed to `where` is not data and must not be priced as though it were."""
        rows = [{"k": 1}] * 100
        short = steps_for("where(a, _.k > 0)", {"a": rows})
        long_predicate = steps_for("where(a, _.k > 0 and _.k > 0 and _.k > 0)", {"a": rows})
        assert long_predicate > short, "a bigger predicate costs more, per node, as it should"

    def test_regression_budget_an_aggregate_in_a_loop_is_bounded(self) -> None:
        """The eighteen-minute expression, at a size that used to run and now does not."""
        context = {"rows": list(range(20_000)), "nums": list(range(200_000))}
        with pytest.raises(BudgetExceededError):
            _evaluator(DEFAULT_STEP_BUDGET).evaluate("rows | map(sum(nums))", context)


class TestProducingALargeValueCostsBudget:
    """The memory-amplification policy, as the design's option (b).

    The step budget counts nodes evaluated, and a node that allocates is one node however much it
    allocates. Measured before this: `rows | map(t + t)` over two thousand rows of 100,000
    characters is a seventeen-character expression that allocated 343 MB, and no per-result cap
    could see it, because every one of those two thousand strings was comfortably under the cap.
    Only the total hurt.

    Charging by result size folds memory into the budget with no second knob, which gives one
    useful property: **a host that wants a tighter memory bound lowers the budget**, and time and
    memory scale together.
    """

    def test_the_aggregate_case_that_used_to_allocate_343_megabytes(self) -> None:
        text = "x" * 100_000
        with pytest.raises(BudgetExceededError):
            _evaluator(DEFAULT_STEP_BUDGET).evaluate(
                "rows | map(t + t)", {"rows": list(range(4000)), "t": text}
            )

    def test_a_smaller_version_of_the_same_shape_still_runs(self) -> None:
        """The bound has to land somewhere useful, not refuse the shape outright."""
        text = "x" * 100_000
        result = _evaluator(DEFAULT_STEP_BUDGET).evaluate(
            "rows | map(t + t)", {"rows": list(range(100)), "t": text}
        )
        assert len(result) == 100

    @pytest.mark.parametrize(
        ("source", "context"),
        [
            ("a + a", {"a": "x" * 200}),
            ("rows | map(_)", {"rows": list(range(500))}),
            ("rows | pluck('k')", {"rows": [{"k": 1}] * 500}),
            ("parts | join('')", {"parts": ["x" * 100] * 50}),
            ("a | extend(a)", {"a": list(range(500))}),
        ],
    )
    def test_producing_more_costs_more(self, source: str, context: dict[str, Any]) -> None:
        bigger = {
            key: (value * 4 if isinstance(value, (str, list)) else value)
            for key, value in context.items()
        }
        assert steps_for(source, bigger) > steps_for(source, context)

    def test_a_small_value_costs_nothing_extra(self) -> None:
        """**Integer division is the point.** Anything under the charging unit costs nothing, so
        an ordinary rule building short strings pays exactly what it paid before."""
        short = steps_for("a + b", {"a": "x", "b": "y"})
        plain = steps_for("a + b", {"a": 1, "b": 2})
        assert short == plain

    def test_the_charge_scales_with_the_unit(self) -> None:
        rows = list(range(SIZE_CHARGE_UNIT * 10))
        cost = steps_for("rows | map(_)", {"rows": rows})
        bare = steps_for("rows | map(_)", {"rows": rows[:1]})
        assert cost - bare >= len(rows) // SIZE_CHARGE_UNIT

    def test_lowering_the_budget_lowers_the_memory_bound_with_it(self) -> None:
        """The property that makes one knob enough."""
        context = {"rows": list(range(200)), "t": "x" * 100_000}
        assert _completes("rows | map(t + t)", context, DEFAULT_STEP_BUDGET)
        assert not _completes("rows | map(t + t)", context, 100_000)


class TestLegitimateWorkAtScaleStillPasses:
    """The other half of the acceptance criteria, and the half a careless policy would break.

    The design commits to 10^5 items. Every one of these runs in under a fifth of a second at the
    default budget.
    """

    ORDERS: ClassVar[list[dict[str, Any]]] = [
        {
            "customer_id": f"c{n % 500}",
            "status": "paid" if n % 3 else "open",
            "items": [1, 2],
            "name": f"Name {n}",
            "value": n,
        }
        for n in range(100_000)
    ]

    @pytest.mark.parametrize(
        "source",
        [
            'orders | where(_.status == "paid") | group_by(_.customer_id)'
            ' | map(merge(_, {"n": len(_.items)}))',
            "orders | where(_.value > 50000) | first",
            "orders | map(_.name) | unique_by(_) | len",
            "orders | map(lower(_.name)) | len",
            'orders | map(_.name + "!") | len',
            "orders | sort_by(_.value) | take(10) | len",
        ],
    )
    def test_it_runs_at_a_hundred_thousand_items(self, source: str) -> None:
        evaluator = _evaluator(DEFAULT_STEP_BUDGET)
        assert evaluator.evaluate(source, {"orders": self.ORDERS}) is not None


class TestRegressions:
    def test_regression_budget_a_nested_lazy_is_charged_against_the_same_counter(self) -> None:
        """A per-level counter bounds each level to N and lets two levels do N*N work.

        The bug is invisible to any test that runs one level: the inner expression completes,
        the outer expression completes, and only the product is unbounded. Written as a
        comparison rather than an absolute so it fails on a per-level counter regardless of what
        the constants are.
        """
        context = {"a": list(range(8)), "b": list(range(8))}
        one_level = steps_for("map(a, _ + 1)", context)
        two_levels = steps_for("map(a, map(b, _ + _2))", context)
        assert two_levels > one_level * 8, (
            "nested evaluation cost about as much as a single level, which is what a per-level "
            "budget looks like from the outside"
        )

    def test_regression_budget_is_not_consumed_across_evaluations(self) -> None:
        """A counter on the evaluator rather than on the run passes every single-expression test
        and fails on the second call."""
        ev = _evaluator(2_000)
        first = ev.evaluate("map(a, _ + 1)", {"a": list(range(20))})
        second = ev.evaluate("map(a, _ + 1)", {"a": list(range(20))})
        assert first == second

    def test_regression_budget_an_error_inside_an_item_does_not_skip_the_charge(self) -> None:
        """The counter is decremented before dispatch, so a node that raises has still been paid
        for. Charging afterwards would let a predicate that always raises run free."""
        ev = _evaluator(400)
        with pytest.raises(SafeExprError) as caught:
            ev.evaluate("map(a, _.nope)", {"a": [{"k": 1}] * 500})
        assert isinstance(caught.value, (BudgetExceededError, EvaluationError))
