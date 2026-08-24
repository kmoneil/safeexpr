r"""The URL tier: the three parts of a URL a rule asks about.

All three go through `urllib.parse`, which is stdlib and does no interpretation of its own: it
splits text at delimiters. There is no format string, no callback and nothing evaluated, so the
reflection gate is satisfied by the shape of the problem rather than by care.

Two decisions worth stating.

**`url_host` gives the hostname rather than the network location.** `urlsplit` offers both, and
`netloc` carries the port and any user information with it, so `url_host(u) == "example.com"`
would quietly be false for `https://user@example.com:8443/x`. A rule asking about a host means
the host, so the port and the credentials are dropped and the result is lower-cased, which is
what makes the comparison mean what it looks like.

**`url_query` gives one value per name.** Repeated names are real in URLs and rare in the config
rules this serves, and a mapping of name to a *list* would make the common case
`url_query(u).id == "7"` wrong in a way that is easy to miss. The first occurrence wins and the
rest are dropped, which is stated here and in the README rather than discovered.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import SplitResult, parse_qsl, urlsplit

from ._guards import text
from ._registry import Function, FunctionError

# How many query parameters one URL may carry. A cap rather than a limit anybody will meet: a
# query string with more than this is an attack or a bug, and `parse_qsl` is happy to build a
# list as long as the input allows.
_MAX_QUERY_FIELDS = 1024


def _split(value: Any) -> SplitResult:
    """Split a URL, turning `urllib`'s objections into ours.

    Args:
        value: The URL as text.

    Returns:
        The `SplitResult`.

    Raises:
        FunctionError: If it is not text, or not splittable.
    """
    subject = text(value, "a URL as text")
    try:
        parts = urlsplit(subject)
    except ValueError:
        # `urlsplit` raises on a malformed IPv6 literal, among others. The URL is not repeated
        # back into the message: it is the host's data.
        failure = FunctionError("needs text that reads as a URL")
    else:
        return parts
    raise failure


def _url_host(value: Any) -> str | None:
    """The host a URL points at, lower-cased, with no port and no credentials.

    `None` when the URL names no host at all, which is the tier's convention for "no answer"
    rather than an empty string that would compare equal to a genuinely empty host.
    """
    # **Split and read the host under one guard**, rather than splitting first and reading
    # after. On every supported interpreter `urlsplit` validates eagerly and `.hostname` cannot
    # then fail, measured on 3.11 through 3.14, but that is an implementation detail of CPython's
    # URL parsing rather than a promise it makes, and that parsing has moved before. One `try`
    # covers both, so a future version that defers the check produces this tier's ordinary
    # message instead of an internal error, and there is no unreachable handler sitting here
    # claiming to be tested.
    subject = text(value, "a URL as text")
    try:
        return urlsplit(subject).hostname
    except ValueError:
        failure = FunctionError("needs text that reads as a URL")
    raise failure


def _url_path(value: Any) -> str:
    """The path part of a URL, empty when there is none."""
    return _split(value).path


def _url_query(value: Any) -> dict[str, str]:
    """The query parameters of a URL, as a mapping of name to its first value.

    Blank values are kept: `?debug` and `?debug=` both give `""`, which is a different statement
    from the name being absent, and a rule using `is_none` can tell them apart.
    """
    parts = _split(value)
    try:
        pairs = parse_qsl(
            parts.query,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=_MAX_QUERY_FIELDS,
        )
    except ValueError:
        failure = FunctionError(f"the URL carries more than {_MAX_QUERY_FIELDS:,} query parameters")
    else:
        found: dict[str, str] = {}
        for name, item in pairs:
            found.setdefault(name, item)
        return found
    raise failure


URLS: dict[str, Function] = {
    "url_host": Function("url_host", _url_host, arity=(1, 1), cost=2),
    "url_path": Function("url_path", _url_path, arity=(1, 1), cost=2),
    "url_query": Function("url_query", _url_query, arity=(1, 1), cost=2),
}
