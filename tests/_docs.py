"""Shared reading for the tests that check a document against the code.

Not a test module. `THREAT-MODEL.md` and `SECURITY.md` both make claims that go stale the moment
something they describe moves, and both are checked the same way, so the mechanics of reading
markdown live in one place rather than being spelled twice and drifting.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HEADING = re.compile(r"^(#{1,6}) +(.*?)\s*$", re.MULTILINE)

# House style: no em dashes anywhere a reader can see. Kept here so both documents are held to it.
EM_DASH = "—"


def read(name: str) -> str:
    """The text of a document at the repository root."""
    return (ROOT / name).read_text(encoding="utf-8")


def pyproject() -> dict:
    """The project's own metadata, so a document can be checked against it rather than a memory."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def supported_pythons() -> list[str]:
    """The versions the package claims to support, from its classifiers.

    The classifiers rather than `requires-python`, because a floor names one version and this
    needs the whole list: a document that promises support has to promise it for each of them.
    """
    prefix = "Programming Language :: Python :: "
    return sorted(
        classifier.removeprefix(prefix)
        for classifier in pyproject()["project"]["classifiers"]
        # `Python :: 3 :: Only` is a statement about Python 2, not a version we ship against.
        if classifier.startswith(prefix) and "." in classifier.removeprefix(prefix)
    )


def development_status() -> str:
    """The `Development Status` classifier, which three places have to agree about."""
    prefix = "Development Status :: "
    found = [c for c in pyproject()["project"]["classifiers"] if c.startswith(prefix)]
    return found[0].removeprefix(prefix)


def slug(heading: str) -> str:
    """The anchor a markdown renderer gives a heading, near enough for the headings we write."""
    kept = "".join(c for c in heading.lower() if c.isalnum() or c in " -_")
    return kept.strip().replace(" ", "-")


def slugs(text: str) -> set[str]:
    return {slug(match.group(2)) for match in HEADING.finditer(text)}


def relative_link_targets(text: str) -> list[str]:
    """Links into the repository, with any anchor stripped. External links are somebody else's."""
    return [
        target.split("#")[0]
        for target in LINK.findall(text)
        if not target.startswith(("#", "http://", "https://", "mailto:"))
    ]


def anchor_link_targets(text: str) -> list[str]:
    """Links to a heading in the same document."""
    return [target[1:] for target in LINK.findall(text) if target.startswith("#")]
