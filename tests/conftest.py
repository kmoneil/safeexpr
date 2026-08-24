"""What the default selection is, and how long it is allowed to take.

**Two tests were a third of the suite.** `test_the_table` and `test_the_json` each run
`scripts/limits.py --quick` as a subprocess, and the script does real timing work, which is the
point of it. Together they were 17.9 s of a 58.8 s run. They are not slow by accident and they are
not wasteful: `--quick` already skips the 100,000-item runs, and cutting the sample counts is how a
measurement becomes a flake, which this repository has already fixed once in exactly this file.

So they are **deselected from the inner loop and run in full in CI**, which puts the cost where it
belongs. `scripts/lanes.py` passes `--runslow` in the `fast` and `compat` lanes, and
`tests/test_limits.py::test_regression_limits_the_script_still_runs_in_ci` asserts that it does. A
marker that quietly stopped running in CI would be strictly worse than the ten seconds it saved.

Three options were weighed and this is the second of them. Adding a `--both` mode to
`scripts/limits.py` so one invocation serves both tests would halve the cost, and it means changing
a published script's command line so that a test can run faster, which is the wrong way round: the
script is documented in `docs/performance.md` as the thing a reader runs to reproduce the table on
their own machine, and its interface belongs to them. **Reducing what `--quick` measures is ruled
out**, and the reason is recorded so it is not re-proposed as the obvious cheap one next quarter.

The other 2,407 tests run in about 41 s, which is 17 ms each, and there is nothing wrong with them.
The three next-slowest are load-bearing and they stay: a denial-of-service regression test that has
to do real work to prove anything, and two differential tests that generate expressions across the
whole allowlist.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

# **A step change, not drift.** The default selection measures about 41 s and the full one about
# 59 s, both on the machine this was written on and both with benchmarks disabled; with them
# enabled the full run is about 90 s, and under `--cov` about 103 s. The ceiling has to sit above
# all of those and above a hosted runner, which is slower again.
#
# 240 s is roughly four times the number this is protecting. That is deliberate: a tripwire close
# to the measurement fails on a busy machine, and a check that fails for a reason nobody can act on
# gets deleted rather than investigated. What this catches is somebody adding a test that takes a
# minute, which is the thing that actually happens.
MAX_SUITE_SECONDS = 240.0

_STARTED = "safeexpr_suite_started"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add `--runslow`, which re-selects what the inner loop leaves out."""
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="also run tests marked `slow`; CI passes this, the inner loop does not",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect `slow` unless asked for.

    Deselected rather than skipped, deliberately. A skip is a line of output per test claiming
    something was considered and not done; a deselection is one number in the summary saying the
    selection was smaller, which is what actually happened. It also means `-m slow` still works to
    run only these, without `--runslow`, because an explicit marker expression is a request.
    """
    if config.getoption("--runslow") or config.option.markexpr:
        return
    kept, dropped = [], []
    for item in items:
        (dropped if item.get_closest_marker("slow") else kept).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def pytest_configure(config: pytest.Config) -> None:
    """Start the clock the tripwire reads."""
    setattr(config, _STARTED, time.perf_counter())


def _is_the_whole_suite(config: pytest.Config) -> bool:
    """Whether this run was the default selection rather than somebody's subset.

    A subset is faster by construction, so timing one against a whole-suite ceiling would be
    measuring nothing. `-k`, `-m` and a path argument are the three ways to ask for one.
    """
    testpaths = list(config.getini("testpaths"))
    return (
        not config.option.keyword and not config.option.markexpr and list(config.args) == testpaths
    )


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    """Report the run's wall time, and refuse a step change in it.

    Printed on every run rather than only when it fails, because a ceiling nobody sees the distance
    to is one that gets crossed in a single commit.
    """
    started = getattr(config, _STARTED, None)
    if started is None:  # pragma: no cover - configure always runs first
        return
    seconds = time.perf_counter() - started
    if not _is_the_whole_suite(config):
        return
    terminalreporter.write_line(
        f"suite wall time: {seconds:.1f}s (tripwire at {MAX_SUITE_SECONDS:.0f}s)"
    )
    if seconds > MAX_SUITE_SECONDS:
        terminalreporter.write_line(
            f"SUITE TOO SLOW: {seconds:.1f}s against a {MAX_SUITE_SECONDS:.0f}s ceiling. This is "
            f"four times the number it protects, so it is a step change rather than drift: run "
            f"with --durations=10 and look for something new. If the suite has genuinely grown, "
            f"move the ceiling deliberately and say what grew.",
            red=True,
        )
        terminalreporter._session.exitstatus = pytest.ExitCode.TESTS_FAILED  # noqa: SLF001
        if exitstatus == 0:
            terminalreporter._session.testsfailed += 1  # noqa: SLF001
