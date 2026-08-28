"""Nothing published from this repository carries tool attribution.

A session link is a pointer to a private transcript, and this repository is public. Six pull
request bodies and all thirty-seven commits on `main` carried one before the lane existed.

The interesting half of these tests is not that the four forms are caught. It is that writing
*about* them stays possible: this file, the script it tests, and the pull request that introduced
both, all quote the exact strings being refused. A check that fails the change explaining it is a
check somebody switches off, which is how the gap it closes stayed open in the first place.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_attribution  # noqa: E402
import lanes  # noqa: E402

SESSION = "https://claude.ai/code/session_01Esnm9mNDpqRWf4QAVHCwHo"


@pytest.mark.parametrize(
    ("text", "form"),
    [
        (f"Subject\n\nClaude-Session: {SESSION}", "session trailer"),
        (f"Subject\n\nSee {SESSION} for the reasoning.", "session link"),
        (
            "Subject\n\nCo-authored-by: Claude <noreply@anthropic.com>",
            "co-author trailer",
        ),
        (
            "Subject\n\n\N{ROBOT FACE} Generated with [Claude Code](https://claude.com/)",
            "generated-with line",
        ),
        ("Subject\n\nGenerated with Claude Code", "generated-with line"),
    ],
)
def test_each_published_form_is_refused(text, form):
    findings = check_attribution.scan(text, "commit abc1234")
    assert [f.form.name for f in findings] == [form]


def test_writing_about_the_forms_is_still_possible():
    """**The property that keeps this check alive.**

    Every line here names a refused form in prose. If any of them tripped the check, the commit
    adding the check could not describe itself, and neither could this file.
    """
    prose = (
        "Refuse the Claude-Session: trailer on commits.\n"
        "The Co-authored-by: trailer naming noreply@anthropic.com is refused too.\n"
        "So is a line reading Generated with Claude Code.\n"
        "Session links live under claude.ai/code and must not be published.\n"
    )
    assert check_attribution.scan(prose, "commit abc1234") == []


def test_a_clean_message_passes():
    assert check_attribution.scan("Subject\n\nAn ordinary body.\n", "commit abc1234") == []


def test_one_finding_per_line_not_one_per_form():
    """A trailer carrying a link matches two forms and is one thing to remove."""
    findings = check_attribution.scan(f"Subject\n\nClaude-Session: {SESSION}", "commit abc1234")
    assert len(findings) == 1


def test_the_body_is_checked_as_well_as_the_commits(tmp_path):
    """The six that leaked were bodies, not commit messages."""
    body = tmp_path / "body.md"
    body.write_text(f"A real description.\n\n{SESSION}\n", encoding="utf-8")
    assert _run("--range", "HEAD..HEAD", "--body-file", str(body)).returncode == 1


def test_an_empty_range_is_not_a_failure():
    """A pull request that only edits its own body has no commits, and must still pass."""
    finished = _run("--range", "HEAD..HEAD")
    assert finished.returncode == 0, finished.stderr


def test_the_event_payload_supplies_the_range_and_the_body(tmp_path, monkeypatch):
    """How it runs in CI: neither the range nor the body is passed on the command line."""
    payload = tmp_path / "event.json"
    payload.write_text(
        json.dumps(
            {
                "pull_request": {
                    "base": {"sha": "HEAD"},
                    "head": {"sha": "HEAD"},
                    "body": f"Description.\n\n{SESSION}\n",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
    assert check_attribution.main([]) == 1


def test_a_push_of_a_new_branch_reports_no_range(tmp_path, monkeypatch):
    """An all-zero `before` is not a range, and treating it as one asks git for every commit."""
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({"before": "0" * 40, "after": "abc1234"}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
    assert check_attribution.from_event(json.loads(payload.read_text())) == (None, None)


def test_regression_attribution_the_history_that_made_this_necessary():
    """**A gate nobody has watched fail is not known to work.**

    The real messages, verbatim, rather than a synthetic fixture: this is the exact text that was
    published thirty-seven times and had to be rewritten out of a protected branch.
    """
    published = (
        "The blind-spot ratio flaked on the reference, not on the thing measured\n"
        "\n"
        "The ratio is stable; the reference it divides by is not.\n"
        "\n"
        f"Claude-Session: {SESSION}\n"
    )
    findings = check_attribution.scan(published, "commit 316c220")
    assert len(findings) == 1
    assert findings[0].form.name == "session trailer"
    # And the same message with the trailer removed is what should have been published.
    assert (
        check_attribution.scan(published.split("Claude-Session", maxsplit=1)[0], "commit 316c220")
        == []
    )


def test_regression_attribution_the_lane_is_invoked_by_ci():
    """`test_every_lane_is_invoked_by_ci` covers this, and a named test pins it anyway.

    This is the lane most likely to be quietly dropped: it gates on the *shape* of a message
    rather than on whether the code works, so it is the one that looks safe to skip when a
    release is waiting.
    """
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.is_file():
        pytest.skip("no .github/workflows/ci.yml: this is a distribution, not a checkout")
    assert "lanes.py attribution" in workflow.read_text(encoding="utf-8")
    assert "attribution" in {lane.name for lane in lanes.LANES}


def _run(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_attribution.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
