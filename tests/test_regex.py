"""`matches`, and the static gate that decides which patterns are allowed to run.

**Every pattern in the corpus below was timed before the gate was written**, against adversarial
input tailored to its own shape, and the rules are fitted to what the measurements separated
rather than to a textbook taxonomy. Two of the obvious rules were wrong and the measurements said
so: rejecting alternation branches that share a first character would refuse `(foo|bar|baz)+$`,
and rejecting one branch that prefixes another would refuse `(a|ab)*$`. Both are fast.

The corpus is the test. `SLOW` patterns must be refused before they compile, `FAST` ones must be
accepted, and every accepted one must then stay under a wall-clock ceiling on the same
adversarial input, which is the property the whole gate exists to provide.
"""

from __future__ import annotations

import re
import statistics
import time
import warnings
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, standard_registry
from safeexpr._regex import (
    _CACHE,
    _MAX_CACHE,
    _MAX_PATTERN_LENGTH,
    _MAX_SUBJECT_LENGTH,
    _PARSER,
    _PARSER_NAME,
    check,
)

EV = Evaluator(registry=standard_registry())

# Patterns that backtrack catastrophically. Every one was measured: at an 18-character subject
# they run between 7ms and 64ms where every benign pattern runs in under 10 microseconds, and the
# time roughly doubles per character added.
CATASTROPHIC = [
    r"^(a+)+$",
    r"(a+)+$",
    r"(x+x+)+y",
    r"^(\w+\s?)*$",
    r"^(\s*\w+)*$",
    r"(\d+)*$",
    r"^(a*)*$",
    r"(a*)+$",
    r"((a)*)*$",
    r"^(([a-z])+.)+[A-Z]([a-z])+$",
    r"(a|a)*$",
    r"((a)|(a))*$",
    r"(?:a|a)+x",
    r"(a|a?)*$",
    r"^(a{1,20}){1,20}$",
]

# Patterns a rule actually contains. All measured in microseconds against the same attacks.
BENIGN = [
    r"a+b",
    r"^[a-z]+@[a-z]+\.[a-z]{2,}$",
    r"(a|b)*$",
    r"(ab|cd)*$",
    r"(a|ab)*$",
    r"^\d{4}-\d{2}-\d{2}$",
    r".*foo.*",
    r"[a-z]+",
    r"(?:abc)+",
    r"^\w+$",
    r"a{2,5}b",
    r"(foo|bar|baz)+$",
    r"(bar|baz)+$",
    r"^https?://[^/]+/",
    r"[a-z]+\d*",
    r"(a?){10}a{10}",
    r"^\S+@\S+$",
    r"(a)|(a)",
    r"^(?>a+)+$",
    r"^(a++)+$",
    r"(?>\w+\s?)*$",
]

# One per shape a pattern in the corpus might be attacked with.
ATTACKS = [
    "a" * 22 + "!",
    "x" * 22 + "!",
    "1" * 22 + "!",
    " " * 22 + "!",
    "ab" * 11 + "!",
    "a" * 22 + "b",
    "foo" * 7 + "!",
    "bar" * 7 + "!",
]


def fastest(run: Callable[[], object], samples: int = 7) -> float:
    """The shortest of several timings of `run`.

    **The minimum, not the mean or the median, and that is the point.** Interference only ever
    adds time: a scheduler preemption, a garbage collection, another process on the machine.
    None of them can make an operation finish sooner, so the smallest observation is the closest
    thing to the operation's own cost and is robust to a loaded box by construction.

    A wall-clock assertion on a single sample is a coin toss with very good odds, and a suite
    with hundreds of runs will eventually lose one. This suite saw exactly that: a single
    unreproducible failure on 3.14 while the machine was busy building another environment.

    Args:
        run: The operation to time.
        samples: How many times to run it.

    Returns:
        The shortest elapsed time, in seconds.
    """
    best = float("inf")
    for _ in range(samples):
        started = time.perf_counter()
        run()
        best = min(best, time.perf_counter() - started)
    return best


@pytest.fixture
def ev() -> Evaluator:
    return EV


class TestTheGateSeparatesTheCorpus:
    @pytest.mark.parametrize("pattern", CATASTROPHIC)
    def test_a_catastrophic_pattern_is_refused(self, pattern: str) -> None:
        assert check(pattern) is not None, (
            f"{pattern!r} was accepted. It backtracks exponentially, and no input-length cap "
            f"helps: the blowup is driven by the pattern's structure."
        )

    @pytest.mark.parametrize("pattern", BENIGN)
    def test_a_benign_pattern_is_accepted(self, pattern: str) -> None:
        assert check(pattern) is None, f"{pattern!r} was refused: {check(pattern)}"

    @pytest.mark.parametrize("pattern", [r"^(a+)+$", r"(x+x+)+y", r"^(\w+\s?)*$", r"(a|a)*$"])
    def test_the_four_the_card_names_are_refused(self, pattern: str) -> None:
        """Named in the acceptance criteria, so they get an assertion of their own rather than
        being lost among the rest of the corpus."""
        assert check(pattern) is not None

    @pytest.mark.parametrize("pattern", [r"a+b", r"^[a-z]+@[a-z]+\.[a-z]{2,}$"])
    def test_the_two_the_card_names_are_accepted(self, pattern: str) -> None:
        assert check(pattern) is None


class TestEachRuleCatchesItsOwnShape:
    """The three rules do different work, and a corpus test alone would not show which.

    Rule 1 scores `(a|a?)*$` and `(a|a)*$` as safe, and rule 2 scores `(a|a)*$` as safe. Losing
    any one of them loses patterns nothing else catches.
    """

    @pytest.mark.parametrize(
        "pattern", [r"^(a+)+$", r"((a)*)*$", r"^(a{1,20}){1,20}$", r"(a|a?)*$"]
    )
    def test_rule_one_nested_backtrackable_repeats(self, pattern: str) -> None:
        assert "nests one repeat inside another" in (check(pattern) or "")

    def test_rule_one_counts_bounded_repeats_too(self) -> None:
        """`^(a{1,20}){1,20}$` has no unbounded quantifier anywhere and is still measurably slow.
        A rule written for unbounded repeats only would let it through."""
        assert check(r"^(a{1,20}){1,20}$") is not None
        assert check(r"^(a{2}){3}$") is None, "a repeat with no choice cannot backtrack"

    @pytest.mark.parametrize("pattern", [r"(a|a)*$", r"((a)|(a))*$", r"(?:a|a)+x"])
    def test_rule_two_a_repeat_over_a_choice_that_repeats_itself(self, pattern: str) -> None:
        assert "can match the same text" in (check(pattern) or "")

    def test_rule_two_ignores_group_numbering(self) -> None:
        """`(a)|(a)` and `a|a` are different groups and the same language, and it is the language
        that decides whether the alternation is ambiguous."""
        assert check(r"((a)|(a))*$") is not None

    @pytest.mark.parametrize("pattern", [r"(a+|a+)*$", r"(a{1,2}|a{1,2})*$", r"(a*|a*)+$"])
    def test_rule_two_compares_branches_that_contain_repeats(self, pattern: str) -> None:
        """The comparison is structural, so branches are equal when their repeats are equal too,
        bounds included."""
        assert check(pattern) is not None

    def test_rule_two_looks_inside_nested_alternations(self) -> None:
        """The twins can be buried one level down and still make the outer repeat ambiguous."""
        assert check(r"((a|a)|b)*$") is not None
        assert check(r"((a|c)|b)*$") is None

    def test_rule_two_is_narrower_than_the_obvious_version(self) -> None:
        """Measured, not assumed. Refusing branches that share a first character would refuse
        the first of these, and refusing a branch that prefixes another would refuse the second.
        Both run in microseconds."""
        assert check(r"(foo|bar|baz)+$") is None
        assert check(r"(a|ab)*$") is None

    def test_an_alternation_with_no_repeat_over_it_is_fine(self) -> None:
        """There is nothing to backtrack over without a quantifier, however redundant the
        alternation is."""
        assert check(r"(a)|(a)") is None


class TestAtomicConstructsResetTheNesting:
    """The Q5 revisit. Atomic groups and possessive quantifiers were ruled out as a mitigation
    only because the floor was 3.10 and they did not exist there; the floor is 3.11 now.

    They cannot give back what they matched, so nesting above them cannot make them backtrack.
    Measured: `^(a+)+$` takes 0.47 seconds on a 24-character subject and `^(?>a+)+$` takes none.
    """

    @pytest.mark.parametrize(
        "pattern", [r"^(?>a+)+$", r"^(a++)+$", r"(?>\w+\s?)*$", r"(a*+)*$", r"(?>a|)*$"]
    )
    def test_an_atomic_or_possessive_inner_repeat_is_accepted(self, pattern: str) -> None:
        assert check(pattern) is None

    def test_the_same_pattern_without_them_is_refused(self) -> None:
        """Side by side, so the difference is visibly the atomic construct and nothing else."""
        assert check(r"^(a+)+$") is not None
        assert check(r"^(?>a+)+$") is None

    def test_they_are_available_on_this_interpreter_at_all(self) -> None:
        """A canary of its own: if a supported interpreter stopped accepting them, the tests
        above would pass for the wrong reason."""
        assert re.compile(r"(?>a+)").search("aaa") is not None
        assert re.compile(r"a++b").search("aab") is not None


class TestTheRuleThatWasMeasuredAndRemoved:
    """ "A repeat whose body can match the empty string" is a real ReDoS class in other engines.

    It was written, measured and taken out. Everything it uniquely flagged is fast on all four
    supported interpreters, because CPython's engine breaks out of an empty-match loop by itself,
    and everything genuinely slow that it flagged is already refused for nesting. A rule whose
    only unique effect is refusing safe patterns is worse than no rule.

    Recorded as tests so that the next person to read the taxonomy and notice the gap finds the
    measurement rather than repeating it.
    """

    @pytest.mark.parametrize("pattern", [r"(a|)*$", r"(|a)*$", r"(a|b|)*$", r"(?:a|)*$"])
    def test_a_repeat_over_an_empty_alternative_is_accepted_and_is_fast(self, pattern: str) -> None:
        assert check(pattern) is None
        compiled = re.compile(pattern)
        assert fastest(lambda: compiled.search("ab" * 11 + "!")) < 0.01

    @pytest.mark.parametrize("pattern", [r"(a|a?)*$", r"((a)*)*$"])
    def test_the_slow_ones_it_flagged_are_refused_for_nesting_anyway(self, pattern: str) -> None:
        assert "nests one repeat inside another" in (check(pattern) or "")


class TestTheGateIsConservativeAndSaysSo:
    @pytest.mark.parametrize("pattern", [r"(.*)*$", r"(.+)+$", r"(a?)*$", r"(x?y?)*$"])
    def test_a_textbook_shape_is_refused_even_where_re_optimises_it(self, pattern: str) -> None:
        """These measure fast on this interpreter because `re` optimises them. They are refused
        anyway: the optimisation is an implementation detail, and a gate that refuses a pattern
        which happens to be fast today is worth much more than one that accepts a pattern which
        is slow tomorrow."""
        assert check(pattern) is not None


class TestNoAcceptedPatternCanBeMadeSlow:
    """The acceptance criterion that matters most, and the only one that tests the *point*.

    Refusing the right patterns is worth nothing if an accepted one still burns a minute. Every
    benign pattern is run against every attack string and timed.
    """

    @pytest.mark.parametrize("pattern", BENIGN)
    def test_it_stays_under_the_ceiling_on_every_attack(self, pattern: str) -> None:
        compiled = re.compile(pattern)
        worst = max(fastest(lambda text=text: compiled.search(text)) for text in ATTACKS)  # type: ignore[misc]
        # Benign patterns measure in microseconds, so a tenth of a second is roughly four orders
        # of magnitude of headroom. Loose enough not to flake on a loaded machine, tight enough
        # that anything actually backtracking blows straight through it.
        assert worst < 0.1, f"{pattern!r} took {worst:.3f}s on adversarial input"

    @staticmethod
    def _worst(pattern: str, length: int) -> float:
        compiled = re.compile(pattern)
        return max(
            fastest(lambda filler=filler: compiled.search(filler * length + "!"), samples=3)  # type: ignore[misc]
            for filler in ("a", "x", "1", " ")
        )

    @pytest.mark.parametrize("pattern", CATASTROPHIC)
    def test_the_refused_ones_would_have_been_slow(self, pattern: str) -> None:
        """The other half of the claim: the gate is not refusing things at random.

        Compared against the slowest *benign* pattern on the same machine rather than against a
        constant, because an absolute threshold is a guess about how fast the box is. One of
        these, `^(([a-z])+.)+[A-Z]([a-z])+$`, is only about a hundred times the benign baseline
        at this length rather than thousands, which is why the multiple is ten and not more.
        """
        # The *median* benign time, not the maximum. One benign pattern measuring slowly on a
        # loaded machine would otherwise raise the bar for every catastrophic one; measured, the
        # benign spread is 0.3 to 14 microseconds around a median of 0.8, so the max is an
        # eighteen-fold outlier and the median is not.
        baseline = statistics.median(self._worst(benign, 18) for benign in BENIGN)
        worst = self._worst(pattern, 18)
        assert worst > baseline * 10, (
            f"{pattern!r} is in the catastrophic corpus but ran in {worst:.6f}s against a benign "
            f"baseline of {baseline:.6f}s; either the attack no longer suits it or it does not "
            f"belong in the list"
        )


class TestTheCanaryOnAPrivateStdlibApi:
    """`re._parser` is private and was renamed from `sre_parse` in 3.11. If it moves again this
    package cannot check patterns, and the failure must be loud rather than silent."""

    def test_the_parser_is_available(self) -> None:
        assert _PARSER is not None, (
            "the standard library's regex parser could not be found under any known name. "
            "matches() now refuses every pattern, which is the correct fail-closed behaviour "
            "and is also a release blocker."
        )
        assert _PARSER_NAME in {"re._parser", "sre_parse"}

    def test_it_still_parses_and_still_produces_the_opcodes_the_gate_reads(self) -> None:
        """Finding the module is not enough: the gate reads opcode names off what it returns."""
        seen = {str(op) for op, _ in _PARSER.parse(r"(a+)+")}
        assert "MAX_REPEAT" in seen

    @pytest.mark.parametrize(
        ("pattern", "opcode"),
        [(r"(a+)+", "MAX_REPEAT"), (r"(?>a+)", "ATOMIC_GROUP"), (r"a++", "POSSESSIVE_REPEAT")],
    )
    def test_every_opcode_the_gate_depends_on_still_exists(self, pattern: str, opcode: str) -> None:
        assert opcode in {str(op) for op, _ in _PARSER.parse(pattern)}

    def test_the_gate_fails_closed_when_the_parser_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Losing the parser must not compile patterns unchecked, and must not stop the package
        importing either."""
        monkeypatch.setattr("safeexpr._regex._PARSER", None)
        assert check(r"a+b") is not None
        assert "unchecked pattern is not run" in (check(r"a+b") or "")


class TestMatchesTheFunction:
    def test_it_searches(self, ev: Evaluator) -> None:
        assert ev.evaluate('matches(x, "b+")', {"x": "abbbc"}) is True
        assert ev.evaluate('matches(x, "z+")', {"x": "abbbc"}) is False

    def test_anchors_mean_what_they_say(self, ev: Evaluator) -> None:
        """Searching rather than requiring the whole subject to match, so an author who wants a
        whole-string match asks for one."""
        assert ev.evaluate('matches(x, "^abc$")', {"x": "abc"}) is True
        assert ev.evaluate('matches(x, "^abc$")', {"x": "xabcx"}) is False
        assert ev.evaluate('matches(x, "abc")', {"x": "xabcx"}) is True

    def test_it_reads_well_in_a_pipe(self, ev: Evaluator) -> None:
        source = 'user.email | matches("^[a-z]+@example\\\\.com$")'
        assert ev.evaluate(source, {"user": {"email": "ada@example.com"}}) is True

    def test_it_works_inside_a_predicate(self, ev: Evaluator) -> None:
        rows = [{"n": "abc"}, {"n": "xyz"}]
        assert ev.evaluate('rows | where(matches(_.n, "^a")) | len', {"rows": rows}) == 1

    def test_a_refused_pattern_says_why_and_how_to_fix_it(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('matches(x, "^(a+)+$")', {"x": "aaa"})
        message = str(caught.value)
        assert "nests one repeat inside another" in message
        assert "(?>...)" in message

    def test_an_invalid_pattern_is_an_ordinary_error(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('matches(x, "(")', {"x": "a"})
        assert "not a valid regular expression" in str(caught.value)
        assert "bug in safeexpr" not in str(caught.value)

    @pytest.mark.parametrize("value", [1, None, ["a"], {"a": 1}])
    def test_something_that_is_not_text_is_refused(self, ev: Evaluator, value: Any) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('matches(x, "a")', {"x": value})
        assert "needs text" in str(caught.value)

    def test_a_pattern_that_is_not_text_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("matches(x, 1)", {"x": "a"})
        assert "needs a pattern as text" in str(caught.value)

    def test_the_error_never_repeats_the_subject_back(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('matches(x, "^(a+)+$")', {"x": "sk-live-SECRET"})
        assert "sk-live" not in str(caught.value)


class TestTheDefencesInDepth:
    """Neither of these is the mitigation, and both are labelled that way in the source. The
    pattern gate is what bounds the work; these only stop sheer size."""

    def test_a_very_long_pattern_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("matches(x, p)", {"x": "a", "p": "a" * (_MAX_PATTERN_LENGTH + 1)})
        assert "over the limit" in str(caught.value)

    def test_the_length_cap_is_part_of_the_gate_rather_than_of_its_caller(self) -> None:
        """So nothing can consult the gate without also getting the bound. It is also what makes
        parsing safe to attempt at all: 512 characters cannot nest more than 256 groups, and the
        standard library's parser handles over 400 without giving out."""
        assert check("a" * (_MAX_PATTERN_LENGTH + 1)) is not None
        assert check("a" * _MAX_PATTERN_LENGTH) is None

    def test_a_very_long_subject_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('matches(x, "a")', {"x": "a" * (_MAX_SUBJECT_LENGTH + 1)})
        assert "over the limit" in str(caught.value)

    def test_a_deeply_nested_pattern_is_refused_before_anything_walks_it(self) -> None:
        """The nesting cap is checked iteratively and is what makes the recursive analysis safe.
        A recursive depth check would be the thing it is checking for."""
        assert check("(" * 60 + "a" + ")" * 60) is not None

    def test_matches_costs_more_than_any_other_function(self) -> None:
        """It is the one function whose work happens outside the step budget entirely: the
        counter sees one call and `re` does the rest in C."""
        registry = standard_registry()
        assert registry["matches"].cost > max(
            f.cost for name, f in registry.items() if name != "matches"
        )


class TestTheCache:
    def test_a_pattern_is_compiled_once(self, ev: Evaluator) -> None:
        _CACHE.clear()
        for _ in range(5):
            ev.evaluate('matches(x, "^a+b$")', {"x": "aab"})
        assert list(_CACHE) == ["^a+b$"]

    def test_a_refused_pattern_is_never_cached(self, ev: Evaluator) -> None:
        _CACHE.clear()
        with pytest.raises(EvaluationError):
            ev.evaluate('matches(x, "^(a+)+$")', {"x": "a"})
        assert _CACHE == {}

    def test_the_cache_is_bounded_because_a_pattern_can_come_from_the_context(self) -> None:
        """A rule running per row with a per-row pattern would otherwise grow it without end."""
        _CACHE.clear()
        for n in range(_MAX_CACHE + 20):
            # Genuinely distinct each time. An earlier version of this test reused ninety
            # patterns and never filled the cache, so it asserted the bound without reaching it.
            EV.evaluate("matches(x, p)", {"x": "a", "p": f"^a{{1,{n + 1}}}b{n}$"})
        assert len(_CACHE) <= _MAX_CACHE
        assert len(_CACHE) < _MAX_CACHE + 20, "the cache never reached its bound"
        _CACHE.clear()


class TestPropertiesOverGeneratedPatterns:
    """Whatever the gate decides, two things must hold: it must decide the same way every time,
    and anything it accepts must compile."""

    PIECES = st.sampled_from(
        ["a", "b", "[a-z]", r"\d", r"\w", ".", "(a|b)", "(?:ab)", "a+", "a*", "a?", "a{1,3}"]
    )
    PATTERNS = st.lists(PIECES, min_size=1, max_size=5).map("".join)

    @given(pattern=PATTERNS)
    @settings(max_examples=300, deadline=None)
    def test_the_verdict_is_stable(self, pattern: str) -> None:
        assert check(pattern) == check(pattern)

    @given(pattern=PATTERNS)
    @settings(max_examples=300, deadline=None)
    def test_anything_accepted_compiles(self, pattern: str) -> None:
        if check(pattern) is None:
            re.compile(pattern)

    @given(pattern=PATTERNS)
    @settings(max_examples=200, deadline=None)
    def test_wrapping_an_accepted_pattern_in_a_repeat_never_makes_it_safer(
        self, pattern: str
    ) -> None:
        """Monotonic in the direction that matters: adding a repeat around something can only
        introduce nesting, never remove it."""
        if check(f"(?:{pattern})") is not None:
            assert check(f"(?:{pattern})*") is not None


class TestPatternsTheEngineWarnsAbout:
    """`re` warns about some patterns rather than refusing them, and a warning is an exception
    under `-W error`, which is ordinary in CI and is this project's own pytest setting."""

    WARNING_PATTERNS = (r"[a--b]", r"[[a]]", r"[a||b]", r"[a&&b]")

    @pytest.mark.parametrize("pattern", WARNING_PATTERNS)
    def test_it_is_refused_cleanly_when_warnings_are_errors(self, pattern: str) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(EvaluationError) as caught:
                EV.evaluate("matches(x, p)", {"x": "ab", "p": pattern})
        message = str(caught.value)
        assert "bug in safeexpr" not in message
        assert "`matches`" in message

    @pytest.mark.parametrize("pattern", [r"[[a]]", r"[a||b]", r"[a&&b]"])
    def test_the_same_pattern_is_accepted_when_warnings_are_not_errors(self, pattern: str) -> None:
        """**An asymmetry, stated rather than hidden.** A pattern the engine only warns about
        compiles fine, so under ordinary filters it works and under `-W error` it is refused.

        Removing the asymmetry would mean capturing warnings around every compile, and
        `warnings.catch_warnings` mutates a process-global filter list. That would quietly break
        the promise that one evaluator is safe to share between threads, which is a worse thing
        to be untrue than this is to be uneven.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert EV.evaluate("matches(x, p)", {"x": "ab", "p": pattern}) is not None

    def test_a_pattern_that_is_also_invalid_is_refused_either_way(self) -> None:
        """`[a--b]` both warns and fails to compile, so the filters change only the message."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(EvaluationError):
                EV.evaluate("matches(x, p)", {"x": "ab", "p": "[a--b]"})

    def test_the_message_does_not_repeat_the_pattern_back(self) -> None:
        """The pattern can come from the host's data, and the engine's own warning text quotes
        it."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(EvaluationError) as caught:
                EV.evaluate("matches(x, p)", {"x": "ab", "p": "[sk-live--secret]"})
        assert "sk-live" not in str(caught.value)

    def test_an_ordinary_pattern_warns_about_nothing(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert EV.evaluate('matches("abc", "^a[b-c]+$")', {}) is True


class TestRegressions:
    def test_regression_regex_a_warned_about_pattern_was_reported_as_a_bug_here(self) -> None:
        """**Found by the audit-hook fuzzer, and by nothing that was looking for it.**

        Under `-W error` a pattern like `[a--b]` raised `FutureWarning` from inside the gate's
        parse, which no handler caught, so it reached the boundary as "internal error while
        evaluating (FutureWarning); this is a bug in safeexpr, please report it".

        The fuzzer did not find it by testing regular expressions. It found it because printing
        the warning made CPython open this package's source to show the offending line, and the
        audit hook saw an `open` during evaluation.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(EvaluationError) as caught:
                EV.evaluate("matches(x, p)", {"x": "ab", "p": "[a--b]"})
        assert "bug in safeexpr" not in str(caught.value)
        assert "warns about" in str(caught.value)

    def test_regression_regex_bounded_nesting_is_still_nesting(self) -> None:
        """The research proposed counting *unbounded* repeats, and `^(a{1,20}){1,20}$` has none.

        Measured at 7ms on an 18-character subject where every benign pattern is under 10
        microseconds. The rule counts any repeat with a choice to make, so `a{1,20}` counts and
        `a{2}` does not.
        """
        assert check(r"^(a{1,20}){1,20}$") is not None
        assert check(r"^(a{2}){3}$") is None

    def test_regression_regex_an_alternation_needs_a_repeat_to_be_dangerous(self) -> None:
        """`(a)|(a)` is redundant and harmless: with no quantifier there is nothing to backtrack
        over. Refusing it would have been a false positive on a pattern people really write."""
        assert check(r"(a)|(a)") is None
        assert check(r"((a)|(a))*$") is not None

    def test_regression_regex_the_gate_runs_before_compilation(self) -> None:
        """A refused pattern must never reach `re.compile`, so a pattern that is both refused and
        expensive to compile costs nothing."""
        _CACHE.clear()
        with pytest.raises(EvaluationError):
            EV.evaluate('matches(x, "^(a+)+$")', {"x": "a"})
        assert "^(a+)+$" not in _CACHE
