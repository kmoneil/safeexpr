"""Every limit traceable to a measurement, asserted rather than commented.

Nearly every constant in this package could have been picked out of the air. Each one is instead
set from a measurement at **ten times observed need or more**, and this file is what stops that
claim from quietly stopping being true: it re-measures and re-checks the ratio, using the same
functions `scripts/limits.py` publishes.

Two of these tests exist because a number went stale rather than wrong. `MAX_EXPRESSION_DEPTH`
was 100 against an observed need of 12, which is 8.3 times and fails the rule this package sets
itself; the ratio was never checked because it was written in a comment. The per-function step
costs were a guess at relative expense that no measurement supported. Both were found by running
the measurement rather than by reading the code.

The measurements here run at 10,000 items to keep the suite quick. Steps per item are asserted to
be constant across scales, which is what makes extrapolating to the committed 100,000 sound;
`scripts/limits.py` does the full run.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from safeexpr import Evaluator, standard_registry
from safeexpr._eval import DEFAULT_STEP_BUDGET
from safeexpr._guards import MAX_DATA_NESTING, MAX_RESULT_SIZE
from safeexpr._validate import MAX_EXPRESSION_DEPTH

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import limits  # noqa: E402

SRC_DIR = ROOT / "src" / "safeexpr"

# The design's rule: every cap sits at ten times what was measured to be needed.
HEADROOM = 10

# Caps whose basis is something other than observed need, each with the reason. A cap here is not
# exempt from having a reason, only from having *this* reason.
NOT_FROM_NEED = {
    "MAX_SOURCE_BYTES": "set by 3.11's parser cliff",
    "MAX_POWER_RESULT_BITS": "set by measured time; no rule needs large powers at all",
    "SIZE_CHARGE_UNIT": "a rate rather than a cap, pinned from both ends",
    "MAX_CACHE": "a cache bound, not a limit on what an expression may do",
    "MAX_NESTING": "a pattern-nesting cap, measured with the regex gate",
    "MAX_DATA_NESTING": "loose on purpose; being generous costs nothing here",
    # Secondary caps, each defence in depth behind a primary one and each argued where it is
    # written. They are listed rather than published because ten-times-need is the wrong question
    # for them: none is a limit on what a rule may reasonably do, and the number is chosen so that
    # nothing reasonable ever meets it.
    "MAX_FORMAT_LENGTH": "a date format longer than this is not a date format",
    "MAX_WALK": "bounds the nesting check's own work, not the expression's",
    "MAX_PATTERN_LENGTH": "a pattern longer than this is not a rule; the gate is the mitigation",
    "MAX_SUBJECT_LENGTH": "defence in depth behind the pattern gate, which is the mitigation",
    "MAX_QUERY_FIELDS": "a URL with more parameters than this is an attack or a bug",
}


# The scale the tests measure at. A tenth of the committed 100,000, because the two
# scale-dependent needs are proportional to it and the suite should not spend two minutes
# re-deriving a number `scripts/limits.py` publishes in full.
SCALE = 10_000
SCALE_FACTOR = 100_000 // SCALE


@pytest.fixture(scope="module")
def need() -> dict[str, int]:
    """What the canonical and deliberately-complex rules require, scaled to 100,000 items.

    `result_size` and `steps` are proportional to the item count, which
    `TestStepsPerItemAreStableAcrossScales` asserts rather than assumes; the depth entries are
    not, and are used as measured.
    """
    measured = limits.observed_need(SCALE)
    return {
        **measured,
        "result_size": measured["result_size"] * SCALE_FACTOR,
        "steps": measured["steps"] * SCALE_FACTOR,
    }


class TestEveryCapClearsTenTimesObservedNeed:
    def test_expression_depth(self, need: dict[str, int]) -> None:
        """**The one that was wrong.** 100 against a measured need of 12 is 8.3 times."""
        assert need["expression_depth"] * HEADROOM <= MAX_EXPRESSION_DEPTH, (
            f"depth cap {MAX_EXPRESSION_DEPTH} is only "
            f"{MAX_EXPRESSION_DEPTH / need['expression_depth']:.1f}x the deepest realistic rule "
            f"({need['expression_depth']}), and this package's rule is {HEADROOM}x"
        )

    def test_result_size(self, need: dict[str, int]) -> None:
        assert need["result_size"] * HEADROOM <= MAX_RESULT_SIZE

    def test_the_step_budget(self, need: dict[str, int]) -> None:
        assert need["steps"] * HEADROOM <= DEFAULT_STEP_BUDGET, (
            f"budget {DEFAULT_STEP_BUDGET:,} is only "
            f"{DEFAULT_STEP_BUDGET / need['steps']:.1f}x the heaviest canonical use case "
            f"({need['steps']:,} steps)"
        )

    def test_data_nesting(self, need: dict[str, int]) -> None:
        assert need["data_depth"] * HEADROOM <= MAX_DATA_NESTING


class TestTheDepthCapAlsoLeavesTheHostItsStack:
    """The cap has a ceiling as well as a floor, and both are measured.

    The evaluator gives out at 497 levels on every supported interpreter. The cap has to clear ten
    times observed need *and* leave room for however deep the host already was when it called us,
    which is why it sits at the bottom of that window rather than the middle.
    """

    def test_it_is_well_under_where_the_evaluator_gives_out(self) -> None:
        assert MAX_EXPRESSION_DEPTH * 3 < 497, (
            "the cap leaves less than three times its own depth of stack for the host"
        )

    def test_an_expression_at_the_cap_evaluates_rather_than_crashing(self) -> None:
        """The floor of the window, checked from the outside: something exactly at the limit has
        to work, or the limit is not the limit."""
        source = "1" + " + 1" * (MAX_EXPRESSION_DEPTH - 3)
        assert Evaluator().evaluate(source, {}) == MAX_EXPRESSION_DEPTH - 2


class TestStepsPerItemAreStableAcrossScales:
    """What makes a budget expressible in items rather than in nodes, and what makes the
    extrapolation from 10,000 to the committed 100,000 sound."""

    def test_the_rate_is_the_measured_band(self) -> None:
        small = limits.workload(1_000)
        large = limits.workload(10_000)
        for label in ("alerting rule", "pipeline"):
            small_rate = small[label]["steps"] / 1_000
            large_rate = large[label]["steps"] / 10_000
            assert 3.5 <= small_rate <= 6.5, f"{label} at 1,000 items: {small_rate:.2f} steps/item"
            assert 3.5 <= large_rate <= 6.5, f"{label} at 10,000 items: {large_rate:.2f} steps/item"
            assert abs(small_rate - large_rate) < 1.0, (
                f"{label} changed rate between scales: {small_rate:.2f} against {large_rate:.2f}"
            )

    def test_the_budget_covers_ten_times_a_hundred_thousand_items(self) -> None:
        """Extrapolated from 10,000 at the rate asserted above, so the suite stays quick."""
        at_scale = max(entry["steps"] for entry in limits.workload(SCALE).values())
        assert at_scale * SCALE_FACTOR * HEADROOM <= DEFAULT_STEP_BUDGET


class TestTheBudgetSeesWhatFunctionsActuallyDo:
    """Time per charged step, which is the only way to find work the counter cannot see.

    A function far above the reference is one the budget under-charges, and the gap is a denial of
    service waiting to be found: `sum` measured 2,000 times the reference before a call was
    charged for what it reads, and `rows | map(sum(nums))` bought eighteen minutes from the
    default budget.
    """

    def test_no_function_is_wildly_cheaper_to_the_counter_than_to_the_machine(self) -> None:
        spots = limits.blind_spots(20_000)
        reference = spots["map (reference)"]
        worst = max(spots.items(), key=lambda kv: kv[1])
        assert worst[1] < reference * 25, (
            f"{worst[0]} costs {worst[1] / reference:.0f}x the reference per charged step, which "
            f"means the budget is not seeing what it does"
        )

    def test_the_aggregates_are_within_a_small_factor(self) -> None:
        """`sum`, `min` and `max` were the worst offenders at 1,500 to 2,300 times, and are the
        clearest evidence that charging for what a call reads was the right fix."""
        spots = limits.blind_spots(20_000)
        reference = spots["map (reference)"]
        for name in ("sum", "min", "max"):
            assert spots[name] < reference * 5, f"{name} is {spots[name] / reference:.1f}x"


class TestNoLimitEscapesTheTable:
    """The forcing function. A constant added without a line in `scripts/limits.py` is a number
    with no published basis, and this is what fails instead of nobody noticing."""

    @staticmethod
    def _constants() -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for path in sorted(SRC_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {
                target.id
                for node in tree.body
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name)
                and target.id.lstrip("_").isupper()
                and ("MAX" in target.id or "DEFAULT" in target.id or "UNIT" in target.id)
            }
            if names:
                found[path.name] = names
        return found

    def test_every_limit_constant_is_published_or_explained(self) -> None:
        published = {name for name, _, _ in limits.LIMITS}
        missing: list[str] = []
        for module, names in self._constants().items():
            for name in names:
                bare = name.lstrip("_")
                if bare not in published and bare not in NOT_FROM_NEED:
                    missing.append(f"{module}:{name}")
        assert not missing, (
            f"limits with no published basis: {missing}. Add them to `LIMITS` in "
            f"`scripts/limits.py`, or to `NOT_FROM_NEED` here with the reason."
        )


class TestTheScriptRuns:
    """The card asks for a benchmark script runnable by a third party, so it is run."""

    def test_the_table(self) -> None:
        finished = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "limits.py"), "--quick"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert finished.returncode == 0, finished.stderr
        assert "steps/item" in finished.stdout
        assert "ns/step" in finished.stdout

    def test_the_json(self) -> None:
        finished = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "limits.py"), "--quick", "--json"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert finished.returncode == 0, finished.stderr
        data = json.loads(finished.stdout)
        # `compile_cache` joined this set when the compile cache landed. It is a measurement
        # rather than a ratio: the bound is set by what the cache can hold rather than by what a
        # rule needs, so it is published beside the caps instead of among them.
        assert set(data) == {
            "python",
            "workloads",
            "observed_need",
            "blind_spots",
            "compile_cache",
            "limits",
        }
        assert data["limits"]["DEFAULT_STEP_BUDGET"] == 6_000_000


class TestRegressions:
    def test_regression_limits_the_depth_cap_met_its_own_rule(self) -> None:
        """It did not. 100 against a measured need of 12 is 8.3 times, and the rule is ten.

        The number was never wrong in a way anything could catch, because the justification lived
        in a comment and the need had never been measured. It is 125 now: the smallest value that
        clears the floor, chosen at that end of the window because the remaining stack is the
        host's rather than ours.
        """
        assert MAX_EXPRESSION_DEPTH == 125
        assert MAX_EXPRESSION_DEPTH >= 12 * HEADROOM

    def test_regression_limits_per_function_costs_are_not_guesses(self) -> None:
        """Every collections entry was priced 1, 2 or 5 by eye. Measured, the whole tier lands
        between 0.85 and 1.5 times a bare `map` per charged step, so the numbers described a
        difference that was not there."""
        dearer = {name: f.cost for name, f in standard_registry().items() if f.cost != 1}
        assert dearer == {"matches": 10}
