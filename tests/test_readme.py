"""The README's numbers, names and examples, checked against the code that makes them true.

Two of this project's own recorded lessons are the same lesson: **a number in a comment is a
number nobody checks**, and the expression depth cap sat at 8.3 times its own stated rule for four
cards because of it. The README is a comment with a wider audience. It publishes a limits table, a
list of every reserved name, a function count and a supported-version range, and every one of
those is a fact about the code written out by hand somewhere else.

`tests/test_limits.py` already asserts that every limit has a published basis in
`scripts/limits.py`. This asserts the other end: that the values a reader is shown are the values
the package actually uses.
"""

from __future__ import annotations

import ast
import doctest
import importlib
import inspect
import re

import pytest

import safeexpr
from _docs import EM_DASH, ROOT, pyproject, read, relative_link_targets, supported_pythons
from safeexpr import Evaluator, SafeExprError, standard_registry
from safeexpr._eval import DEFAULT_STEP_BUDGET, MAX_POWER_RESULT_BITS
from safeexpr._guards import MAX_DATA_NESTING, MAX_RESULT_SIZE, SIZE_CHARGE_UNIT
from safeexpr._parse import MAX_SOURCE_BYTES
from safeexpr._stdlib import COLLECTIONS, DATES, REGEX, STRINGS, TYPES, URLS
from safeexpr._validate import MAX_EXPRESSION_DEPTH

# The rule number the limits work measured against, imported rather than restated: the README's
# sentence about supported scale is that test in words.
from test_limits import HEADROOM

TIERS = (COLLECTIONS, TYPES, STRINGS, REGEX, DATES, URLS)

# The row label in the README's limits table, and the constant it is reporting. Asserted to be
# exactly the table's rows, so a row added without a check here fails rather than going unchecked.
PUBLISHED: dict[str, int] = {
    "Source length": MAX_SOURCE_BYTES,
    "Expression nesting": MAX_EXPRESSION_DEPTH,
    "Data nesting": MAX_DATA_NESTING,
    "Result size": MAX_RESULT_SIZE,
    "Step budget": DEFAULT_STEP_BUDGET,
    # Reported in mebibytes of integer rather than in bits, because a reader cares how big a
    # number can get and not how the cap is spelled.
    "Power result": MAX_POWER_RESULT_BITS // 8 // 1024 // 1024,
}

_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip


def _spelled(number: int) -> str:
    """The way this README writes a small number, so the code can be the only source of truth."""
    if number in _UNITS.values():
        return next(word for word, value in _UNITS.items() if value == number)
    tens, units = divmod(number, 10)
    word = next(w for w, value in _TENS.items() if value == tens * 10)
    return word if not units else f"{word}-{next(u for u, v in _UNITS.items() if v == units)}"


def _flat(text: str) -> str:
    """One line, single-spaced.

    The README wraps at the column the rest of the project wraps at, so a sentence a reader sees
    whole is two lines in the file and a substring check against the raw text is a coin toss on
    where the wrap landed.
    """
    return " ".join(text.split())


@pytest.fixture(scope="module")
def readme() -> str:
    return read("README.md")


@pytest.fixture(scope="module")
def prose(readme: str) -> str:
    return _flat(readme)


class TestTheLimitsTable:
    @staticmethod
    def _rows(readme: str) -> dict[str, int]:
        table = readme.split("| Limit | Value | Measured need | Ratio |", 1)[1]
        found: dict[str, int] = {}
        for raw in table.splitlines():
            line = raw.strip()
            if not line:
                # The blank line before the table is not the end of it; the one after is.
                if found:
                    break
                continue
            if line.startswith("| ---"):
                continue
            if not line.startswith("|"):
                break
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 2 or not cells[0]:
                continue
            number = re.search(r"[\d,]+", cells[1])
            if number:
                found[cells[0]] = int(number.group().replace(",", ""))
        return found

    def test_the_table_is_found_at_all(self, readme: str) -> None:
        """A parser that quietly matches nothing would pass every test below."""
        assert len(self._rows(readme)) == len(PUBLISHED)

    def test_the_rows_are_exactly_the_limits_checked_here(self, readme: str) -> None:
        """A row added to the README without a constant behind it is a published number with no
        source, which is the shape of the defect this file exists to stop."""
        assert set(self._rows(readme)) == set(PUBLISHED)

    @pytest.mark.parametrize("label", sorted(PUBLISHED))
    def test_the_published_value_is_the_value_in_force(self, readme: str, label: str) -> None:
        assert self._rows(readme)[label] == PUBLISHED[label], (
            f"the README publishes {self._rows(readme)[label]:,} for {label!r} and the package "
            f"uses {PUBLISHED[label]:,}"
        )


class TestTheReservedNames:
    """The list a host is told to check their context against, so a stale one is a collision
    nobody was warned about."""

    @staticmethod
    def _listed(readme: str) -> set[str]:
        block = re.search(r"are:\n\n```\n(.*?)\n```", readme, re.S)
        assert block is not None, "the reserved-names block is gone"
        return set(block.group(1).split())

    def test_the_list_is_exactly_what_is_reserved(self, readme: str) -> None:
        reserved = set(Evaluator(registry=standard_registry()).function_names)
        listed = self._listed(readme)
        assert listed - reserved == set(), f"README names what is not reserved: {listed - reserved}"
        assert reserved - listed == set(), f"reserved but not listed: {reserved - listed}"

    def test_the_list_is_sorted(self, readme: str) -> None:
        """It is read by a person looking for one name in it."""
        block = re.search(r"are:\n\n```\n(.*?)\n```", readme, re.S)
        assert block is not None
        names = block.group(1).split()
        assert names == sorted(names)

    def test_bitor_is_listed_even_though_it_is_not_in_the_registry(self, readme: str) -> None:
        """The distinction the list has to keep straight, and the one an update is most likely to
        get wrong: `bitor` is a builtin rather than a tier entry, so the reserved names are the
        registry's **plus** it. Forty-one functions, forty-two reserved names."""
        assert "bitor" in self._listed(readme)
        assert "bitor" not in standard_registry()
        assert Evaluator().function_names == frozenset({"bitor"})


class TestTheCountsInProse:
    def test_the_function_count(self, prose: str) -> None:
        total = len(standard_registry())
        assert f"{_spelled(total).capitalize()} functions" in prose

    def test_the_tier_count_and_every_tier_named(self, prose: str) -> None:
        assert f"across {_spelled(len(TIERS))} tiers" in prose
        for tier in ("collections", "types", "strings", "regex", "dates"):
            assert tier in prose

    def test_the_tiers_are_disjoint_so_the_count_means_something(self) -> None:
        assert sum(len(tier) for tier in TIERS) == len(standard_registry())

    def test_the_default_budget(self, prose: str) -> None:
        millions, remainder = divmod(DEFAULT_STEP_BUDGET, 1_000_000)
        assert remainder == 0, "the README spells the budget in whole millions"
        assert f"the default is {_spelled(millions)} million steps" in prose

    def test_the_size_charge_unit(self, prose: str) -> None:
        assert f"under {SIZE_CHARGE_UNIT} elements is charged nothing" in prose

    def test_the_supported_versions(self, prose: str) -> None:
        versions = supported_pythons()
        assert f"Python {versions[0]} through {versions[-1]}" in prose


class TestTheExamplesRun:
    """The three expressions on the front page, evaluated rather than admired."""

    def test_the_headline_expression(self) -> None:
        assert (
            Evaluator().evaluate(
                'user.plan == "pro" and user.region in ["us", "eu"]',
                {"user": {"plan": "pro", "region": "eu"}},
            )
            is True
        )

    def test_the_two_pipe_examples(self) -> None:
        ev = Evaluator(registry=standard_registry())
        assert ev.evaluate(
            "metrics | where(_.value > threshold) | first",
            {"metrics": [{"value": 4}, {"value": 40}], "threshold": 10},
        ) == {"value": 40}
        assert ev.evaluate(
            'orders | where(_.status == "paid") | group_by(_.customer_id)',
            {"orders": [{"customer_id": "c1", "status": "paid"}]},
        ) == [{"key": "c1", "items": [{"customer_id": "c1", "status": "paid"}]}]

    def test_every_expression_in_the_front_page_block_is_one_of_them(self, readme: str) -> None:
        """So an example added to that block without a test here is noticed."""
        block = readme.split("```\n", 1)[1].split("```", 1)[0]
        assert len([line for line in block.splitlines() if line.strip()]) == 3


class TestTheShippedDocstringsRun:
    """`safeexpr.__init__` opens with a worked example, and nothing was running it.

    `testpaths` is `tests/`, so no doctest in `src/` is collected by an ordinary run. The example
    in the package docstring is the first thing a reader sees in an editor's tooltip, and it had
    the same standing as a comment until this.
    """

    MODULES = (
        "safeexpr",
        "safeexpr._eval",
        "safeexpr._errors",
        "safeexpr._parse",
        "safeexpr._registry",
        "safeexpr._stdlib",
        "safeexpr._validate",
    )

    def test_every_doctest_in_the_shipped_modules_passes(self) -> None:
        attempted = 0
        for name in self.MODULES:
            result = doctest.testmod(importlib.import_module(name), verbose=False)
            assert result.failed == 0, f"{name} has {result.failed} failing doctest(s)"
            attempted += result.attempted
        assert attempted > 0, "no doctests found, so this test proves nothing"


class TestTheFrontPagePointsAtWhatExists:
    """The README grew a hero, a documentation table and a list of runnable commands. Each of
    those is a claim about a file, and a front page pointing at nothing is the first thing a
    reader finds."""

    def test_the_banner_image_is_there(self, readme: str) -> None:
        found = re.search(r'<img src="([^"]+)" alt="safeexpr"', readme)
        assert found is not None, "the banner image is gone"
        assert (ROOT / found.group(1)).is_file(), f"the banner points at nothing: {found.group(1)}"

    def test_the_documentation_table_lists_every_guide(self, readme: str) -> None:
        listed = set(re.findall(r"\]\((docs/[a-z-]+\.md)\)", readme))
        present = {
            f"docs/{path.name}" for path in (ROOT / "docs").glob("*.md") if path.name != "README.md"
        }
        assert present - listed == set(), (
            f"guides the README does not link: {sorted(present - listed)}"
        )

    def test_every_example_command_on_the_front_page_exists(self, readme: str) -> None:
        for named in re.findall(r"python (examples/[a-z_]+\.py)", readme):
            assert (ROOT / named).is_file(), f"the README runs {named}, which is not there"

    def test_the_example_count_in_the_prose_is_the_count_on_disk(self, prose: str) -> None:
        total = len(list((ROOT / "examples").glob("*.py")))
        assert f"{_spelled(total).capitalize()} programs" in prose, (
            f"there are {total} examples, and the README says otherwise"
        )


class TestTheDocumentItself:
    def test_relative_links_resolve(self, readme: str) -> None:
        for target in relative_link_targets(readme):
            assert (ROOT / target).exists(), f"link points at nothing: {target}"

    def test_no_em_dashes(self, readme: str) -> None:
        assert EM_DASH not in readme

    def test_the_documents_it_points_at_exist(self, readme: str) -> None:
        for named in ("THREAT-MODEL.md", "SECURITY.md", "CHANGELOG.md", "LICENSE"):
            assert named in readme, f"the README does not mention {named}"
            assert (ROOT / named).is_file()


def _cell(text: str) -> str:
    """A table cell with markdown emphasis and code ticks taken off."""
    return text.replace("**", "").replace("`", "").strip()


def _table_rows(readme: str, header: str) -> list[list[str]]:
    """The body rows of the table whose header line is `header`."""
    body = readme.split(header, 1)[1]
    rows = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if rows:
                break
            continue
        if line.startswith("| ---"):
            continue
        if not line.startswith("|"):
            break
        rows.append([_cell(cell) for cell in line.strip("|").split("|")])
    return rows


class TestTheSectionsTheCardAsksFor:
    """The sections a security reviewer needs, each by the heading they would look for."""

    @pytest.mark.parametrize(
        "heading",
        ["## Install", "## Limits", "## Non-goals", "## Alternatives", "## Threat model"],
    )
    def test_the_section_is_present(self, readme: str, heading: str) -> None:
        assert heading in readme

    def test_install_names_the_distribution(self, readme: str) -> None:
        assert f"pip install {pyproject()['project']['name']}" in readme

    def test_install_does_not_promise_a_release_that_does_not_exist(self, readme: str) -> None:
        """`SECURITY.md` says there is no released version. An install line implying otherwise
        would be the two documents disagreeing about the same fact."""
        assert "Not published yet" in readme


class TestTheNonGoals:
    """Four, and they are the shape of the package rather than a backlog."""

    @pytest.mark.parametrize(
        "claim",
        [
            "Not Turing-complete",
            "No I/O of any kind, ever",
            "Not CEL, and not CEL-compatible",
            "No custom grammar, ever",
        ],
    )
    def test_each_non_goal_is_stated(self, readme: str, claim: str) -> None:
        assert claim in readme

    def test_the_language_actually_refuses_what_the_non_goals_promise(self) -> None:
        """A non-goal is a claim about the evaluator, so it is checked against the evaluator."""
        ev = Evaluator(registry=standard_registry())
        for source in ("[y for y in z]", "lambda: 1", "(a := 1)", "(y for y in z)"):
            with pytest.raises(SafeExprError):
                ev.evaluate(source, {"z": [1], "a": 1})

    def test_no_shipped_module_imports_anything_that_does_io(self) -> None:
        """The "no I/O, ever" non-goal, read off the imports rather than taken on trust.

        `scripts/check_zero_deps.py` proves nothing third-party is imported. This is the narrower
        claim: nothing in the standard library that opens a file, a socket or a process is
        imported either.
        """
        forbidden = {"os", "io", "socket", "subprocess", "pathlib", "shutil", "urllib.request"}
        src = ROOT / "src" / "safeexpr"
        seen = 0
        for path in sorted(src.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {node.module or ""}
                else:
                    continue
                seen += 1
                overlap = names & forbidden
                assert overlap == set(), f"{path.name} imports {sorted(overlap)}"
        # `urllib.parse` is allowed and used: parsing a URL string reads nothing. A scan that
        # found no imports at all would pass this test while checking nothing.
        assert seen > 20, f"only {seen} imports scanned, so this test proves little"


class TestTheAlternativesTable:
    HEADER = "| Package | Version checked | Runtime deps | What you give up |"

    def test_the_table_is_found(self, readme: str) -> None:
        assert len(_table_rows(readme, self.HEADER)) >= 5

    def test_our_own_dependency_count_is_the_one_in_the_metadata(self, readme: str) -> None:
        """The row a reader checks first, and the only one this repository can verify."""
        rows = {row[0]: row for row in _table_rows(readme, self.HEADER)}
        name = pyproject()["project"]["name"]
        assert int(rows[name][2]) == len(pyproject()["project"]["dependencies"])

    def test_every_row_carries_a_version_and_a_count(self, readme: str) -> None:
        """A comparison without a version on it is not traceable to anything."""
        for row in _table_rows(readme, self.HEADER):
            assert row[1], f"{row[0]} has no version"
            assert row[2].isdigit(), f"{row[0]}'s dependency count is {row[2]!r}"

    def test_the_cel_dependency_count_matches_the_ones_named(self, readme: str) -> None:
        """Six is a number in a table, and the six are named in the prose under it. They have to
        be the same six."""
        rows = {row[0]: row for row in _table_rows(readme, self.HEADER)}
        named = re.search(r"`cel-python`'s six are (.+?)\. That means", readme, re.S)
        assert named is not None
        count = len(re.findall(r"`[a-z0-9-]+`", named.group(1)))
        assert count == int(rows["cel-python"][2])

    def test_the_stale_numpy_claim_is_not_repeated(self, readme: str) -> None:
        """asteval 1.0.10 has zero runtime dependencies. The old comparison said numpy."""
        assert "asteval` no longer requires numpy" in readme

    def test_the_figures_carry_the_date_they_were_checked(self, readme: str) -> None:
        assert re.search(r"checked against PyPI metadata on \d{4}-\d{2}-\d{2}", readme)


class TestWhatHappensPastALimit:
    HEADER = "| Past this | You get |"

    def test_every_error_named_is_a_real_exported_type(self, readme: str) -> None:
        for row in _table_rows(readme, self.HEADER):
            for name in re.findall(r"\b\w+Error\b", row[1]):
                assert name in safeexpr.__all__, f"{name} is not exported"

    def test_the_supported_scale_is_the_measured_rule(self, readme: str, prose: str) -> None:
        """The claim a reader most needs and the acceptance criterion the card names: what scale
        is supported, and what happens past it."""
        assert "supported scale is ten times a hundred thousand items" in prose
        assert "`BudgetExceededError` rather than a slow answer" in prose

    def test_the_scale_claim_agrees_with_the_test_that_measures_it(self) -> None:
        """`test_limits.py` asserts the budget covers ten times a hundred thousand items at the
        measured rate. The README's sentence is that test in words, so the rule it names is read
        from the same constant rather than typed again."""
        assert HEADROOM == 10


class TestTheConfigurationSurface:
    HEADER = "| Argument | Default | What it decides |"

    def test_the_table_names_exactly_the_constructor_arguments(self, readme: str) -> None:
        """Three knobs, and the README says they are the whole configuration surface. That is a
        claim about a signature, so it is read off the signature."""
        documented = {row[0] for row in _table_rows(readme, self.HEADER)}
        declared = set(inspect.signature(Evaluator.__init__).parameters) - {"self"}
        assert documented == declared

    def test_the_defaults_are_the_defaults(self, readme: str) -> None:
        rows = {row[0]: row for row in _table_rows(readme, self.HEADER)}
        parameters = inspect.signature(Evaluator.__init__).parameters
        assert rows["budget"][1].replace(",", "") == str(parameters["budget"].default)
        for name in ("registry", "attribute_types"):
            assert parameters[name].default is None
            assert rows[name][1] == "empty"

    def test_the_one_argument_that_gives_something_up_says_so(self, prose: str) -> None:
        """`attribute_types` re-opens F2 for the type it names, and a configuration table that
        listed it beside `budget` without saying so would be the most expensive omission in this
        file."""
        assert "attribute_types` is the one argument that gives something up" in prose
