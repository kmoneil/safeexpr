#!/usr/bin/env python
"""The numbers lane: timings, allocation ceilings, and how long importing this package takes.

**This exists because the benchmark suite ran nowhere.** `tests/benchmarks/` has been in the
repository since the tier work, `.benchmarks/` held seven saved baselines, `CLAUDE.md` mandated a
10% regression gate, and not one of CI's eight lanes executed any of it. A win with no gate is a
temporary win: it gets undone by an unrelated change within two quarters and nobody notices until
somebody profiles again.

Three things run here, and they answer different questions:

*Timings*, which is what a regression gate compares. Written to JSON so two runs can be compared
rather than eyeballed.

*Allocation ceilings*, under `pytest-memray`, because a change that trades memory for speed must
fail rather than pass quietly. Timings alone would wave it through.

*Import time*, which is a tripwire rather than a target. `import safeexpr` is 20 to 30 ms and has
no budget in a long-lived host, which is the documented assumption. What the ceiling catches is a
new module-scope import: this package's whole pitch is that installing it installs nothing else,
and the first sign of that going wrong is an import that suddenly costs ten times what it did.

## Comparing, and why not `--benchmark-compare-fail`

pytest-benchmark can gate on its own. It gates on **`mean`**, which is what `CLAUDE.md` asked for,
and mean is unusable on this workload: one 147x outlier in twenty thousand rounds moves it further
than the effect being measured. Measured twice, on two separate changes, against baselines taken
minutes earlier on the same machine: mean reported a 45% regression in `pluck` for a change that
provably cannot touch it, while median and minimum agreed within three points on every row.

So the comparison here reports **all three** and gates on one of them, and the failure message
names the row, the statistic and the two numbers. That is the other reason not to use the built-in:
its failure output is a stack trace ending in `PerformanceRegression: Performance has regressed.`
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Where the timings land by default. Under `.benchmarks/`, which is git-ignored, because a
# committed baseline ages against a moving runner fleet and the refresh becomes a chore nobody
# owns. The comparison this lane runs takes its baseline from the same runner in the same minutes
# instead; see `.github/workflows/ci.yml`.
DEFAULT_OUT = REPO_ROOT / ".benchmarks" / "measure.json"

# **A tripwire, not a target.** Measured at 20 to 30 ms on the development machine and on a hosted
# runner, and set an order of magnitude above that deliberately: this number is allowed to drift
# with the interpreter, the filesystem and the phase of the runner. What it is not allowed to do is
# jump, and a new module-scope import of anything substantial is what would make it jump.
#
# Deliberately loose enough that it will never fail for a reason nobody can act on. If it does
# fail, the thing to look at is `sys.modules` after `import safeexpr`, not this number.
MAX_IMPORT_SECONDS = 0.400

# The statistics compared, in the order they are printed. `mean` is reported and never gated on;
# the module docstring says why.
STATISTICS = ("min", "median", "mean")


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    print(f"==> {' '.join(argv)}")
    return subprocess.run(argv, cwd=REPO_ROOT, check=False, text=True, **kwargs)


def import_seconds(rounds: int = 5) -> float:
    """How long `import safeexpr` costs, net of starting an interpreter.

    **The minimum of several rounds, and the empty interpreter subtracted.** Interference only ever
    adds time, so the smallest observation is the closest thing to the operation's own cost; and
    roughly half of a bare `python -c "import safeexpr"` is CPython starting up, which is not this
    package's to answer for.

    Args:
        rounds: How many times to measure each half.

    Returns:
        Seconds, and never below zero.
    """

    def best(code: str) -> float:
        samples = []
        for _ in range(rounds):
            started = time.perf_counter()
            subprocess.run([sys.executable, "-c", code], check=True, cwd=REPO_ROOT)
            samples.append(time.perf_counter() - started)
        return min(samples)

    return max(0.0, best("import safeexpr") - best("pass"))


def check_import_time() -> int:
    """Measure the import and refuse a step change. Returns an exit status."""
    seconds = import_seconds()
    ceiling = MAX_IMPORT_SECONDS * 1000
    print(f"==> import safeexpr: {seconds * 1000:.1f} ms (ceiling {ceiling:.0f} ms)")
    if seconds > MAX_IMPORT_SECONDS:
        print(
            f"import safeexpr took {seconds * 1000:.1f} ms, over the {ceiling:.0f} ms tripwire."
            f" This ceiling is an order of magnitude above the measured cost, so it is not drift:"
            f" look for a new module-scope import.",
            file=sys.stderr,
        )
        return 1
    return 0


def run_timings(out: Path) -> int:
    """Run the benchmark suite and write its JSON. Returns an exit status."""
    out.parent.mkdir(parents=True, exist_ok=True)
    finished = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/benchmarks",
            "--benchmark-only",
            f"--benchmark-json={out}",
            "-q",
        ]
    )
    return finished.returncode


def run_ceilings() -> int:
    """Run the allocation ceilings under memray. Returns an exit status.

    Selected by marker rather than by filename, so a ceiling added to any file in the directory is
    picked up. `--benchmark-disable` because the timing rounds are not wanted here: memray tracks
    every allocation, and running a benchmark twenty thousand times under it measures the tracer.
    """
    finished = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/benchmarks",
            "-m",
            "limit_memory",
            "--memray",
            "--benchmark-disable",
            "-q",
        ]
    )
    return finished.returncode


def rows(path: Path) -> dict[str, dict[str, float]]:
    """A benchmark JSON file as `fullname` to its statistics."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {entry["fullname"]: entry["stats"] for entry in data["benchmarks"]}


def compare(base: Path, head: Path, fail_over: tuple[str, float] | None, label: str) -> int:
    """Print a per-row comparison and gate on one statistic. Returns an exit status.

    A row present in only one of the two files is listed and not compared, because a comparison
    silently covering fewer rows than it appears to is worse than one that says so.

    Args:
        base: The earlier run.
        head: The later run.
        fail_over: `(statistic, fraction)` to gate on, or `None` to report only.
        label: What this comparison is, for the heading.

    Returns:
        0, or 1 if a row regressed past the threshold.
    """
    before, after = rows(base), rows(head)
    shared = sorted(set(before) & set(after))
    print(f"\n==> {label}: {len(shared)} rows\n")
    header = f"{'row':<44}" + "".join(f"{name:>12}" for name in STATISTICS)
    print(header)
    print("-" * len(header))
    offenders: list[str] = []
    spreads: dict[str, list[float]] = {name: [] for name in STATISTICS}
    for name in shared:
        line = f"{_short(name):<44}"
        for statistic in STATISTICS:
            change = after[name][statistic] / before[name][statistic] - 1
            spreads[statistic].append(change)
            line += f"{change * 100:>11.1f}%"
        print(line)
        if fail_over is not None:
            statistic, threshold = fail_over
            change = after[name][statistic] / before[name][statistic] - 1
            if change > threshold:
                offenders.append(
                    f"{_short(name)}: {statistic} {before[name][statistic] * 1e6:.1f}us -> "
                    f"{after[name][statistic] * 1e6:.1f}us, {change * 100:+.1f}%"
                )

    print()
    for statistic in STATISTICS:
        values = spreads[statistic]
        if values:
            print(
                f"    {statistic:<7}"
                f"   worst {max(values) * 100:+6.1f}%"
                f"   best {min(values) * 100:+6.1f}%"
                f"   median {statistics.median(values) * 100:+6.1f}%"
                f"   |max| {max(abs(value) for value in values) * 100:5.1f}%"
            )
    for name in sorted(set(before) ^ set(after)):
        print(f"    not compared (one side only): {_short(name)}")

    if offenders:
        statistic, threshold = fail_over  # type: ignore[misc]
        print(
            f"\nregressed past {statistic} {threshold * 100:.0f}%:\n  "
            + "\n  ".join(offenders)
            + "\n\nCompare against the noise floor printed above before acting on this. Two runs "
            "of identical code on this repository's own development machine have differed by 12% "
            "on a single row.",
            file=sys.stderr,
        )
        return 1
    return 0


def _short(fullname: str) -> str:
    """A benchmark's name without the file path, which is the same for every row."""
    return fullname.split("::", 1)[-1]


def _threshold(text: str) -> tuple[str, float]:
    """Parse `median:25%` into `("median", 0.25)`."""
    statistic, _, percent = text.partition(":")
    if statistic not in STATISTICS:
        raise argparse.ArgumentTypeError(
            f"statistic must be one of {STATISTICS}, not {statistic!r}"
        )
    if statistic == "mean":
        raise argparse.ArgumentTypeError(
            "refusing to gate on `mean`: one outlier in twenty thousand rounds moves it further "
            "than the effect being measured. See this module's docstring. Gate on median or min."
        )
    return statistic, float(percent.rstrip("%")) / 100


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description="Run the measurements, or compare two of them.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the timings")
    parser.add_argument("--timings-only", action="store_true", help="skip memray and the import")
    parser.add_argument(
        "--compare", nargs=2, type=Path, metavar=("BASE", "HEAD"), help="compare two JSON files"
    )
    parser.add_argument("--fail-over", type=_threshold, help="gate, e.g. median:25%%")
    parser.add_argument("--label", default="comparison", help="heading for the comparison")
    parsed = parser.parse_args()

    if parsed.compare:
        return compare(parsed.compare[0], parsed.compare[1], parsed.fail_over, parsed.label)

    status = run_timings(parsed.out)
    if parsed.timings_only:
        return status
    return run_ceilings() or check_import_time() or status


if __name__ == "__main__":
    raise SystemExit(main())
