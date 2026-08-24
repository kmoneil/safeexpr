"""The audit-hook tripwire: fuzz the evaluator and fail if anything fires.

T5. Every other test here checks something somebody thought of. The corpus lists escapes that
have been published; the differential generator compares against a subset somebody wrote down.
`sys.addaudithook` (PEP 578) is the only mechanism that watches for what nobody thought of: it
observes `exec`, `compile`, `import`, `open`, `os.system` and the subprocess events
**process-wide**, below the level any expression could reach.

**Run in a subprocess, and that is not incidental.** An audit hook cannot be removed once
installed and fires for every audited operation in the process, so installing one inside pytest
would slow every remaining test and mix the suite's own file reads into the signal.

**Exactly one event is expected**, measured rather than assumed: `compile`, from `ast.parse`
turning source into a tree, and allowed only when its argument is the very source that was passed
in. Everything else is a finding.

**Not a defence layer.** Hooks observe rather than block, they fire process-wide so a host would
pay for every audited operation, and one cannot be uninstalled, so a broken sandbox could use it
as a target rather than a shield. Nothing here ships in the package.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import audit_fuzz  # noqa: E402

SCRIPT = ROOT / "scripts" / "audit_fuzz.py"

# Enough to be worth running in every CI job without being the slowest thing in the suite. The
# published run is larger; this is the tripwire's floor rather than its ceiling.
ROUNDS = 1200


def fuzz(rounds: int = ROUNDS, seed: int = 20260824, program: str | None = None) -> dict[str, int]:
    """Run the fuzzer in its own process and return its report."""
    command = (
        [sys.executable, "-c", program]
        if program
        else [sys.executable, str(SCRIPT), "--rounds", str(rounds), "--seed", str(seed), "--json"]
    )
    finished = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    assert finished.returncode in {0, 1}, f"the fuzzer crashed: {finished.stderr[-2000:]}"
    return json.loads(finished.stdout)


@pytest.fixture(scope="module")
def report() -> dict[str, int]:
    return fuzz()


class TestNothingFiresTheHook:
    def test_zero_audit_events(self, report: dict[str, int]) -> None:
        """The acceptance criterion. Any event other than this package parsing its own source is
        a finding, and a finding is an escape or the beginning of one."""
        assert report["findings"] == [], (
            f"{len(report['findings'])} audit events during evaluation: {report['findings'][:5]}"
        )

    def test_nothing_escaped_as_something_other_than_a_safeexpr_error(
        self, report: dict[str, int]
    ) -> None:
        """Recorded as a finding too, because "every failure is a `SafeExprError`" is a promise
        this package makes and a fuzzer is the right thing to check it against."""
        assert not [event for event, _ in report["findings"] if event == "not-a-safeexpr-error"]


class TestTheRunActuallyReachedSomething:
    """**Without this the tripwire reports zero findings by finding nothing.**

    A fuzzer whose inputs all die at the parser hammers one barrier and proves nothing about the
    rest. These are floors on what the run reached, and they are the same lesson the differential
    generator's coverage assertion teaches: a generator that shrinks still passes every test it
    has.
    """

    def test_most_inputs_got_past_the_parser(self, report: dict[str, int]) -> None:
        assert report["parsed"] > report["tried"] * 0.5, (
            f"only {report['parsed']} of {report['tried']} inputs reached validation, so the "
            f"run mostly tested the parser"
        )

    def test_a_real_number_of_inputs_evaluated_successfully(self, report: dict[str, int]) -> None:
        """The hook has to watch an evaluator that actually evaluates, not one that refuses
        everything it is given."""
        assert report["evaluated"] > 50, (
            f"only {report['evaluated']} inputs evaluated, so the hook was watching an evaluator "
            f"that never ran anything"
        )

    def test_and_a_real_number_were_refused(self, report: dict[str, int]) -> None:
        """The other side of the same coin: a run that accepted everything would mean the
        escape-shaped seeds had stopped being escape-shaped."""
        assert report["refused"] > report["tried"] * 0.5


class TestTheTripwireActuallyFires:
    """Proof the run above is not vacuous, by giving it something to find.

    A registry function that reads a file is exactly the shape of a real escape's last step, and
    it is invisible to every other test here: the corpus does not list it, the differential
    generator does not produce it, and the evaluator raises nothing. The hook sees it.
    """

    def test_a_registry_function_that_opens_a_file_is_caught(self) -> None:
        program = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            "import audit_fuzz\n"
            "from pathlib import Path\n"
            "from safeexpr import standard_registry\n"
            "real = audit_fuzz.standard_registry\n"
            "def leaky():\n"
            "    registry = real()\n"
            "    registry['first'] = lambda items: Path('/etc/hostname').read_text()\n"
            "    return registry\n"
            "audit_fuzz.standard_registry = leaky\n"
            "print(json.dumps(audit_fuzz.run(600, 20260824)))\n"
        )
        report = fuzz(program=program)
        events = {event for event, _ in report["findings"]}
        assert "open" in events, (
            "a registry function read a file and the hook did not notice, so the tripwire in "
            "the tests above proves nothing"
        )

    def test_the_script_exits_non_zero_when_it_finds_something(self) -> None:
        """So CI fails rather than printing a finding into a log nobody reads."""
        program = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            "import audit_fuzz\n"
            "audit_fuzz.run = lambda rounds, seed: {"
            "'seed': seed, 'tried': 1, 'parsed': 1, 'evaluated': 1, 'refused': 0,"
            " 'findings': [('open', '(\\'/etc/passwd\\',)')]}\n"
            "raise SystemExit(audit_fuzz.main())\n"
        )
        finished = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert finished.returncode == 1
        assert "AUDIT FINDINGS" in finished.stdout


class TestItIsReproducible:
    def test_the_same_seed_gives_the_same_run(self) -> None:
        first = fuzz(rounds=400, seed=99)
        second = fuzz(rounds=400, seed=99)
        assert first == second

    def test_a_different_seed_gives_a_different_run(self) -> None:
        """Otherwise the seed is decorative and every run tries the same expressions."""
        assert fuzz(rounds=400, seed=1) != fuzz(rounds=400, seed=2)


class TestTheMutations:
    """The part that decides what the fuzzer reaches, so it is tested rather than assumed."""

    @pytest.mark.parametrize("mutation", audit_fuzz.MUTATIONS)
    def test_every_mutation_is_deterministic_for_a_seed(self, mutation: object) -> None:
        source = 'x.__class__ | first("a")'
        first = mutation(source, random.Random(7))  # type: ignore[operator]
        second = mutation(source, random.Random(7))  # type: ignore[operator]
        assert first == second

    @pytest.mark.parametrize("mutation", audit_fuzz.MUTATIONS)
    def test_every_mutation_survives_an_empty_string(self, mutation: object) -> None:
        """The character-level edits index into the source, and the fuzzer feeds them their own
        output, so an empty string reaches them eventually."""
        assert isinstance(mutation("", random.Random(7)), str)  # type: ignore[operator]

    def test_the_mutations_actually_differ_from_their_input(self) -> None:
        rng = random.Random(11)
        source = "items | where(_ == 1)"
        changed = sum(audit_fuzz.mutate(source, rng) != source for _ in range(200))
        assert changed > 150, "most mutations returned their input unchanged"

    def test_the_escape_seeds_are_the_shapes_that_broke_other_sandboxes(self) -> None:
        """A seed list that drifted away from real escape shapes would still fuzz and would stop
        aiming at anything."""
        joined = " ".join(audit_fuzz.ESCAPE_SEEDS)
        for shape in ("__class__", "__subclasses__", "format", "__import__", "globals", "lambda"):
            assert shape in joined
