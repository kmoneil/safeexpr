"""SECURITY.md is a set of promises, so the ones a test can hold it to are held.

Most of a disclosure policy is a commitment about behaviour and no test can check it. Three parts
are not: the versions it promises to support, the status it declares, and the workflow it says
every accepted escape follows. Those are facts about the project that live in three files, and
three files is exactly the number that drift apart.

`pyproject.toml` says so itself: the `Development Status` classifier is stated there, in the
README's "Status" section and in this policy's support window, "so all three move together". That
sentence was a comment, which is a promise nobody checks. It is a test now.
"""

from __future__ import annotations

import re

import pytest

import safeexpr
from _docs import (
    EM_DASH,
    ROOT,
    anchor_link_targets,
    development_status,
    pyproject,
    read,
    relative_link_targets,
    slugs,
    supported_pythons,
)

POLICY = "SECURITY.md"

# The four steps the card requires an accepted escape to go through, each by a phrase a reader
# would search for rather than by an exact sentence.
WORKFLOW = ("new release", "CVE", "credited", "corpus entry")


@pytest.fixture(scope="module")
def policy() -> str:
    return read(POLICY)


@pytest.fixture(scope="module")
def readme() -> str:
    return read("README.md")


class TestThePolicyExists:
    def test_the_file_is_published(self) -> None:
        """`pyproject.toml` already lists it in the sdist, so its absence was a promise unkept."""
        assert (ROOT / POLICY).is_file()

    def test_it_ships_in_the_sdist(self) -> None:
        include = pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
        assert f"/{POLICY}" in include

    def test_it_is_not_a_stub(self, policy: str) -> None:
        assert len(policy) > 2_000


class TestReportingIsPrivate:
    def test_there_is_a_private_contact(self, policy: str) -> None:
        """A policy with no way to reach anybody is a policy that routes escapes to the issue
        tracker."""
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", policy)
        advisory = "security/advisories/new" in policy
        assert email or advisory, "no private reporting channel"

    def test_the_github_channel_points_at_this_repository(self, policy: str) -> None:
        """A private-reporting link to somebody else's repository sends the report to them."""
        source = pyproject()["project"]["urls"]["Source"].rstrip("/")
        if "security/advisories/new" in policy:
            assert f"{source}/security/advisories/new" in policy

    def test_public_issues_are_refused_in_so_many_words(self, policy: str) -> None:
        """The single most important instruction in the file, so it is asserted rather than
        assumed to have survived an edit."""
        assert "Do not open a public issue" in policy

    def test_the_reason_is_given_and_not_just_the_rule(self, policy: str) -> None:
        """A rule with no reason gets argued with. This one has an argument: a public report on a
        sandbox is a working exploit published before a fix exists."""
        assert "exploit" in policy


class TestTheEscapeWorkflowIsDocumented:
    @pytest.mark.parametrize("step", WORKFLOW)
    def test_every_step_is_named(self, policy: str, step: str) -> None:
        assert step in policy, f"the release workflow does not mention {step!r}"

    def test_a_silent_push_is_ruled_out(self, policy: str) -> None:
        assert "never as a silent push" in policy or "never a silent push" in policy

    def test_the_corpus_entry_is_required_in_the_same_change(self, policy: str) -> None:
        """The step that makes the other three durable: a fix without an entry is a patch, and a
        patch is what regresses."""
        assert "same change as the fix" in policy


class TestThreeFilesAgree:
    """`pyproject.toml`, the README and this policy, which the classifier comment says move
    together."""

    def test_the_policy_names_the_development_status(self, policy: str) -> None:
        assert f"Development Status :: {development_status()}" in policy

    def test_the_readme_names_the_same_one(self, readme: str) -> None:
        assert f"Development Status :: {development_status()}" in readme

    def test_the_policy_promises_exactly_the_supported_pythons(self, policy: str) -> None:
        """Every version the package claims, and no version it does not.

        Both halves matter. A missing one is a supported interpreter nobody promised to fix on; a
        stray one is a promise about an interpreter the package will not even install on.
        """
        promised = set(re.findall(r"\b3\.\d+\b", policy))
        assert promised == set(supported_pythons())

    def test_the_floor_in_the_policy_is_the_floor_in_the_metadata(self, policy: str) -> None:
        floor = pyproject()["project"]["requires-python"].removeprefix(">=")
        assert floor in policy


# PEP 440 spells a pre-release four ways. A version carrying any of them is not something
# `pip install safeexpr` resolves to by default, which is the difference the policy has to reflect.
PRE_RELEASE = re.compile(r"(\.dev\d*|a\d+|b\d+|rc\d+)$")


class TestTheReleaseStateIsTheSameInBothPlaces:
    """The version and the policy's support window, tied together.

    `SECURITY.md` says there is no released version yet, which is true and will stop being true.
    A support window describing a world that no longer exists is worse than none: it tells a
    reporter their version is unsupported when it is the only supported one.

    So the sentence is keyed to the version rather than to somebody remembering. **Bumping
    `__version__` to a final release fails this test until the policy is updated**, which is what
    makes cutting a real release mechanical instead of a checklist.
    """

    STATES_UNRELEASED = "This package has not been released yet."

    def test_the_version_is_a_valid_release_identifier(self) -> None:
        """A version PyPI rejects fails at upload, which is the most expensive place to find it."""
        assert re.fullmatch(r"\d+(\.\d+)*((\.dev|a|b|rc)\d*)?", safeexpr.__version__), (
            f"{safeexpr.__version__!r} is not a version PyPI will accept"
        )

    def test_a_pre_release_version_and_an_unreleased_policy_agree(self, policy: str) -> None:
        pre_release = bool(PRE_RELEASE.search(safeexpr.__version__))
        says_unreleased = self.STATES_UNRELEASED in policy
        assert pre_release == says_unreleased, (
            f"__version__ is {safeexpr.__version__!r} and SECURITY.md "
            f"{'says' if says_unreleased else 'does not say'} there is no released version. "
            f"Either bump the version and rewrite the support window, or leave both alone."
        )

    def test_the_policy_describes_what_happens_once_there_is_a_release(self, policy: str) -> None:
        """The window that begins at the first release, written before it is needed."""
        assert "The latest release" in policy
        assert "no backporting" in policy


class TestTheStandingCommitment:
    """The README carries it too, because the README is where a reader decides whether to take
    the dependency and the policy is where they go once something has already happened."""

    SENTENCE = "A sandbox escape is always a critical bug here."

    def test_the_policy_carries_it(self, policy: str) -> None:
        assert self.SENTENCE in policy

    def test_the_readme_carries_it(self, readme: str) -> None:
        assert self.SENTENCE in readme

    def test_the_readme_points_at_the_policy(self, readme: str) -> None:
        assert POLICY in readme

    def test_semi_trusted_is_not_offered_as_a_defence(self, policy: str) -> None:
        """The scope statement and the commitment could be read as contradicting each other, and
        a reporter being told "semi-trusted" as a reason to close their report is the failure mode
        this file exists to prevent. The policy has to say which one wins."""
        assert "not** a reason to downgrade" in policy or "not a reason to downgrade" in policy


class TestTheDocumentItself:
    def test_relative_links_resolve(self, policy: str) -> None:
        for target in relative_link_targets(policy):
            assert (ROOT / target).exists(), f"link points at nothing: {target}"

    def test_anchor_links_resolve(self, policy: str) -> None:
        available = slugs(policy)
        for target in anchor_link_targets(policy):
            assert target in available, f"anchor points at no heading: {target}"

    def test_no_em_dashes(self, policy: str) -> None:
        assert EM_DASH not in policy

    def test_it_points_at_the_threat_model(self, policy: str) -> None:
        """Scope belongs in one place, and a policy that restates it grows a second version of
        it."""
        assert "THREAT-MODEL.md" in policy
