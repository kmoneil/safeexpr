"""The URL tier.

`urllib.parse` splits text at delimiters and interprets nothing, so the reflection gate is
satisfied by the shape of the problem rather than by care. What is worth testing is the two
decisions this tier makes on top of it, because both change what a comparison means:

- `url_host` gives the hostname, not the network location, so the port and any credentials do
  not silently make `url_host(u) == "example.com"` false.
- `url_query` gives one value per name, so `url_query(u).id == "7"` is not comparing against a
  list.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from safeexpr import EvaluationError, Evaluator, standard_registry

EV = Evaluator(registry=standard_registry())


@pytest.fixture
def ev() -> Evaluator:
    return EV


class TestUrlHost:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://example.com/a/b", "example.com"),
            ("https://EXAMPLE.COM/a", "example.com"),
            ("https://example.com:8443/a", "example.com"),
            ("https://user:pw@example.com/a", "example.com"),
            ("https://user@example.com:8443/a", "example.com"),
            ("http://sub.example.co.uk", "sub.example.co.uk"),
        ],
    )
    def test_it_gives_the_host_alone(self, ev: Evaluator, url: str, expected: str) -> None:
        """**The port and the credentials are dropped and the case is folded**, which is what
        makes the obvious comparison mean what it looks like. `netloc` would carry all three."""
        assert ev.evaluate("url_host(u)", {"u": url}) == expected

    @pytest.mark.parametrize("url", ["/just/a/path", "", "mailto:a@b.c"])
    def test_a_url_with_no_host_gives_nothing(self, ev: Evaluator, url: str) -> None:
        """`None` rather than an empty string, so it does not compare equal to a genuinely empty
        host and `is_none` can tell them apart."""
        assert ev.evaluate("url_host(u)", {"u": url}) is None

    def test_it_reads_well_as_a_rule(self, ev: Evaluator) -> None:
        source = 'url_host(request.referer) == "example.com"'
        assert ev.evaluate(source, {"request": {"referer": "https://example.com:443/x"}}) is True


class TestUrlPath:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://example.com/a/b", "/a/b"),
            ("https://example.com", ""),
            ("https://example.com/?q=1", "/"),
            ("/just/a/path", "/just/a/path"),
            ("https://example.com/a%20b", "/a%20b"),
        ],
    )
    def test_it_gives_the_path(self, ev: Evaluator, url: str, expected: str) -> None:
        assert ev.evaluate("url_path(u)", {"u": url}) == expected

    def test_the_query_and_fragment_are_not_part_of_it(self, ev: Evaluator) -> None:
        assert ev.evaluate("url_path(u)", {"u": "https://e.com/a?b=1#c"}) == "/a"


class TestUrlQuery:
    def test_it_gives_a_mapping(self, ev: Evaluator) -> None:
        assert ev.evaluate("url_query(u)", {"u": "https://e.com/?a=1&b=2"}) == {"a": "1", "b": "2"}

    def test_a_field_reads_off_it(self, ev: Evaluator) -> None:
        assert ev.evaluate("url_query(u).id", {"u": "https://e.com/?id=7"}) == "7"

    def test_the_first_value_wins_when_a_name_repeats(self, ev: Evaluator) -> None:
        """Stated rather than discovered. Repeated names are real in URLs and rare in the rules
        this serves, and a mapping to a list would make the common comparison wrong."""
        assert ev.evaluate("url_query(u)", {"u": "https://e.com/?a=1&a=2"}) == {"a": "1"}

    @pytest.mark.parametrize("url", ["https://e.com/?debug", "https://e.com/?debug="])
    def test_a_blank_value_is_kept_and_is_not_absence(self, ev: Evaluator, url: str) -> None:
        """`?debug` present-and-empty is a different statement from `debug` being absent, and a
        rule using `is_none` can tell them apart."""
        assert ev.evaluate("url_query(u)", {"u": url}) == {"debug": ""}
        assert ev.evaluate("is_none(url_query(u).debug)", {"u": url}) is False

    def test_no_query_gives_an_empty_mapping(self, ev: Evaluator) -> None:
        assert ev.evaluate("url_query(u)", {"u": "https://e.com/a"}) == {}

    def test_percent_escapes_are_decoded(self, ev: Evaluator) -> None:
        assert ev.evaluate("url_query(u)", {"u": "https://e.com/?a=x%20y"}) == {"a": "x y"}

    def test_too_many_parameters_is_refused(self, ev: Evaluator) -> None:
        many = "https://e.com/?" + "&".join(f"k{n}=1" for n in range(2000))
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("url_query(u)", {"u": many})
        assert "query parameters" in str(caught.value)

    def test_an_underscore_name_is_present_in_the_data_and_unreachable_from_the_language(
        self, ev: Evaluator
    ) -> None:
        """The mapping is ordinary data, and the language's underscore rules still apply to every
        way of reading it."""
        context = {"u": "https://e.com/?__class__=x&_private=y&ok=z"}
        assert ev.evaluate("url_query(u)", context) == {
            "__class__": "x",
            "_private": "y",
            "ok": "z",
        }
        for attempt in ["url_query(u).__class__", 'url_query(u)["_private"]']:
            with pytest.raises(Exception, match="underscore"):
                ev.evaluate(attempt, context)


class TestNothingCoerces:
    @pytest.mark.parametrize("source", ["url_host(u)", "url_path(u)", "url_query(u)"])
    @pytest.mark.parametrize("value", [1, None, ["https://e.com"], {"a": 1}])
    def test_something_that_is_not_text_is_refused(
        self, ev: Evaluator, source: str, value: Any
    ) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"u": value})
        assert "needs a URL as text" in str(caught.value)

    @pytest.mark.parametrize("source", ["url_host(u)", "url_path(u)", "url_query(u)"])
    def test_a_url_urllib_refuses_is_an_ordinary_error(self, ev: Evaluator, source: str) -> None:
        """`urlsplit` raises on a malformed IPv6 literal. It must arrive as an evaluation error
        rather than as an internal one."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate(source, {"u": "http://[::1/x"})
        assert "bug in safeexpr" not in str(caught.value)
        assert "reads as a URL" in str(caught.value)

    def test_the_refusal_does_not_repeat_the_url_back(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("url_host(u)", {"u": "http://[sk-live-SECRET"})
        assert "sk-live" not in str(caught.value)


class TestDifferentialAgainstUrllib:
    """Against `urllib` itself, over generated URLs, so a disagreement means this tier drifted
    rather than that a chosen example was lucky."""

    URLS = st.builds(
        lambda scheme, host, port, path, query: f"{scheme}://{host}{port}{path}{query}",
        scheme=st.sampled_from(["http", "https"]),
        host=st.sampled_from(["example.com", "EXAMPLE.com", "sub.example.co.uk", "a.b"]),
        port=st.sampled_from(["", ":80", ":8443"]),
        path=st.sampled_from(["", "/", "/a", "/a/b", "/a%20b"]),
        query=st.sampled_from(["", "?", "?a=1", "?a=1&b=2", "?a=1&a=2", "?blank"]),
    )

    @given(url=URLS)
    @settings(max_examples=250, deadline=None)
    def test_host_matches_urlsplit_hostname(self, url: str) -> None:
        assert EV.evaluate("url_host(u)", {"u": url}) == urlsplit(url).hostname

    @given(url=URLS)
    @settings(max_examples=250, deadline=None)
    def test_path_matches_urlsplit_path(self, url: str) -> None:
        assert EV.evaluate("url_path(u)", {"u": url}) == urlsplit(url).path

    @given(url=URLS)
    @settings(max_examples=250, deadline=None)
    def test_query_names_match_urlsplit_and_every_value_is_text(self, url: str) -> None:
        found = EV.evaluate("url_query(u)", {"u": url})
        raw = urlsplit(url).query
        expected = {pair.split("=", 1)[0] for pair in raw.split("&") if pair}
        assert set(found) == expected
        assert all(isinstance(value, str) for value in found.values())
