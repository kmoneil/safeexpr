"""Every example runs, as a subprocess, exactly as a reader would run it.

An example that has drifted out of sync with the library is worse than no example: it is a
confident, wrong answer that somebody will copy. So these are executed rather than imported. The
`__main__` block, the imports and the output are all part of what is being checked.

They need nothing to run: no network, no server, no files, no arguments. That is a property of
the package rather than a convenience of the examples, and it is why this file has no skip in it.

Beyond "it exited zero", a handful of examples have their **claim** pinned below. An example that
still runs while no longer demonstrating the thing it exists to demonstrate is the drift a
process exit code cannot see.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from _docs import EM_DASH, ROOT

EXAMPLES_DIR = ROOT / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*.py"))


def run_example(example: Path) -> tuple[int, str, str]:
    finished = subprocess.run(
        [sys.executable, str(example)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        cwd=ROOT,
    )
    return finished.returncode, finished.stdout, finished.stderr


@pytest.fixture(scope="module")
def outputs() -> dict[str, str]:
    """Every example's stdout, run once for the whole module rather than once per assertion."""
    collected = {}
    for example in EXAMPLES:
        returncode, stdout, stderr = run_example(example)
        assert returncode == 0, f"{example.name} exited {returncode}:\n{stderr}"
        collected[example.stem] = stdout
    return collected


def test_there_are_examples_to_run():
    """Guards the guard: an empty directory would make everything below vacuous."""
    assert len(EXAMPLES) > 15, f"only {len(EXAMPLES)} examples found"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.stem)
def test_an_example_runs_clean(example: Path):
    returncode, stdout, stderr = run_example(example)
    assert returncode == 0, f"{example.name} failed:\n{stderr}"
    assert stdout.strip(), f"{example.name} printed nothing"
    assert "Traceback" not in stderr, f"{example.name} wrote a traceback:\n{stderr}"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.stem)
def test_an_example_explains_itself(example: Path):
    """A module docstring saying what it shows, and the command line to run it."""
    text = example.read_text(encoding="utf-8")
    assert text.startswith('"""'), f"{example.name} has no module docstring"
    assert f"python examples/{example.name}" in text, f"{example.name} does not say how to run it"
    assert EM_DASH not in text


def tabled_examples() -> set[str]:
    """Every example named in a row of the `What each one shows` table.

    Rows only. The command lists above that table already name every example as a command line,
    so matching anywhere would report an example as documented on the strength of a line saying
    how to start it rather than what it shows.
    """
    readme = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
    table = readme.split("## What each one shows", 1)[1]
    return set(re.findall(r"^\|\s*`([a-z_]+\.py)`", table, re.MULTILINE))


class TestTheIndexResolvesAgainstTheDirectory:
    """An index cannot be audited by reading it. Both directions, and the second is not
    decoration: a row for a file that was renamed is an index pointing at nothing."""

    def test_every_example_has_a_row_saying_what_it_shows(self):
        present = {path.name for path in EXAMPLES}
        missing = present - tabled_examples()
        assert missing == set(), f"examples with no row in examples/README.md: {sorted(missing)}"

    def test_the_index_has_no_row_for_a_file_that_is_gone(self):
        present = {path.name for path in EXAMPLES}
        stale = tabled_examples() - present
        assert stale == set(), f"examples/README.md has rows for missing files: {sorted(stale)}"

    def test_the_table_check_is_not_vacuous(self):
        assert len(tabled_examples()) > 15

    def test_every_example_is_also_named_as_a_command(self):
        readme = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
        for example in EXAMPLES:
            assert f"python examples/{example.name}" in readme, (
                f"{example.name} is in the table but not in a runnable command list"
            )

    def test_the_index_has_no_em_dashes(self):
        assert EM_DASH not in (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")

    def test_every_example_is_paired_with_a_document(self):
        """The second table: each example points at the written argument behind it."""
        readme = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
        pairs = readme.split("## Reading these next to the docs", 1)[1]
        named = set(re.findall(r"`([a-z_]+\.py)`", pairs))
        missing = {path.name for path in EXAMPLES} - named
        assert missing == set(), f"examples with no doc pairing: {sorted(missing)}"

    def test_every_document_named_in_that_table_exists(self):
        readme = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
        pairs = readme.split("## Reading these next to the docs", 1)[1]
        for target in re.findall(r"\]\((\.\./[\w./-]+)\)", pairs):
            assert (EXAMPLES_DIR / target).resolve().is_file(), f"points at nothing: {target}"


class TestTheCountsWrittenOutInProse:
    """Two documents quote numbers that only running the code can settle."""

    def test_the_front_page_says_how_many_examples_there_are(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        spelled = {16: "Sixteen", 17: "Seventeen", 18: "Eighteen", 19: "Nineteen", 20: "Twenty"}
        assert f"{spelled[len(EXAMPLES)]} programs" in readme, (
            f"there are {len(EXAMPLES)} examples and README.md says otherwise"
        )

    def test_the_refusal_count_both_indexes_quote_is_the_one_that_runs(self, outputs):
        """`what_is_refused.py` prints its own total. Two documents repeat it in words."""
        total = int(re.search(r"== \d+ of (\d+) refused", outputs["what_is_refused"]).group(1))
        spelled = {32: "thirty-two", 33: "thirty-three", 34: "thirty-four", 35: "thirty-five"}
        for document in ("README.md", "examples/README.md"):
            text = (ROOT / document).read_text(encoding="utf-8")
            assert (
                f"{spelled[total]} escape attempts" in text or f"{spelled[total]} attempts" in text
            ), f"{document} does not say {spelled[total]}, and the example runs {total}"


class TestTheClaimsTheExamplesExistToMake:
    """Pinned per example, because "it exited zero" cannot see an example that has stopped
    demonstrating its own subject."""

    def test_quickstart_shows_both_the_refusal_and_the_opt_in(self, outputs: dict[str, str]):
        stdout = outputs["quickstart"]
        # The same call, refused without a registry and answered with one. An example that
        # showed only the second half would be a tour of an API.
        assert "len(user.plan)           !! EvaluationError: `len` is not a function" in stdout
        assert "len(user.plan)                            -> 3" in stdout

    def test_reserved_names_shows_the_collision_and_the_three_ways_out(self, outputs):
        stdout = outputs["reserved_names"]
        assert "ReservedNameError" in stdout
        assert "flags | first                                -> 1" in stdout, "way out 1"
        assert "flags | head                               -> 1" in stdout, "way out 2"
        # And the positions where it is deliberately *not* a collision.
        assert "first                                        -> 'a value of my own'" in stdout
        assert "42 names are reserved" in stdout

    def test_what_is_refused_actually_refuses(self, outputs: dict[str, str]):
        stdout = outputs["what_is_refused"]
        # The count line, and the one deliberate exception to it: a callable in the context
        # comes back as a value and can never be called.
        assert re.search(r"== \d+ of \d+ refused ==", stdout)
        refused, total = (int(n) for n in re.search(r"== (\d+) of (\d+) refused", stdout).groups())
        assert total - refused == 1, "exactly one line is allowed, and it is the F3 one"
        assert "callback('escaped')" in stdout
        assert "values from the context cannot be called" in stdout
        assert "ALLOWED          <built-in function print>" in stdout

    def test_budget_measures_rather_than_asserts(self, outputs: dict[str, str]):
        stdout = outputs["budget"]
        # Steps per item, measured by bisection, stable across two orders of magnitude. A flat
        # rule stays flat; if either stops being true the example has stopped making its point.
        assert "a feature flag                     11 steps" in stdout
        assert re.search(r"a filter over 1,000 rows\s+4,0\d\d steps\s+4\.0\d steps/item", stdout)
        assert re.search(r"a filter over 10,000 rows\s+40,4\d\d steps\s+4\.0\d steps/item", stdout)
        assert "budget    : 500" in stdout

    def test_threads_shows_agreement_and_independent_budgets(self, outputs: dict[str, str]):
        stdout = outputs["threads"]
        assert "the same answers as a serial run: True" in stdout
        # Three refusals and three answers from one shared evaluator at the same time.
        assert "['refused', 'answered', 'refused', 'answered', 'refused', 'answered']" in stdout
        # The registry was copied rather than held, proven by mutating the caller's dict.
        assert "len([1, 2, 3])   -> 3" in stdout
        assert "no __dict__ for setting new attributes" in stdout

    def test_attributes_shows_that_an_attribute_can_run_code(self, outputs: dict[str, str]):
        stdout = outputs["attributes"]
        # The whole reason `attribute_types` is a decision rather than a convenience.
        assert "queries before: 0" in stdout
        assert "queries after three evaluations of `account.balance > 10`: 3" in stdout
        # And the closed default, which is the other half of the claim.
        assert "attribute access works on mappings" in stdout

    def test_rules_from_config_catches_four_different_failures(self, outputs: dict[str, str]):
        stdout = outputs["rules_from_config"]
        for expected in ("EvaluationError", "ParseError", "ValidationError", "BudgetExceededError"):
            assert expected in stdout, f"{expected} no longer appears"
        assert "3 loaded, 4 rejected" in stdout
        # The budget one is the claim: a valid rule, quadratic in its input, caught at load time
        # by a sample that is the right *size* rather than merely the right shape.
        assert "BudgetExceededError: expression used more than its budget of 50,000 steps" in stdout
        assert "context keys that shadow a function: ['first']" in stdout

    def test_data_validation_shows_the_check_that_passes_for_the_wrong_reason(self, outputs):
        stdout = outputs["data_validation"]
        assert "against an empty basket -> True" in stdout
        # And the pattern gate, refusing the one no input-length cap would save you from.
        assert "nests one repeat inside another" in stdout

    def test_dates_and_urls_shows_the_allowlist_and_denylist_disagreeing(self, outputs):
        stdout = outputs["dates_and_urls"]
        # The same input, no scheme, one rule failing closed and the other failing open.
        assert re.search(r"hooks\.example\.com/v2/in\s+allowlist=False\s+denylist=True", stdout)
        assert 'url_host("example.com/x")' in stdout

    def test_pipelines_shows_where_the_item_is_not_in_scope(self, outputs: dict[str, str]):
        stdout = outputs["pipelines"]
        assert "orders | first | _.id" in stdout
        assert "`_` is only available inside a function argument" in stdout
        assert "(orders | first).id\n    -> 'a'" in stdout
        # Nesting, and reaching past what is in scope.
        assert "`_2` reaches 2 levels out but only 1 is in scope here" in stdout

    def test_access_control_fails_closed_twice(self, outputs: dict[str, str]):
        stdout = outputs["access_control"]
        assert "invoice:delete   deny   no rule for this action" in stdout
        assert "broken:rule      deny   rule is broken:" in stdout

    def test_types_and_defaults_shows_default_next_to_or(self, outputs: dict[str, str]):
        stdout = outputs["types_and_defaults"]
        assert "default(settings.retries, 3)                 -> 0" in stdout
        assert "settings.retries or 3                        -> 3" in stdout

    def test_errors_shows_every_class_and_a_clean_error(self, outputs: dict[str, str]):
        stdout = outputs["errors"]
        for name in (
            "ParseError",
            "ValidationError",
            "EvaluationError",
            "ReservedNameError",
            "SourceTooLongError",
        ):
            assert name in stdout
        # The scrubbing claim, which is the one an example can actually demonstrate.
        assert "cause   : None" in stdout
        assert "context : None" in stdout

    def test_custom_functions_shows_the_lazy_argument_running_once_per_item(self, outputs):
        stdout = outputs["custom_functions"]
        assert "the predicate was evaluated once per item, for 4 items" in stdout
        # Arity checked before the call, so the function's own error means something else.
        assert "`round_to` takes 2 arguments, got 1" in stdout
        assert "`round_to`: needs a number, got `str`" in stdout

    def test_rollups_shows_the_group_shape_and_what_the_filter_decided(self, outputs):
        stdout = outputs["rollups"]
        assert "key='eu'  2 item(s)" in stdout
        assert "filtered first: ['acme', 'globex', 'hooli']" in stdout
        assert "not filtered:   ['initech', 'acme', 'hooli', 'globex']" in stdout

    def test_feature_flags_shows_one_broken_flag_failing_alone(self, outputs: dict[str, str]):
        stdout = outputs["feature_flags"]
        assert stdout.count("skipped broken:") == 3, "one skip per user, and the others still ran"
        assert "on: ['beta-search', 'slow-rollout', 'everyone']" in stdout

    def test_alert_rules_shows_the_pipe_shape_people_get_wrong(self, outputs: dict[str, str]):
        stdout = outputs["alert_rules"]
        assert "queues | max_by(_.depth) | _.depth > 10000   !!" in stdout
        assert "(queues | max_by(_.depth)).depth > 10000     -> True" in stdout
        assert "BROKEN  stale-metric" in stdout

    def test_strings_and_regex_shows_slugify_dropping_rather_than_guessing(self, outputs):
        stdout = outputs["strings_and_regex"]
        assert 'slugify("日本語")' in stdout
        assert "-> ''" in stdout
        # The f-string refusal, next to the two spellings that work.
        assert "f-strings are not supported" in stdout
        assert "'order A-17 has 3 lines'" in stdout
