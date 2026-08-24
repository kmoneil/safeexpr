"""One `Evaluator`, many threads, and the contract that makes that safe.

The decision (Q10) is **immutable after construction**. It costs nothing when it is taken early
and is expensive to retrofit, and the step budget is why it had to be taken at all: a counter
living on the instance would make a shared evaluator quietly wrong under concurrency, with two
threads spending each other's budget and neither of them failing in a way that names the cause.

So the budget, the `_` scope stack and everything else an evaluation needs live in a call-scoped
`_Run`. Nothing an evaluation touches is reachable from the evaluator, which is what these tests
assert: structurally first, by reading the source for module-level state that anything mutates,
and then by running the thing concurrently and checking the answers.
"""

from __future__ import annotations

import ast
import itertools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from safeexpr import BudgetExceededError, Evaluator, standard_registry
from safeexpr._regex import _CACHE, _MAX_CACHE
from safeexpr._registry import Function

SRC = Path(__file__).resolve().parent.parent / "src" / "safeexpr"

# Module-level state that something in the package writes to after import, with the reason it is
# allowed to exist. Anything not named here fails the scan below.
#
# **The cache is the only entry and it is a memoisation cache**, which is the one kind of shared
# state that does not make a contract dishonest: compiling a pattern is a pure function of the
# pattern string, so a cache hit and a cache miss produce the same object graph, differ only in
# time, and cannot carry anything between the evaluations that share them. `re.Pattern` objects
# are themselves safe to use from several threads.
#
# The sequence "if full, clear, then insert" is not atomic, so two threads can race and lose an
# entry. That costs a recompile and nothing else, which is why it is not worth a lock: a lock here
# would be contended by every `matches` call in the process to prevent an outcome indistinguishable
# from a cold cache.
ALLOWED_MODULE_STATE = {
    "_regex.py": {"_CACHE": "a bounded memoisation cache; see TestTheCacheIsNotObservable"},
}

_MUTATING_METHODS = frozenset(
    {"append", "extend", "insert", "remove", "pop", "clear", "update", "setdefault", "add",
     "discard", "sort", "popitem"}
)  # fmt: skip


def _module_level_containers(tree: ast.Module) -> dict[str, int]:
    """Names bound at module level to something that can be mutated in place."""
    found: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
            continue
        mutable = isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp)) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"dict", "list", "set"}
        )
        if mutable:
            found.update(
                {target.id: node.lineno for target in targets if isinstance(target, ast.Name)}
            )
    return found


def _written_after_import(tree: ast.Module, names: dict[str, int]) -> dict[str, list[str]]:
    """Which of `names` something writes to, and where."""
    written: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id in names
        ):
            written.setdefault(node.value.id, []).append(f"line {node.lineno}: item assignment")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
            and node.func.attr in _MUTATING_METHODS
        ):
            written.setdefault(node.func.value.id, []).append(
                f"line {node.lineno}: .{node.func.attr}()"
            )
    return written


def _shared() -> Evaluator:
    return Evaluator(registry=standard_registry())


class TestNoMutableModuleLevelState:
    """No mutable module-level state, read off the source rather than reasoned about.

    A tier that grows a module-level counter, memo or registry is the way this contract stops
    being true, and it would not look like a threading change when it was written.
    """

    def test_nothing_writes_to_module_state_except_what_is_argued_for(self) -> None:
        offenders: list[str] = []
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            written = _written_after_import(tree, _module_level_containers(tree))
            allowed = ALLOWED_MODULE_STATE.get(path.name, {})
            offenders += [
                f"{path.name}:{name} ({', '.join(where)})"
                for name, where in written.items()
                if name not in allowed
            ]
        assert offenders == [], (
            f"module-level state written after import: {offenders}. One `Evaluator` is documented "
            f"as safe to share between threads, and state at module scope is shared by every "
            f"evaluator in the process. Move it into `_Run`, or add it to ALLOWED_MODULE_STATE "
            f"with the argument for why it is safe."
        )

    def test_the_allowlist_names_only_things_that_still_exist(self) -> None:
        """An allowlist outliving the thing it excuses is how the next one gets waved through."""
        for filename, names in ALLOWED_MODULE_STATE.items():
            tree = ast.parse((SRC / filename).read_text(encoding="utf-8"))
            written = _written_after_import(tree, _module_level_containers(tree))
            for name in names:
                assert name in written, f"{filename}:{name} is allowlisted and nothing writes it"

    def test_the_scan_sees_the_module_state_that_does_exist(self) -> None:
        """A scan matching nothing would pass the test above while checking nothing."""
        tree = ast.parse((SRC / "_eval.py").read_text(encoding="utf-8"))
        assert "_BUILTINS" in _module_level_containers(tree)

    def test_the_scan_catches_a_write(self) -> None:
        source = "CACHE = {}\ndef f(k, v):\n    CACHE[k] = v\n    CACHE.clear()\n"
        tree = ast.parse(source)
        written = _written_after_import(tree, _module_level_containers(tree))
        assert "CACHE" in written
        assert len(written["CACHE"]) == 2


class TestTheEvaluatorIsImmutableAfterConstruction:
    def test_it_has_no_instance_dictionary(self) -> None:
        """`__slots__` is what makes "nothing is added later" a property of the type rather than
        a habit of the code."""
        assert not hasattr(_shared(), "__dict__")

    def test_the_registry_is_copied_rather_than_held(self) -> None:
        """A host that keeps the dict it passed in must not be able to change what an evaluator
        can call, least of all from another thread."""
        registry = standard_registry()
        evaluator = Evaluator(registry=registry)
        registry["smuggled"] = Function("smuggled", lambda: 1)
        assert "smuggled" not in evaluator.function_names

    def test_the_attribute_types_are_copied_rather_than_held(self) -> None:
        opened: dict[type, frozenset[str]] = {}
        evaluator = Evaluator(attribute_types=opened)

        class Host:
            api_key = "sk-live-must-not-become-reachable"

        opened[Host] = frozenset({"api_key"})
        with pytest.raises(Exception, match="attribute access works on mappings"):
            evaluator.evaluate("h.api_key", {"h": Host()})

    def test_evaluating_changes_nothing_the_evaluator_carries(self) -> None:
        evaluator = _shared()
        before = (evaluator.budget, evaluator.function_names)
        for _ in range(50):
            evaluator.evaluate("map(items, _ + 1)", {"items": [1, 2, 3]})
        assert (evaluator.budget, evaluator.function_names) == before


class TestConcurrentEvaluation:
    """Threads sharing one evaluator, and the answers checked."""

    THREADS = 8
    ROUNDS = 60

    def test_results_are_correct_under_contention(self) -> None:
        evaluator = _shared()

        def work(worker: int) -> list[int]:
            return [
                evaluator.evaluate(
                    "sum(map(where(items, _ > n), _ * 2))",
                    {"items": list(range(worker + 5)), "n": worker},
                )
                for _ in range(self.ROUNDS)
            ]

        expected = {
            worker: sum(value * 2 for value in range(worker + 5) if value > worker)
            for worker in range(self.THREADS)
        }
        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            for worker, results in enumerate(pool.map(work, range(self.THREADS))):
                assert set(results) == {expected[worker]}, f"worker {worker} saw {set(results)}"

    def test_the_underscore_stack_does_not_leak_between_threads(self) -> None:
        """The scope stack is the other piece of per-evaluation state, and `_2` reaching outward
        is exactly the thing that would go wrong if it lived on the instance."""
        evaluator = _shared()
        source = "map(outer, sum(map(inner, _ + _2)))"

        def work(base: int) -> set[int]:
            outer, inner = [base], [1, 2, 3]
            return {
                evaluator.evaluate(source, {"outer": outer, "inner": inner})[0]
                for _ in range(self.ROUNDS)
            }

        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            for base, seen in enumerate(pool.map(work, range(self.THREADS))):
                assert seen == {sum(item + base for item in (1, 2, 3))}

    def test_one_thread_exhausting_the_budget_does_not_starve_the_others(self) -> None:
        """The test the contract exists for.

        If the counter lived on the evaluator, the heavy worker below would spend the light
        workers' budget and they would start failing, intermittently, with an error naming the
        budget rather than the sharing. Every light evaluation must succeed, every heavy one must
        refuse, and the two must not affect each other.
        """
        evaluator = Evaluator(registry=standard_registry(), budget=4_000)
        heavy = {"items": list(range(5_000))}

        def light(_worker: int) -> int:
            return sum(evaluator.evaluate("1 + 1", {}) for _ in range(self.ROUNDS))

        def burn(_worker: int) -> int:
            refused = 0
            for _ in range(self.ROUNDS):
                with pytest.raises(BudgetExceededError):
                    evaluator.evaluate("sum(map(items, _ + 1))", heavy)
                refused += 1
            return refused

        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            light_results = list(pool.map(light, range(self.THREADS // 2)))
            burn_results = list(pool.map(burn, range(self.THREADS // 2)))
        assert light_results == [2 * self.ROUNDS] * (self.THREADS // 2)
        assert burn_results == [self.ROUNDS] * (self.THREADS // 2)

    def test_the_pattern_cache_survives_contention(self) -> None:
        """Distinct patterns per thread, more of them than the cache holds, so the clear-then-
        insert sequence really is raced. Losing an entry costs a recompile; losing an answer
        would be a defect."""
        evaluator = _shared()

        def work(worker: int) -> bool:
            return all(
                evaluator.evaluate(
                    "matches(x, p)", {"x": "a" * (round_ % 5 + 1), "p": f"^a{{1,{round_ + 1}}}$"}
                )
                is not None
                for round_ in range(self.ROUNDS)
            )

        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            assert all(pool.map(work, range(self.THREADS)))

    def test_errors_from_one_thread_do_not_reach_another(self) -> None:
        """Errors are built per call, so a refusal in one thread must not appear in another's
        result or carry another's data."""
        evaluator = _shared()
        secrets = {worker: f"sk-live-worker-{worker}" for worker in range(self.THREADS)}

        def work(worker: int) -> set[str]:
            seen = set()
            for _ in range(self.ROUNDS):
                try:
                    evaluator.evaluate("len(x)", {"x": secrets[worker], "n": 1})
                    evaluator.evaluate("x.missing", {"x": secrets[worker]})
                except Exception as error:
                    seen.add(str(error))
            return seen

        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            for worker, messages in enumerate(pool.map(work, range(self.THREADS))):
                joined = " ".join(messages)
                for other, secret in secrets.items():
                    if other != worker:
                        assert secret not in joined
                assert secrets[worker] not in joined


class TestTheCacheIsNotObservable:
    """The one piece of shared state, and why it does not make the contract dishonest.

    A shared cache normally raises a fair question: can one evaluation learn something about
    another's history from it? Here the answer is no, and not by argument. **`matches` is charged
    its declared cost whether the pattern was compiled or fetched**, so the budget reads the same
    either way, and the language has no clock, so the only thing that differs is wall time which
    nothing inside an expression can see.
    """

    @staticmethod
    def _steps(source: str, context: dict[str, object]) -> int:
        """The smallest budget that still evaluates, found by bisection."""
        low, high = 1, 100_000
        while low < high:
            middle = (low + high) // 2
            try:
                Evaluator(registry=standard_registry(), budget=middle).evaluate(source, context)
            except BudgetExceededError:
                low = middle + 1
            else:
                high = middle
        return low

    def test_a_cold_cache_and_a_warm_one_cost_the_same(self) -> None:
        source, context = 'matches(x, "^a+b$")', {"x": "aaab"}
        _CACHE.clear()
        cold = self._steps(source, context)
        assert "^a+b$" in _CACHE, "the warm measurement below would prove nothing"
        assert self._steps(source, context) == cold

    def test_the_cache_stays_bounded(self) -> None:
        evaluator = _shared()
        for index in itertools.islice(itertools.count(), _MAX_CACHE + 50):
            evaluator.evaluate("matches(x, p)", {"x": "a", "p": f"^a{{1,{index + 1}}}$"})
        assert len(_CACHE) <= _MAX_CACHE


class TestTheContractIsDocumented:
    """An API commitment nobody can read is not a commitment."""

    def test_the_readme_states_it(self) -> None:
        readme = (SRC.parent.parent / "README.md").read_text(encoding="utf-8")
        assert "Thread safety" in readme
        assert "immutable after construction" in " ".join(readme.split())
