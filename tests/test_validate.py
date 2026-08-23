"""The node allowlist rejects everything outside the language, and says what it rejected.

Two properties are being defended here and they are different. That unsupported syntax is
rejected is the obvious one. The one worth the effort is that the allowlist is **closed**: a node
type nobody has thought about is rejected because it is absent, not because someone remembered to
deny it. That is what made `t"{x}"` safe on the day Python 3.14 shipped it.
"""

from __future__ import annotations

import ast
import sys

import pytest

from safeexpr._errors import SafeExprError, ValidationError
from safeexpr._parse import MAX_SOURCE_BYTES, parse
from safeexpr._validate import _ALLOWED_NODES, validate


def check(source: str) -> ast.Expression:
    return validate(parse(source), source)


class TestTheLanguageWeDoSupport:
    @pytest.mark.parametrize(
        "source",
        [
            "1 + 1",
            "1 - 2 * 3 / 4 // 5 % 6 ** 7",
            "-1",
            "+1",
            "not a",
            "a and b or c",
            "1 < a < 3",
            "a == b != c",
            "a in [1, 2]",
            "a not in [1, 2]",
            "a if b else c",
            "a | first",
            'user.plan == "pro" and user.region in ["us", "eu"]',
            "metrics | where(_.value > threshold) | first",
            'orders | where(_.status == "paid") | group_by(_.customer_id)',
            "items[0]",
            "items[1:2]",
            "items[1:2:3]",
            'd["key"]',
            "d[k]",
            '{"a": 1, "b": [2, 3]}',
            "(1, 2)",
            "[]",
            "{}",
            "f(a, b)",
            "True",
            "None",
        ],
    )
    def test_supported_expressions_validate(self, source: str) -> None:
        assert isinstance(check(source), ast.Expression)

    @pytest.mark.parametrize("name", ["_", "_1", "_2", "_10"])
    def test_the_reserved_lazy_bindings_are_accepted(self, name: str) -> None:
        """`_` is the innermost item, `_2` and beyond reach outward one level per index."""
        assert check(f"{name}.field > 1") is not None


class TestConstructsRejectedByName:
    """Each message names the construct, because "node type not in allowlist" helps nobody."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("lambda x: x", "lambda expressions"),
            ("[y for y in z]", "list comprehensions"),
            ("{y for y in z}", "set comprehensions"),
            ("{y: y for y in z}", "dict comprehensions"),
            ("(y for y in z)", "generator expressions"),
            ("(x := 1)", "the walrus operator"),
            ("[*a]", "star unpacking"),
            ("f(*a)", "star unpacking"),
            ('f"{x}"', "f-strings"),
            ("{1, 2}", "set literals"),
            ("f(a, k=1)", "keyword arguments"),
            ("a & b", "the & operator"),
            ("a ^ b", "the ^ operator"),
            ("a << b", "the << operator"),
            ("a >> b", "the >> operator"),
            ("a @ b", "the @ operator"),
            ("~a", "the ~ operator"),
            ("a is b", "the `is` operator"),
            ("a is not b", "the `is not` operator"),
            ("{**a}", "dict unpacking"),
            ("a.b(1)", "method calls on values"),
            ("f()(1)", "calling the result of an expression"),
        ],
    )
    def test_the_message_names_the_construct(self, source: str, expected: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert expected in str(caught.value), f"message was: {caught.value}"

    @pytest.mark.parametrize(
        "source",
        ["lambda x: x", "[y for y in z]", "a & b", "f(a, k=1)"],
    )
    def test_rejections_carry_a_position(self, source: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert caught.value.lineno == 1
        assert caught.value.offset is not None

    def test_a_lambda_is_blamed_on_the_lambda_not_its_argument_list(self) -> None:
        """`ast.arguments` carries no position, so a naive sort blames it before the `Lambda`
        that contains it. The message a user sees would then be about "arguments"."""
        with pytest.raises(ValidationError) as caught:
            check("lambda x: x")
        assert "lambda" in str(caught.value)
        assert caught.value.lineno is not None

    @pytest.mark.skipif(sys.version_info < (3, 14), reason="t-strings are 3.14+")
    def test_t_strings_are_rejected_on_314(self) -> None:
        """PEP 750 added `TemplateStr` and `Interpolation` as **expression** nodes, so they are
        reachable in `mode="eval"`. Nothing was added to reject them: the allowlist is closed, so
        they were rejected the day the interpreter shipped. That is the whole argument for a
        closed allowlist, running as a test."""
        with pytest.raises(ValidationError) as caught:
            check('t"{x}"')
        assert "t-strings" in str(caught.value)


class TestPrivateNameBlocking:
    @pytest.mark.parametrize(
        "source",
        [
            "x.__class__",
            "x.__class__.__mro__",
            "x._private",
            "x.__globals__",
            "x.__code__",
            "x.__builtins__",
        ],
    )
    def test_private_attributes_are_blocked(self, source: str) -> None:
        """F2: the `__class__` to `__mro__` to `__subclasses__` climb, blocked at the first step."""
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert "underscore" in str(caught.value)

    @pytest.mark.parametrize(
        "source",
        ['x["__class__"]', 'x["_private"]', 'x["__globals__"]'],
    )
    def test_private_subscript_keys_are_blocked(self, source: str) -> None:
        """The spelling that walks past an attribute-only check.

        A validator checking only `Attribute` rejects `x.__class__` and lets `x["__class__"]`
        straight through. Verified in the prototype that preceded this module.
        """
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert "underscore" in str(caught.value)

    @pytest.mark.parametrize("name", ["__lazy_0", "_foo", "__x", "_a1", "_1a"])
    def test_private_names_are_blocked(self, name: str) -> None:
        """`__lazy_0` in particular: the prototype's lazy side table keyed synthetic names into
        the namespace the user writes into, and naming one handed back a live AST subtree."""
        with pytest.raises(ValidationError) as caught:
            check(f"{name} + 1")
        assert "underscore" in str(caught.value)

    def test_a_computed_private_key_is_not_caught_here(self) -> None:
        """Deliberate boundary. Only constant keys are visible statically; `x["__cl" + "ass__"]`
        is the evaluator's job, which is why the design has both a static and a dynamic layer.

        This test exists so the gap is documented rather than discovered.
        """
        assert check('x["__cl" + "ass__"]') is not None


class TestTheAllowlistIsClosed:
    def test_an_unknown_node_type_is_rejected(self) -> None:
        """The property that matters: rejection is by absence, not by enumeration.

        A hand-built tree containing a node type nobody considered must fail. If this ever passes
        by accident, the allowlist has become a denylist.
        """
        tree = parse("1 + 1")
        tree.body = ast.Await(value=ast.Constant(value=1), lineno=1, col_offset=0)  # type: ignore[assignment]
        with pytest.raises(ValidationError):
            validate(tree, "1 + 1")

    def test_no_statement_node_is_allowed(self) -> None:
        """`mode="eval"` makes these unreachable through parsing, but the allowlist should not
        depend on that being true forever."""
        statements = {
            n
            for n in vars(ast).values()
            if isinstance(n, type) and issubclass(n, ast.stmt) and n is not ast.stmt
        }
        assert statements, "sanity: ast should expose statement nodes"
        assert not (statements & _ALLOWED_NODES)

    def test_the_allowlist_holds_only_ast_types(self) -> None:
        assert all(isinstance(n, type) and issubclass(n, ast.AST) for n in _ALLOWED_NODES)


class TestF8ValidateOnceAndDoNotReExpose:
    def test_the_validated_tree_is_the_same_object(self) -> None:
        """asteval GHSA-vp47-9734-prjw is a time-of-check/time-of-use bug: the tree that was
        checked was not the tree that ran. Returning the same object, rather than a copy, means
        there is no window for the two to differ."""
        tree = parse("1 + 1")
        assert validate(tree, "1 + 1") is tree

    def test_validation_does_not_mutate_the_tree(self) -> None:
        source = "items | where(_.price > 10) | map(_.name) | first"
        tree = parse(source)
        before = ast.dump(tree)
        validate(tree, source)
        assert ast.dump(tree) == before


class TestTheWalkIsIterative:
    def test_a_tree_at_the_source_cap_does_not_exhaust_the_stack(self) -> None:
        """**A recursive validator would fail this**, and would have shipped.

        The source cap allows 2048 bytes, which is ~2,040 levels of unary nesting, while the
        default recursion limit is 1000. Measured: a recursive walk of this exact tree raises
        `RecursionError`. The walk uses an explicit stack for that reason.
        """
        source = "-" * (MAX_SOURCE_BYTES - 8) + "1"
        assert len(source.encode()) <= MAX_SOURCE_BYTES
        assert check(source) is not None

    def test_a_deep_tree_of_rejected_nodes_still_reports_rather_than_crashing(self) -> None:
        source = "~" * 1500 + "1"
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert "~" in str(caught.value)


class TestOnlyOurErrorsEscape:
    @pytest.mark.parametrize(
        "source",
        ["lambda x: x", "x.__class__", "a & b", "[y for y in z]", "{**a}", "f(a, k=1)"],
    )
    def test_every_rejection_is_a_safeexpr_error(self, source: str) -> None:
        with pytest.raises(SafeExprError):
            check(source)

    def test_nothing_is_chained(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check("lambda x: x")
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
