"""Lazy arguments: expressions passed to functions, evaluated once per item.

This is what makes `where(_.price > 10)` work without a lambda, and it is where the design's
original plan had a hole. The plan hoisted lazy arguments into a side table keyed by synthetic
names (`__lazy_0`), which the evaluator then resolved like any other name. Built and attacked,
that hands a user a live AST subtree just by naming one.

There is no table here. The function declares which of its positions are expressions and the
evaluator skips them, so there is no name to collide with and nothing to reach.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from typing import Any

import pytest

from safeexpr import EvaluationError, Evaluator, SafeExprError
from safeexpr._eval import LazyExpr, _Run
from safeexpr._registry import Function, as_function

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "safeexpr"


def _where(items: list[Any], lazy: LazyExpr) -> list[Any]:
    return [item for item in items if lazy.evaluate(item)]


def _map(items: list[Any], lazy: LazyExpr) -> list[Any]:
    return [lazy.evaluate(item) for item in items]


def _any(items: list[Any], lazy: LazyExpr) -> bool:
    return any(lazy.evaluate(item) for item in items)


def _escape(items: list[Any], lazy: LazyExpr) -> LazyExpr:
    """Deliberately antisocial: hands the LazyExpr back as a value."""
    return lazy


REGISTRY: dict[str, Function | Any] = {
    "where": Function("where", _where, frozenset({1})),
    "map": Function("map", _map, frozenset({1})),
    "any_": Function("any_", _any, frozenset({1})),
    "escape": Function("escape", _escape, frozenset({1})),
    "first": lambda items: items[0] if items else None,
    "len": len,
}

CUSTOMERS = [
    {"name": "acme", "threshold": 100, "orders": [{"total": 50}, {"total": 150}]},
    {"name": "globex", "threshold": 500, "orders": [{"total": 200}, {"total": 300}]},
]


@pytest.fixture
def ev() -> Evaluator:
    return Evaluator(registry=REGISTRY)


class TestLazyArgumentsWork:
    def test_a_predicate_runs_once_per_item(self, ev: Evaluator) -> None:
        assert ev.evaluate("where(items, _ > 1)", {"items": [1, 2, 3]}) == [2, 3]

    def test_field_access_inside_a_predicate(self, ev: Evaluator) -> None:
        items = [{"price": 5, "name": "a"}, {"price": 20, "name": "b"}]
        assert ev.evaluate("map(where(items, _.price > 10), _.name)", {"items": items}) == ["b"]

    def test_eager_arguments_are_still_evaluated(self, ev: Evaluator) -> None:
        """Only the declared positions are lazy; position 0 is an ordinary value."""
        assert ev.evaluate("where(items, _ > n)", {"items": [1, 5], "n": 2}) == [5]

    def test_the_context_is_visible_inside_a_predicate(self, ev: Evaluator) -> None:
        assert ev.evaluate("where(items, _ > threshold)", {"items": [1, 9], "threshold": 5}) == [9]

    def test_a_function_with_no_lazy_positions_is_unaffected(self, ev: Evaluator) -> None:
        assert ev.evaluate("len(items)", {"items": [1, 2, 3]}) == 3


class TestNestedScoping:
    """Q3, decided as innermost-wins plus outward access by index."""

    def test_innermost_binding_in_a_nested_predicate(self, ev: Evaluator) -> None:
        source = "map(where(cs, any_(_.orders, _.total > 100)), _.name)"
        assert ev.evaluate(source, {"cs": CUSTOMERS}) == ["acme", "globex"]

    def test_reaching_one_level_out(self, ev: Evaluator) -> None:
        """**The case innermost-only binding cannot express at all.**

        "orders above *this customer's* threshold" is an ordinary rules-engine expression. Under
        a single `_` it is unwriteable, and it fails with a confusing "no field" rather than a
        syntax error. `_2` is what makes it sayable.
        """
        source = "map(where(cs, any_(_.orders, _.total > _2.threshold)), _.name)"
        assert ev.evaluate(source, {"cs": CUSTOMERS}) == ["acme"]

    def test_underscore_one_is_a_synonym_for_underscore(self, ev: Evaluator) -> None:
        assert ev.evaluate("where(items, _1 > 1)", {"items": [1, 2]}) == [2]
        assert ev.evaluate("where(items, _ > 1)", {"items": [1, 2]}) == [2]

    def test_bare_underscore_outside_a_lazy_argument_is_refused(self, ev: Evaluator) -> None:
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("_ > 1", {})
        assert "only available inside" in str(caught.value)

    def test_reaching_past_the_nesting_depth_is_a_clear_error(self, ev: Evaluator) -> None:
        """Not a silent `None`, which is how this would fail if `_` were a context lookup."""
        with pytest.raises(EvaluationError) as caught:
            ev.evaluate("where(items, _3 > 1)", {"items": [1]})
        assert "reaches 3 levels out" in str(caught.value)

    def test_the_stack_unwinds_after_an_error_inside_an_item(self, ev: Evaluator) -> None:
        """An error part-way through a collection must not leave `_` bound one level too deep,
        which would silently change what a later `_` means."""
        with pytest.raises(SafeExprError):
            ev.evaluate("where(items, _.nope > 1)", {"items": [{"a": 1}]})
        assert ev.evaluate("where(items, _ > 1)", {"items": [1, 2]}) == [2]


class TestParseOnceEvaluateManyTimes:
    def test_a_ten_thousand_item_filter_parses_once(self, monkeypatch: Any) -> None:
        """R4. The predecessor re-quoted and re-parsed the predicate per item, which on a
        collection this size is the difference between usable and not."""
        calls: list[str] = []
        real = ast.parse

        def counting(source: str, *args: Any, **kwargs: Any) -> Any:
            calls.append(source)
            return real(source, *args, **kwargs)

        monkeypatch.setattr(ast, "parse", counting)
        evaluator = Evaluator(registry=REGISTRY)
        result = evaluator.evaluate("where(items, _ > 5000)", {"items": list(range(10_000))})

        assert len(result) == 4999
        assert len(calls) == 1, f"parsed {len(calls)} times for 10,000 items"

    def test_nothing_converts_a_tree_back_to_source(self) -> None:
        """D1: no expression is ever text again after the first parse.

        `ast.unparse` is how that promise gets broken, and it is a one-line change away at any
        time, so it is asserted rather than trusted.
        """
        offenders = []
        for path in SRC_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in {"unparse", "dump"}
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "ast"
                ):
                    offenders.append(f"{path.name}: ast.{node.attr}")
        assert not offenders, f"the tree is being serialised back to source: {offenders}"


class TestF8NoSideTableAndNothingToReach:
    """The hole the design's original plan had, and why this one does not."""

    def test_the_synthetic_name_that_used_to_leak_is_rejected(self, ev: Evaluator) -> None:
        """`__lazy_0` returned a live AST subtree in the prototype built from the design."""
        with pytest.raises(SafeExprError) as caught:
            ev.evaluate("where(items, _ > 1) and __lazy_0", {"items": [1, 2]})
        assert "underscore" in str(caught.value)

    @pytest.mark.parametrize("name", ["__lazy_0", "__lazy_1", "_lazy", "__lazy"])
    def test_no_spelling_of_the_old_synthetic_name_resolves(self, ev: Evaluator, name: str) -> None:
        with pytest.raises(SafeExprError):
            ev.evaluate(f"{name}", {"items": [1]})

    def test_there_is_no_side_table_to_name(self, ev: Evaluator) -> None:
        """The structural claim: even the run state has no name-to-subtree mapping.

        A test on the shape rather than on behaviour, because the behaviour tests above would
        keep passing if somebody reintroduced a table under a harder-to-guess prefix.

        **The set is exact on purpose**, so that adding anything to the run state is a decision
        somebody has to come here and make rather than something that happens quietly. `budget`
        and `steps` were added with the step budget and are both plain integers; neither maps a
        name to anything, which is the property this test exists to hold.
        """
        assert set(_Run.__slots__) == {"budget", "context", "items", "source", "steps"}

    def test_nothing_in_the_run_state_holds_a_tree(self, ev: Evaluator) -> None:
        """The intent behind the slot list, checked against values rather than names.

        A slot added later and named innocuously would pass the test above only after somebody
        updated it; this one fails on what the slot actually holds, which is the thing that
        matters.
        """
        run = _Run({"a": 1}, "a", 100)
        for slot in _Run.__slots__:
            value = getattr(run, slot)
            assert not isinstance(value, ast.AST), f"{slot} holds an AST node"
            if isinstance(value, dict):
                assert not any(isinstance(v, ast.AST) for v in value.values()), (
                    f"{slot} maps names to AST nodes, which is the side table this design removed"
                )

    def test_a_leaked_lazyexpr_is_inert(self, ev: Evaluator) -> None:
        """Defence in depth. A registry function *could* hand its LazyExpr back as a value; ours
        do not, but a third-party one might. Even then there is no way to reach the tree, because
        attribute access reads mapping keys and a LazyExpr is not a mapping.
        """
        leaked = ev.evaluate("escape(items, _ > 1)", {"items": [1]})
        assert isinstance(leaked, LazyExpr)
        for attempt in ("x._node", "x.node", 'x["_node"]', "x._evaluator"):
            with pytest.raises(SafeExprError):
                ev.evaluate(attempt, {"x": leaked})

    def test_the_lazyexpr_repr_says_nothing_about_the_tree(self, ev: Evaluator) -> None:
        """A repr that unparsed the subtree would put expression internals into any log that
        touched one."""
        leaked = ev.evaluate("escape(items, _.secret > 1)", {"items": []})
        assert repr(leaked) == "<LazyExpr>"
        assert "secret" not in repr(leaked)

    def test_the_subtree_is_the_validated_one(self, ev: Evaluator) -> None:
        """No copy, no rebuild: the node evaluated per item is the node that was validated, so
        there is no window between the check and the use."""
        source = "where(items, _ > 1)"
        tree = ast.parse(source, mode="eval")
        expected = ast.dump(tree.body.args[1])  # type: ignore[attr-defined]
        leaked = ev.evaluate("escape(items, _ > 1)", {"items": []})
        assert ast.dump(leaked._node) == expected  # noqa: SLF001


class TestTheRegistryEntry:
    def test_a_bare_callable_has_no_lazy_positions(self) -> None:
        function = as_function("len", len)
        assert function.lazy == frozenset()
        assert function.name == "len"

    def test_a_function_passes_through_unchanged(self) -> None:
        original = Function("where", _where, frozenset({1}))
        assert as_function("where", original) is original

    def test_negative_lazy_positions_are_refused(self) -> None:
        """A negative index would silently mean "from the end", which is not what a positional
        declaration means."""
        with pytest.raises(ValueError, match="zero-based"):
            Function("bad", _where, frozenset({-1}))

    def test_a_function_is_immutable(self) -> None:
        function = Function("where", _where, frozenset({1}))
        with pytest.raises(AttributeError):
            function.lazy = frozenset()  # type: ignore[misc]


class TestLazyEvaluationIsThreadSafe:
    def test_the_binding_stack_is_per_evaluation(self) -> None:
        """`_` lives on the run, not on the evaluator, so concurrent evaluations cannot see each
        other's items."""
        evaluator = Evaluator(registry=REGISTRY)
        results: list[Any] = []
        errors: list[BaseException] = []

        def work(n: int) -> None:
            try:
                for _ in range(40):
                    got = evaluator.evaluate(
                        "where(items, _ > n)", {"items": list(range(10)), "n": n}
                    )
                    results.append((n, tuple(got)))
            except BaseException as exc:  # pragma: no cover - only on a real failure
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert len(results) == 240
        for n, got in results:
            assert got == tuple(range(n + 1, 10))


class TestScopeStackProperties:
    """T6. The example tests above pin the cases anyone would think to write; these pin the rule.

    Nesting is built programmatically so the properties hold at depths nobody would type by hand,
    which is where an off-by-one in the stack indexing would hide.
    """

    @staticmethod
    def _nest(depth: int, body: str) -> str:
        """Build `where(v, where(v, ... body ...))` to a given nesting depth."""
        source = body
        for _ in range(depth):
            source = f"where(v, {source})"
        return source

    @pytest.mark.parametrize("depth", range(1, 9))
    def test_underscore_always_binds_the_innermost_item(self, ev: Evaluator, depth: int) -> None:
        """Whatever the depth, `_` is the item of the closest enclosing lazy argument."""
        source = self._nest(depth, "_ == 1")
        assert ev.evaluate(source, {"v": [1]}) == [1]

    @pytest.mark.parametrize("depth", range(1, 9))
    def test_every_index_in_range_resolves(self, ev: Evaluator, depth: int) -> None:
        """`_1` through `_depth` are all in scope, and none of them errors."""
        for index in range(1, depth + 1):
            source = self._nest(depth, f"_{index} == 1")
            assert ev.evaluate(source, {"v": [1]}) == [1], f"_{index} failed at depth {depth}"

    @pytest.mark.parametrize("depth", range(1, 9))
    def test_the_first_index_out_of_range_errors(self, ev: Evaluator, depth: int) -> None:
        """The boundary, checked at every depth rather than at one."""
        source = self._nest(depth, f"_{depth + 1} == 1")
        with pytest.raises(EvaluationError, match="levels out"):
            ev.evaluate(source, {"v": [1]})

    @pytest.mark.parametrize("depth", range(1, 7))
    def test_indices_count_outward_from_the_innermost(self, ev: Evaluator, depth: int) -> None:
        """The direction of the indexing, which is the part an off-by-one would invert.

        Each level filters a list holding its own depth number, so `_i` must equal the depth of
        the level `i` steps out from the innermost.
        """
        source = f"_{depth} == {depth}"
        # Innermost level sees [1], next out [2], and so on, so `_i` counting outward from the
        # innermost lands on i.
        for level in range(1, depth + 1):
            source = f"where(v{level}, {source})"
        context = {f"v{level}": [level] for level in range(1, depth + 1)}
        assert ev.evaluate(source, context) == [depth]

    def test_the_stack_is_empty_again_after_a_full_evaluation(self, ev: Evaluator) -> None:
        """Nothing may survive one evaluation into the next."""
        ev.evaluate(self._nest(4, "_4 == 1"), {"v": [1]})
        with pytest.raises(EvaluationError, match="only available inside"):
            ev.evaluate("_ == 1", {})
