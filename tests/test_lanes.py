"""Every lane the runner knows about must be named in CI.

`scripts/lanes.py` exists so there is one spelling of how this project runs its checks. That only
holds if CI actually invokes the lanes, so a lane added to the runner and left unwired is a check
nobody runs, which is worse than an absent one because the table implies coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lanes  # noqa: E402


def test_every_lane_is_invoked_by_ci() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    missing = [lane.name for lane in lanes.LANES if f"lanes.py {lane.name}" not in workflow]
    assert not missing, (
        f"lanes defined but never invoked by ci.yml: {missing}. A lane nobody runs is a check "
        f"the table claims and CI does not perform"
    )


def test_every_lane_explains_itself() -> None:
    """`checks` and `needs` are what make the table worth printing."""
    for lane in lanes.LANES:
        assert lane.checks.strip(), f"lane {lane.name!r} does not say what it checks"
        assert lane.needs.strip(), f"lane {lane.name!r} does not say what it needs"
