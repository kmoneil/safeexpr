"""The evaluator computes the right answer, and cannot be talked into computing anything else.

The security tests here are about *reachability*, not about rejection. There is no denylist to
check: the question each one asks is whether a path exists at all.
"""

from __future__ import annotations

import ast
import collections
import os
import threading
import time
import types
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

import safeexpr._eval as eval_module
from safeexpr import EvaluationError, Evaluator, SafeExprError, evaluate, standard_registry
from safeexpr._eval import MAX_POWER_RESULT_BITS
from safeexpr._guards import MAX_RESULT_SIZE

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "safeexpr"


class _CustomMapping(Mapping):
    """A `collections.abc.Mapping` that is not a `dict` and never will be.

    It carries a real attribute called `api_key`, so a guard that ever let a mapping fall through
    to the `getattr` branch would answer `u.api_key` with the object's attribute instead of "no
    field". That is the difference between data access and object access, and it is the whole of
    F2's first step. Named to match `tests/test_thread_safety.py`, which uses the same shape for
    the same reason.
    """

    api_key = "sk-live-must-not-become-reachable"

    def __init__(self, pairs: Mapping[str, Any]) -> None:
        self._pairs = dict(pairs)

    def __getitem__(self, key: str) -> Any:
        return self._pairs[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._pairs)

    def __len__(self) -> int:
        return len(self._pairs)


class TestCanonicalUseCases:
    """Three of the design's five. The other two need pipes, lazy arguments and the registry."""

    def test_feature_flag_targeting(self) -> None:
        source = 'user.plan == "pro" and user.region in ["us", "eu"]'
        assert evaluate(source, {"user": {"plan": "pro", "region": "eu"}}) is True
        assert evaluate(source, {"user": {"plan": "free", "region": "eu"}}) is False
        assert evaluate(source, {"user": {"plan": "pro", "region": "ap"}}) is False

    def test_authorization_policy(self) -> None:
        source = 'resource.owner_id == principal.id or "admin" in principal.roles'
        allowed = {"resource": {"owner_id": 7}, "principal": {"id": 7, "roles": []}}
        by_role = {"resource": {"owner_id": 7}, "principal": {"id": 3, "roles": ["admin"]}}
        denied = {"resource": {"owner_id": 7}, "principal": {"id": 3, "roles": ["dev"]}}
        assert evaluate(source, allowed) is True
        assert evaluate(source, by_role) is True
        assert evaluate(source, denied) is False

    def test_workflow_condition(self) -> None:
        source = 'event.type == "deploy" and event.env != "prod"'
        assert evaluate(source, {"event": {"type": "deploy", "env": "staging"}}) is True
        assert evaluate(source, {"event": {"type": "deploy", "env": "prod"}}) is False


class TestF3ContextValuesAreNeverCallable:
    """simpleeval CVE-2026-32640 and asteval's `reduce`: a dangerous callable handed in as data
    and then invoked by the sandbox on the attacker's behalf.

    There is nothing to block here, which is the point. Call position resolves in the registry
    and the registry only, so the context is not consulted and a callable in it is just a value.
    """

    def test_a_callable_in_the_context_cannot_be_called(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("danger(1)", {"danger": lambda n: n * 2})
        assert "cannot be called" in str(caught.value)

    def test_the_error_says_why_rather_than_just_that(self) -> None:
        """A user who put a function in the context needs to know it will never be reachable."""
        with pytest.raises(EvaluationError) as caught:
            evaluate("f(1)", {"f": print})
        assert "values from the context cannot be called" in str(caught.value)

    def test_a_genuinely_dangerous_callable_is_equally_unreachable(self) -> None:
        with pytest.raises(EvaluationError):
            evaluate("system('echo pwned')", {"system": os.system})

    def test_a_callable_is_still_a_perfectly_good_value(self) -> None:
        """Not being callable is not the same as not existing."""
        marker = object()
        assert evaluate("x == x", {"x": marker}) is True

    def test_a_registry_function_shadowed_by_context_still_resolves_to_the_registry(self) -> None:
        """Whichever way the collision falls, it must not fall towards the context."""
        ev = Evaluator(registry={"double": lambda n: n * 2})
        assert ev.evaluate("double(4)", {"double": lambda n: n * 999}) == 8


class TestF2AttributeTraversalDoesNotReachObjects:
    """The `__class__` to `__mro__` to `__subclasses__` climb needs a first step.

    There is not one.
    """

    def test_attribute_access_on_an_arbitrary_object_is_refused(self) -> None:
        class Thing:
            colour = "red"

        with pytest.raises(EvaluationError) as caught:
            evaluate("thing.colour", {"thing": Thing()})
        assert "registered" in str(caught.value)

    def test_a_dict_key_never_falls_back_to_a_method(self) -> None:
        """`d.items` is the key "items". Falling back to `getattr` would hand back the bound
        method, and from a bound method the rest of the object model is one step away."""
        assert evaluate("d.items", {"d": {"items": [1, 2]}}) == [1, 2]

    def test_a_missing_key_does_not_fall_back_to_a_method_either(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("d.keys", {"d": {"a": 1}})
        assert "no field" in str(caught.value)

    def test_registered_types_are_the_only_opt_in(self) -> None:
        class Point:
            def __init__(self) -> None:
                self.x = 3
                self.hidden = "no"

        ev = Evaluator(attribute_types={Point: frozenset({"x"})})
        assert ev.evaluate("p.x", {"p": Point()}) == 3
        with pytest.raises(EvaluationError):
            ev.evaluate("p.hidden", {"p": Point()})

    def test_registration_is_by_exact_type_not_by_inheritance(self) -> None:
        """A subclass is a different type with different attributes; inheriting the permission
        would let a host's allowlist apply to a class it has never seen."""

        class Base:
            x = 1

        class Sub(Base):
            pass

        ev = Evaluator(attribute_types={Base: frozenset({"x"})})
        assert ev.evaluate("v.x", {"v": Base()}) == 1
        with pytest.raises(EvaluationError):
            ev.evaluate("v.x", {"v": Sub()})


class TestDynamicPrivateKeyBlocking:
    """The half a static check cannot do."""

    def test_a_computed_dunder_key_is_blocked_at_eval_time(self) -> None:
        """`x["__cl" + "ass__"]` passes validation by design, because the key is not a literal."""
        with pytest.raises(EvaluationError) as caught:
            evaluate('x["__cl" + "ass__"]', {"x": {"__class__": "reached"}})
        assert "underscore" in str(caught.value)

    @pytest.mark.parametrize(
        "source",
        ["x[k]", 'x["_" + "private"]', "x[keys[0]]"],
    )
    def test_private_keys_from_any_source_are_blocked(self, source: str) -> None:
        context: dict[str, Any] = {
            "x": {"__class__": "reached", "_private": "reached"},
            "k": "__class__",
            "keys": ["_private"],
        }
        with pytest.raises(EvaluationError):
            evaluate(source, context)

    def test_ordinary_keys_still_work(self) -> None:
        assert evaluate('x["a"]', {"x": {"a": 1}}) == 1
        assert evaluate("x[k]", {"x": {"a": 1}, "k": "a"}) == 1


class TestThePowerCapIsOnTheResultNotTheExponent:
    """Measured: `(10**100) ** 100_000` takes 9.6s with an exponent 40x under simpleeval's cap of
    4,000,000, because cost scales with `bit_length(base) * exponent`."""

    def test_a_large_exponent_is_refused(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("2 ** 999999999")
        assert "limit" in str(caught.value)

    def test_a_large_base_with_a_modest_exponent_is_refused(self) -> None:
        """**The case an exponent-only cap misses.** The exponent here is 100,000."""
        with pytest.raises(EvaluationError):
            evaluate("(10 ** 100) ** 100000")

    def test_the_refusal_is_fast(self) -> None:
        """A guard that computes the value before rejecting it is not a guard.

        Timed as the shortest of five, because interference only ever adds time and a single
        wall-clock sample on a busy machine is a coin toss. Computing the value would be slow in
        the minimum too.
        """
        best = float("inf")
        for _ in range(5):
            start = time.perf_counter()
            with pytest.raises(EvaluationError):
                evaluate("(10 ** 1000) ** 3000000")
            best = min(best, time.perf_counter() - start)
        assert best < 1.0

    def test_ordinary_powers_still_work(self) -> None:
        assert evaluate("2 ** 10") == 1024
        assert evaluate("2 ** 0.5") == pytest.approx(1.4142135623730951)
        assert evaluate("2 ** -1") == 0.5
        assert evaluate("(-8) ** 2") == 64

    def test_the_cap_is_documented_in_bits(self) -> None:
        assert MAX_POWER_RESULT_BITS == 8_388_608


class TestOperatorSemanticsMatchPython:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("1 + 2", 3),
            ("7 - 2", 5),
            ("3 * 4", 12),
            ("7 / 2", 3.5),
            ("7 // 2", 3),
            ("7 % 3", 1),
            ("-5", -5),
            ("+5", 5),
            ("not 0", True),
            ("6 | 9", 15),
            ("1 < 2 < 3", True),
            ("1 < 3 < 2", False),
            ("1 == 1 == 1", True),
            ("'a' in ['a', 'b']", True),
            ("'c' not in ['a', 'b']", True),
            ("[1, 2] + [3]", [1, 2, 3]),
            ("'ab' * 2", "abab"),
            ("1 if True else 2", 1),
            ("(1, 2)", (1, 2)),
            ("{'a': 1}", {"a": 1}),
            ("[1, 2, 3][1:]", [2, 3]),
            ("[1, 2, 3][::2]", [1, 3]),
        ],
    )
    def test_results_match(self, source: str, expected: Any) -> None:
        assert evaluate(source) == expected

    def test_boolops_return_the_deciding_value(self) -> None:
        """Python semantics, which is what makes `name or "anonymous"` useful."""
        assert evaluate('name or "anonymous"', {"name": ""}) == "anonymous"
        assert evaluate('name or "anonymous"', {"name": "kim"}) == "kim"
        assert evaluate("a and b", {"a": 0, "b": 5}) == 0
        assert evaluate("a and b", {"a": 3, "b": 5}) == 5

    def test_boolops_short_circuit(self) -> None:
        """The right operand must not be evaluated when the left decides it."""
        assert evaluate("False and missing_name") is False
        assert evaluate("True or missing_name") is True

    def test_chained_comparison_short_circuits(self) -> None:
        assert evaluate("1 > 2 > missing_name") is False

    def test_a_chained_comparison_evaluates_the_middle_once(self) -> None:
        """`1 < f() < 3` must call `f` once, as Python does."""
        calls: list[int] = []
        ev = Evaluator(registry={"tick": lambda: (calls.append(1), 2)[1]})
        assert ev.evaluate("1 < tick() < 3") is True
        assert len(calls) == 1


class TestErrorsAreOursAndPositioned:
    @pytest.mark.parametrize(
        "source",
        ["missing", "1 / 0", "1 + 'a'", "d.nope", "[1][9]", "1 < 'a'", "(-1) ** 0.5 + none.x"],
    )
    def test_every_failure_is_a_safeexpr_error(self, source: str) -> None:
        with pytest.raises(SafeExprError):
            evaluate(source, {"d": {}})

    def test_errors_carry_a_position(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("1 + missing", {})
        assert caught.value.lineno == 1
        assert caught.value.offset == 5
        assert caught.value.source == "1 + missing"

    def test_annotated_points_at_the_problem(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("1 + missing", {})
        assert caught.value.annotated() == ("`missing` is not defined\n  1 + missing\n      ^")

    def test_nothing_is_chained(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("1 / 0")
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    def test_a_misspelt_name_gets_a_suggestion(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("usr.plan", {"user": {"plan": "pro"}})
        assert "did you mean `user`" in str(caught.value)

    def test_a_misspelt_field_gets_a_suggestion(self) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate("user.pln", {"user": {"plan": "pro"}})
        assert "did you mean `plan`" in str(caught.value)

    def test_error_messages_never_include_the_value(self) -> None:
        """R8. A type name is fine; the caller's data is not."""
        sensitive = "sk-live-must-not-appear"
        with pytest.raises(EvaluationError) as caught:
            evaluate("token + 1", {"token": sensitive})
        assert sensitive not in str(caught.value)
        assert "str" in str(caught.value)


class TestContainmentAtTheBoundary:
    def test_a_misbehaving_dunder_on_a_context_value_is_contained(self) -> None:
        """F5. Nothing in the language raises, but comparing calls `__eq__`, and the object is
        the caller's. A host's `except Exception` would not have stopped this one."""

        class Hostile:
            __hash__ = object.__hash__

            def __eq__(self, other: object) -> bool:
                raise SystemExit(1)

        with pytest.raises(SafeExprError):
            evaluate("x == 1", {"x": Hostile()})

    def test_a_misbehaving_dunder_does_not_leak_the_object(self) -> None:
        class Hostile:
            __hash__ = object.__hash__
            api_key = "sk-live"

            def __eq__(self, other: object) -> bool:
                raise KeyboardInterrupt

        with pytest.raises(SafeExprError) as caught:
            evaluate("x == 1", {"x": Hostile()})
        assert caught.value.__context__ is None
        assert "sk-live" not in str(caught.value)


class TestTheEvaluatorIsImmutable:
    """Q10 wants an evaluator that is either immutable after construction or documented
    single-thread. Immutable costs nothing if it is built that way from the start."""

    def test_no_per_evaluation_state_lives_on_the_instance(self) -> None:
        ev = Evaluator(registry={"double": lambda n: n * 2})
        before = {slot: getattr(ev, slot) for slot in Evaluator.__slots__}
        ev.evaluate("double(2) + a", {"a": 1})
        after = {slot: getattr(ev, slot) for slot in Evaluator.__slots__}
        assert before == after

    def test_slots_prevent_attributes_being_added(self) -> None:
        ev = Evaluator()
        with pytest.raises(AttributeError):
            ev.surprise = 1  # type: ignore[attr-defined]

    def test_one_evaluator_is_safe_across_threads(self) -> None:
        ev = Evaluator(registry={"double": lambda n: n * 2})
        results: list[Any] = []
        errors: list[BaseException] = []

        def work(n: int) -> None:
            try:
                for _ in range(50):
                    results.append(ev.evaluate("double(n) + 1", {"n": n}))
            except BaseException as exc:  # pragma: no cover - only on a real failure
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert len(results) == 400
        assert set(results) == {2 * i + 1 for i in range(8)}

    def test_context_is_not_mutated(self) -> None:
        context = {"a": 1, "d": {"k": 2}}
        snapshot = {"a": 1, "d": {"k": 2}}
        evaluate("a + d.k", context)
        assert context == snapshot


class TestStructuralPropertiesOfTheSource:
    """Acceptance criteria that are about the code rather than its behaviour."""

    def test_the_package_does_not_import_os(self) -> None:
        """simpleeval imports `os` at module scope purely to name `os.system` in a denylist,
        which leaves `os` in the sandbox module's own globals. Not having it is better."""
        offenders = []
        for path in SRC_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    offenders += [(path.name, a.name) for a in node.names if a.name == "os"]
                elif isinstance(node, ast.ImportFrom) and node.module == "os":
                    offenders.append((path.name, "os"))
        assert not offenders, f"the package imports os: {offenders}"

    def test_getattr_on_user_data_happens_in_exactly_one_place(self) -> None:
        """The opt-in registered-type path, and nowhere else.

        `getattr(node, "lineno", None)` on AST nodes is not user data and is excluded by looking
        only for the single-argument form the attribute path uses.
        """
        source = (SRC_DIR / "_eval.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        two_arg = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) == 2
        ]
        assert len(two_arg) == 1, f"expected one getattr on user data, found {len(two_arg)}"

    def test_there_is_no_denylist(self) -> None:
        """Principle 1: allowlist, never denylist. asteval's escape count against simpleeval's is
        largely this difference, and simpleeval still carries three denylists internally."""
        for path in SRC_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in ("DISALLOW", "FORBIDDEN", "BLOCKLIST", "DENYLIST"):
                assert banned not in text, f"{path.name} contains a {banned} list"


class TestFieldAccessWithoutAWrapper:
    """The design proposed wrapping dicts in a `DotDict` so `user.plan` works.

    This evaluator does the lookup directly in `_attribute` instead, and gets the same behaviour
    with none of the wrapper's costs. These tests pin the properties the wrapper would have had
    to earn: that access is equivalent, that nothing is copied on the way in, and that a cyclic
    context is harmless.
    """

    def test_dot_and_subscript_are_equivalent(self) -> None:
        context = {"user": {"plan": "pro"}}
        assert evaluate("user.plan", context) == evaluate('user["plan"]', context)

    def test_nested_access_works_at_any_depth(self) -> None:
        assert evaluate("a.b.c.d", {"a": {"b": {"c": {"d": 42}}}}) == 42

    def test_a_large_context_costs_nothing_at_entry(self) -> None:
        """**The reason not to wrap eagerly.** A wrapper that converts the context on the way in
        is O(size of context) per evaluation, regardless of what the expression touches. Here a
        100k-element list is never walked, because nothing asked for it.
        """
        context = {"big": list(range(100_000)), "flag": True}

        def two_hundred_evaluations() -> None:
            for _ in range(200):
                assert evaluate("flag", context) is True

        # The shortest of five, not a single sample. Interference only ever adds time, so the
        # minimum is the closest thing to the operation's own cost and cannot be inflated by a
        # busy machine. The claim is about whether the context is copied, and copying a
        # 100,000-element list would show in the minimum too.
        best = float("inf")
        for _ in range(5):
            started = time.perf_counter()
            two_hundred_evaluations()
            best = min(best, time.perf_counter() - started)
        assert best < 0.5, f"200 evaluations took {best:.3f}s; is something copying `big`?"

    def test_a_self_referential_context_is_harmless(self) -> None:
        """A wrapper that recursed on the way in would hang here."""
        node: dict[str, Any] = {"name": "root"}
        node["self"] = node
        assert evaluate("d.self.self.self.name", {"d": node}) == "root"

    def test_the_context_object_itself_is_returned_not_a_copy(self) -> None:
        """No wrapper means the value a caller put in is the value an expression sees."""
        inner = {"k": 1}
        assert evaluate("d.inner", {"d": {"inner": inner}}) is inner

    def test_non_string_keys_are_reachable_by_subscript(self) -> None:
        """Dot access cannot spell them, but subscript can, so they are not lost."""
        assert evaluate("d[1]", {"d": {1: "one"}}) == "one"


class TestSequenceRepetitionIsBounded:
    """R7 lists a string length cap among the deterministic bounds, and it had never been built.

    The step budget cannot see this hole, because the budget counts *nodes evaluated* and
    `"a" * 5000000` is three nodes. The `**` cap does not cover it either: that guards the width
    of an integer result, and repetition produces a sequence.
    """

    @pytest.mark.parametrize(
        "source",
        [
            '"a" * 5000000',
            '5000000 * "a"',
            "[0] * 5000000",
            "(1, 2) * 3000000",
            'b"a" * 5000000',
        ],
    )
    def test_a_repetition_over_the_cap_is_refused(self, source: str) -> None:
        with pytest.raises(EvaluationError) as caught:
            evaluate(source, {})
        assert "over the limit" in str(caught.value)

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ('"ab" * 3', "ababab"),
            ('3 * "ab"', "ababab"),
            ("[1, 2] * 2", [1, 2, 1, 2]),
            ('"a" * 0', ""),
            ('"a" * -1', ""),
            ("3 * 4", 12),
            ("2.5 * 2", 5.0),
        ],
    )
    def test_ordinary_multiplication_and_repetition_are_untouched(
        self, source: str, expected: Any
    ) -> None:
        assert evaluate(source, {}) == expected

    def test_the_cap_is_on_the_predicted_size_not_the_allocated_one(self) -> None:
        """An error raised after the allocation has already cost the allocation, which is the
        whole thing being prevented. Asserted by making the multiplication itself explode if it
        is reached at all."""

        class Counted(list):  # type: ignore[type-arg]
            def __mul__(self, other: object) -> Any:  # pragma: no cover - must not be called
                raise AssertionError("the repetition was performed before the cap was checked")

        with pytest.raises(EvaluationError) as caught:
            evaluate("x * n", {"x": Counted([0]), "n": MAX_RESULT_SIZE + 1})
        assert "over the limit" in str(caught.value)

    def test_the_cap_holds_at_the_boundary(self) -> None:
        assert len(evaluate("x * n", {"x": "a", "n": MAX_RESULT_SIZE})) == MAX_RESULT_SIZE
        with pytest.raises(EvaluationError):
            evaluate("x * n", {"x": "a", "n": MAX_RESULT_SIZE + 1})


class TestRegressions:
    def test_regression_repetition_a_short_expression_could_allocate_without_limit(self) -> None:
        """Fifteen characters of expression allocated five megabytes, and the constant was free
        to be larger.

        Measured against this evaluator before the guard existed: `"a" * 5000000` produced a
        five-million-character string and `[0] * 5000000` a five-million-item list, neither
        bounded by anything. The source cap bounds the *expression*, the step budget bounds the
        *nodes evaluated*, and the power cap bounds the width of an integer; none of the three
        looks at the size of a sequence.
        """
        for source in ('"a" * 5000000', "[0] * 5000000"):
            with pytest.raises(EvaluationError) as caught:
                evaluate(source, {})
            assert "over the limit" in str(caught.value)

    def test_regression_repetition_nesting_it_does_not_get_around_the_cap(self) -> None:
        """Each repetition is checked on its own, so the inner one is refused before the outer
        one can multiply it."""
        with pytest.raises(EvaluationError):
            evaluate('("a" * 100000) * 100000', {})


class TestTheMappingFastPath:
    """The concrete type is tested before the ABC, and the order is load-bearing.

    `collections.abc.Mapping.__instancecheck__` is Python-level and dispatches into
    `_abc._abc_instancecheck`; `isinstance(d, dict)` is a C type check. `isinstance` walks a
    tuple left to right and stops at the first hit, so `(dict, Mapping)` answers a plain dict
    without ever entering the ABC and `(Mapping, dict)` does not. Both spellings behave
    identically, which is exactly why a test has to read the line: reversing it would give back
    the whole measured gain with nothing failing.
    """

    @staticmethod
    def _guard() -> ast.expr:
        """The first `isinstance` test inside `Evaluator._attribute`, read off the source."""
        tree = ast.parse((SRC_DIR / "_eval.py").read_text(encoding="utf-8"))
        handler = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_attribute"
        )
        return next(
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
        )

    def test_the_concrete_type_is_tested_before_the_abc(self) -> None:
        call = self._guard()
        assert len(call.args) == 2
        classes = call.args[1]
        assert isinstance(classes, ast.Tuple), (
            "the mapping guard in `_attribute` is no longer a tuple of classes. The point of the "
            "tuple is that `isinstance` short-circuits on the first entry, so a plain dict never "
            "reaches `Mapping.__instancecheck__`."
        )
        names = [entry.id for entry in classes.elts if isinstance(entry, ast.Name)]
        assert names == ["dict", "Mapping"], (
            f"the mapping guard checks {names}, not ['dict', 'Mapping']. `isinstance` stops at "
            f"the first match, so putting the ABC first pays 99.9ns per attribute per item where "
            f"the concrete check costs 27.4ns. Measured at 7 to 11% on the collections tier; see "
            f"tests/benchmarks/test_attribute_path_bench.py."
        )

    @staticmethod
    def _abc_checks(context: dict[str, Any], source: str) -> int:
        """How many times the ABC half of the guard was consulted during one evaluation.

        The counter replaces `_eval`'s module-level `Mapping`, which is the name the guard reads.
        Counting rather than timing is deliberate, and it is the same move `tests/test_lazy.py`
        makes one level down: a threshold drifts and gets raised, a call count does not. The
        whole optimisation is "a plain dict never reaches the ABC", and that is a number rather
        than a duration.
        """

        class Probe:
            checks = 0

            def __instancecheck__(self, other: object) -> bool:
                type(self).checks += 1
                return isinstance(other, Mapping)

        probe = Probe()
        original = eval_module.Mapping
        eval_module.Mapping = probe  # type: ignore[misc]
        try:
            Evaluator(registry=standard_registry()).evaluate(source, context)
        finally:
            eval_module.Mapping = original  # type: ignore[misc]
        return Probe.checks

    def test_a_plain_dict_never_reaches_the_abc(self) -> None:
        """The optimisation, as a count rather than as a duration."""
        rows = [{"name": f"row-{n}"} for n in range(200)]
        assert self._abc_checks({"rows": rows}, "rows | map(_.name)") == 0

    def test_a_mapping_that_is_not_a_dict_still_reaches_the_abc(self) -> None:
        """The other side of the same count: without this, the test above would pass on a guard
        that had dropped the ABC half entirely, which is the exact simplification the regression
        battery below exists to catch."""
        rows = [collections.ChainMap({"name": f"row-{n}"}) for n in range(200)]
        assert self._abc_checks({"rows": rows}, "rows | map(_.name)") == 200

    def test_the_reader_would_notice_the_wrong_order(self) -> None:
        """The test above is only worth what it would catch, so this is what it catches."""
        reversed_source = "def _attribute(self, node, run):\n    isinstance(v, (Mapping, dict))\n"
        call = next(
            node
            for node in ast.walk(ast.parse(reversed_source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
        )
        classes = call.args[1]
        assert isinstance(classes, ast.Tuple)
        assert [entry.id for entry in classes.elts if isinstance(entry, ast.Name)] != [
            "dict",
            "Mapping",
        ]


class TestRegressionsAttributeFastPath:
    """Every mapping kind a host might realistically pass still reads its keys.

    The failure mode this battery exists for is somebody simplifying the guard down to its
    concrete half. That is silent and data-dependent: every `dict` in every test would keep
    working and a `ChainMap` in production would stop.
    """

    @staticmethod
    def _mappings(payload: dict[str, Any]) -> dict[str, Any]:
        """One of each mapping kind, all holding the same pairs."""
        return {
            "dict": dict(payload),
            "OrderedDict": collections.OrderedDict(payload),
            "Counter": collections.Counter(payload),
            "defaultdict": collections.defaultdict(lambda: None, payload),
            "ChainMap": collections.ChainMap(dict(payload)),
            "MappingProxyType": types.MappingProxyType(dict(payload)),
            "custom": _CustomMapping(payload),
        }

    @pytest.mark.parametrize("kind", ["ChainMap", "MappingProxyType", "custom"])
    def test_regression_attribute_a_non_dict_mapping_still_reads_its_keys(self, kind: str) -> None:
        """The test that catches the simplification.

        None of these is a `dict` or a subclass of one, so all three reach the mapping branch
        only through the ABC half of the guard.
        """
        value = self._mappings({"plan": "pro"})[kind]
        assert not isinstance(value, dict), f"{kind} is a dict; it would not test the ABC half"
        assert evaluate("u.plan", {"u": value}) == "pro"

    @pytest.mark.parametrize("kind", ["OrderedDict", "Counter", "defaultdict"])
    def test_regression_attribute_a_dict_subclass_reads_its_keys(self, kind: str) -> None:
        """Dict subclasses take the concrete half, because `isinstance` accepts them.

        Recorded rather than assumed. The fast path was first written as `type(value) is dict`,
        which would have sent these three through the ABC instead. Interleaved medians put the
        two spellings inside each other's spread on plain dicts and 7 to 11% apart on rows of
        `OrderedDict`, so the wider test shipped. Either way the behaviour here is identical,
        which is what this pins.
        """
        value: Any = self._mappings({"plan": 3})[kind]
        assert isinstance(value, dict)
        assert evaluate("u.plan", {"u": value}) == 3

    @pytest.mark.parametrize("kind", ["dict", "custom"])
    def test_regression_attribute_a_missing_field_reports_the_same_message_on_both_paths(
        self, kind: str
    ) -> None:
        """Same wording, same `_suggest` did-you-mean text, whichever half of the guard hit."""
        value = self._mappings({"plan": "pro", "region": "eu"})[kind]
        with pytest.raises(EvaluationError) as caught:
            evaluate("u.plann", {"u": value})
        message = str(caught.value)
        assert "no field `plann`" in message
        assert "did you mean `plan`?" in message

    @pytest.mark.parametrize("kind", ["dict", "ChainMap", "custom"])
    def test_regression_attribute_a_mapping_key_still_wins_over_a_method(self, kind: str) -> None:
        """`d.items` is the key "items", never the method, on both halves of the guard.

        The comment above the branch says falling through to `getattr` is how a sandbox exposes
        `.keys` and `.__class__` on data the host thought was inert. This is that comment as an
        assertion.
        """
        value = self._mappings({"items": [1, 2]})[kind]
        assert evaluate("d.items", {"d": value}) == [1, 2]
        with pytest.raises(EvaluationError) as caught:
            evaluate("d.keys", {"d": value})
        assert "no field `keys`" in str(caught.value)

    @pytest.mark.parametrize("kind", ["dict", "ChainMap", "custom"])
    def test_regression_attribute_a_dunder_is_still_blocked_on_both_paths(self, kind: str) -> None:
        """Refused before `_attribute` runs at all, so the guard's shape cannot affect it.

        Asserted per mapping kind anyway: "the validator catches it" is the claim, and a claim
        that only holds for one concrete type is not the claim.
        """
        value = self._mappings({"plan": "pro"})[kind]
        with pytest.raises(SafeExprError) as caught:
            evaluate("u.__class__", {"u": value})
        assert "attributes beginning with an underscore are blocked" in str(caught.value)

    def test_regression_attribute_a_non_mapping_still_needs_registration(self) -> None:
        """The `else` half of the guard, unchanged: no mapping, no access without an opt-in."""

        class Point:
            def __init__(self) -> None:
                self.x = 3

        with pytest.raises(EvaluationError) as caught:
            evaluate("p.x", {"p": Point()})
        assert "registered" in str(caught.value)
        opened = Evaluator(attribute_types={Point: frozenset({"x"})})
        assert opened.evaluate("p.x", {"p": Point()}) == 3

    def test_regression_attribute_a_mapping_subclass_cannot_reach_getattr(self) -> None:
        """A mapping wins the branch outright, so a real attribute never shadows a missing key.

        If the guard were ever reordered so that a mapping could fall through, this is the shape
        that would start answering with the object instead of the data.
        """
        value = _CustomMapping({"plan": "pro"})
        assert value.api_key == "sk-live-must-not-become-reachable"
        with pytest.raises(EvaluationError) as caught:
            evaluate("u.api_key", {"u": value})
        assert "no field `api_key`" in str(caught.value)
        assert "sk-live" not in str(caught.value)
