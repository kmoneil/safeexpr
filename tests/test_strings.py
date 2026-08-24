"""The strings tier.

Two things here are more than ordinary string handling.

**Nothing coerces.** `lower(user.age)` is a mistake in the rule and answering it with `"30"`
hides the mistake until the field arrives missing instead of numeric.

**Three functions can turn a short expression into a large value**, and all three are capped on
the size they would produce rather than after producing it. The step budget cannot see this: it
counts nodes evaluated, and the expression that asks for a megabyte is three nodes long.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, standard_registry
from safeexpr._guards import MAX_RESULT_SIZE

EV = Evaluator(registry=standard_registry())

# A deliberately awkward alphabet: accents that decompose, a script with no ASCII form, marks,
# separators and case. Plain `st.text()` would mostly generate characters that exercise nothing.
ALPHABET = "abcXYZ 09-_.,!éÜßﬁ日Ωq\t\n"
TEXT = st.text(alphabet=ALPHABET, max_size=40)


@pytest.fixture
def ev() -> Evaluator:
    return EV


class TestCaseAndWhitespace:
    def test_lower_and_upper(self, ev: Evaluator) -> None:
        assert ev.evaluate('lower("AbC")', {}) == "abc"
        assert ev.evaluate('upper("AbC")', {}) == "ABC"

    def test_strip(self, ev: Evaluator) -> None:
        assert ev.evaluate("strip(x)", {"x": "  a b  \n"}) == "a b"

    def test_upper_can_grow_text_and_is_capped_for_it(self, ev: Evaluator) -> None:
        """German sharp s uppercases to two characters, so the result is not bounded by the
        input's length and the cap is not decorative."""
        assert ev.evaluate('upper("ß")', {}) == "SS"


class TestSplitAndJoin:
    def test_split_on_whitespace_by_default(self, ev: Evaluator) -> None:
        assert ev.evaluate("split(x)", {"x": " a  b\tc "}) == ["a", "b", "c"]

    def test_split_on_a_separator(self, ev: Evaluator) -> None:
        assert ev.evaluate('split(x, ",")', {"x": "a,b,,c"}) == ["a", "b", "", "c"]

    def test_an_empty_separator_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('split(x, "")', {"x": "abc"})
        assert "not empty" in str(caught.value)

    def test_join_reads_in_pipe_order(self, ev: Evaluator) -> None:
        assert ev.evaluate('parts | join(", ")', {"parts": ["a", "b"]}) == "a, b"

    def test_join_of_nothing_is_empty(self, ev: Evaluator) -> None:
        assert ev.evaluate('join(parts, ",")', {"parts": []}) == ""

    def test_join_names_the_item_that_is_not_text(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('join(parts, ",")', {"parts": ["a", 2, "c"]})
        assert "item 1" in str(caught.value)
        assert "`int`" in str(caught.value)

    def test_split_then_join_round_trips(self, ev: Evaluator) -> None:
        assert ev.evaluate('x | split(",") | join(",")', {"x": "a,b,c"}) == "a,b,c"


class TestReplace:
    def test_it_replaces_every_occurrence(self, ev: Evaluator) -> None:
        assert ev.evaluate('replace(x, "a", "b")', {"x": "banana"}) == "bbnbnb"

    def test_replacing_with_nothing_removes(self, ev: Evaluator) -> None:
        assert ev.evaluate('replace(x, "a", "")', {"x": "banana"}) == "bnn"

    def test_an_empty_target_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('replace(x, "", "b")', {"x": "a"})
        assert "not empty" in str(caught.value)

    def test_growth_is_refused_before_it_is_allocated(self, ev: Evaluator) -> None:
        """`replace(x, "a", "aaaa...")` multiplies length by the ratio of its arguments, and
        nesting multiplies again. The count is one pass; the allocation would be a gigabyte."""
        source = 'replace(x, "a", big)'
        context = {"x": "a" * 10_000, "big": "b" * 1_000}
        with pytest.raises(EvaluationError) as caught:
            EV.evaluate(source, context)
        assert "over the limit" in str(caught.value)

    def test_shrinking_is_never_refused(self, ev: Evaluator) -> None:
        assert ev.evaluate('replace(x, "aa", "a")', {"x": "a" * 200_000}) == "a" * 100_000


class TestPredicates:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ('starts_with(x, "ab")', True),
            ('starts_with(x, "b")', False),
            ('ends_with(x, "cd")', True),
            ('ends_with(x, "c")', False),
            ('contains(x, "bc")', True),
            ('contains(x, "ca")', False),
        ],
    )
    def test_them(self, ev: Evaluator, source: str, expected: bool) -> None:
        assert ev.evaluate(source, {"x": "abcd"}) is expected

    def test_contains_reads_well_in_a_pipe(self, ev: Evaluator) -> None:
        assert ev.evaluate('user.email | contains("@")', {"user": {"email": "a@b.c"}}) is True


class TestSlugify:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Hello World", "hello-world"),
            ("  --Hello--World--  ", "hello-world"),
            ("Hello, World!", "hello-world"),
            ("a1 b2", "a1-b2"),
            ("", ""),
            ("!!!", ""),
        ],
    )
    def test_the_ordinary_cases(self, ev: Evaluator, value: str, expected: str) -> None:
        assert ev.evaluate("slugify(x)", {"x": value}) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("Héllo, Wörld!", "hello-world"), ("Ünïcödé Tëst", "unicode-test"), ("café", "cafe")],
    )
    def test_accented_latin_keeps_its_base_letter(
        self, ev: Evaluator, value: str, expected: str
    ) -> None:
        assert ev.evaluate("slugify(x)", {"x": value}) == expected

    @pytest.mark.parametrize(("value", "expected"), [("Ⅻ", "xii"), ("ﬁle", "file")])
    def test_compatibility_forms_decompose_usefully(
        self, ev: Evaluator, value: str, expected: str
    ) -> None:
        """A bonus of normalising rather than filtering: a Roman numeral and a ligature both have
        ASCII forms and both survive."""
        assert ev.evaluate("slugify(x)", {"x": value}) == expected

    @pytest.mark.parametrize("value", ["Ελληνικά", "日本語", "Привет"])
    def test_a_script_with_no_ascii_form_is_dropped_entirely(
        self, ev: Evaluator, value: str
    ) -> None:
        """**The documented, lossy part of ASCII-only slugify.** A title written entirely in one
        of these slugs to nothing at all. That is a real limitation rather than a corner case,
        and transliteration is what the `unicode` extra is for."""
        assert ev.evaluate("slugify(x)", {"x": value}) == ""

    def test_a_dropped_script_still_separates_the_words_around_it(self, ev: Evaluator) -> None:
        """Dropping the characters rather than the boundary: `a日b` is two words, not one."""
        assert ev.evaluate("slugify(x)", {"x": "a日b"}) == "a-b"


class TestNothingCoerces:
    @pytest.mark.parametrize(
        "source",
        [
            "lower(x)",
            "upper(x)",
            "strip(x)",
            "split(x)",
            "slugify(x)",
            'starts_with(x, "a")',
            'ends_with(x, "a")',
            'contains(x, "a")',
            'replace(x, "a", "b")',
        ],
    )
    @pytest.mark.parametrize("value", [1, None, ["a"], {"a": 1}, True])
    def test_a_value_that_is_not_text_is_refused(
        self, ev: Evaluator, source: str, value: Any
    ) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"x": value})
        assert "needs text" in str(caught.value)

    def test_the_message_names_the_type_and_never_the_value(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("lower(x)", {"x": {"secret": "sk-live"}})
        assert "`dict`" in str(caught.value)
        assert "sk-live" not in str(caught.value)


class TestDifferentialAgainstPython:
    """Every function against the plain Python that means the same thing, over generated text.

    Case folding, whitespace and separators are where string handling quietly disagrees with
    itself, and generated input finds the disagreements that chosen examples do not.
    """

    @given(value=TEXT)
    @settings(max_examples=200, deadline=None)
    def test_case_and_whitespace(self, value: str) -> None:
        assert EV.evaluate("lower(x)", {"x": value}) == value.lower()
        assert EV.evaluate("upper(x)", {"x": value}) == value.upper()
        assert EV.evaluate("strip(x)", {"x": value}) == value.strip()

    @given(value=TEXT)
    @settings(max_examples=200, deadline=None)
    def test_split_on_whitespace(self, value: str) -> None:
        assert EV.evaluate("split(x)", {"x": value}) == value.split()

    @given(value=TEXT, separator=st.sampled_from([",", "a", " ", "ab", "-"]))
    @settings(max_examples=200, deadline=None)
    def test_split_and_join_on_a_separator(self, value: str, separator: str) -> None:
        context = {"x": value, "s": separator}
        pieces = EV.evaluate("split(x, s)", context)
        assert pieces == value.split(separator)
        assert EV.evaluate("join(split(x, s), s)", context) == value

    @given(value=TEXT, old=st.sampled_from(["a", "b", "ab", "é"]), new=st.sampled_from(["", "z"]))
    @settings(max_examples=200, deadline=None)
    def test_replace(self, value: str, old: str, new: str) -> None:
        result = EV.evaluate("replace(x, o, n)", {"x": value, "o": old, "n": new})
        assert result == value.replace(old, new)

    @given(value=TEXT, needle=st.sampled_from(["a", "ab", "", "é", "日"]))
    @settings(max_examples=200, deadline=None)
    def test_the_predicates(self, value: str, needle: str) -> None:
        context = {"x": value, "n": needle}
        assert EV.evaluate("starts_with(x, n)", context) is value.startswith(needle)
        assert EV.evaluate("ends_with(x, n)", context) is value.endswith(needle)
        assert EV.evaluate("contains(x, n)", context) is (needle in value)


class TestSlugProperties:
    """What a slug *is*, rather than what any one input produces.

    An example test pins the cases somebody thought of. These say the output is always usable in
    a URL, which is the reason the function exists.
    """

    @given(value=TEXT)
    @settings(max_examples=300, deadline=None)
    def test_a_slug_holds_only_lowercase_ascii_words_and_single_hyphens(self, value: str) -> None:
        slug = EV.evaluate("slugify(x)", {"x": value})
        assert set(slug) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")
        assert "--" not in slug
        assert not slug.startswith("-")
        assert not slug.endswith("-")

    @given(value=TEXT)
    @settings(max_examples=300, deadline=None)
    def test_slugifying_a_slug_changes_nothing(self, value: str) -> None:
        once = EV.evaluate("slugify(x)", {"x": value})
        assert EV.evaluate("slugify(x)", {"x": once}) == once

    @given(value=TEXT)
    @settings(max_examples=200, deadline=None)
    def test_a_slug_is_bounded_by_the_shared_cap(self, value: str) -> None:
        """Bounded by the cap, **not** by the input's length. See the regression below: a slug
        can be longer than what it was given."""
        assert len(EV.evaluate("slugify(x)", {"x": value})) <= MAX_RESULT_SIZE


class TestRegressions:
    def test_regression_slugify_a_combining_mark_is_not_a_word_boundary(self) -> None:
        """`Héllo` slugged to `he-llo`.

        Normalising to NFKD splits `é` into `e` plus a combining acute so that the base letter
        survives the ASCII filter. The first draft then treated the leftover accent as
        punctuation, which broke the word in half at every accented letter. Combining marks
        belong to the letter before them and are now skipped rather than separating.
        """
        assert EV.evaluate("slugify(x)", {"x": "Héllo, Wörld!"}) == "hello-world"
        assert EV.evaluate("slugify(x)", {"x": "Ünïcödé Tëst"}) == "unicode-test"

    def test_regression_slugify_normalising_can_make_text_longer(self) -> None:
        """A property test asserting "a slug never grows" failed on `ﬁ`, and it was the test that
        was wrong.

        Normalising decomposes compatibility characters, so one character can become several: the
        ligature is two, the Roman numeral twelve is three, and Unicode permits worse. The bug it
        exposed was real, though: the size cap was being checked against the *input* length,
        which is not a bound on the output at all. It is now checked against the normalised text.
        """
        assert EV.evaluate("slugify(x)", {"x": "ﬁ"}) == "fi"
        assert EV.evaluate("slugify(x)", {"x": "Ⅻ"}) == "xii"
        assert len(EV.evaluate("slugify(x)", {"x": "ﬁ"})) > len("ﬁ")

    def test_regression_strings_a_growing_result_is_bounded(self) -> None:
        """`replace` and `join` can both produce far more text than they were given, and the step
        budget cannot see it because it counts nodes rather than characters."""
        with pytest.raises(EvaluationError):
            EV.evaluate('replace(x, "a", big)', {"x": "a" * 5_000, "big": "b" * 5_000})
        with pytest.raises(EvaluationError):
            EV.evaluate('join(parts, "")', {"parts": ["a" * 10_000] * 500})

    def test_regression_strings_the_cap_is_the_shared_one(self) -> None:
        """One ceiling with one name, so text and sequence repetition cannot drift apart."""
        assert MAX_RESULT_SIZE == 1_048_576
        assert EV.evaluate('join(parts, "")', {"parts": ["a" * 1000] * 100}) == "a" * 100_000
