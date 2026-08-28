#!/usr/bin/env python
"""Refuse tool-attribution in commit messages and in the pull request body.

A session link is a pointer to a private transcript, and this repository is public. Six PR bodies
and every commit on `main` carried one before this lane existed, and the cleanup was a history
rewrite and a force push through a protected branch.

**This is the layer that does not depend on which machine or which kind of session produced the
text.** The generator setting that stops it being written, and the PreToolUse hook that refuses to
publish it, both live in one developer's home directory. Neither is in this repository, neither
travels with a clone, and neither runs for a commit made from a web session, from a phone, or from
somebody else's checkout. This does, because it reads what actually arrived.

WHAT IT REFUSES, AND WHY THREE OF THE FOUR ARE ANCHORED

Three patterns are anchored to the start of a line so that *writing about* them stays possible.
That is not a nicety: this file, its tests and the pull request that introduced them all discuss
the exact strings being refused, and a check that fails the change explaining it is a check
somebody deletes. A trailer occupies a whole line, so anchoring loses nothing real.

The session link is not anchored, because a link can legitimately appear mid-sentence in a body,
which is precisely how it appeared in the six that started this. It is matched as a full URL
carrying an identifier rather than as a bare domain, so prose naming the host is still writable.

Run it through the lane runner: `python scripts/lanes.py attribution`.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class Form:
    """One refused spelling of tool attribution."""

    name: str
    pattern: re.Pattern[str]
    why: str


FORMS: tuple[Form, ...] = (
    Form(
        name="session trailer",
        pattern=re.compile(r"^[ \t]*Claude-Session[ \t]*:", re.MULTILINE),
        why="a commit trailer pointing at a private transcript",
    ),
    Form(
        name="session link",
        pattern=re.compile(r"https?://claude\.ai/code/session[_/]\S+"),
        why="a link to a private transcript, published on a public repository",
    ),
    Form(
        name="co-author trailer",
        pattern=re.compile(
            r"^[ \t]*Co-authored-by:.*noreply@anthropic\.com", re.MULTILINE | re.IGNORECASE
        ),
        why="a synthetic co-author on a commit a person authored",
    ),
    Form(
        name="generated-with line",
        pattern=re.compile(r"^[ \t]*\W*Generated with \[?Claude Code", re.MULTILINE),
        why="a tool advertisement in a commit message",
    ),
)


@dataclass(frozen=True)
class Finding:
    """One refused form, found in one place."""

    where: str
    form: Form
    line: str


@functools.cache
def _git_executable() -> str:
    """The absolute path to git.

    Resolved rather than spelled as a bare name: the lane runs in CI with a PATH this file does
    not control, and a partial executable path is resolved against it at call time.

    Returns:
        An absolute path to the git executable.

    Raises:
        SystemExit: If git is not installed anywhere on PATH.
    """
    found = shutil.which("git")
    if found is None:
        raise SystemExit("git not found on PATH, so there is no history to check")
    return found


def _git(*args: str) -> str:
    """Run git in the repository and return its stdout.

    Args:
        *args: Arguments after `git`.

    Returns:
        Captured stdout, stripped of the trailing newline.

    Raises:
        SystemExit: If git fails, which for these read-only queries means the range is wrong.
    """
    result = subprocess.run(
        [_git_executable(), *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.rstrip("\n")


def scan(text: str, where: str) -> list[Finding]:
    """Every refused form present in one piece of text.

    Args:
        text: The commit message or body to inspect.
        where: How to name this text in the report.

    Returns:
        One finding per matching line, in the order the forms are declared.
    """
    findings: list[Finding] = []
    # One finding per offending line, under the first form that matches it. A trailer carrying a
    # link violates two of these, and reporting it twice doubles the length of a message somebody
    # reads while working out what to remove.
    seen: set[str] = set()
    for form in FORMS:
        for match in form.pattern.finditer(text):
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.start())
            line = (text[start:] if end == -1 else text[start:end]).strip()
            if line in seen:
                continue
            seen.add(line)
            findings.append(Finding(where=where, form=form, line=line))
    return findings


def commit_messages(revision_range: str) -> list[tuple[str, str]]:
    """Every commit in a range, as `(abbreviated sha, full message)`.

    Args:
        revision_range: Anything `git log` accepts, such as `base..head`.

    Returns:
        One pair per commit. Empty when the range is empty, which is not an error: a pull request
        that only edits its own body has no commits to check and must still pass.
    """
    # A NUL between records, because a commit message contains blank lines and may contain any
    # line-oriented separator somebody chooses to write in it.
    raw = _git("log", "--format=%h%x1f%B%x00", revision_range)
    commits: list[tuple[str, str]] = []
    for record in raw.split("\0"):
        if not record.strip():
            continue
        sha, _, message = record.lstrip("\n").partition("\x1f")
        commits.append((sha, message))
    return commits


def _event() -> dict[str, object]:
    """The GitHub event payload, or an empty mapping when running outside Actions.

    Returns:
        The parsed payload, or `{}` when `GITHUB_EVENT_PATH` is unset or unreadable.
    """
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).is_file():
        return {}
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def from_event(event: dict[str, object]) -> tuple[str | None, str | None]:
    """The range and body this event asks us to check.

    Args:
        event: A GitHub Actions event payload.

    Returns:
        A `(revision_range, body)` pair, either of which may be None when the event does not
        carry it. A push of a new branch reports an all-zero `before`, which is not a range.
    """
    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        base = pull_request.get("base")
        head = pull_request.get("head")
        body = pull_request.get("body")
        if isinstance(base, dict) and isinstance(head, dict):
            base_sha, head_sha = base.get("sha"), head.get("sha")
            if isinstance(base_sha, str) and isinstance(head_sha, str):
                return f"{base_sha}..{head_sha}", body if isinstance(body, str) else None
        return None, body if isinstance(body, str) else None

    before, after = event.get("before"), event.get("after")
    if isinstance(before, str) and isinstance(after, str) and before != ZERO_SHA:
        return f"{before}..{after}", None
    return None, None


def _default_range() -> str | None:
    """What to check when nothing told us, which is the local case.

    Returns:
        `origin/main..HEAD` when that resolves, else None. A checkout sitting on `main` with
        nothing ahead of it has nothing to check, and saying so beats inventing a window.
    """
    result = subprocess.run(
        [_git_executable(), "rev-parse", "--verify", "--quiet", "origin/main"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return "origin/main..HEAD" if result.returncode == 0 else None


def report(findings: list[Finding]) -> None:
    """Print findings to stderr in the order they were found.

    Args:
        findings: What `scan` produced.
    """
    print("tool attribution must not be published from this repository:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.where}: {finding.form.name}", file=sys.stderr)
        print(f"    {finding.line}", file=sys.stderr)
        print(f"    {finding.form.why}", file=sys.stderr)
    print(
        "\nRemove the line and amend, or edit the pull request body. If a generator produced it, "
        "switch it off rather than deleting it once.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """Check a commit range and a body for tool attribution.

    Args:
        argv: Command-line arguments, or None to read `sys.argv`.

    Returns:
        0 when nothing was found, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--range", dest="revision_range", help="a git revision range to check")
    parser.add_argument("--body-file", type=Path, help="a file holding a pull request body")
    parser.add_argument("--body", help="a pull request body, given directly")
    arguments = parser.parse_args(argv)

    event = _event()
    event_range, event_body = from_event(event)

    revision_range = arguments.revision_range or event_range or _default_range()
    body = arguments.body
    if body is None and arguments.body_file is not None:
        body = arguments.body_file.read_text(encoding="utf-8")
    if body is None:
        body = event_body

    findings: list[Finding] = []
    checked = 0
    if revision_range is not None:
        for sha, message in commit_messages(revision_range):
            checked += 1
            findings += scan(message, f"commit {sha}")
    if body:
        findings += scan(body, "pull request body")

    if findings:
        report(findings)
        return 1

    scope = f"{checked} commit message{'' if checked == 1 else 's'}"
    if body:
        scope += " and the pull request body"
    elif revision_range is None:
        scope = "nothing: no range given and origin/main is not available"
    print(f"no tool attribution in {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
