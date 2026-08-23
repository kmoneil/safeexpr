"""The dependency rule is a test, not a policy.

Intent decays. A build that fails when someone adds a dependency does not. This ships in the
package's own suite so the constraint is visible to a contributor before review rather than after,
and so a downstream packager rebuilding from the sdist checks it too.

**This file is necessary and not sufficient**, and the boundary is worth knowing. It reads declared
metadata, which catches somebody adding an entry to `[project.dependencies]`. It cannot catch a
module that grew `import yaml` at the top, because this suite runs in an environment where plenty
is installed and the eager import simply succeeds. That failure is only visible from an
interpreter with nothing in it. See `scripts/check_zero_deps.py` and the `zero-deps` lane.
"""

from __future__ import annotations

import importlib.metadata as md

import pytest

import safeexpr

DISTRIBUTION = "safeexpr"


def _requires() -> list[str]:
    try:
        return md.requires(DISTRIBUTION) or []
    except md.PackageNotFoundError:  # pragma: no cover - only from a bare checkout
        pytest.skip(f"{DISTRIBUTION} is not installed; install it to check its metadata")


def test_no_unconditional_runtime_dependencies() -> None:
    # Extras carry an `extra == "..."` marker; anything without one is installed for every user,
    # everywhere, and is what R1 promises does not exist.
    hard = [requirement for requirement in _requires() if "extra ==" not in requirement]
    assert hard == [], f"package grew runtime dependencies: {hard}"


def test_the_declared_extras_are_the_two_the_design_allows() -> None:
    """`[dates]` and `[unicode]` are additive capability. A third extra is a design change.

    Both are empty today (the functions that would use them do not exist yet), so this asserts
    the declared *names* rather than their contents.
    """
    meta = md.metadata(DISTRIBUTION)
    declared = set(meta.get_all("Provides-Extra") or [])
    assert declared == {"dates", "unicode"}, (
        f"extras are {sorted(declared)}; core must never require an extra, and a new one is a "
        f"decision for DECISIONS.md rather than a packaging change"
    )


def test_the_public_error_hierarchy_is_importable() -> None:
    """Callers write `except` clauses against these, so they are API rather than internals.

    Also a zero-deps assertion in disguise: importing the package's public surface must not need
    anything beyond the standard library, which the `zero-deps` lane checks from an interpreter
    that has nothing else in it.
    """
    for name in (
        "SafeExprError",
        "ParseError",
        "ValidationError",
        "SourceTooLongError",
        "InternalError",
    ):
        assert name in safeexpr.__all__
        assert issubclass(getattr(safeexpr, name), Exception)
    assert issubclass(safeexpr.ParseError, safeexpr.SafeExprError)
