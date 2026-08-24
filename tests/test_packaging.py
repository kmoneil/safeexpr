"""What a user actually receives, asserted rather than assumed.

Packaging defects are silent: a missing licence file, a version that disagrees with itself, a
build requirement that has drifted from the group that pins it. None of them fail a test suite
that only exercises the library, and all of them are discovered by somebody else after release.
"""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import safeexpr

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
LICENCE = ROOT / "LICENSE"


@pytest.fixture(scope="module")
def config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_the_licence_text_ships_in_the_repository() -> None:
    """The identifier is not the grant. `license = "Apache-2.0"` is an SPDX expression; the text
    is a separate obligation under section 4(a) and it is this file."""
    assert LICENCE.is_file(), "LICENSE is missing; the SPDX identifier alone grants nothing"
    text = LICENCE.read_text(encoding="utf-8")
    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "3. Grant of Patent License." in text


def test_the_version_is_single_sourced(config: dict) -> None:
    """`safeexpr.__version__` and the installed distribution cannot name different releases.

    hatchling reads the version out of the package, so these agree by construction. This asserts
    the construction rather than the value, because the failure mode is somebody replacing
    `dynamic` with a hard-coded `version` and leaving `__init__.py` behind.
    """
    assert "version" in config["project"].get("dynamic", []), (
        "[project] no longer declares a dynamic version, so pyproject.toml and __init__.py can "
        "now disagree"
    )
    assert config["tool"]["hatch"]["version"]["path"] == "src/safeexpr/__init__.py"
    try:
        installed = md.version("safeexpr")
    except md.PackageNotFoundError:  # pragma: no cover - only from a bare checkout
        pytest.skip("safeexpr is not installed")
    assert installed == safeexpr.__version__


def test_the_build_group_agrees_with_the_build_requirement(config: dict) -> None:
    """PEP 517 build requirements resolve fresh from PyPI in an isolated environment, so they are
    the one declaration `uv.lock` does not cover. The `build` group exists to pin them, and a
    group that has drifted produces a build that quietly re-resolves, which looks exactly like
    this working."""
    requires = config["build-system"]["requires"]
    group = config["dependency-groups"]["build"]
    names = {re.split(r"[<>=!~\[]", spec)[0].strip().lower() for spec in requires}
    grouped = {re.split(r"[<>=!~\[]", spec)[0].strip().lower() for spec in group}
    assert names == grouped, (
        f"[build-system] requires {sorted(names)} but the `build` group pins {sorted(grouped)}"
    )


def test_the_floor_is_declared_everywhere_it_is_claimed(config: dict) -> None:
    """The floor appears in four places and they have to move together: `requires-python`, the
    classifiers, ruff's `target-version` and mypy's `python_version`.

    A classifier list that has drifted from `requires-python` tells pip one thing and a human
    another.
    """
    assert config["project"]["requires-python"] == ">=3.11"
    classifiers = config["project"]["classifiers"]
    claimed = {
        c.rsplit(" :: ", 1)[-1]
        for c in classifiers
        if c.startswith("Programming Language :: Python :: 3.")
    }
    assert claimed == {"3.11", "3.12", "3.13", "3.14"}, (
        f"classifiers claim {sorted(claimed)}; the floor is 3.11 and the matrix runs to 3.14"
    )
    assert config["tool"]["ruff"]["target-version"] == "py311", (
        "ruff's target-version is the only gate that stops 3.12+ *syntax* reaching src/"
    )
    assert config["tool"]["mypy"]["python_version"] == "3.11", (
        "mypy's python_version checks the floor's *semantics*; it is not interchangeable with "
        "ruff's target-version"
    )


def test_the_package_is_typed() -> None:
    """The `Typing :: Typed` classifier is a promise PEP 561 keeps only if the marker ships."""
    assert (ROOT / "src" / "safeexpr" / "py.typed").is_file(), (
        "py.typed is missing, so the Typing :: Typed classifier is a claim no type checker honours"
    )


def test_the_sdist_ships_the_corpus_and_the_tests(config: dict) -> None:
    """The corpus is the security argument. A distribution shipping the code without the tests
    that prove it is shipping an unverifiable claim, which is why the sdist include list names
    both.

    `THREAT-MODEL.md` is on the list for the same reason: it is the map from a failure class to
    the corpus entries proving it, and the corpus without the map is a thousand lines of JSON.
    """
    include = config["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    for required in ("/src", "/tests", "/corpus", "/LICENSE", "/README.md", "/THREAT-MODEL.md"):
        assert required in include, f"sdist would not ship {required}"


def test_the_changelog_has_a_section_for_the_packaged_version() -> None:
    """Either a heading for the packaged version, or `Unreleased` before the first tag.

    Both are honest at different moments, which is why this accepts either. What closes the gap is
    `release.yml`: tagging refuses a version with no `## <version>` section, and tagging is the
    only moment at which "not released yet" and "forgot to write it up" become distinguishable.
    """
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = re.escape(safeexpr.__version__)
    assert re.search(rf"^## {version}( |$)", text, re.MULTILINE) or re.search(
        r"^## Unreleased( |$)", text, re.MULTILINE
    ), f"CHANGELOG.md has neither a '## {safeexpr.__version__}' section nor '## Unreleased'"


def test_the_project_urls_are_absolute_and_share_one_host(config: dict) -> None:
    """A renamed repository leaves dead links in METADATA that nothing else notices.

    Sharing a host is the cheap check that catches a half-finished rename, where some URLs moved
    and some did not.
    """
    urls = config["project"]["urls"]
    for required in ("Homepage", "Source", "Issues", "Changelog"):
        assert required in urls, f"[project.urls] lost {required}"
    hosts = set()
    for name, url in urls.items():
        assert url.startswith("https://"), f"{name} is not an absolute https URL: {url}"
        hosts.add(urlsplit(url).netloc)
    assert len(hosts) == 1, f"[project.urls] spans several hosts, usually a typo: {sorted(hosts)}"


def test_the_changelog_link_points_at_a_file_that_exists(config: dict) -> None:
    """The link is to a path in this repository, so it can be checked here rather than by a
    reader clicking it after release."""
    changelog = config["project"]["urls"]["Changelog"].rsplit("/", 1)[-1]
    assert (ROOT / changelog).is_file(), f"Changelog URL points at {changelog}, which is missing"


def test_the_interpreter_running_this_is_one_we_claim() -> None:
    """A guard against the suite silently passing on something outside the support window."""
    assert sys.version_info >= (3, 11), (
        f"running on {sys.version_info.major}.{sys.version_info.minor}, below the declared floor"
    )


# Directories that hold working notes rather than product: plans, scratch, review write-ups. They
# are not in the sdist include list, so nothing in them ships. What can still leak is a *reference*
# to one, or to the card ids kept inside them, written into a file that does ship.
# Assembled from parts rather than written out, so that **this file is covered by its own scan**.
# Excluding the checker from the check is the one blind spot a check like this reliably grows.
#
# The boundaries matter more than they look. A plain substring search for the fourth name below
# finds it inside `test_a_clean_error_reports_nothing`, which is a test name and not a reference
# to anything, so the pattern requires the leading underscore to start a word and the name to end
# one.
PRIVATE_NAMES = ("plans", "reviews", "reports", "tmp")
PRIVATE_MARKERS = tuple("_" + name for name in PRIVATE_NAMES)
PRIVATE_REF = re.compile(r"(?<![A-Za-z0-9])_(" + "|".join(PRIVATE_NAMES) + r")(?![A-Za-z0-9_])")
# The pointing kind: a path into one of them, which is never acceptable in a shipped file.
PRIVATE_PATH = re.compile(r"(?<![A-Za-z0-9])_(" + "|".join(PRIVATE_NAMES) + r")/")
CARD_ID = re.compile(r"\bSE-\d{3}\b")

# Everything the sdist include list names, which is exactly what a user downloads.
SHIPPED = ("src", "tests", "corpus", "scripts")
SHIPPED_FILES = ("README.md", "CHANGELOG.md", "THREAT-MODEL.md", "SECURITY.md", "pyproject.toml")


def _shipped_text_files() -> list[Path]:
    found = [ROOT / name for name in SHIPPED_FILES]
    for directory in SHIPPED:
        found += [
            path
            for path in sorted((ROOT / directory).rglob("*"))
            if path.is_file()
            and path.suffix in {".py", ".md", ".jsonl", ".toml", ".txt"}
            and "__pycache__" not in path.parts
        ]
    return found


class TestNothingShippedPointsAtSomethingPrivate:
    """The working notes stay out of the download, and so do references to them.

    `tests/` and `corpus/` ship deliberately, because the corpus is the security argument and a
    distribution without the tests that prove it is shipping an unverifiable claim. The cost of
    that decision is that a docstring written while working from a planning card can carry the
    card's id into a published artifact, where it names a document the reader cannot open.

    This is the check for the thing the include list cannot catch: not what ships, but what the
    shipped files point at.
    """

    def test_the_scan_covers_what_actually_ships(self) -> None:
        """A scan reading nothing would pass both tests below."""
        found = _shipped_text_files()
        assert len(found) > 40, f"only {len(found)} shipped files scanned"
        assert any(path.name == "escapes-v1.jsonl" for path in found)

    def test_no_shipped_file_names_a_private_directory(self) -> None:
        """One exemption, and it is a different kind of reference.

        `pyproject.toml` names the scratch directory in ruff's `extend-exclude` and pytest's
        `norecursedirs`, which is tooling being told to skip it rather than a reader being told to
        go and read it. A path-like reference is still a finding there, because that would be the
        pointing kind.
        """
        offenders = []
        for path in _shipped_text_files():
            text = path.read_text(encoding="utf-8")
            found = set(PRIVATE_PATH.findall(text))
            if path.name != "pyproject.toml":
                found |= set(PRIVATE_REF.findall(text))
            if found:
                offenders.append(f"{path.relative_to(ROOT)}: {sorted(found)}")
        assert offenders == [], f"shipped files pointing at working notes: {offenders}"

    def test_no_shipped_file_cites_a_planning_card(self) -> None:
        """A card id is a reference to a file that is not in the download and never will be."""
        offenders = []
        for path in _shipped_text_files():
            found = CARD_ID.findall(path.read_text(encoding="utf-8"))
            if found:
                offenders.append(f"{path.relative_to(ROOT)}: {sorted(set(found))}")
        assert offenders == [], f"shipped files citing planning cards: {offenders}"

    def test_the_scan_catches_both_shapes(self) -> None:
        """A checker that cannot fail is decoration.

        The examples are built rather than typed, for the same reason the markers above are: a
        literal here would be a finding here.
        """
        card = f"SE-{29:03d}"
        directory = PRIVATE_MARKERS[0]
        assert PRIVATE_REF.findall(f"see {directory}/BACKLOG.md") == [PRIVATE_NAMES[0]]
        assert PRIVATE_PATH.findall(f"see {directory}/BACKLOG.md") == [PRIVATE_NAMES[0]]
        assert PRIVATE_PATH.findall(f'norecursedirs = ["{directory}"]') == []
        assert CARD_ID.findall(f"this is {card}'s second criterion") == [card]
        assert CARD_ID.findall("a normal sentence about SE and 029") == []
        assert CARD_ID.findall("SE-1234 is not a card id") == []

    def test_the_scan_does_not_fire_on_an_ordinary_test_name(self) -> None:
        """The false positive that made the first version of this useless: the fourth private
        name is a substring of a word people write."""
        assert PRIVATE_REF.findall("def test_a_clean_error_reports_nothing() -> None:") == []
        assert PRIVATE_REF.findall("the parser reports a real position") == []
