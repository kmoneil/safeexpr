"""Every lane the runner knows about must be named in CI.

`scripts/lanes.py` exists so there is one spelling of how this project runs its checks. That only
holds if CI actually invokes the lanes, so a lane added to the runner and left unwired is a check
nobody runs, which is worse than an absent one because the table implies coverage.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import lanes  # noqa: E402


def test_every_lane_is_invoked_by_ci() -> None:
    """**Not applicable outside a checkout, and that is a real distinction rather than a dodge.**

    `tests/` and `corpus/` ship in the sdist on purpose, because downstream packagers rebuild from
    it and run this suite to validate the build, and for this package that matters more than usual:
    the corpus is the security argument. `.github/` does not ship, because CI plumbing is not the
    product and shipping it to keep a test green would be the wrong way round.

    So in a distribution the workflow is genuinely absent, and this check has nothing to compare
    against. It skips there and stays mandatory in a checkout, which is where the wiring can
    actually be wrong. `test_the_workflow_is_present_in_a_checkout` below is what stops the skip
    from hiding a deleted file.
    """
    if not WORKFLOW.is_file():
        pytest.skip("no .github/workflows/ci.yml: this is a distribution, not a checkout")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    missing = [lane.name for lane in lanes.LANES if f"lanes.py {lane.name}" not in workflow]
    assert not missing, (
        f"lanes defined but never invoked by ci.yml: {missing}. A lane nobody runs is a check "
        f"the table claims and CI does not perform"
    )


def test_the_workflow_is_present_in_a_checkout() -> None:
    """The other half of the skip above.

    A skip keyed on a missing file passes just as quietly when somebody deletes the file, so the
    two conditions are separated: in a checkout, identified by `.git`, the workflow must exist.
    """
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert WORKFLOW.is_file(), f"{WORKFLOW.relative_to(ROOT)} is missing from the checkout"


def test_every_lane_explains_itself() -> None:
    """`checks` and `needs` are what make the table worth printing."""
    for lane in lanes.LANES:
        assert lane.checks.strip(), f"lane {lane.name!r} does not say what it checks"
        assert lane.needs.strip(), f"lane {lane.name!r} does not say what it needs"


def _workflow(path: Path) -> str:
    """A workflow's text, or a skip.

    Same reason as `test_every_lane_is_invoked_by_ci`: `.github/` does not ship, so in a
    distribution these files are genuinely absent rather than missing.
    """
    if not path.is_file():
        pytest.skip(f"no {path.name}: this is a distribution, not a checkout")
    return path.read_text(encoding="utf-8")


def test_third_party_actions_are_pinned_to_a_sha() -> None:
    """A floating tag is a mutable reference to somebody else's code.

    `release.yml` runs with permission to publish, so this is not a style rule. A tag is moved by
    whoever owns it, and the thing it moves is code running in a job that can mint a token for
    this repository.
    """
    for path in (WORKFLOW, RELEASE):
        for line in _workflow(path).splitlines():
            match = re.search(r"uses:\s*(\S+)", line)
            if match is None or match.group(1).startswith("./"):
                continue
            reference = match.group(1)
            _, _, version = reference.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", version), (
                f"{path.name}: {reference} is not pinned to a commit SHA"
            )
            assert "#" in line, f"{path.name}: {reference} has no trailing tag comment"


def test_release_keeps_the_decisions_that_are_written_out() -> None:
    """Defaults a documented decision rests on are restated, so an edit cannot silently drop one.

    Each of these is a line somebody could remove while reading the file as unchanged, because
    the action's own default would keep the behaviour until the day it did not.
    """
    text = _workflow(RELEASE)
    assert "attestations: true" in text, (
        "SECURITY.md tells readers provenance is a signed PEP 740 attestation; "
        "that is only true while one is produced"
    )
    assert "digest-mismatch: error" in text, (
        "without it a corrupted artifact is logged and then uploaded"
    )
    assert "--no-build-isolation" in text, (
        "without it the build backend is resolved fresh from PyPI, unpinned and unhashed, and "
        "the `build` dependency group exists for no reason"
    )


def _executable(path: Path) -> str:
    """A workflow with its comment lines removed.

    **Not a tidy-up.** This file argues at length for why there is no long-lived token and why
    exactly one job may mint one, so a naive substring search finds the very strings it is looking
    for inside the paragraphs arguing for them, and fails a correct file. What the checks below
    care about is what the runner executes.
    """
    return "\n".join(
        line for line in _workflow(path).splitlines() if not line.lstrip().startswith("#")
    )


def test_release_holds_no_long_lived_credential() -> None:
    """Trusted publishing means there is no token to store, so there must not be one."""
    executable = _executable(RELEASE)
    assert "PYPI_API_TOKEN" not in executable
    assert "secrets." not in executable, "release.yml reads a repository secret"
    assert "id-token: write" in executable, "nothing can mint the publishing token"


def test_the_release_gate_runs_the_lanes_that_read_an_artifact() -> None:
    """The two that matter at the moment of publishing, and the one deliberately absent.

    `zero-deps` and `sdist` are the only lanes that check the thing a user downloads rather than
    the tree it was built from, and both check a promise a published version cannot take back.
    `compat` is not here on purpose: it means the matrix, which is a `ci.yml` construct, and one
    row of it on the development interpreter would look like coverage and be one row.
    """
    text = _workflow(RELEASE)
    for lane in ("zero-deps", "sdist", "gates", "fast", "corpus"):
        assert f"lanes.py {lane}" in text, f"release.yml does not run the {lane} lane"
    assert "lanes.py compat" not in text, (
        "compat in release.yml is a single row wearing a matrix's clothes; see the job comment"
    )


def test_publishing_is_the_only_job_that_can_publish() -> None:
    """`id-token: write` is granted to one job, not to the workflow."""
    executable = _executable(RELEASE)
    assert executable.count("id-token: write") == 1
    assert "permissions:\n  contents: read\n" in executable, (
        "the workflow's default permissions are not read-only"
    )
    # `contents: write` exists for the GitHub release and must not spread either.
    assert executable.count("contents: write") == 1
