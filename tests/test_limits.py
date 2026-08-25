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
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from safeexpr import Evaluator, standard_registry
from safeexpr._eval import DEFAULT_STEP_BUDGET
from safeexpr._guards import MAX_DATA_NESTING, MAX_RESULT_SIZE
from safeexpr._validate import MAX_EXPRESSION_DEPTH

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lanes  # noqa: E402
import limits  # noqa: E402

# The suite's own selection rules, loaded by path under a name of its own. `import conftest` would
# work today because pytest has already imported this one, and would start resolving to
# `tests/benchmarks/conftest.py` the moment anything about collection order changed.
_SPEC = importlib.util.spec_from_file_location(
    "safeexpr_suite_conftest", ROOT / "tests" / "conftest.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
conftest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(conftest)

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


# Time per charged step, as a multiple of the `map` reference, and the ceiling each is held to.
#
# **The default is 15x and it is tighter than the 25x it replaces.** Measured on both runner
# platforms, fifteen rounds, eight repeats, worst observed: `join` 7.91x, `min` 2.52x, `max` 2.39x,
# `unique_by` 1.87x, `sort_by` 1.62x, `sum` 1.69x. So 15 is roughly twice the worst thing the
# default covers, and a new function landing at 20x is now caught where it used to pass.
#
# **`pluck` is the one exemption and it is a documented property rather than a concession.** It
# reads a key directly and walks no tree per item, so it charges almost nothing for real work:
# 18.48x worst on Linux, 21.67x worst on macOS. `docs/performance.md` says so in prose. 40x is
# roughly twice that, which leaves the class this test exists to catch, the 1,500 to 2,300x
# aggregates before calls were charged for what they read, two orders of magnitude away.
BLIND_SPOT_CEILINGS = {"pluck": 40}
DEFAULT_BLIND_SPOT_CEILING = 15


def _ceiling(name: str) -> int:
    """The multiple of the reference `name` is allowed to cost per charged step."""
    return BLIND_SPOT_CEILINGS.get(name, DEFAULT_BLIND_SPOT_CEILING)


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
        offenders = [
            f"{name} at {value / reference:.1f}x (ceiling {_ceiling(name)}x)"
            for name, value in spots.items()
            if name != "map (reference)" and value >= reference * _ceiling(name)
        ]
        assert not offenders, (
            f"the budget is not seeing what these do: {offenders}\n"
            f"the whole table, as a multiple of the reference:\n"
            + "\n".join(
                f"    {name:<18} {value / reference:7.2f}x"
                for name, value in sorted(spots.items(), key=lambda kv: -kv[1])
            )
        )

    def test_regression_limits_a_noisy_reference_inflated_the_ratio(self) -> None:
        """The flake this battery exists in its current shape because of.

        The assertion above was one cap of 25x over every function, and `pluck` sits at 17 to 22
        depending on the platform. It failed twice in one afternoon on `macos-latest`, once on
        `main`, both times on green code.

        The cause was not `pluck`. It was the **reference in the denominator**: `map`'s minimum
        swung 52.6% across eight runs on that runner against 1.4% on Linux, and a reference that
        happens to measure fast inflates every ratio above it. Measured, `pluck` against `map`,
        eight repeats:

            runner            rounds=5                    rounds=15
            ubuntu-latest     17.18 to 17.40  (0.22)      17.28 to 17.48  (0.20)
            macos-latest      14.75 to 26.25 (11.49)      17.53 to 20.82  (3.28)

        So the fix is in the estimator rather than in the number, and this asserts the estimator.
        A future change that puts `blind_spots` back on the shared five-round default reopens the
        flake, and nothing else here would notice: the ceilings would still pass on Linux.
        """
        source = (ROOT / "scripts" / "limits.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "blind_spots"
        )
        rounds = function.args.defaults[-1]
        assert isinstance(rounds, ast.Name), (
            "blind_spots no longer takes its round count from a named constant, so the argument "
            "for why it differs from everything else has nowhere to live"
        )
        assert rounds.id == "BLIND_SPOT_ROUNDS"
        assert limits.BLIND_SPOT_ROUNDS >= 15, (
            f"blind_spots takes {limits.BLIND_SPOT_ROUNDS} rounds. Fifteen was chosen from a "
            f"measured 11.49-point spread at five, on a runner this suite runs on."
        )

    def test_the_ceilings_are_still_earned(self) -> None:
        """An exemption outliving its reason is how the next one gets waved through.

        `pluck` has a ceiling of its own because it genuinely sits an order of magnitude above
        everything else: it reads a key directly and walks no tree per item, so it charges almost
        nothing for real work. If it ever comes back under the default, the exemption should go
        rather than sit there making the general cap look looser than it is.
        """
        spots = limits.blind_spots(20_000)
        reference = spots["map (reference)"]
        for name in BLIND_SPOT_CEILINGS:
            assert name in spots, f"{name} has a ceiling and is not measured any more"
            ratio = spots[name] / reference
            assert ratio > DEFAULT_BLIND_SPOT_CEILING * 0.5, (
                f"{name} is at {ratio:.1f}x, comfortably under the "
                f"{DEFAULT_BLIND_SPOT_CEILING}x default. Drop its exemption."
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


@pytest.mark.slow
class TestTheScriptRuns:
    """The script is meant to be runnable by a third party, so it is run.

    **These two are a third of the suite's wall time**, 17.9 s of 58.8 s measured, because each
    runs `scripts/limits.py --quick` as a subprocess and the script does real timing work. That is
    the point of it, and `--quick` already skips the 100,000-item runs.

    They differ only in output format: `--json` emits JSON *instead of* the table, so neither
    invocation can serve both. Rather than change a published script's command line so a test can
    run faster, the pair is marked `slow`, deselected from a bare `pytest`, and run in full by the
    `fast` and `compat` lanes. `tests/conftest.py` carries the argument and
    `test_regression_limits_the_script_still_runs_in_ci` below is what keeps the second half true.
    """

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


class TestTheSuiteKeepsItsShape:
    """Two tests were a third of the suite, and this is what keeps the fix honest.

    They are deselected from a bare `pytest` and run in full by the lanes CI invokes. The saving is
    real, 58.8 s to 39.6 s measured on one machine, and every part of it depends on the second half
    of that sentence staying true. A marker that quietly stopped running in CI would be strictly
    worse than the ten seconds it saved.
    """

    def test_regression_limits_the_script_still_runs_in_ci(self) -> None:
        """The half that a marker makes easy to lose.

        Asserted at both ends: the lane's command carries `--runslow`, and CI invokes that lane.
        Either one alone would pass while the tests stopped running.
        """
        assert "--runslow" in lanes.LANES_BY_NAME["fast"].command, (
            "the `fast` lane no longer asks for the slow tests, so nothing in CI runs "
            "scripts/limits.py at all"
        )
        assert "--runslow" in lanes.LANES_BY_NAME["compat"].command, (
            "the `compat` lane no longer asks for them, so the matrix stopped covering the limits "
            "that are interpreter behaviour, which is the reason the matrix exists"
        )
        workflow = ROOT / ".github" / "workflows" / "ci.yml"
        if not workflow.is_file():
            pytest.skip("no .github/workflows/ci.yml: this is a distribution, not a checkout")
        text = workflow.read_text(encoding="utf-8")
        assert "lanes.py fast" in text
        assert "lanes.py compat" in text

    def test_regression_limits_the_marked_tests_are_the_two_that_were_measured(self) -> None:
        """A marker that spread would take coverage out of the inner loop a test at a time."""
        source = ast.parse((ROOT / "tests" / "test_limits.py").read_text(encoding="utf-8"))
        marked = {
            node.name
            for node in ast.walk(source)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Attribute) and decorator.attr == "slow"
        }
        assert marked == {"TestTheScriptRuns"}, (
            f"`slow` is now on {sorted(marked)}. It was added for two tests that run a real "
            f"measurement as a subprocess; anything else marked with it is coverage leaving the "
            f"inner loop without an argument."
        )

    def test_regression_limits_the_samples_were_not_quietly_reduced(self) -> None:
        """**The flake this repository has already fixed once, in this file.**

        `seconds_for` took a median of three and flaked `test_the_aggregates_are_within_a_small_
        factor` about one run in five on a busy machine. It takes the minimum of five now, because
        interference only ever adds time. Cutting sample counts is the cheapest-looking way to make
        this suite faster and it is how a measurement becomes a flake, so it is ruled out in
        writing and asserted here rather than left to a comment.
        """
        source = (ROOT / "scripts" / "limits.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "seconds_for"
        )
        rounds = function.args.defaults[-1]
        assert isinstance(rounds, ast.Constant), f"seconds_for's rounds is {ast.dump(rounds)}"
        assert rounds.value >= 5, (
            f"seconds_for takes {rounds.value} rounds. Five was chosen after a flake, and fewer "
            f"is how it comes back."
        )
        returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
        called = {
            node.func.id
            for statement in returns
            for node in ast.walk(statement)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "min" in called, "seconds_for no longer returns a minimum"
        assert "median" not in called, (
            "seconds_for returns a median again. A median of a few samples moves with whatever "
            "else the machine is doing; the minimum cannot be inflated by a busy box."
        )

    def test_the_runtime_tripwire_is_loose_enough_not_to_be_noise(self) -> None:
        """A tripwire near the measurement fails on a busy machine and gets deleted."""
        assert conftest.MAX_SUITE_SECONDS >= 180, (
            "the ceiling is close enough to the measured runtime to fail for a reason nobody can "
            "act on, which is how a check gets deleted rather than investigated"
        )

    @staticmethod
    def _aged(args: list[str]) -> Any:
        """A stand-in for pytest's `Config`, with the clock set far past the ceiling.

        Cheaper and more exact than running a deliberately slow suite as a subprocess, and it
        exercises the line that actually fails rather than something shaped like it.
        """
        config = SimpleNamespace(
            args=args,
            option=SimpleNamespace(keyword="", markexpr=""),
            getini=lambda name: ["tests"],
        )
        # The attribute the hook looks for. Spelled out rather than reached through the module's
        # private name, so this test still reads as a description of the contract.
        config.safeexpr_suite_started = time.perf_counter() - 10_000
        return config

    @staticmethod
    def _reporter() -> tuple[Any, list[str], Any]:
        """A stand-in for pytest's terminal reporter, its captured lines, and its session."""
        lines: list[str] = []
        session = SimpleNamespace(exitstatus=0, testsfailed=0)
        reporter = SimpleNamespace(
            _session=session,
            write_line=lambda text, **_: lines.append(text),
        )
        return reporter, lines, session

    def test_the_runtime_tripwire_can_actually_fail(self) -> None:
        """A gate nobody has watched fail is not known to work."""
        reporter, lines, session = self._reporter()
        conftest.pytest_terminal_summary(reporter, 0, self._aged(["tests"]))
        assert any("SUITE TOO SLOW" in line for line in lines), lines
        assert session.exitstatus != 0
        assert session.testsfailed == 1

    def test_the_tripwire_stays_quiet_for_a_subset(self) -> None:
        """A subset is faster by construction, so timing one against a whole-suite ceiling would
        be measuring nothing at all."""
        reporter, lines, _ = self._reporter()
        conftest.pytest_terminal_summary(reporter, 0, self._aged(["tests/test_eval.py"]))
        assert lines == []

    def test_the_tripwire_reports_the_time_even_when_it_passes(self) -> None:
        """A ceiling nobody sees the distance to is one that gets crossed in a single commit."""
        reporter, lines, session = self._reporter()
        config = self._aged(["tests"])
        config.safeexpr_suite_started = time.perf_counter()
        conftest.pytest_terminal_summary(reporter, 0, config)
        assert len(lines) == 1
        assert lines[0].startswith("suite wall time:")
        assert session.exitstatus == 0
