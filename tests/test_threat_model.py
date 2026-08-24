"""THREAT-MODEL.md is a set of claims about the corpus, so it is checked against the corpus.

The document's whole value is that a reviewer can trace all nine failure classes to passing tests
without reading the source. That only holds while its citations are live: a renamed corpus entry
turns a claim into a dead reference, and a dead reference in a security document is worse than no
reference, because it still reads like evidence.

So the entry lists in the document are asserted to equal the corpus, class by class, and every
advisory identifier in the body is asserted to appear in the document's own sources table. A new
corpus entry fails this suite until the document names it, which is the intended cost: the
catalog and the corpus move together or not at all.

`TestTheCheckerFailsLoudly` is the other half. A document checker that cannot fail is decoration.
"""

from __future__ import annotations

import re

import pytest

from _docs import (
    EM_DASH,
    HEADING,
    ROOT,
    anchor_link_targets,
    read,
    relative_link_targets,
    slugs,
)
from test_corpus import ENTRIES

DOC = ROOT / "THREAT-MODEL.md"

FAILURE_CLASSES = tuple(f"F{n}" for n in range(1, 10))

# A corpus id as the document cites one: inline code, `F<n>-...` or `control-...`.
_ID = re.compile(r"`((?:F[1-9]|control)-[a-z0-9-]+)`")

# The advisory registries this document cites. GHSA ids are four-character triples; Snyk ids are
# upper-case and dashed. Anything else that looks like a citation should be added here rather than
# left unchecked.
_ADVISORY = re.compile(
    r"\b(CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}|SNYK-[A-Z]+(?:-[A-Z0-9]+)+)\b"
)

_CLASS_HEADING = re.compile(r"^(F[1-9])\.")


def _sections(text: str) -> dict[str, str]:
    """Split the document at `##` headings, keyed by failure class or by `controls`/`sources`.

    Headings that are neither are dropped: the prose sections carry no entry lists.
    """
    found: dict[str, str] = {}
    marks = [m for m in HEADING.finditer(text) if len(m.group(1)) == 2]
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        body = text[mark.end() : end]
        title = mark.group(2)
        klass = _CLASS_HEADING.match(title)
        if klass:
            found[klass.group(1)] = body
        elif title.startswith("Controls"):
            found["controls"] = body
        elif title.startswith("Sourcing"):
            found["sources"] = body
    return found


def _proven_by(section: str) -> set[str]:
    """The corpus ids a section claims prove it.

    The list is the `**Proven by**` paragraph, which ends at the first blank line. Ids named
    elsewhere in a section are prose rather than citation, and are checked separately for
    existence but not for class membership.
    """
    lines = section.splitlines()
    for number, line in enumerate(lines):
        if line.startswith("**Proven by**"):
            paragraph = []
            for following in lines[number:]:
                if not following.strip():
                    break
                paragraph.append(following)
            return set(_ID.findall("\n".join(paragraph)))
    return set()


def _corpus_ids(failure_class: str) -> set[str]:
    return {e["id"] for e in ENTRIES if e["failure_class"] == failure_class}


def _control_ids() -> set[str]:
    """The unclassified controls.

    An `allowed` entry may still carry a failure class: `F1-format-date-brace-template` is a
    control that is really a rejection, and it belongs in F1's list rather than here.
    """
    return {e["id"] for e in ENTRIES if e["stage"] == "allowed" and e["failure_class"] is None}


@pytest.fixture(scope="module")
def text() -> str:
    return read("THREAT-MODEL.md")


@pytest.fixture(scope="module")
def sections(text: str) -> dict[str, str]:
    return _sections(text)


class TestEveryClaimIsTraceable:
    def test_the_document_exists_and_is_not_a_stub(self, text: str) -> None:
        assert len(text) > 5_000, "THREAT-MODEL.md is too short to be the catalog"

    @pytest.mark.parametrize("failure_class", FAILURE_CLASSES)
    def test_every_failure_class_has_a_section(
        self, sections: dict[str, str], failure_class: str
    ) -> None:
        assert failure_class in sections, f"no section for {failure_class}"

    @pytest.mark.parametrize("failure_class", FAILURE_CLASSES)
    def test_each_section_cites_exactly_its_own_corpus_entries(
        self, sections: dict[str, str], failure_class: str
    ) -> None:
        """The check the document exists to support.

        Equality rather than containment, in both directions. A citation with no entry behind it
        is a dead reference; an entry with no citation is coverage the catalog does not claim, and
        the corpus is the evidence for this document rather than a superset of it.
        """
        cited = _proven_by(sections[failure_class])
        actual = _corpus_ids(failure_class)
        assert cited, f"{failure_class} names no corpus entries"
        assert cited - actual == set(), (
            f"{failure_class} cites entries that are not tagged {failure_class}: "
            f"{sorted(cited - actual)}"
        )
        assert actual - cited == set(), (
            f"{failure_class} corpus entries the document does not name: {sorted(actual - cited)}"
        )

    def test_the_controls_section_cites_every_control(self, sections: dict[str, str]) -> None:
        assert _proven_by(sections["controls"]) == _control_ids()

    def test_the_summary_table_counts_agree_with_the_corpus(self, text: str) -> None:
        """The counts in the summary are numbers, so they are checked like numbers."""
        rows = re.findall(r"^\| \[(F[1-9])\].*?\| (\d+) \|$", text, re.MULTILINE)
        assert len(rows) == len(FAILURE_CLASSES), "the summary table is missing rows"
        for failure_class, claimed in rows:
            assert int(claimed) == len(_corpus_ids(failure_class)), (
                f"summary says {claimed} entries for {failure_class}, "
                f"corpus has {len(_corpus_ids(failure_class))}"
            )

    def test_every_id_named_anywhere_exists_in_the_corpus(self, text: str) -> None:
        """Prose citations too, not only the `Proven by` lists."""
        known = {e["id"] for e in ENTRIES}
        named = set(_ID.findall(text))
        assert named - known == set(), f"named but not in the corpus: {sorted(named - known)}"


class TestTheDocumentItself:
    def test_every_advisory_cited_in_the_body_is_in_the_sources_table(
        self, text: str, sections: dict[str, str]
    ) -> None:
        """An identifier a reader cannot look up is a citation in name only."""
        body = text.replace(sections["sources"], "")
        listed = set(_ADVISORY.findall(sections["sources"]))
        cited = set(_ADVISORY.findall(body))
        assert cited - listed == set(), f"cited but not sourced: {sorted(cited - listed)}"

    def test_f9_is_present_with_its_advisory(self, text: str) -> None:
        """F9 is the class the original catalog did not have, so it is asserted by name."""
        assert "CVE-2024-47532" in text

    def test_the_unverified_simpleeval_frame_claim_is_not_relied_on(
        self, sections: dict[str, str]
    ) -> None:
        """It could not be verified against OSV, so no row may rest on it.

        The document is allowed to say the claim exists and is not cited, which is what the
        sourcing section does. What it may not do is credit simpleeval for the generator escape,
        which is F6's territory and rests on RestrictedPython's CVE alone.
        """
        assert "simpleeval" not in sections["F6"], "F6 credits simpleeval for an unverified escape"
        assert "could not be verified" in sections["sources"]

    def test_the_scope_limits_are_stated(self, text: str) -> None:
        """The three the card requires, each by the words a reader would search for."""
        for phrase in (
            "does not bound regular-expression time",
            "Memory amplification is mitigated, not eliminated",
            "Bounded, not eliminated",
            "An error names the type of a value it could not work with",
        ):
            assert phrase in text, f"scope limit not stated: {phrase!r}"

    def test_relative_links_resolve(self, text: str) -> None:
        for target in relative_link_targets(text):
            assert (ROOT / target).exists(), f"link points at nothing: {target}"

    def test_anchor_links_resolve(self, text: str) -> None:
        available = slugs(text)
        for target in anchor_link_targets(text):
            assert target in available, f"anchor points at no heading: {target}"

    def test_no_em_dashes(self, text: str) -> None:
        """House style, and the one typographic rule this project keeps."""
        assert EM_DASH not in text


class TestTheCheckerFailsLoudly:
    """A document checker that cannot fail is decoration.

    Each of these is a way the document realistically rots: an entry is renamed in the corpus and
    not here, a section loses its citations, an advisory is cited without being sourced.
    """

    def test_a_renamed_entry_is_caught(self, sections: dict[str, str]) -> None:
        broken = sections["F5"].replace("`F5-systemexit`", "`F5-system-exit`")
        assert "F5-system-exit" in _proven_by(broken)
        assert _proven_by(broken) - _corpus_ids("F5") == {"F5-system-exit"}

    def test_an_entry_added_to_the_corpus_and_not_the_document_is_caught(
        self, sections: dict[str, str]
    ) -> None:
        cited = _proven_by(sections["F6"])
        assert (_corpus_ids("F6") | {"F6-something-new"}) - cited == {"F6-something-new"}

    def test_a_section_with_no_citations_is_caught(self) -> None:
        assert _proven_by("## F1. A class\n\nProse with no list.\n") == set()

    def test_an_uncited_advisory_is_caught(self) -> None:
        assert _ADVISORY.findall("broken by CVE-2099-11111 and GHSA-aaaa-bbbb-cccc") == [
            "CVE-2099-11111",
            "GHSA-aaaa-bbbb-cccc",
        ]

    def test_the_section_splitter_splits_at_two_hashes_only(self) -> None:
        """A `###` inside a class section belongs to that section, not to a new one."""
        text = "## F1. One\n\n**Proven by** `F1-a`.\n\n### Still F1\n\nprose about `F1-b`.\n"
        section = _sections(text)["F1"]
        assert _proven_by(section) == {"F1-a"}
        assert "F1-b" in section
