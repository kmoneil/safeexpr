"""The transform layer, as properties over generated trees.

T3 and T6. The pipe rewrite and the `_` scope stack are where two of the design's decisions live,
and their guarantees are exactly the kind example tests miss: idempotence, position preservation,
independence from data, and validating the same object that is then evaluated.

**The generator is richer than the one the pipe work shipped with**, and deliberately. That one
chained a handful of names with `|`, which exercises the rewrite but never puts a pipe inside a
lazy argument, never nests a call inside a call, and never mixes a rewrite with a subscript. The
properties below are only worth what the trees they run on are worth, so this generator produces
those shapes and a coverage test asserts it kept producing them.

**The last property is the one that cannot be written as a unit test.** "Validation and use see
the same tree" is about two calls in `evaluate` agreeing on an object; asserting it means watching
both. It is F8, the asteval GHSA-vp47-9734-prjw shape, where a tree is checked and then a
different tree is run.
"""

from __future__ import annotations

import ast
import contextlib
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import safeexpr._eval as evaluator_module
from safeexpr import Evaluator, SafeExprError, standard_registry
from safeexpr._parse import parse
from safeexpr._pipes import transform
from safeexpr._validate import validate

NAMES = frozenset(standard_registry())

CONTEXT: dict[str, Any] = {
    "rows": [{"k": 1, "g": "a"}, {"k": 2, "g": "b"}],
    "nums": [3, 1, 2],
    "words": ["a", "b"],
    "n": 2,
    "flags": 5,
    "other": 3,
    "rec": {"k": 1, "inner": {"k": 2}},
}

# Names that are *not* in the registry, so `|` before them stays bitwise or.
PLAIN = st.sampled_from(["flags", "other", "n"])
# Values worth piping into.
PIPEABLE = st.sampled_from(["rows", "nums", "words"])


@st.composite
def _lazy_body(draw: st.DrawFn, depth: int) -> str:
    """An expression for a lazy argument, which is where `_` and `_2` live."""
    outward = draw(st.integers(min_value=1, max_value=max(1, depth)))
    reference = "_" if outward == 1 else f"_{outward}"
    return draw(
        st.sampled_from(
            [
                f"{reference}.k",
                f"{reference}.k > 0",
                f"{reference}.g",
                f"{reference}",
                f"{reference}.k + 1",
                f"len({reference}.g)",
            ]
        )
    )


@st.composite
def _piped(draw: st.DrawFn, depth: int = 1) -> str:
    """A pipe chain, sometimes with another pipe inside one of its lazy arguments."""
    source = draw(PIPEABLE)
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        stage = draw(st.integers(min_value=0, max_value=5))
        if stage == 0:
            source = f"{source} | first"
        elif stage == 1:
            source = f"{source} | len"
        elif stage == 2:
            source = f"{source} | take({draw(st.integers(min_value=0, max_value=3))})"
        elif stage == 3:
            source = f"{source} | where({draw(_lazy_body(depth))})"
        elif stage == 4:
            source = f"{source} | map({draw(_lazy_body(depth))})"
        elif depth < 3:
            inner = draw(_piped(depth + 1))
            source = f"{source} | where(({inner}) == ({inner}))"
        else:
            source = f"{source} | sort_by({draw(_lazy_body(depth))})"
    return source


@st.composite
def _mixed(draw: st.DrawFn) -> str:
    """A pipe chain wrapped in something that is not a pipe, so the rewrite has to sit inside
    a subscript, a literal or an operator without disturbing it."""
    inner = draw(_piped())
    shape = draw(st.integers(min_value=0, max_value=5))
    if shape == 0:
        return f"({inner})"
    if shape == 1:
        return f"[{inner}, {draw(PLAIN)}]"
    if shape == 2:
        return f'{{"a": {inner}}}'
    if shape == 3:
        return f"({inner}) if {draw(PLAIN)} else {draw(PLAIN)}"
    if shape == 4:
        return f"({draw(PLAIN)} | {draw(PLAIN)}) == ({inner} | len)"
    return f"({inner})[0] if ({inner} | len) > 0 else {draw(PLAIN)}"


TREES = st.one_of(_piped(), _mixed())

# Expressions with no registry name anywhere, so nothing can be rewritten.
NO_PIPES = st.builds(
    lambda a, op, b: f"{a} {op} {b}",
    PLAIN,
    st.sampled_from(["|", "+", "*", "-", "==", "and"]),
    PLAIN,
)


def positions(tree: ast.AST) -> list[tuple[str, int, int]]:
    """Every positioned node, as (kind, line, column)."""
    return [
        (type(node).__name__, node.lineno, node.col_offset)
        for node in ast.walk(tree)
        if isinstance(node, ast.expr)
    ]


class TestTheRewriteIsWellBehaved:
    @given(source=TREES)
    @settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_it_is_idempotent(self, source: str) -> None:
        once = transform(parse(source), NAMES)
        twice = transform(once, NAMES)
        assert ast.dump(once) == ast.dump(twice)

    @given(source=TREES)
    @settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_every_position_still_points_inside_the_source(self, source: str) -> None:
        """R8 rests on this: an error after the rewrite must point at what the author wrote."""
        for kind, line, column in positions(transform(parse(source), NAMES)):
            assert line == 1, f"{kind} moved to line {line}"
            assert 0 <= column <= len(source), f"{kind} at column {column} of {len(source)}"

    @given(source=TREES)
    @settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_it_produces_a_tree_that_still_validates(self, source: str) -> None:
        """The rewrite can only build a call to a name already in the registry, out of subtrees
        that were already there, so it cannot launder anything past the allowlist."""
        validate(transform(parse(source), NAMES), source)

    @given(source=NO_PIPES)
    @settings(max_examples=200, deadline=None)
    def test_a_tree_with_nothing_to_rewrite_comes_back_byte_identical(self, source: str) -> None:
        """Attributes included, so a rewrite that reconstructed an equal node would still fail."""
        before = ast.dump(parse(source), include_attributes=True)
        after = ast.dump(transform(parse(source), NAMES), include_attributes=True)
        assert before == after

    @given(source=TREES)
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_the_rewrite_never_consults_the_context(self, source: str) -> None:
        """Q2's whole argument. Three contexts that disagree about every name must produce one
        tree, or an expression means different things on different data."""
        shapes = {
            ast.dump(transform(parse(source), NAMES), include_attributes=True) for _ in range(3)
        }
        assert len(shapes) == 1

    @given(source=TREES)
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_the_same_object_comes_back(self, source: str) -> None:
        """Returning a copy would open a window between the check and the use."""
        tree = parse(source)
        assert transform(tree, NAMES) is tree


class TestValidationAndUseSeeTheSameTree:
    """**F8, asserted at the level where it could actually go wrong.**

    `validate(tree) is tree` and `transform(tree) is tree` are each true on their own and neither
    says what this does: that the object `evaluate` handed to the validator is the object it then
    walked. That is the asteval GHSA-vp47-9734-prjw shape, a tree checked and a different tree
    run, and the only way to see it is to watch both calls.
    """

    @staticmethod
    def _watched(source: str) -> dict[str, Any]:
        """Evaluate `source`, recording the tree each stage was handed.

        Patched by hand rather than with `monkeypatch`, because hypothesis refuses a
        function-scoped fixture inside `@given`: the fixture is not reset between generated
        inputs, which for a patch is exactly the surprise it warns about.
        """
        seen: dict[str, Any] = {}
        real_validate = evaluator_module.validate
        real_run = Evaluator._run  # noqa: SLF001 - the property is about this call

        def watched_validate(tree: ast.Expression, text: str = "") -> ast.Expression:
            seen["validated"] = tree
            return real_validate(tree, text)

        def watched_run(self: Evaluator, tree: ast.Expression, run: Any) -> Any:
            seen["evaluated"] = tree
            return real_run(self, tree, run)

        evaluator_module.validate = watched_validate  # type: ignore[assignment]
        Evaluator._run = watched_run  # type: ignore[method-assign]  # noqa: SLF001
        try:
            with contextlib.suppress(SafeExprError):
                Evaluator(registry=standard_registry()).evaluate(source, CONTEXT)
        finally:
            evaluator_module.validate = real_validate  # type: ignore[assignment]
            Evaluator._run = real_run  # type: ignore[method-assign]  # noqa: SLF001
        return seen

    @given(source=TREES)
    @settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_the_validated_tree_is_the_evaluated_tree(self, source: str) -> None:
        seen = self._watched(source)
        assert "validated" in seen, "validation never ran"
        if "evaluated" in seen:
            assert seen["evaluated"] is seen["validated"], (
                "the tree that was validated is not the tree that was evaluated"
            )

    def test_the_watcher_sees_both_stages_on_an_expression_that_evaluates(self) -> None:
        """So the property above is not passing because the evaluation half never happens."""
        seen = self._watched("rows | len")
        assert set(seen) == {"validated", "evaluated"}

    def test_the_assertion_would_notice_a_swap(self) -> None:
        """Two equal trees are still two objects, so identity is the right comparison and
        equality would not have caught the failure mode this exists for."""
        first, second = parse("1 + 1"), parse("1 + 1")
        assert ast.dump(first) == ast.dump(second)
        assert first is not second


class TestScopingOverGeneratedNesting:
    """T6. `_` binds the innermost item and `_2` reaches one level out, at any depth."""

    @staticmethod
    def _nest(depth: int) -> tuple[str, dict[str, Any]]:
        """`map(a, map(a, ... _N ...))` at the given depth, reaching all the way out."""
        source = f"_{depth}" if depth > 1 else "_"
        for _ in range(depth):
            source = f"map(xs, {source})"
        return source, {"xs": [1]}

    @given(depth=st.integers(min_value=1, max_value=8))
    @settings(max_examples=40, deadline=None)
    def test_the_outermost_item_is_reachable_from_the_innermost_scope(self, depth: int) -> None:
        source, context = self._nest(depth)
        result = Evaluator(registry=standard_registry()).evaluate(source, context)
        for _ in range(depth):
            assert isinstance(result, list)
            result = result[0]
        assert result == 1

    @given(depth=st.integers(min_value=1, max_value=8))
    @settings(max_examples=40, deadline=None)
    def test_reaching_one_level_past_the_nesting_is_refused(self, depth: int) -> None:
        source = f"_{depth + 1}"
        for _ in range(depth):
            source = f"map(xs, {source})"
        with pytest.raises(SafeExprError, match="levels out"):
            Evaluator(registry=standard_registry()).evaluate(source, {"xs": [1]})

    @given(depth=st.integers(min_value=2, max_value=6))
    @settings(max_examples=40, deadline=None)
    def test_underscore_always_means_the_innermost_whatever_the_depth(self, depth: int) -> None:
        source = "_"
        for level in range(depth):
            source = f"map(xs{level}, {source})"
        context = {f"xs{level}": [level] for level in range(depth)}
        result = Evaluator(registry=standard_registry()).evaluate(source, context)
        for _ in range(depth - 1):
            result = result[0]
        assert result == [0], "`_` did not resolve to the innermost binding"


class TestTheGeneratorKeepsProducingTheShapes:
    """**The lesson the differential and audit work both taught, applied here.**

    Every property above is worth exactly what the trees it runs on are worth. A generator that
    drifted back to bare `a | first` would still satisfy idempotence, position preservation and
    context independence, and would stop testing anything the earlier, narrower generator did not
    already cover.
    """

    @staticmethod
    def _shapes(examples: int = 400) -> dict[str, int]:
        counts = {
            "rewrote a pipe": 0,
            "a lazy argument": 0,
            "reached outward with _2": 0,
            "a pipe inside a lazy argument": 0,
            "a rewrite inside a container or subscript": 0,
            "a bitwise or left alone": 0,
        }

        @given(source=TREES)
        @settings(
            max_examples=examples,
            deadline=None,
            derandomize=True,
            database=None,
            suppress_health_check=[HealthCheck.too_slow],
        )
        def collect(source: str) -> None:
            before = parse(source)
            pipes = [
                node
                for node in ast.walk(before)
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            ]
            after = transform(parse(source), NAMES)
            calls = [node for node in ast.walk(after) if isinstance(node, ast.Call)]
            remaining = [
                node
                for node in ast.walk(after)
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            ]
            if len(pipes) > len(remaining):
                counts["rewrote a pipe"] += 1
            if remaining:
                counts["a bitwise or left alone"] += 1
            names = {node.id for node in ast.walk(before) if isinstance(node, ast.Name)}
            if any(name.startswith("_") for name in names):
                counts["a lazy argument"] += 1
            if any(name.startswith("_") and name[1:].isdigit() for name in names):
                counts["reached outward with _2"] += 1
            if any(
                isinstance(inner, ast.Call)
                for call in calls
                for argument in call.args
                for inner in ast.walk(argument)
            ):
                counts["a pipe inside a lazy argument"] += 1
            if any(
                isinstance(node, (ast.List, ast.Dict, ast.Subscript, ast.IfExp))
                for node in ast.walk(after)
            ):
                counts["a rewrite inside a container or subscript"] += 1

        collect()
        return counts

    def test_every_shape_the_properties_need_is_still_produced(self) -> None:
        counts = self._shapes()
        empty = [shape for shape, count in counts.items() if count == 0]
        assert not empty, (
            f"the generator stopped producing {empty}, so the properties above are running on a "
            f"narrower set of trees than they were written for"
        )

    def test_the_shapes_are_not_one_lucky_example_each(self) -> None:
        counts = self._shapes()
        thin = {shape: count for shape, count in counts.items() if count < 10}
        assert not thin, f"barely produced: {thin}"
