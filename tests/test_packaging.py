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
    both."""
    include = config["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    for required in ("/src", "/tests", "/corpus", "/LICENSE", "/README.md"):
        assert required in include, f"sdist would not ship {required}"


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
