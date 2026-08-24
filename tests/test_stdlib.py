"""The standard registry, and the five canonical use cases running against it.

This is the headline claim for the collections tier: the design lists five expressions a v1 has to
serve cleanly, and three of them worked before this tier existed. The other two are the reason the
tier exists.
"""

from __future__ import annotations

from typing import Any

import pytest

from safeexpr import Evaluator, standard_registry
from safeexpr._collections import COLLECTIONS
from safeexpr._dates import DATES
from safeexpr._regex import REGEX
from safeexpr._strings import STRINGS
from safeexpr._types import TYPES
from safeexpr._urls import URLS

CANONICAL: list[tuple[str, str, dict[str, Any], Any]] = [
    (
        "feature-flag targeting",
        'user.plan == "pro" and user.region in ["us", "eu"]',
        {"user": {"plan": "pro", "region": "eu"}},
        True,
    ),
    (
        "alerting rule",
        "metrics | where(_.value > threshold) | first",
        {"metrics": [{"value": 4}, {"value": 40}], "threshold": 10},
        {"value": 40},
    ),
    (
        "authorization policy",
        'resource.owner_id == principal.id or "admin" in principal.roles',
        {"resource": {"owner_id": 7}, "principal": {"id": 3, "roles": ["admin"]}},
        True,
    ),
    (
        "pipeline transform",
        'orders | where(_.status == "paid") | group_by(_.customer_id) '
        '| map(merge(_, {"n": len(_.items)}))',
        {
            "orders": [
                {"customer_id": "c1", "status": "paid", "items": [1, 2]},
                {"customer_id": "c2", "status": "open", "items": [3]},
                {"customer_id": "c1", "status": "paid", "items": [4]},
            ]
        },
        [
            {
                "key": "c1",
                "items": [
                    {"customer_id": "c1", "status": "paid", "items": [1, 2]},
                    {"customer_id": "c1", "status": "paid", "items": [4]},
                ],
                "n": 2,
            }
        ],
    ),
    (
        "workflow condition",
        'event.type == "deploy" and event.env != "prod"',
        {"event": {"type": "deploy", "env": "staging"}},
        True,
    ),
]


@pytest.fixture
def ev() -> Evaluator:
    return Evaluator(registry=standard_registry())


@pytest.mark.parametrize(
    ("source", "context", "expected"),
    [(source, context, expected) for _, source, context, expected in CANONICAL],
    ids=[name for name, *_ in CANONICAL],
)
def test_a_canonical_use_case_runs_end_to_end(
    ev: Evaluator, source: str, context: dict[str, Any], expected: Any
) -> None:
    assert ev.evaluate(source, context) == expected


class TestTheRegistryIsPerCaller:
    def test_each_call_returns_a_new_dictionary(self) -> None:
        assert standard_registry() is not standard_registry()

    def test_editing_one_does_not_reach_another(self) -> None:
        """A host dropping `merge` or shadowing `first` must not change what anyone else gets."""
        mine = standard_registry()
        del mine["merge"]
        mine["first"] = lambda items: "mine"
        assert "merge" in standard_registry()
        assert standard_registry()["first"].call is not mine["first"]

    def test_it_holds_every_tier(self) -> None:
        """**This assertion was `== set(COLLECTIONS)` and had to change**, because it was written
        when collections was the only tier and is now false by four tiers.

        Containment rather than equality, so it keeps saying what it was written to say, that a
        tier is actually wired in, without needing an edit every time one is added. The exact
        composition is asserted in `tests/test_tiers.py`, which checks the registry is precisely
        the union of the tiers and so would catch a name appearing from nowhere.
        """
        registry = set(standard_registry())
        for tier in (COLLECTIONS, TYPES, STRINGS, REGEX, DATES, URLS):
            assert set(tier) <= registry
        assert len(registry) == sum(
            len(tier) for tier in (COLLECTIONS, TYPES, STRINGS, REGEX, DATES, URLS)
        )


class TestFunctionsAreOptIn:
    """Registry membership is what tells the pipe transform that a `|` is a pipe, so every name
    added becomes reserved on the right of one. That cost belongs to a host who asked for the
    functions, not to one who only wanted `a == b`."""

    def test_a_bare_evaluator_still_has_no_data_functions(self) -> None:
        assert Evaluator().function_names == frozenset({"bitor"})

    def test_and_so_a_bare_pipe_is_still_bitwise_or(self) -> None:
        assert Evaluator().evaluate("a | b", {"a": 4, "b": 1}) == 5

    def test_while_an_opted_in_evaluator_reads_it_as_a_pipe(self, ev: Evaluator) -> None:
        assert ev.evaluate("a | first", {"a": [4, 1]}) == 4

    def test_a_host_can_still_shadow_a_standard_name(self) -> None:
        registry = standard_registry()
        registry["first"] = lambda items: "shadowed"
        assert Evaluator(registry=registry).evaluate("a | first", {"a": [4]}) == "shadowed"
