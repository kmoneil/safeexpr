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
from safeexpr._validate import _ALLOWED_NODES, MAX_EXPRESSION_DEPTH, validate


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
    def test_a_tree_at_the_source_cap_reports_rather_than_crashing(self) -> None:
        """**A recursive validator would fail this**, and would have shipped.

        The source cap allows 2048 bytes, which is ~2,040 levels of unary nesting, while the
        default recursion limit is 1000. Measured: a recursive walk of this exact tree raises
        `RecursionError`. The walk uses an explicit stack, so the tree is walked to the end and
        the depth limit gets to report on it, rather than the walk dying first.

        The distinction that matters is *which* error comes out: a clean `ValidationError`
        naming the depth, not a `RecursionError` dressed up as an internal bug.
        """
        source = "-" * (MAX_SOURCE_BYTES - 8) + "1"
        assert len(source.encode()) <= MAX_SOURCE_BYTES
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert "nests" in str(caught.value)
        assert "bug" not in str(caught.value)

    def test_a_deep_tree_of_rejected_nodes_still_names_the_construct(self) -> None:
        """Under the depth limit, the per-node message is what a reader needs."""
        source = "~" * (MAX_EXPRESSION_DEPTH - 5) + "1"
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert "~" in str(caught.value)

    def test_depth_is_reported_before_anything_else(self) -> None:
        """An expression this deep is unusable whatever else is wrong with it, so telling the
        author about a lambda buried inside it would send them to fix the wrong thing."""
        source = "~" * (MAX_EXPRESSION_DEPTH + 50) + "(lambda: 1)"
        with pytest.raises(ValidationError) as caught:
            check(source)
        assert "nests" in str(caught.value)


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


def _expression_nodes() -> set[type[ast.expr]]:
    """Every expression node type this interpreter has.

    `ast.expr.__subclasses__()` rather than a scan of `vars(ast)`, which is the difference between
    27 node types and 27 plus the deprecated `Num`/`Str`/`Bytes` aliases. Those subclass
    `Constant`, not `expr`, and constructing one warns.
    """
    return set(ast.expr.__subclasses__())


def _bare(node_type: type[ast.expr]) -> ast.expr:
    """One instance of `node_type`, with every field filled so nothing deprecated is triggered.

    Since 3.13, omitting a required field warns and says it becomes an error in 3.15, and this
    project runs its suite with warnings as errors. The values are nonsense on purpose: the
    allowlist decides on the node's *type* before anything looks at what is inside it, which is
    the property being tested.
    """
    return node_type(**{field: ast.Constant(value=1) for field in node_type._fields})


class TestTheAllowlistIsClosedOverEveryExpressionNode:
    """F7, proven by construction rather than by two hand-written entries.

    "New CPython syntax is new attack surface" is the lesson RestrictedPython's CVE-2025-22153
    taught, and the corpus proves it for the one case that has actually happened: 3.14's t-strings
    are expression nodes, so `t"{x}"` parses in `mode="eval"` and a denylist would have run it.

    Two entries prove one case. This enumerates **every expression node type the running
    interpreter has** and asserts each one is either on the allowlist deliberately or rejected,
    so the property is re-proven on each interpreter in the matrix and fails on the day a future
    Python adds a node nobody has reviewed. That is the whole claim behind a closed allowlist, and
    it was the one part of it nothing checked.
    """

    # The expression nodes the language is made of. Written out rather than derived, because
    # adding one is exactly the change that should be hard to make by accident: this list is the
    # language surface, and a diff here is a diff to what an expression can be.
    EXPECTED_ALLOWED = frozenset(
        {
            "Attribute", "BinOp", "BoolOp", "Call", "Compare", "Constant", "Dict", "IfExp",
            "List", "Name", "Slice", "Subscript", "Tuple", "UnaryOp",
        }
    )  # fmt: skip

    def test_the_interpreter_has_expression_nodes_to_check(self) -> None:
        """A scan that found nothing would pass everything below."""
        assert len(_expression_nodes()) >= 25

    def test_the_allowed_expression_nodes_are_exactly_the_language(self) -> None:
        allowed = {node.__name__ for node in _ALLOWED_NODES & _expression_nodes()}
        assert allowed == set(self.EXPECTED_ALLOWED)

    def test_every_other_expression_node_is_rejected(self) -> None:
        """Including ones this test has never heard of.

        On 3.14 that covers `TemplateStr` and `Interpolation` without either being named here,
        which is the point: the allowlist closed them the day the interpreter shipped and this
        says so without an edit.
        """
        for node_type in sorted(_expression_nodes() - _ALLOWED_NODES, key=lambda n: n.__name__):
            tree = ast.Expression(body=_bare(node_type))
            with pytest.raises(ValidationError):
                validate(tree, "")

    def test_the_rejection_names_the_construct_where_a_person_would_hit_it(self) -> None:
        """A closed allowlist can reject a node it has never heard of, and the message then says
        "`Whatever` nodes are not supported", which is honest and unhelpful. The constructs a
        person actually types have a sentence written for them, and this is the list of the ones
        that must."""
        for name, expected in (
            ("Lambda", "lambda"),
            ("ListComp", "list comprehension"),
            ("GeneratorExp", "generator expression"),
            ("JoinedStr", "f-string"),
            ("FormattedValue", "f-string"),
            ("NamedExpr", "walrus"),
            ("Starred", "star unpacking"),
            ("Await", "await"),
            ("Set", "set literal"),
        ):
            node_type = getattr(ast, name, None)
            assert node_type is not None, f"ast has no {name}"
            with pytest.raises(ValidationError, match=expected):
                validate(ast.Expression(body=_bare(node_type)), "")

    def test_no_interpolation_node_is_allowed_whatever_it_is_called(self) -> None:
        """The no-interpolation decision, read off the allowlist rather than a corpus entry.

        `JoinedStr` and `FormattedValue` exist everywhere; `TemplateStr` and `Interpolation` only
        from 3.14. Checking by name means the assertion is the same sentence on every interpreter
        and simply covers more on the newer ones.
        """
        present = [
            getattr(ast, name)
            for name in ("JoinedStr", "FormattedValue", "TemplateStr", "Interpolation")
            if hasattr(ast, name)
        ]
        assert len(present) >= 2, "sanity: f-string nodes exist on every supported version"
        for node_type in present:
            assert node_type not in _ALLOWED_NODES
