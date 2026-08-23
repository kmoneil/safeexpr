#!/usr/bin/env python
"""Prove the zero-dependency claim against a built wheel, not against the source tree.

`tests/test_zero_deps.py` reads installed metadata and catches somebody adding an entry to
`[project.dependencies]`. It cannot catch a module that grew `import yaml` at the top, because
the development environment has plenty installed and the import simply succeeds there.

This script is the half that can see that. It builds a wheel, installs it alone into a fresh
interpreter created with `--no-project`, imports the package, and fails if anything outside the
standard library arrived in `sys.modules`.

Run through the lane runner: `python scripts/lanes.py zero-deps`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# What the child interpreter runs once the wheel is the only thing installed. It reports back as
# JSON on stdout so this side does the asserting and the child stays trivially readable.
#
# `sys.stdlib_module_names` is the authority for "is this the standard library", and it is a
# frozenset of top-level names present since 3.10, which is our floor. Comparing against it beats
# checking file paths, which vary by platform and by how the interpreter was built.
PROBE = """
import json, sys
before = set(sys.modules)
import safeexpr
after = set(sys.modules)
arrived = {name.split(".")[0] for name in after - before}
foreign = sorted(
    name for name in arrived
    if name not in sys.stdlib_module_names and not name.startswith("_") and name != "safeexpr"
)
print(json.dumps({
    "version": safeexpr.__version__,
    "foreign": foreign,
}))
"""


def _run(
    argv: list[str], *, diagnosis: str | None = None, **kw: object
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing output, and fail loudly on a non-zero status.

    Args:
        argv: The command.
        diagnosis: What a failure of *this particular* step means. Without it a failing step
            reports a raw traceback, which says what broke but not why it matters.
        **kw: Passed through to `subprocess.run`.

    Returns:
        The completed process.

    Raises:
        SystemExit: On a non-zero status.
    """
    print(f"    $ {' '.join(argv)}")
    proc = subprocess.run(argv, capture_output=True, text=True, check=False, **kw)  # type: ignore[call-overload]
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(diagnosis or f"command failed with {proc.returncode}: {' '.join(argv)}")
    return proc


def main() -> int:
    """Build a wheel, import it in isolation, and assert nothing foreign came with it.

    Returns:
        0 when the claim holds. Raises SystemExit otherwise.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        dist = work / "dist"

        print("==> building a wheel")
        _run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=REPO_ROOT)
        wheels = sorted(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit(f"expected exactly one wheel, got {[w.name for w in wheels]}")
        wheel = wheels[0]

        # R1 is not only "no dependencies", it is "one pure-Python wheel". A wheel tagged for a
        # platform would mean a compiled artifact, which is the procurement problem this package
        # exists to avoid, so the filename is checked rather than assumed.
        if not wheel.name.endswith("-py3-none-any.whl"):
            raise SystemExit(
                f"wheel is not pure Python: {wheel.name}. R1 promises py3-none-any, so a "
                f"platform tag here means a compiled artifact reached the build"
            )
        print(f"    wheel: {wheel.name}")

        print("==> creating an interpreter with nothing in it")
        venv = work / "venv"
        _run(["uv", "venv", "--no-project", str(venv)])
        # POSIX first, which is every CI runner here; the Windows layout is kept so that a
        # developer running this locally on Windows gets the check rather than a path error.
        python = next(
            (p for p in (venv / "bin" / "python", venv / "Scripts" / "python.exe") if p.exists()),
            None,
        )
        if python is None:
            raise SystemExit(f"no interpreter found under {venv}")

        print("==> installing the wheel, and only the wheel")
        _run(["uv", "pip", "install", "--python", str(python), str(wheel)])

        # What is actually installed, read back rather than assumed. `uv pip list` reporting
        # anything beyond our own distribution means the wheel dragged something in.
        listing = _run(["uv", "pip", "list", "--python", str(python), "--format", "json"])
        installed = {pkg["name"].lower().replace("_", "-") for pkg in json.loads(listing.stdout)}
        extra = installed - {"safeexpr"}
        if extra:
            raise SystemExit(
                f"installing the wheel brought {sorted(extra)} with it; "
                f"[project.dependencies] is supposed to be empty"
            )

        print("==> importing it")
        # **The two failure modes here are different and both matter.** In an interpreter this
        # empty, an eager third-party import usually fails outright with ModuleNotFoundError, so
        # the probe exits non-zero and never reports anything: that is what `diagnosis` is for.
        # The `foreign` check below catches the residual case where the import *succeeds*,
        # because the module happened to be reachable, which metadata alone would never show.
        probe = _run(
            [str(python), "-c", PROBE],
            diagnosis=(
                "importing safeexpr failed in an interpreter with nothing else installed. The "
                "usual cause is a module that grew an eager third-party import; the traceback "
                "above names it. Import it lazily, inside the function that needs it, or declare "
                "it behind an extra"
            ),
        )
        report = json.loads(probe.stdout)
        if report["foreign"]:
            raise SystemExit(
                f"importing safeexpr loaded non-stdlib modules: {report['foreign']}. "
                f"A module grew an eager third-party import"
            )

        print(
            f"\nzero-deps holds: safeexpr {report['version']} imports {len(report['foreign'])} "
            f"third-party modules from a wheel with no declared requirements"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
