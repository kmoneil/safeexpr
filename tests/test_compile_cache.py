"""Compile once, evaluate many: the per-call re-parse, and the correctness it can cost.

`evaluate()` used to parse, rewrite and validate on **every call** and throw the result away. For
the three flat shapes in the design's canonical five that was **91% of the wall clock**: a feature
flag's eleven steps cost 2.7 us and the call cost 33.1 us. All of the other 30 us depends only on
`(source, registry)`, and the registry is fixed at construction.

This is the same defect the design already fixed one level down. The predecessor re-parsed per hop
and per item; `LazyExpr` removed that, and `tests/test_lazy.py` pins it with a call count rather
than a benchmark. Per-**call** re-parsing was never looked at.

**The hard part is not the cache, it is the one decision that must not go into it.** The
shadowed-pipe refusal reads `context.keys()`, so it is a decision about the data and has to be
made per call, on a table computed from the tree at compile time. A cache that memoised the
decision instead of the table would make `flags | first` succeed or refuse according to which
context happened to arrive first, and every other test in this repository would still pass.

The tests here are ordered by what they would catch:

1. **Call counts**, because a benchmark drifts and gets raised and a count does not.
2. **The shadow decision**, per call, in both orders.
3. **Registry isolation**, which per-instance caching makes structural rather than argued.
4. **Refusals**, which must be re-raised identically and never stored as successes.
5. **The tree is now shared**, so nothing may write to it.
6. **The budget**, which must not be able to tell a warm cache from a cold one.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path
from typing import Any

import pytest

from safeexpr import (
    BudgetExceededError,
    Evaluator,
    ParseError,
    ReservedNameError,
    SafeExprError,
    SourceTooLongError,
    ValidationError,
    evaluate,
    standard_registry,
)
from safeexpr._eval import _SHARED, MAX_COMPILE_CACHE
from safeexpr._parse import MAX_SOURCE_BYTES
from safeexpr._validate import MAX_EXPRESSION_DEPTH

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "safeexpr"

FLAG = 'user.plan == "pro" and user.region in ["us", "eu"]'
FLAG_CONTEXT: dict[str, Any] = {"user": {"plan": "pro", "region": "eu"}}


@pytest.fixture(autouse=True)
def _cold_shared_evaluator() -> Any:
    """The module-level `evaluate` shares one evaluator, so its cache outlives a test.

    Cleared around every test here rather than inside the two that count parses, because a warm
    entry left behind by one test silently turns another's "parsed once" into "parsed never",
    which passes.
    """
    _SHARED._cache.clear()  # noqa: SLF001
    yield
    _SHARED._cache.clear()  # noqa: SLF001


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every source `ast.parse` is called on, and return the list."""
    calls: list[str] = []
    real = ast.parse

    def counting(source: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(source)
        return real(source, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting)
    return calls


def _calls(node: ast.AST, name: str) -> bool:
    """Whether `node` is a call to the bare name `name`."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _reads_cache(node: ast.AST) -> bool:
    """Whether `node` is `self._cache.get(...)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_cache"
    )


def _ev(**kwargs: Any) -> Evaluator:
    return Evaluator(registry=standard_registry(), **kwargs)


class TestItCompilesOncePerSource:
    """The guard that actually protects this.

    A benchmark drifts and its threshold gets raised. `ast.parse` running exactly once across two
    hundred evaluations does not drift, and it fails the moment somebody adds a path that bypasses
    the cache. This is `tests/test_lazy.py`'s assertion one level up.
    """

    def test_one_source_parses_once_however_many_evaluations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _counting(monkeypatch)
        evaluator = _ev()
        for _ in range(200):
            assert evaluator.evaluate(FLAG, FLAG_CONTEXT) is True
        assert calls == [FLAG], f"parsed {len(calls)} times for 200 evaluations"

    def test_the_module_level_evaluate_parses_once_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The convenience function is the one most readers try first**, and a fresh evaluator
        per call would leave it permanently cold: the cache lives on the instance, so it would be
        discarded on the way out of every call. That is the difference between 14x and 1x."""
        calls = _counting(monkeypatch)
        for _ in range(200):
            assert evaluate(FLAG, FLAG_CONTEXT) is True
        assert calls == [FLAG], f"parsed {len(calls)} times for 200 evaluations"

    def test_it_is_once_per_distinct_source_not_once_per_evaluator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _counting(monkeypatch)
        evaluator = _ev()
        for _ in range(50):
            evaluator.evaluate("1 + 1", {})
            evaluator.evaluate("2 + 2", {})
        assert sorted(calls) == ["1 + 1", "2 + 2"]

    def test_more_sources_than_the_bound_parse_every_time_they_are_evicted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A working set larger than the bound is not a bug, it is the bound doing its job.

        Asserted as a floor rather than an equality: the policy drops the whole cache when it
        fills, so a workload cycling through more sources than it holds parses at least once per
        source and there is no promise about how many more.
        """
        calls = _counting(monkeypatch)
        evaluator = _ev()
        sources = [f"{n} + 1" for n in range(MAX_COMPILE_CACHE + 72)]
        for source in sources:
            evaluator.evaluate(source, {})
        assert len(calls) == len(sources)
        assert set(calls) == set(sources)

    def test_a_second_evaluator_compiles_for_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The cache is per instance, so two evaluators parse the same source once each."""
        calls = _counting(monkeypatch)
        first, second = _ev(), _ev()
        for _ in range(20):
            first.evaluate("1 + 1", {})
            second.evaluate("1 + 1", {})
        assert calls == ["1 + 1", "1 + 1"]


class TestTheShadowDecisionStaysPerCall:
    """The defect this change is most likely to be built with.

    `_refuse_shadowed_pipes` reads `context.keys()`. Everything it needs from the *tree* is
    computed once, at compile time, and kept; the decision itself is not, and cannot be.
    """

    SOURCE = "flags | first"

    def test_regression_cache_a_shadowed_pipe_is_decided_per_call_not_per_entry(self) -> None:
        """One source, two contexts, **both orders**.

        A cache that stored the decision passes one of these two orders and fails the other, which
        is exactly the kind of defect that looks like flakiness in production.
        """
        clean: dict[str, Any] = {"flags": [1, 2, 3]}
        shadowing: dict[str, Any] = {"flags": [1, 2, 3], "first": 9}

        clean_first = _ev()
        assert clean_first.evaluate(self.SOURCE, clean) == 1
        with pytest.raises(ReservedNameError):
            clean_first.evaluate(self.SOURCE, shadowing)

        shadowing_first = _ev()
        with pytest.raises(ReservedNameError):
            shadowing_first.evaluate(self.SOURCE, shadowing)
        assert shadowing_first.evaluate(self.SOURCE, clean) == 1

    def test_regression_cache_it_alternates_indefinitely_on_one_entry(self) -> None:
        """Ten alternations, so a decision cached on the second call is caught as well as one
        cached on the first."""
        evaluator = _ev()
        clean: dict[str, Any] = {"flags": [1, 2, 3]}
        shadowing: dict[str, Any] = {"flags": [1, 2, 3], "first": 9}
        for _ in range(10):
            assert evaluator.evaluate(self.SOURCE, clean) == 1
            with pytest.raises(ReservedNameError):
                evaluator.evaluate(self.SOURCE, shadowing)

    def test_regression_cache_the_shadow_position_survives_precomputation(self) -> None:
        """`x | first | last` must still report the earliest colliding name, not the first the
        walk happened to reach.

        Both `|` operators start at column zero, so the position has to come from the right-hand
        *name*. A precomputed table is exactly what gets this wrong: it is built once, in whatever
        order the walk produced, and the sort has to survive into it.
        """
        evaluator = _ev()
        source = "x | first | last"
        both: dict[str, Any] = {"x": [1], "first": 2, "last": 3}
        with pytest.raises(ReservedNameError) as caught:
            evaluator.evaluate(source, both)
        assert (caught.value.lineno, caught.value.offset) == (1, 5)
        assert "`first`" in str(caught.value)

        # The later name, reported only when the earlier one does not collide. Same cache entry.
        with pytest.raises(ReservedNameError) as later:
            evaluator.evaluate(source, {"x": [1], "last": 3})
        assert (later.value.lineno, later.value.offset) == (1, 13)
        assert "`last`" in str(later.value)

    def test_regression_cache_a_realistic_threshold_context_is_still_not_refused(self) -> None:
        """The case the refusal was deliberately narrowed for.

        `min` is a registry name and a perfectly good field name, and a bare name reads the
        context, so this is unambiguous and must keep working. A precompute that widened the check
        to "any registry name anywhere" would break it, and it is the shape a real rule has.
        """
        evaluator = _ev()
        context: dict[str, Any] = {
            "metrics": [{"value": 5}, {"value": 15}],
            "min": 10,
        }
        for _ in range(3):
            assert evaluator.evaluate("metrics | where(_.value > min)", context) == [{"value": 15}]

    def test_regression_cache_an_empty_context_still_reaches_the_same_entry(self) -> None:
        """The early return for an empty context must not be the thing that fills the cache."""
        evaluator = _ev()
        assert evaluator.evaluate("[1, 2] | first", {}) == 1
        with pytest.raises(ReservedNameError):
            evaluator.evaluate("[1, 2] | first", {"first": 9})


class TestRegistryIsolation:
    """Per-instance caching makes this structural: two evaluators do not share a dict."""

    def test_regression_cache_two_evaluators_with_different_registries_do_not_share(self) -> None:
        """`flags | first` is **two different expressions** depending on the registry.

        With `first` registered it is `first(flags)`. Without it, `|` is bitwise or and the whole
        thing is an integer. A source-keyed cache shared between evaluators would answer one with
        the other's grammar, and which one won would depend on call order.
        """
        with_registry, without = _ev(), Evaluator()
        context: dict[str, Any] = {"flags": [1, 2, 3]}
        assert with_registry.evaluate("flags | first", context) == 1
        with pytest.raises(SafeExprError):
            without.evaluate("flags | first", context)

        # And the other way round, so neither order is the one that happens to work.
        bits: dict[str, Any] = {"flags": 5, "first": 2}
        assert Evaluator().evaluate("flags | first", bits) == 7
        with pytest.raises(ReservedNameError):
            _ev().evaluate("flags | first", bits)

    def test_regression_cache_a_custom_registry_is_not_served_a_standard_entry(self) -> None:
        custom = Evaluator(registry={"first": lambda items: items[-1]})
        standard = _ev()
        context: dict[str, Any] = {"xs": [1, 2, 3]}
        assert standard.evaluate("xs | first", context) == 1
        assert custom.evaluate("xs | first", context) == 3
        assert standard.evaluate("xs | first", context) == 1

    def test_regression_cache_a_budget_is_not_shared_either(self) -> None:
        """Budgets live on the instance beside the cache, and one evaluator's entry must not
        arrive carrying another's allowance."""
        generous, mean = _ev(), _ev(budget=8)
        source, context = "sum(map(items, _ + 1))", {"items": list(range(50))}
        assert generous.evaluate(source, context) == sum(range(1, 51))
        with pytest.raises(BudgetExceededError):
            mean.evaluate(source, context)
        assert generous.evaluate(source, context) == sum(range(1, 51))


class TestRefusalsAreNeverCachedAsSuccesses:
    """Nothing is stored until the whole compile has returned, so every refusal is recomputed."""

    def test_regression_cache_a_rejected_source_is_rejected_every_time(self) -> None:
        evaluator = _ev()
        seen = []
        for _ in range(3):
            with pytest.raises(ValidationError) as caught:
                evaluator.evaluate("lambda x: x", {})
            seen.append((str(caught.value), caught.value.lineno, caught.value.offset))
        assert len(set(seen)) == 1, seen
        assert evaluator._cache == {}  # noqa: SLF001

    def test_regression_cache_a_parse_failure_is_raised_every_time(self) -> None:
        evaluator = _ev()
        seen = []
        for _ in range(3):
            with pytest.raises(ParseError) as caught:
                evaluator.evaluate("1 +", {})
            seen.append((str(caught.value), caught.value.lineno, caught.value.offset))
        assert len(set(seen)) == 1, seen
        assert evaluator._cache == {}  # noqa: SLF001

    def test_regression_cache_an_over_deep_expression_is_refused_every_time(self) -> None:
        evaluator = _ev()
        source = "-" * (MAX_EXPRESSION_DEPTH + 10) + "1"
        for _ in range(3):
            with pytest.raises(ValidationError, match="levels deep"):
                evaluator.evaluate(source, {})
        assert evaluator._cache == {}  # noqa: SLF001

    def test_regression_cache_a_source_too_long_is_refused_before_the_cache_is_touched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The cap has to run before the lookup, not only before the parser.**

        Otherwise the cache is the first thing to handle hostile input: a multi-megabyte string
        gets hashed in full before anything decides it is too long.
        """
        calls = _counting(monkeypatch)
        evaluator = _ev()
        with pytest.raises(SourceTooLongError):
            evaluator.evaluate("1 + " * MAX_SOURCE_BYTES + "1", {})
        assert calls == [], "the parser saw a source the cap should have stopped"
        assert evaluator._cache == {}  # noqa: SLF001

    def test_the_source_cap_runs_before_the_cache_lookup(self) -> None:
        """Read off the source, because the harm is invisible from outside.

        Moving the lookup above the cap does not change any answer: the parser is still protected,
        because the compile happens after both. What changes is that a caller-supplied string of
        any length gets hashed before anything has decided it is too long, and there is no
        observable difference to assert on. So the order is asserted where it is written.
        """
        module = ast.parse((SRC_DIR / "_eval.py").read_text(encoding="utf-8"))
        # The method, not the module-level convenience function of the same name. `ast.walk` is
        # breadth-first, so a plain search for "evaluate" finds the wrong one: the module-level
        # def is a child of the module and the method is a grandchild.
        evaluator = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "Evaluator"
        )
        body = next(
            node
            for node in evaluator.body
            if isinstance(node, ast.FunctionDef) and node.name == "evaluate"
        )
        # Sorted by line, because `ast.walk` yields breadth-first and says nothing about the
        # order the lines were written in, which is the whole property here.
        found = [
            (node.lineno, "check_source" if _calls(node, "check_source") else "lookup")
            for node in ast.walk(body)
            if _calls(node, "check_source") or _reads_cache(node)
        ]
        order = [kind for _, kind in sorted(found)]
        assert order[:2] == ["check_source", "lookup"], (
            f"`evaluate` does {order[:2]}. The cap has to run before the cache lookup, not only "
            f"before the parser, or a multi-megabyte string is hashed in full before anything "
            f"decides it is too long."
        )

    def test_regression_cache_an_unhashable_source_is_our_error_not_a_typeerror(self) -> None:
        """A dict lookup on an unhashable key raises `TypeError`, which is not one of ours.

        The type check is part of the same guard as the length cap and runs in the same place, so
        this is the other half of the test above rather than a separate concern.
        """
        evaluator = _ev()
        for wrong in ([1, 2], {"a": 1}, b"1 + 1"):
            with pytest.raises(ParseError, match="must be str"):
                evaluator.evaluate(wrong, {})  # type: ignore[arg-type]

    def test_regression_cache_a_source_that_fails_late_does_not_evict_good_entries(self) -> None:
        """Compiling into a full cache clears it, so a failure must not do so on the way out."""
        evaluator = _ev()
        evaluator.evaluate("1 + 1", {})
        with pytest.raises(SafeExprError):
            evaluator.evaluate("lambda x: x", {})
        assert "1 + 1" in evaluator._cache  # noqa: SLF001


class TestTheTreeIsSharedSoNothingWritesToIt:
    """Before this change every call got its own tree, so a write to one could not be observed.

    Now one tree serves every evaluation of a source. `_eval.py` contains no node assignment and no
    `setattr`, so this pins a property that already held rather than one being introduced.
    """

    def test_regression_cache_evaluation_does_not_mutate_the_cached_tree(self) -> None:
        evaluator = _ev()
        source = "rows | where(_.n > cut) | map(_.n) | sum"
        contexts: list[dict[str, Any]] = [
            {"rows": [{"n": 1}, {"n": 5}], "cut": 0},
            {"rows": [{"n": 9}], "cut": 100},
            {"rows": [], "cut": 3},
        ]
        evaluator.evaluate(source, contexts[0])
        tree, _targets = evaluator._cache[source]  # noqa: SLF001
        before = ast.dump(tree, include_attributes=True)
        for index in range(200):
            evaluator.evaluate(source, contexts[index % len(contexts)])
        assert ast.dump(tree, include_attributes=True) == before

    def test_regression_cache_the_validated_tree_is_still_the_evaluated_tree(self) -> None:
        """F8, restated for a tree validated once and evaluated many times.

        The window this closes is wider than it was: before, a swap could only happen between two
        calls in one `evaluate`. Now the object lives between calls, so "the thing that was
        validated" and "the thing being run" are separated by everything the host does in between.
        """
        evaluator = _ev()
        source = "rows | where(_.n > 0)"
        context: dict[str, Any] = {"rows": [{"n": 1}]}
        evaluator.evaluate(source, context)
        first, _ = evaluator._cache[source]  # noqa: SLF001
        for _ in range(20):
            evaluator.evaluate(source, context)
            again, _ = evaluator._cache[source]  # noqa: SLF001
            assert again is first

    def test_regression_cache_two_contexts_against_one_entry_do_not_bleed(self) -> None:
        """Including the `_` scope stack, which lives in `_Run` and must stay there."""
        evaluator = _ev()
        source = "map(outer, sum(map(inner, _ + _2)))"
        for base in range(10):
            result = evaluator.evaluate(source, {"outer": [base], "inner": [1, 2, 3]})
            assert result == [sum(item + base for item in (1, 2, 3))]

    def test_regression_cache_an_error_in_one_call_does_not_change_the_next(self) -> None:
        evaluator = _ev()
        source = "rows | map(_.n)"
        good: dict[str, Any] = {"rows": [{"n": 1}, {"n": 2}]}
        bad: dict[str, Any] = {"rows": [{"n": 1}, {"other": 2}]}
        for _ in range(5):
            assert evaluator.evaluate(source, good) == [1, 2]
            with pytest.raises(SafeExprError, match="no field `n`"):
                evaluator.evaluate(source, bad)
            assert evaluator.evaluate(source, good) == [1, 2]


class TestTheCacheIsNotObservableFromInsideAnExpression:
    """The Q10 argument, proved the way the pattern cache's was rather than asserted.

    Compiling is not charged to the budget on a miss, so it cannot be charged less on a hit. The
    language has no clock, so wall time is the only thing that differs and nothing inside an
    expression can see it.
    """

    @staticmethod
    def _steps(source: str, context: dict[str, Any], *, warm: bool) -> int:
        """The smallest budget that still evaluates, found by bisection.

        **The budget is fixed at construction and so is the cache's owner**, which makes "the same
        evaluation on a warm cache" awkward to arrange: a probe cannot be warmed by evaluating,
        because the budget being probed is the thing that might refuse. So each probe is built at
        the budget under test and, for the warm arm, has the entry a donor already compiled put
        into it directly. That is exactly what a warm cache is, and it is the only way the two arms
        differ.
        """
        entry = None
        if warm:
            donor = Evaluator(registry=standard_registry())
            donor.evaluate(source, context)
            entry = donor._cache[source]  # noqa: SLF001

        low, high = 1, 100_000
        while low < high:
            middle = (low + high) // 2
            probe = Evaluator(registry=standard_registry(), budget=middle)
            if entry is not None:
                probe._cache[source] = entry  # noqa: SLF001
            try:
                probe.evaluate(source, context)
            except BudgetExceededError:
                low = middle + 1
            else:
                high = middle
        return low

    @pytest.mark.parametrize(
        ("source", "context"),
        [
            (FLAG, FLAG_CONTEXT),
            ("m | where(_.v > 2) | len", {"m": [{"v": n} for n in range(50)]}),
        ],
        ids=["flat", "collection"],
    )
    def test_a_cold_cache_and_a_warm_one_cost_the_same(
        self, source: str, context: dict[str, Any]
    ) -> None:
        cold = self._steps(source, context, warm=False)
        assert self._steps(source, context, warm=True) == cold

    def test_regression_cache_compilation_is_not_charged_to_the_budget(self) -> None:
        """A budget of twelve evaluates the eleven-step flag on a cold cache as well as a warm one.

        If any part of compiling were charged, the cold call would refuse and the warm one would
        succeed, which is the budget observing the cache.
        """
        evaluator = Evaluator(budget=12)
        assert evaluator.evaluate(FLAG, FLAG_CONTEXT) is True
        assert evaluator.evaluate(FLAG, FLAG_CONTEXT) is True

    def test_the_budget_is_still_spent_on_a_warm_entry(self) -> None:
        """The other direction: a cache hit must not make an over-budget expression affordable."""
        evaluator = Evaluator(registry=standard_registry(), budget=40)
        source, context = "sum(map(items, _ + 1))", {"items": list(range(100))}
        for _ in range(3):
            with pytest.raises(BudgetExceededError):
                evaluator.evaluate(source, context)


class TestTheCacheStaysBounded:
    def test_the_compile_cache_stays_bounded(self) -> None:
        """Mirrors `test_the_cache_stays_bounded` for the pattern cache, on the same policy."""
        evaluator = _ev()
        for index in itertools.islice(itertools.count(), MAX_COMPILE_CACHE + 50):
            evaluator.evaluate(f"{index} + 1", {})
            assert len(evaluator._cache) <= MAX_COMPILE_CACHE  # noqa: SLF001

    def test_the_cache_starts_empty_and_holds_only_what_was_evaluated(self) -> None:
        evaluator = _ev()
        assert evaluator._cache == {}  # noqa: SLF001
        evaluator.evaluate("1 + 1", {})
        assert list(evaluator._cache) == ["1 + 1"]  # noqa: SLF001
