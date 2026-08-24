#!/usr/bin/env python
"""Prove the source distribution is self-testing, from the distribution rather than the checkout.

`pyproject.toml` ships `tests/` and `corpus/` on purpose, and says why: Debian, Fedora and
conda-forge rebuild from the sdist and run the suite to validate the build, and for this package
that matters more than usual, because **the corpus is the security argument** and a distribution
carrying the code without the tests that prove it is shipping an unverifiable claim.

That is a promise about an artifact nobody in the repository ever runs. Running the suite in a
checkout says nothing about it: the checkout has `.github/`, `.git`, `uv.lock` and a synced
environment, and the sdist has none of those. The gap is not hypothetical. The first time this
script ran, `tests/test_lanes.py` failed from the sdist with `FileNotFoundError`, because it reads
`.github/workflows/ci.yml` and CI plumbing is not in the distribution.

So: build both artifacts, unpack the sdist, install the wheel into an interpreter that has only
pytest and hypothesis, and run the shipped suite from the unpacked tree. **The isolation is the
lane**, the same way it is for `check_zero_deps.py`.

Run through the lane runner: `python scripts/lanes.py sdist`.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# What a downstream packager has when they run our suite: a test runner and whatever the package
# declares. Nothing else, and in particular nothing from the `measure` group, so the benchmark
# directory skips itself exactly as it does in the `corpus` and `compat` lanes.
TEST_REQUIREMENTS = ("pytest", "hypothesis")


def _run(
    argv: list[str], *, diagnosis: str | None = None, cwd: Path | None = None, quiet: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command and fail loudly on a non-zero status.

    Args:
        argv: The command.
        diagnosis: What a failure of this particular step means, since a raw traceback says what
            broke and not why it matters.
        cwd: Working directory.
        quiet: Capture output. The test run passes `False` so its progress is visible.

    Returns:
        The completed process.

    Raises:
        SystemExit: On a non-zero status.
    """
    print(f"    $ {' '.join(argv)}")
    proc = subprocess.run(  # type: ignore[call-overload]
        argv, capture_output=quiet, text=True, check=False, cwd=cwd
    )
    if proc.returncode != 0:
        if quiet:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
        raise SystemExit(diagnosis or f"command failed with {proc.returncode}: {' '.join(argv)}")
    return proc


def _unpack(sdist: Path, into: Path) -> Path:
    """Extract `sdist` and return the single directory it contains.

    Raises:
        SystemExit: If the archive escapes its own directory, or does not hold exactly one.
    """
    with tarfile.open(sdist) as archive:
        roots = {Path(name).parts[0] for name in archive.getnames() if name.strip("./")}
        # An archive member with an absolute path or a `..` component writes outside the
        # destination. Nothing we build does that; checking costs one pass and this script
        # unpacks an archive as its whole job.
        for member in archive.getmembers():
            target = Path(member.name)
            if target.is_absolute() or ".." in target.parts:
                raise SystemExit(f"sdist member escapes its directory: {member.name}")
        archive.extractall(into)  # noqa: S202
    if len(roots) != 1:
        raise SystemExit(f"expected one directory in the sdist, got {sorted(roots)}")
    return into / roots.pop()


def main() -> int:
    """Build, unpack, install and run the shipped suite.

    Returns:
        0 when the suite passes from the distribution. Raises SystemExit otherwise.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        dist = work / "dist"

        print("==> building the sdist and the wheel from it")
        # Not `--sdist` alone: `uv build` with neither flag builds the sdist and then builds the
        # wheel *from* it, which is the round trip a packager performs and the one that catches a
        # file the wheel needs and the sdist does not carry.
        _run(
            ["uv", "build", "--out-dir", str(dist)],
            cwd=REPO_ROOT,
            diagnosis="the sdist or the wheel built from it did not build at all",
        )
        sdists = sorted(dist.glob("*.tar.gz"))
        wheels = sorted(dist.glob("*.whl"))
        if len(sdists) != 1 or len(wheels) != 1:
            raise SystemExit(f"expected one sdist and one wheel, got {sdists} and {wheels}")
        print(f"    sdist: {sdists[0].name}")

        print("==> unpacking the sdist")
        unpacked = _unpack(sdists[0], work / "unpacked")
        if not (unpacked / "tests").is_dir() or not (unpacked / "corpus").is_dir():
            raise SystemExit(
                f"{sdists[0].name} does not carry tests/ and corpus/. The security claim of this "
                f"package is a corpus of published escapes; a distribution without it is a claim "
                f"nobody downstream can check"
            )

        print("==> creating an interpreter with a test runner and nothing else")
        venv = work / "venv"
        _run(["uv", "venv", "--no-project", str(venv)])
        python = next(
            (p for p in (venv / "bin" / "python", venv / "Scripts" / "python.exe") if p.exists()),
            None,
        )
        if python is None:
            raise SystemExit(f"no interpreter found under {venv}")

        print("==> installing the wheel and the test runner")
        _run(
            ["uv", "pip", "install", "--python", str(python), str(wheels[0]), *TEST_REQUIREMENTS],
        )

        print("==> running the shipped suite from the unpacked sdist")
        # From the unpacked directory, so `testpaths` and every path a test resolves relative to
        # its own location point inside the distribution rather than back at the checkout.
        #
        # `--runslow` for the same reason the `fast` lane passes it: two tests are deselected from a
        # developer's inner loop and must run wherever a green result is meant to mean the suite
        # passed. This lane is also the one that would notice `tests/conftest.py` or `scripts/`
        # falling out of the distribution, since the flag is defined in the first and the tests it
        # re-selects run the second.
        _run(
            [str(python), "-m", "pytest", "-q", "--no-header", "--runslow"],
            cwd=unpacked,
            quiet=False,
            diagnosis=(
                "the shipped suite does not pass from the source distribution. A test that reads "
                "a file the sdist does not carry is the usual cause: `.github/`, `uv.lock` and "
                "`.git` are all absent there. Make the test skip when what it needs is missing, "
                "and say so in the skip reason, rather than adding the file to the sdist"
            ),
        )

        print(f"\nthe sdist is self-testing: {sdists[0].name} passes its own suite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
