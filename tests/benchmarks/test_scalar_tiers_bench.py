"""Timings for the types, strings, dates and URL tiers.

These are hot for the same reason the collections tier is: a scalar function on its own is
trivial, and a scalar function inside a `map` runs once per row. `lower` over a thousand names is
the shape a real rule has, so it is the shape measured here.

Measured over the thousand rows below, mean per evaluation, on the machine the tiers were
written on:

    split_join             659 us     one call over the whole collection, not one per row
    lower_each           1,041 us
    parse_iso_each       1,100 us
    int_each             1,110 us
    default_each         1,129 us
    starts_with_each     1,198 us
    replace_each         1,479 us     counts occurrences before allocating
    slugify_each         2,455 us     normalise, then filter, per character
    format_date_each     3,013 us     directive scan plus strftime
    url_host_each        3,382 us
    url_query_each       3,977 us

Two of those are worth reading rather than skimming, and both were surprises.

**URL parsing is the dearest thing in the tiers**, more than three times a plain string call and
dearer than `slugify`. `urlsplit` normalises and validates the network location before it returns
anything, and `parse_qsl` allocates a list of pairs on top; a rule filtering a million rows on
`url_host` is doing real work per row, and it is worth hoisting out of the loop where a host can.

**`parse_iso` is as cheap as `lower`**, which is the opposite of what a name containing "parse"
suggests. `datetime.fromisoformat` is C and does no formatting work, so the cost is the call
rather than the parsing. `format_date` is three times dearer than reading a timestamp, because
the directive scan and `strftime` both walk the format.

Nothing here does hidden work per row, which is the property worth watching. A regression in this
table means a per-row function started allocating or re-deriving something it could have done
once.

These are absolute figures on one noisy box, where a mean can move 15% between runs. Treat them
as an order of magnitude and a ranking, and compare like against like with `--benchmark-compare`
on the same machine.
"""

from __future__ import annotations

from typing import Any

import pytest

from safeexpr import Evaluator, standard_registry

ROWS: list[dict[str, Any]] = [
    {
        "name": f"Ünïcödé Näme {n}",
        "count": str(n),
        "at": f"2026-08-{(n % 28) + 1:02d}T13:45:00",
        "url": f"https://sub{n % 7}.example.com:8443/a/b?id={n}&flag=1",
    }
    for n in range(1000)
]


@pytest.fixture(scope="module")
def ev() -> Evaluator:
    return Evaluator(registry=standard_registry())


@pytest.mark.benchmark
@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("lower_each", "rows | map(lower(_.name))"),
        ("starts_with_each", 'rows | map(starts_with(_.name, "Ü"))'),
        ("slugify_each", "rows | map(slugify(_.name))"),
        ("replace_each", 'rows | map(replace(_.name, "ä", "a"))'),
        ("split_join", 'rows | map(_.name) | join(",") | split(",") | len'),
        ("int_each", "rows | map(int(_.count))"),
        ("default_each", "rows | map(default(_.missing_field, 0))"),
        ("parse_iso_each", "rows | map(parse_iso(_.at))"),
        ("format_date_each", 'rows | map(format_date(parse_iso(_.at), "%Y-%m-%d"))'),
        ("url_host_each", "rows | map(url_host(_.url))"),
        ("url_query_each", "rows | map(url_query(_.url))"),
    ],
    ids=lambda value: value if isinstance(value, str) and " " not in value else "",
)
def test_scalar_tier_hot_path(benchmark: Any, ev: Evaluator, name: str, source: str) -> None:
    rows = ROWS if name != "default_each" else [{**row, "missing_field": None} for row in ROWS]
    benchmark.group = name
    result = benchmark(ev.evaluate, source, {"rows": rows})
    assert result is not None
