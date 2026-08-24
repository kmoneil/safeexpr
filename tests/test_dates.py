"""The dates tier.

The card singles out one requirement: `format_date` must not route a user's string through
`str.format`. That is F1 exactly. A format string is interpreted at runtime, `"{0.__class__}"`
performs an attribute lookup no AST check reads, and it is the most-repeated escape in the
competitive scan. Formatting here goes through `strftime`, whose directives name calendar fields
and nothing else, and `tests/test_tiers.py` asserts that against the source.

The rest of this file is about two problems `strftime` has of its own: directives vary by C
library, and format strings multiply.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, standard_registry
from safeexpr._dates import _DIRECTIVES, _MAX_FORMAT_LENGTH

EV = Evaluator(registry=standard_registry())


@pytest.fixture
def ev() -> Evaluator:
    return EV


class TestParseIso:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-08-24T13:45:00", datetime(2026, 8, 24, 13, 45, 0)),
            ("2026-08-24", datetime(2026, 8, 24, 0, 0, 0)),
            ("2026-08-24T13:45:00.500", datetime(2026, 8, 24, 13, 45, 0, 500_000)),
        ],
    )
    def test_it_reads_iso_timestamps(self, ev: Evaluator, value: str, expected: datetime) -> None:
        assert ev.evaluate("parse_iso(x)", {"x": value}) == expected

    def test_a_date_alone_lands_at_midnight(self, ev: Evaluator) -> None:
        """Which is what `_.due > parse_iso("2026-01-01")` means."""
        assert ev.evaluate('parse_iso("2026-01-01")', {}) == datetime(2026, 1, 1)

    def test_a_zone_offset_is_kept(self, ev: Evaluator) -> None:
        parsed = ev.evaluate('parse_iso("2026-08-24T13:45:00+00:00")', {})
        assert parsed.tzinfo is not None

    @pytest.mark.parametrize("value", ["not a date", "", "2026-13-45", "24/08/2026", "2026-08-"])
    def test_text_that_is_not_iso_is_refused(self, ev: Evaluator, value: str) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("parse_iso(x)", {"x": value})
        assert "ISO 8601" in str(caught.value)

    def test_the_refusal_does_not_repeat_the_text_back(self, ev: Evaluator) -> None:
        """It is the host's data, and the message is read by whoever wrote the expression."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("parse_iso(x)", {"x": "sk-live-SECRET"})
        assert "sk-live" not in str(caught.value)

    @pytest.mark.parametrize("value", [1, None, ["2026-01-01"], datetime(2026, 1, 1)])
    def test_something_that_is_not_text_is_refused(self, ev: Evaluator, value: Any) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("parse_iso(x)", {"x": value})
        assert "needs an ISO 8601 timestamp as text" in str(caught.value)

    def test_the_result_compares(self, ev: Evaluator) -> None:
        """The main thing a rule does with a date."""
        source = 'parse_iso(event.at) > parse_iso("2026-01-01")'
        assert ev.evaluate(source, {"event": {"at": "2026-08-24"}}) is True
        assert ev.evaluate(source, {"event": {"at": "2025-08-24"}}) is False

    def test_reading_a_field_off_one_needs_the_host_to_opt_in(self, ev: Evaluator) -> None:
        """Attribute access reaches mapping keys, and a datetime is not a mapping. That is the
        F2 rule holding rather than an oversight; a host that wants `.year` registers the type."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('parse_iso("2026-08-24").year', {})
        assert "attribute access works on mappings" in str(caught.value)

        opted_in = Evaluator(
            registry=standard_registry(), attribute_types={datetime: frozenset({"year"})}
        )
        assert opted_in.evaluate('parse_iso("2026-08-24").year', {}) == 2026


class TestFormatDate:
    def test_it_formats(self, ev: Evaluator) -> None:
        context = {"d": datetime(2026, 8, 24, 13, 45, 5)}
        assert ev.evaluate('format_date(d, "%Y-%m-%d")', context) == "2026-08-24"
        assert ev.evaluate('format_date(d, "%H:%M:%S")', context) == "13:45:05"

    def test_a_literal_percent(self, ev: Evaluator) -> None:
        assert ev.evaluate('format_date(d, "100%%")', {"d": datetime(2026, 1, 1)}) == "100%"

    def test_a_plain_date_works_too(self, ev: Evaluator) -> None:
        assert ev.evaluate('format_date(d, "%Y")', {"d": date(2026, 8, 24)}) == "2026"

    def test_it_round_trips_with_parse_iso(self, ev: Evaluator) -> None:
        source = 'parse_iso(x) | format_date("%Y-%m-%dT%H:%M:%S")'
        assert ev.evaluate(source, {"x": "2026-08-24T13:45:05"}) == "2026-08-24T13:45:05"

    @pytest.mark.parametrize("directive", ["c", "x", "X", "s", "e", "Q", "1", "-"])
    def test_a_directive_outside_the_portable_set_is_refused(
        self, ev: Evaluator, directive: str
    ) -> None:
        """**Output must not depend on which C library built the interpreter.** `%-d`, `%e` and
        `%s` exist on some platforms and not others, and `%c` and `%x` are locale-defined in
        their entirety. A package that matrices across four interpreters cannot have its answers
        vary with libc."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(f'format_date(d, "%{directive}")', {"d": datetime(2026, 1, 1)})
        assert "is not a date field" in str(caught.value)

    def test_the_refusal_lists_what_is_supported(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('format_date(d, "%Q")', {"d": datetime(2026, 1, 1)})
        assert "%Y" in str(caught.value)

    def test_a_trailing_percent_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('format_date(d, "a %")', {"d": datetime(2026, 1, 1)})
        assert "names no field" in str(caught.value)

    @pytest.mark.parametrize("directive", sorted(_DIRECTIVES))
    def test_every_allowed_directive_actually_works(self, ev: Evaluator, directive: str) -> None:
        """The allowlist and `strftime` must agree. A directive listed here but unsupported by
        the interpreter would be advertised and then fail."""
        result = ev.evaluate(
            f'format_date(d, "%{directive}")', {"d": datetime(2026, 8, 24, 13, 45, 5)}
        )
        assert isinstance(result, str)

    def test_a_long_format_is_refused_before_it_is_expanded(self, ev: Evaluator) -> None:
        """`format_date(d, "%Y" * 100000)` is a short expression asking for a large string, and
        the step budget counts nodes rather than bytes."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("format_date(d, f)", {"d": datetime(2026, 1, 1), "f": "%Y" * 10_000})
        assert "over the limit" in str(caught.value)
        assert str(_MAX_FORMAT_LENGTH) in str(caught.value).replace(",", "")

    @pytest.mark.parametrize("value", ["2026-01-01", 1, None, ["x"]])
    def test_something_that_is_not_a_date_is_refused_with_advice(
        self, ev: Evaluator, value: Any
    ) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('format_date(x, "%Y")', {"x": value})
        assert "parse_iso" in str(caught.value)

    def test_a_format_that_is_not_text_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("format_date(d, 1)", {"d": datetime(2026, 1, 1)})
        assert "needs a format as text" in str(caught.value)


class TestNothingInterpretsATemplate:
    """The card's own acceptance criterion, from the outside as well as from the source."""

    def test_a_brace_template_is_text_and_nothing_else(self, ev: Evaluator) -> None:
        """If `format_date` reached `str.format`, this would perform an attribute lookup. It
        comes back as the literal characters instead."""
        result = ev.evaluate('format_date(d, "{0.__class__} %Y")', {"d": datetime(2026, 8, 24)})
        assert result == "{0.__class__} 2026"

    def test_a_percent_mapping_key_is_refused_as_an_unknown_directive(self, ev: Evaluator) -> None:
        """`"%(__class__)s"` is the F1 shape for `%`-formatting. Here `%(` is simply not a date
        field."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate('format_date(d, "%(__class__)s")', {"d": datetime(2026, 8, 24)})
        assert "is not a date field" in str(caught.value)


class TestPropertiesOverGeneratedTimestamps:
    TIMESTAMPS = st.datetimes(min_value=datetime(1900, 1, 1), max_value=datetime(2200, 1, 1))

    @given(moment=TIMESTAMPS)
    @settings(max_examples=200, deadline=None)
    def test_formatting_then_parsing_returns_the_same_moment(self, moment: datetime) -> None:
        formatted = EV.evaluate(
            'format_date(d, "%Y-%m-%dT%H:%M:%S.%f")', {"d": moment.replace(tzinfo=None)}
        )
        assert EV.evaluate("parse_iso(x)", {"x": formatted}) == moment.replace(tzinfo=None)

    @given(moment=TIMESTAMPS)
    @settings(max_examples=200, deadline=None)
    def test_format_date_agrees_with_strftime(self, moment: datetime) -> None:
        layout = "%Y-%m-%d %H:%M:%S"
        assert EV.evaluate("format_date(d, f)", {"d": moment, "f": layout}) == moment.strftime(
            layout
        )

    @given(moment=TIMESTAMPS)
    @settings(max_examples=200, deadline=None)
    def test_parse_iso_agrees_with_fromisoformat(self, moment: datetime) -> None:
        text = moment.isoformat()
        assert EV.evaluate("parse_iso(x)", {"x": text}) == datetime.fromisoformat(text)

    @given(moment=TIMESTAMPS, other=TIMESTAMPS)
    @settings(max_examples=200, deadline=None)
    def test_parsed_timestamps_order_the_way_the_timestamps_do(
        self, moment: datetime, other: datetime
    ) -> None:
        context = {"a": moment.isoformat(), "b": other.isoformat()}
        assert EV.evaluate("parse_iso(a) < parse_iso(b)", context) is (moment < other)


class TestRegressions:
    def test_regression_dates_comparing_naive_with_aware_is_an_ordinary_error(self) -> None:
        """Python refuses to compare a naive timestamp with an aware one, and that arrives as a
        `TypeError` from inside a comparison. It must surface as a plain evaluation error rather
        than as an internal one."""
        context = {"a": "2026-08-24T00:00:00", "b": "2026-08-24T00:00:00+00:00"}
        with pytest.raises(EvaluationError) as caught:
            EV.evaluate("parse_iso(a) < parse_iso(b)", context)
        assert "bug in safeexpr" not in str(caught.value)
        assert "cannot compare" in str(caught.value)

    def test_regression_dates_an_aware_pair_still_compares(self) -> None:
        context = {
            "a": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "b": datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
        }
        assert EV.evaluate("parse_iso(a) < parse_iso(b)", context) is True
