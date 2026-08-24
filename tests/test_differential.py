"""Against CPython itself, on the subset this package claims to implement.

The strongest correctness signal available: for everything inside the safe subset, the answer
here must be the answer Python gives. Not similar, the same.

**The property is stated as agreement rather than equality**, and the difference matters. Plenty
of generated expressions are errors in both: dividing by zero, comparing text with a number,
indexing past the end. Requiring equal *values* would force the generator away from all of them,
which is exactly where disagreement is most likely to hide. Requiring that the two either agree
on a value or both refuse keeps those cases in.

**Coverage is asserted, because a generator can shrink toward easy cases and pass while covering
nothing.** Every node type on the validator's allowlist has to appear in the expressions actually
generated, and the test that checks this reads the allowlist rather than a copy of it, so a node
type added to the language without a way to generate one fails here.

Reproducing a failure: hypothesis prints a `@reproduce_failure` blob with any falsifying example,
and `pytest --hypothesis-seed=N` re-runs a whole session deterministically.
"""

from __future__ import annotations

import ast
from typing import Any, ClassVar

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from safeexpr import Evaluator, SafeExprError, standard_registry
from safeexpr._parse import parse
from safeexpr._validate import _ALLOWED_NODES

# Values every generated expression may name. Chosen so that arithmetic stays small, comparisons
# have something to disagree about, and the containers have something to index into.
CONTEXT: dict[str, Any] = {
    "a": 3,
    "b": -2,
    "c": 0,
    "f": 1.5,
    "s": "abc",
    "t": "",
    "yes": True,
    "no": False,
    "nil": None,
    "xs": [10, 20, 30],
    "ys": (1, 2),
    "d": {"k": 1, "j": 2},
}

# Node types that cannot appear in a generated expression, each for a stated reason. Everything
# else on the allowlist has to be generated.
STRUCTURAL = {"Expression", "Load"}
COVERED_ELSEWHERE = {
    # `d.k` means `d["k"]` here and `getattr(d, "k")` in Python, so the two do not agree by
    # construction and cannot be compared source-for-source. `TestAttributeAccessAgreesWithA
    # SubscriptInstead` covers it against the operation it actually corresponds to.
    "Attribute",
    # Only registry functions are callable, and the registry is empty for the core comparison.
    # `TestCallsAgreeWhenTheFunctionIsPythons` covers it with Python's own builtins registered.
    "Call",
}


class _Plain:
    """An ordinary host object, for the attribute-access divergence."""

    colour = "red"


_evaluator = Evaluator()


def _python(source: str, context: dict[str, Any]) -> Any:
    return eval(source, {"__builtins__": {}}, dict(context))  # noqa: S307


def agree(source: str, context: dict[str, Any], evaluator: Evaluator | None = None) -> None:
    """Assert this package and CPython either give the same value or both refuse.

    Args:
        source: The expression.
        context: The names available to it.
        evaluator: The evaluator to use; a bare one by default.

    Raises:
        AssertionError: On any disagreement.
    """
    ours: Any
    theirs: Any
    try:
        ours, our_error = (evaluator or _evaluator).evaluate(source, context), None
    except SafeExprError as refused:
        ours, our_error = None, refused
    try:
        theirs, their_error = _python(source, context), None
    except Exception as raised:  # any refusal counts as a refusal
        theirs, their_error = None, raised

    if our_error is None and their_error is None:
        assert ours == theirs, f"{source!r} gave {ours!r} here and {theirs!r} in Python"
        assert type(ours) is type(theirs), (
            f"{source!r} gave a {type(ours).__name__} here and a {type(theirs).__name__} in Python"
        )
        return
    assert not (our_error is None and their_error is not None), (
        f"{source!r} was accepted here, giving {ours!r}, and refused by Python: {their_error!r}"
    )
    # Refusing where Python succeeds is allowed and is covered by
    # `TestTheDivergencesAreDeliberate`, which names every one of them.


# ---------------------------------------------------------------------------
# The generator
# ---------------------------------------------------------------------------

NUMERIC_NAMES = st.sampled_from(["a", "b", "c", "f"])
ANY_NAME = st.sampled_from(sorted(CONTEXT))
SMALL = st.integers(min_value=-5, max_value=5)

CONSTANTS = st.one_of(
    SMALL.map(repr),
    st.sampled_from(["0.5", "-1.25", "True", "False", "None", '"x"', '""']),
)


@st.composite
def _atom(draw: st.DrawFn) -> str:
    return draw(st.one_of(CONSTANTS, ANY_NAME))


def _numeric(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Arithmetic, kept to shapes where a result stays small enough to be worth comparing."""
    operators = st.sampled_from(["+", "-", "*", "//", "%", "/", "|"])
    return st.builds(
        lambda left, op, right: f"({left} {op} {right})",
        inner,
        operators,
        inner,
    )


def _power(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """`**` with a small exponent, so the result cap is not what is being tested."""
    return st.builds(
        lambda base, exponent: f"({base} ** {exponent})",
        inner,
        st.integers(min_value=0, max_value=3).map(repr),
    )


def _comparison(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    operators = st.sampled_from(["==", "!=", "<", "<=", ">", ">=", "in", "not in"])
    chained = st.builds(
        lambda a, o1, b, o2, c: f"({a} {o1} {b} {o2} {c})",
        inner,
        st.sampled_from(["<", "<=", ">", ">=", "==", "!="]),
        inner,
        st.sampled_from(["<", "<=", ">", ">=", "==", "!="]),
        inner,
    )
    simple = st.builds(lambda a, o, b: f"({a} {o} {b})", inner, operators, inner)
    return st.one_of(simple, chained)


def _boolean(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return st.one_of(
        st.builds(lambda a, o, b: f"({a} {o} {b})", inner, st.sampled_from(["and", "or"]), inner),
        st.builds(lambda a: f"(not {a})", inner),
    )


def _unary(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return st.builds(lambda o, a: f"({o}{a})", st.sampled_from(["-", "+"]), inner)


def _containers(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return st.one_of(
        st.lists(inner, min_size=0, max_size=3).map(lambda parts: "[" + ", ".join(parts) + "]"),
        st.lists(inner, min_size=2, max_size=3).map(lambda parts: "(" + ", ".join(parts) + ")"),
        st.lists(inner, min_size=1, max_size=2).map(
            lambda parts: "{" + ", ".join(f'"k{n}": {p}' for n, p in enumerate(parts)) + "}"
        ),
    )


def _subscripts(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    indexed = st.builds(
        lambda target, index: f"{target}[{index}]",
        st.sampled_from(["xs", "ys", "s"]),
        st.integers(min_value=-4, max_value=4).map(repr),
    )
    keyed = st.builds(lambda key: f'd["{key}"]', st.sampled_from(["k", "j", "missing"]))
    sliced = st.builds(
        lambda target, low, high: f"{target}[{low}:{high}]",
        st.sampled_from(["xs", "ys", "s"]),
        st.one_of(st.just(""), st.integers(min_value=-3, max_value=3).map(repr)),
        st.one_of(st.just(""), st.integers(min_value=-3, max_value=3).map(repr)),
    )
    return st.one_of(indexed, keyed, sliced, st.builds(lambda a: a, inner))


def _ternary(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return st.builds(lambda a, t, b: f"({t} if {a} else {b})", inner, inner, inner)


EXPRESSIONS = st.recursive(
    st.one_of(_atom(), _subscripts(_atom())),
    lambda inner: st.one_of(
        _numeric(inner),
        _power(inner),
        _comparison(inner),
        _boolean(inner),
        _unary(inner),
        _containers(inner),
        _subscripts(inner),
        _ternary(inner),
    ),
    max_leaves=8,
)


def node_types(source: str) -> set[str]:
    """Every node type in a parsed expression, by name."""
    return {type(node).__name__ for node in ast.walk(parse(source))}


def _collect(examples: int) -> set[str]:
    """Every node type the generator produces, over a **deterministic** draw.

    `derandomize` and a disabled database on purpose. The first version of this let hypothesis
    draw randomly, which made a coverage assertion depend on luck: it passed on its own and
    failed inside the full suite, because the example database and the run order changed what was
    drawn. A test that says "the generator covers the language" must give the same answer every
    time, or it is reporting the weather.

    Args:
        examples: How many expressions to draw.

    Returns:
        The node type names seen.
    """
    seen: set[str] = set()

    @given(source=EXPRESSIONS)
    @settings(
        max_examples=examples,
        deadline=None,
        derandomize=True,
        database=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def collect(source: str) -> None:
        seen.update(node_types(source))

    collect()
    return seen


class TestTheGeneratedSubsetAgreesWithCPython:
    """T2. Zero divergence, across every generated shape."""

    @given(source=EXPRESSIONS)
    @settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_it_agrees(self, source: str) -> None:
        agree(source, CONTEXT)

    @given(source=EXPRESSIONS)
    @settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_and_the_generated_source_is_real_python(self, source: str) -> None:
        """The control that stops a generator producing something neither side can parse and
        calling the matching failures agreement."""
        ast.parse(source, mode="eval")


class TestTheGeneratorReachesTheWholeAllowlist:
    """**Without this the suite can pass while covering nothing.**

    A generator that drifts toward atoms still produces thousands of examples and still reports
    zero divergence. The allowlist is read from the validator rather than copied, so a node type
    added to the language with no way to generate one fails here rather than going untested.
    """

    def test_every_allowlisted_node_type_is_generated(self) -> None:
        seen = _collect(1200)
        allowed = {node.__name__ for node in _ALLOWED_NODES}
        missing = allowed - seen - STRUCTURAL - COVERED_ELSEWHERE
        assert not missing, (
            f"the generator never produced {sorted(missing)}, so nothing was compared against "
            f"CPython for them. Extend the generator, or explain the exclusion in "
            f"COVERED_ELSEWHERE."
        )

    def test_the_exclusions_are_all_real_node_types(self) -> None:
        """So an exclusion cannot be a typo that quietly excuses nothing."""
        allowed = {node.__name__ for node in _ALLOWED_NODES}
        assert allowed >= STRUCTURAL | COVERED_ELSEWHERE


class TestAttributeAccessAgreesWithASubscriptInstead:
    """`d.k` means `d["k"]` here and `getattr(d, "k")` in Python, so the comparison has to be
    against the operation it actually corresponds to rather than against the same source."""

    @given(key=st.sampled_from(["k", "j", "missing"]))
    @settings(max_examples=50, deadline=None)
    def test_a_field_read_is_a_key_read(self, key: str) -> None:
        ours: Any
        try:
            ours = _evaluator.evaluate(f"d.{key}", CONTEXT)
        except SafeExprError:
            ours = KeyError
        try:
            theirs = _python(f'd["{key}"]', CONTEXT)
        except KeyError:
            theirs = KeyError
        assert ours == theirs

    def test_and_never_reaches_a_real_attribute(self) -> None:
        """The divergence that is the whole point: Python would find `items`, we find the key."""
        assert _evaluator.evaluate("d.items", {"d": {"items": 7}}) == 7
        with pytest.raises(SafeExprError):
            _evaluator.evaluate("d.items", {"d": {"k": 1}})


class TestCallsAgreeWhenTheFunctionIsPythons:
    """`Call` is excluded from the core generator because only registry functions are callable.
    Registering Python's own builtins puts it back under comparison."""

    REGISTRY: ClassVar[dict[str, Any]] = {
        "len": len,
        "abs": abs,
        "int": int,
        "str": str,
        "bool": bool,
    }

    @given(
        name=st.sampled_from(["len", "abs", "int", "str", "bool"]),
        argument=st.sampled_from(["a", "b", "f", "s", "xs", "ys", "d", "yes", "nil", "t"]),
    )
    @settings(max_examples=300, deadline=None)
    def test_a_call_agrees(self, name: str, argument: str) -> None:
        evaluator = Evaluator(registry=self.REGISTRY)
        context = {**CONTEXT, **self.REGISTRY}
        agree(f"{name}({argument})", context, evaluator)


class TestOutOfSubsetIsRefused:
    """The second acceptance criterion. Zero acceptance outside the subset, and the generator
    is a grammar of things Python accepts and this package must not."""

    OUTSIDE: ClassVar[list[str]] = [
        "lambda: 1",
        "[x for x in xs]",
        "{x for x in xs}",
        "{x: x for x in xs}",
        "(x for x in xs)",
        "f'{a}'",
        "a if a else (b := 2)",
        "[*xs]",
        "{**d}",
        "a & b",
        "a ^ b",
        "a << b",
        "a >> b",
        "~a",
        "a is b",
        "a is not b",
        "s.upper()",
        "(lambda: 1)()",
        "a.__class__",
        'd["__class__"]',
        "__name__",
        "_secret",
        "await a",
    ]

    @pytest.mark.parametrize("source", OUTSIDE)
    def test_it_is_refused(self, source: str) -> None:
        with pytest.raises(SafeExprError):
            _evaluator.evaluate(source, CONTEXT)

    @pytest.mark.parametrize("source", OUTSIDE)
    def test_and_it_is_refused_with_a_registry_too(self, source: str) -> None:
        """So nothing in the subset check depends on the registry being empty."""
        with pytest.raises(SafeExprError):
            Evaluator(registry=standard_registry()).evaluate(source, CONTEXT)

    @given(
        source=st.sampled_from(OUTSIDE),
        wrapper=st.sampled_from(["({})", "1 + ({})", "[{}]", "({}) if a else b", "not ({})"]),
    )
    @settings(max_examples=200, deadline=None)
    def test_burying_it_inside_a_valid_expression_does_not_help(
        self, source: str, wrapper: str
    ) -> None:
        with pytest.raises(SafeExprError):
            _evaluator.evaluate(wrapper.format(source), CONTEXT)


class TestTheDivergencesAreDeliberate:
    """Every place this package refuses something Python accepts, named.

    Agreement allows refusing where Python succeeds, because a sandbox that never refused would
    not be one. That freedom is only safe if the list of refusals is written down, so this is the
    list, and each entry says which rule it belongs to.
    """

    @pytest.mark.parametrize(
        ("source", "context", "rule"),
        [
            ('"%s" % a', {"a": 1}, "F1: %-formatting reads attributes and keys"),
            ('"%(k)s" % d', {"d": {"k": 1}}, "F1: %-formatting does its own lookup in C"),
            ("obj.colour", {"obj": _Plain()}, "F2: attribute access reaches mappings only"),
            ("s.upper", {"s": "abc"}, "F2: attribute access reaches mappings only"),
        ],
    )
    def test_python_accepts_it_and_this_package_does_not(
        self, source: str, context: dict[str, Any], rule: str
    ) -> None:
        _python(source, context)
        with pytest.raises(SafeExprError):
            _evaluator.evaluate(source, context)

    @pytest.mark.parametrize(
        ("source", "small", "large", "rule"),
        [
            ('"a" * n', {"n": 4}, {"n": 10**7}, "the result-size cap"),
            ("xs * n", {"xs": [0], "n": 4}, {"xs": [0], "n": 10**7}, "the result-size cap"),
            (
                "xs + xs",
                {"xs": [0] * 4},
                {"xs": [0] * 600_000},
                "the result-size cap on concatenation",
            ),
            ("a ** b", {"a": 10, "b": 3}, {"a": 10**100, "b": 10**6}, "the power cap"),
        ],
    )
    def test_the_size_caps_refuse_what_python_would_build(
        self, source: str, small: dict[str, Any], large: dict[str, Any], rule: str
    ) -> None:
        """**Written as small-accepted plus large-refused, and never as large-computed.**

        The obvious shape is to evaluate the large case in Python first, to show it is accepted
        there. For the power entry that means asking CPython for `(10**100) ** 10**6`, which is a
        hundred million digits: the first draft of this test did exactly that and took the suite
        past two minutes. What the caps refuse is the size, so the size is the one thing not to
        materialise in order to prove it.
        """
        agree(source, small)
        with pytest.raises(SafeExprError):
            _evaluator.evaluate(source, large)


class TestRegressions:
    def test_regression_differential_the_generator_covers_the_allowlist(self) -> None:
        """A generator that drifts toward atoms passes every agreement test and proves nothing.

        Pinned as its own assertion rather than left to the coverage test above, because the
        coverage test could itself be weakened by adding an exclusion; this one names the node
        types that were hardest to reach and would be the first to go.
        """
        seen = _collect(1200)
        for name in ("Slice", "Dict", "Tuple", "IfExp", "NotIn", "Pow", "FloorDiv", "Mod"):
            assert name in seen, f"the generator stopped producing {name}"
