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
    matches_each         1,381 us     gate and compile once, then search per row
    matches_context_pattern
                         1,416 us     same, with the pattern arriving as a value

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

`matches` costs about what a string call costs, which is the pattern cache doing its job: the
gate parses and the engine compiles once, and every row after that is a search. Without the
cache it would be the dearest thing here by a wide margin.

**Measuring a small change on this box needs interleaving.** Running variant A eleven times and
then variant B eleven times lands the drift on whichever went second: measured that way, the
memory policy's size charge looked like +15% on an expression it does not touch. Alternating the
two within one process and taking medians gave +2.3%, -1.7% and +3.9% on the same three
expressions, which matches what the change actually does: nothing where no value is produced per
item, and about 4% where one is. Alternate before believing a number under 10%.

**How much of this table to believe.** Measured on the machine these were written on, two runs of
*identical code* differed by up to 12% on a single row, and a controlled comparison of one commit
against the next scattered from -15% to +36% with functions the change never touched moving most
in both directions. **That noise floor is above the project's 10% regression gate**, so on a box
like this the gate cannot certify a change on its own: read it alongside whether the change
plausibly touches the path at all, and re-run before believing a single red row. Treat the
numbers as an order of magnitude and a ranking, and compare like against like with
`--benchmark-compare` on an idle machine.
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
        ("matches_each", 'rows | map(matches(_.name, "^.n.c\\\\w+"))'),
        ("matches_context_pattern", "rows | map(matches(_.name, p))"),
    ],
    ids=lambda value: value if isinstance(value, str) and " " not in value else "",
)
def test_scalar_tier_hot_path(benchmark: Any, ev: Evaluator, name: str, source: str) -> None:
    rows = ROWS if name != "default_each" else [{**row, "missing_field": None} for row in ROWS]
    benchmark.group = name
    context = {"rows": rows, "p": "^.n.c\\w+"}
    result = benchmark(ev.evaluate, source, context)
    assert result is not None
