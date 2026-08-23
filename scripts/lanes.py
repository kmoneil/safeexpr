#!/usr/bin/env python
"""The single spelling of how this project runs its checks.

CI invokes lanes by name and never spells out a `pytest` or `ruff` command of its own. Two
spellings of "how this project runs its tests" is how a lane ends up with one set of flags
locally and another in CI, and the difference is only discovered when the two disagree about a
failure. `tests/test_lanes.py` asserts that every lane defined here is named in
`.github/workflows/ci.yml`, so a lane cannot be added and left unwired.

Run `python scripts/lanes.py` with no arguments for the table.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Lane:
    """One named check, with the reason it exists and what it needs to run."""

    name: str
    checks: str
    needs: str
    command: tuple[str, ...]


LANES: tuple[Lane, ...] = (
    Lane(
        name="gates",
        checks="ruff lint over the tree",
        needs="uv sync. Nothing here is platform-dependent",
        # Split from `types` rather than bundled, because the two check different things and a
        # combined lane hides which half failed. ruff's `target-version = "py310"` is what stops
        # 3.11+ *syntax* reaching `src/`; mypy's `python_version` checks the floor's *semantics*.
        # Neither sees what the other does, and CI runs all three.
        command=("ruff", "check", "."),
    ),
    Lane(
        name="format",
        checks="ruff format --check over the tree",
        needs="uv sync",
        command=("ruff", "format", "--check", "."),
    ),
    Lane(
        name="types",
        checks="mypy --strict over src/, at the 3.11 floor's semantics",
        needs="uv sync",
        command=("mypy",),
    ),
    Lane(
        name="zero-deps",
        checks=(
            "the claim on the front of the README: a built wheel declares no unconditional "
            "runtime requirement, and importing the package in an interpreter with nothing "
            "else in it loads no third-party module"
        ),
        needs=(
            "uv, and an interpreter with nothing else in it. **The isolation is the lane.** "
            "tests/test_zero_deps.py reads METADATA and can run anywhere, but it cannot catch a "
            "module that grew an import of something the development environment happens to "
            "have installed. Only a clean interpreter can see that, so this cannot be a flag "
            "on `fast`"
        ),
        command=("python", "scripts/check_zero_deps.py"),
    ),
    Lane(
        name="fast",
        checks="the unit suite on the development interpreter",
        needs="uv sync --frozen",
        command=("pytest",),
    ),
    Lane(
        name="corpus",
        checks=(
            "the escape corpus: every published escape class this package claims is "
            "structurally unreachable, each asserted to be rejected at the stage it declares"
        ),
        needs=(
            "uv sync --frozen. Its own lane rather than a subset of `fast` because it is the "
            "artifact a security reviewer asks to see run, and a reviewer should not have to "
            "read a full test run to find it"
        ),
        command=("pytest", "tests/test_corpus.py", "-v", "--no-header"),
    ),
    Lane(
        name="compat",
        checks=(
            "the same suite on one supported interpreter, invoked with that interpreter's own "
            "pytest"
        ),
        needs=(
            "an environment built for the matrix row's interpreter, which is what `_resolve` "
            "below looks for beside `sys.executable` before it falls back to `.venv/`"
        ),
        command=("pytest",),
    ),
)

LANES_BY_NAME: dict[str, Lane] = {lane.name: lane for lane in LANES}


def _resolve(tool: str) -> str:
    """Find a lane's tool, preferring the interpreter that invoked this script.

    The order matters for `compat`: CI runs that lane as
    `.venv-compat/bin/python scripts/lanes.py compat`, and looking beside `sys.executable`
    first is what makes it pick that row's pytest rather than the development one.

    Args:
        tool: The executable name.

    Returns:
        An absolute path to the executable.

    Raises:
        SystemExit: If the tool is not installed anywhere this can see it.
    """
    if tool == "python":
        return sys.executable
    beside = Path(sys.executable).parent / tool
    if beside.exists():
        return str(beside)
    candidate = REPO_ROOT / ".venv" / "bin" / tool
    if candidate.exists():
        return str(candidate)
    found = shutil.which(tool)
    if found is None:
        raise SystemExit(f"lane tool not found: {tool!r} (is the environment synced?)")
    return found


def run_lane(lane: Lane) -> int:
    """Run one lane and return its exit status.

    Args:
        lane: The lane to run.

    Returns:
        The process exit status.
    """
    argv = (_resolve(lane.command[0]), *lane.command[1:])
    print(f"==> {lane.name}: {' '.join(lane.command)}")
    return subprocess.run(argv, cwd=REPO_ROOT, check=False).returncode


def _table() -> None:
    """Print every lane, what it checks and what it needs."""
    for lane in LANES:
        print(f"{lane.name}\n    checks: {lane.checks}\n    needs:  {lane.needs}\n")


def main() -> int:
    """Entry point.

    Returns:
        The exit status of the last lane run, or 0 for the table.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lanes", nargs="*", help="lane names to run; omit for the table")
    parsed = parser.parse_args()
    if not parsed.lanes:
        _table()
        return 0
    unknown = [name for name in parsed.lanes if name not in LANES_BY_NAME]
    if unknown:
        known = ", ".join(lane.name for lane in LANES)
        raise SystemExit(f"unknown lane(s): {', '.join(unknown)}. Known lanes: {known}")
    status = 0
    for name in parsed.lanes:
        status = run_lane(LANES_BY_NAME[name]) or status
    return status


if __name__ == "__main__":
    raise SystemExit(main())
