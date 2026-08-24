"""The escape corpus: every published escape class, proven unreachable here.

This is the artifact a security reviewer asks for, and the part a competitor cannot shortcut. It
is a versioned data file rather than a pile of test functions so that each entry carries its
provenance, and so that a reviewer can read the claims without reading the assertions.

**What makes an entry worth anything is the stage check.** "The expression was rejected" is a
weak claim: it is also true of a typo. Every entry declares *where* it must be rejected, and for
anything expected past the parser the harness first asserts the expression is valid Python. An
entry that stops testing what it says it tests fails rather than quietly passing.

Two properties are checked on every entry rather than being entries of their own:

- **F9**: no error may carry `__cause__` or `__context__`, so exception-borne object leakage is
  checked once per entry rather than once in total.
- Every rejection must be a `SafeExprError`, so nothing reaches a caller as a bare built-in.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from safeexpr import Evaluator, SafeExprError, standard_registry
from safeexpr._errors import (
    BudgetExceededError,
    EvaluationError,
    InternalError,
    ParseError,
    SourceTooLongError,
    ValidationError,
)
from safeexpr._eval import DEFAULT_STEP_BUDGET, LazyExpr, _Run
from safeexpr._parse import MAX_SOURCE_BYTES, parse
from safeexpr._validate import validate

CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "escapes-v1.jsonl"

FAILURE_CLASSES = {f"F{n}" for n in range(1, 10)}
STAGES = {"parse", "validate", "evaluate", "allowed"}
REQUIRED = {"id", "failure_class", "source_lib", "provenance", "expression", "stage", "context"}

# Which functions an entry runs against, named by its optional `functions` field.
#
# **"none" is the default and most entries keep it**, because a registry changes what the source
# *means*: registry membership is what tells the pipe transform a `|` is a pipe. An entry that
# does not need functions must not silently acquire them, so opting in is per entry rather than
# global. The collections tier gets entries of its own because it adds a surface the validator
# structurally cannot see: a field name that arrives as a value rather than as source.
REGISTRIES: dict[str, Callable[[], dict[str, Any]]] = {
    "none": dict,
    "standard": standard_registry,
}

# Errors each stage is allowed to raise. A rejection at the right stage for the wrong reason is
# still a failed entry.
STAGE_ERRORS: dict[str, tuple[type[SafeExprError], ...]] = {
    "parse": (ParseError, SourceTooLongError),
    "validate": (ValidationError,),
    "evaluate": (EvaluationError, InternalError, BudgetExceededError),
}


class _Plain:
    """An ordinary host object. Attribute access on it must not work."""

    api_key = "sk-live-must-not-be-reachable"
    colour = "red"


class _HostileEq:
    """A context value whose `__eq__` raises something a host's `except Exception` would miss."""

    __hash__ = object.__hash__

    def __init__(self, raises: type[BaseException]) -> None:
        self._raises = raises

    def __eq__(self, other: object) -> bool:
        raise self._raises


class _Talkative:
    """A host object whose text form carries a secret. Nothing may ever ask it for one."""

    def __str__(self) -> str:  # pragma: no cover - reaching this is the failure
        return "sk-live-LEAKED-THROUGH-STR"

    def __repr__(self) -> str:  # pragma: no cover - reaching this is the failure
        return "sk-live-LEAKED-THROUGH-REPR"


def _a_lazy_expression() -> Any:
    """Build a real `LazyExpr` over a subtree, for the F8 entries to probe."""
    tree = parse("_.secret > 1")
    return LazyExpr(Evaluator(), tree.body, _Run({}, "_.secret > 1", DEFAULT_STEP_BUDGET))


def _contexts() -> dict[str, dict[str, Any]]:
    """Named contexts, because JSON cannot express a callable or a hostile object.

    Keyed by the `context` field of an entry.
    """
    return {
        "empty": {},
        "data": {
            "x": {"__class__": "REACHED", "_private": "REACHED", "plan": "pro", "items": [1, 2]},
            "d": {"__class__": "REACHED", "plan": "pro"},
            "k": "__class__",
            "keys": ["_private"],
            "n": 10,
            "a": 2,
            "z": [1, 2, 3],
            "user": {"plan": "pro", "region": "eu"},
            # For the collections tier: a row whose keys include the ones the language blocks,
            # so `pluck` has something real to fail to reach.
            "rows": [{"__class__": "REACHED", "_private": "REACHED", "plan": "pro"}],
        },
        # F4: work that multiplies. Two hundred items is nothing on its own and a hundred
        # thousand inner evaluations once nested, which is the whole point of the failure class.
        "large": {"items": list(range(200)), "other": list(range(200))},
        # F3: the callback-smuggling class. `os.system` is here deliberately: if any path from a
        # context value to call position existed, this is what would come through it.
        "callables": {
            "danger": lambda n: n,
            "system": os.system,
            "obj": _Plain(),
            # Something to pipe, so the pipe entries fail on the pipe rather than on
            # an undefined name.
            "items": [1, 2, 3],
            "x": {"k": 1},
        },
        "object": {"obj": _Plain(), "x": _Plain()},
        # F1: an object that *talks*. `_Plain` has no `__str__`, so converting one would produce
        # a bland default and prove nothing; this one puts the secret in the text it returns, so
        # an entry that reached `__str__` would show it.
        "talkative": {"obj": _Talkative()},
        "hostile_systemexit": {"x": _HostileEq(SystemExit)},
        "hostile_keyboardinterrupt": {"x": _HostileEq(KeyboardInterrupt)},
        # F8: a LazyExpr placed directly in the context, which is a stronger test than
        # the design's scenario. The side table would have made one reachable by *naming*
        # it; this hands one over outright and asserts the tree inside is still
        # unreachable.
        "lazy": {"x": _a_lazy_expression()},
    }


class CorpusError(Exception):
    """A corpus file that cannot be trusted. Raised rather than reported, so the meta-tests below
    can assert on it and a real load can turn it into a failure."""


def _check_entry(entry: dict[str, Any], number: int, contexts: dict[str, Any]) -> None:
    """Validate one entry's shape, raising `CorpusError` on anything wrong.

    Split out from `_load` so each rule reads as its own line rather than as a branch in a long
    loop, and so the meta-tests can point at a single failure per rule.
    """
    missing = REQUIRED - set(entry)
    if missing:
        raise CorpusError(f"line {number} ({entry.get('id')}) is missing {sorted(missing)}")
    if entry["stage"] not in STAGES:
        raise CorpusError(f"{entry['id']}: unknown stage {entry['stage']!r}")
    if entry["stage"] != "allowed" and entry["failure_class"] not in FAILURE_CLASSES:
        raise CorpusError(f"{entry['id']}: unknown failure class {entry['failure_class']!r}")
    # A typo here would otherwise run the attack against the wrong data and pass for free.
    if entry["context"] not in contexts:
        raise CorpusError(f"{entry['id']}: unknown context {entry['context']!r}")
    if entry.get("functions", "none") not in REGISTRIES:
        raise CorpusError(f"{entry['id']}: unknown functions {entry['functions']!r}")
    budget = entry.get("budget", DEFAULT_STEP_BUDGET)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise CorpusError(f"{entry['id']}: budget must be a positive integer, got {budget!r}")


def _load(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the corpus, failing loudly on anything malformed.

    A corpus that silently skips a broken entry is worse than no corpus: the count still looks
    right while the coverage does not.
    """
    corpus = path or CORPUS
    if not corpus.is_file():  # pragma: no cover - only if the file is deleted
        raise CorpusError(f"corpus file missing: {corpus}")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    contexts = _contexts()
    for number, line in enumerate(corpus.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - only on a malformed file
            raise CorpusError(f"corpus line {number} is not valid JSON: {exc.msg}") from None
        _check_entry(entry, number, contexts)
        if entry["id"] in seen:
            raise CorpusError(f"duplicate corpus id: {entry['id']}")
        seen.add(entry["id"])
        entries.append(entry)
    if not entries:  # pragma: no cover - only if the file is emptied
        raise CorpusError("corpus is empty")
    return entries


ENTRIES = _load()


def _applies(entry: dict[str, Any]) -> bool:
    """Whether an entry runs on this interpreter.

    Some escapes only exist where the syntax does. t-strings parse on 3.14 and are a syntax error
    before it, so the same attack is rejected at a different stage on either side of that line and
    needs two entries.
    """
    version = (sys.version_info.major, sys.version_info.minor)
    floor = entry.get("python_min")
    ceiling = entry.get("python_max")
    if floor and version < tuple(int(part) for part in floor.split(".")):
        return False
    return not (ceiling and version > tuple(int(part) for part in ceiling.split(".")))


def _run_stages(entry: dict[str, Any]) -> tuple[str | None, SafeExprError | None, Any]:
    """Run parse, validate and evaluate separately, reporting which stage rejected.

    Returns:
        (stage that raised or None, the error or None, the value if it evaluated).
    """
    source = entry["expression"]
    if entry.get("expand_to_bytes"):
        # Entries whose whole point is length cannot be written out literally.
        source = source * (MAX_SOURCE_BYTES // len(source) + 8)
    context = _contexts()[entry["context"]]
    # An entry may lower the budget. A denial-of-service entry has to *exhaust* the budget to
    # prove anything, and exhausting the six-million-step default means about a second of real
    # work per interpreter in the matrix. Lowering it tests the same barrier in milliseconds: the
    # claim is that the counter stops unbounded work, not that six million is the right number.
    evaluator = Evaluator(
        registry=REGISTRIES[entry.get("functions", "none")](),
        budget=entry.get("budget", DEFAULT_STEP_BUDGET),
    )

    try:
        tree = parse(source)
    except SafeExprError as exc:
        return "parse", exc, None
    try:
        validate(tree, source)
    except SafeExprError as exc:
        return "validate", exc, None
    try:
        return None, None, evaluator.evaluate(source, context)
    except SafeExprError as exc:
        return "evaluate", exc, None


def _identify(entry: dict[str, Any]) -> str:
    return entry["id"]


@pytest.mark.parametrize("entry", ENTRIES, ids=_identify)
def test_corpus_entry(entry: dict[str, Any]) -> None:
    if not _applies(entry):
        pytest.skip(
            f"entry applies to python_min={entry.get('python_min', '-')} "
            f"python_max={entry.get('python_max', '-')}"
        )

    expected = entry["stage"]
    stage, error, value = _run_stages(entry)

    if expected == "allowed":
        # Controls. Without these the corpus could pass by rejecting everything, which would be a
        # sandbox nobody can use rather than a sandbox that works.
        assert stage is None, f"control entry was rejected at {stage}: {error}"
        if "expect_value" in entry:
            assert value == entry["expect_value"]
        return

    assert stage is not None, (
        f"NOT REJECTED. {entry['expression']!r} evaluated to {value!r}. "
        f"This entry claims {entry['failure_class']} is unreachable and it is reachable."
    )
    assert stage == expected, (
        f"rejected at the wrong stage: expected {expected}, got {stage} ({error}). "
        f"An entry rejected earlier than it claims may no longer be testing what it says."
    )
    assert isinstance(error, STAGE_ERRORS[expected]), (
        f"stage {expected} raised {type(error).__name__}, "
        f"expected one of {[e.__name__ for e in STAGE_ERRORS[expected]]}"
    )
    if "expect_message" in entry:
        assert entry["expect_message"] in str(error), (
            f"rejected for the wrong reason. Expected a message containing "
            f"{entry['expect_message']!r}, got: {error}"
        )

    # F9, checked on every entry rather than as an entry of its own.
    assert error is not None
    assert error.__cause__ is None, f"{entry['id']} leaks a cause"
    assert error.__context__ is None, f"{entry['id']} leaks a context exception"


@pytest.mark.parametrize("entry", ENTRIES, ids=_identify)
def test_the_expression_is_real_python(entry: dict[str, Any]) -> None:
    """The positive control that stops a typo from looking like a defence.

    An entry claiming rejection at validate or evaluate must be syntactically valid Python. If
    somebody mistypes it into a syntax error it would still be "rejected", and the corpus would
    keep reporting a pass while testing nothing.

    Entries that expect rejection *at* the parser are exempt, since being invalid is their point.
    """
    if entry["stage"] == "parse" or not _applies(entry):
        pytest.skip("entry is about the parser itself, or does not apply to this interpreter")
    ast.parse(entry["expression"], mode="eval")


class TestTheCorpusItself:
    def test_every_failure_class_is_covered(self) -> None:
        """T10: one entry per row of the failure-mode catalog. A class with no entry is a claim
        with no test behind it."""
        covered = {e["failure_class"] for e in ENTRIES if e["stage"] != "allowed"}
        missing = FAILURE_CLASSES - covered
        assert not missing, f"failure classes with no corpus entry: {sorted(missing)}"

    def test_there_are_controls(self) -> None:
        """A corpus of nothing but rejections would pass against a sandbox that rejects
        everything."""
        controls = [e for e in ENTRIES if e["stage"] == "allowed"]
        assert len(controls) >= 4, "too few controls to show the language still works"

    def test_every_entry_cites_a_source(self) -> None:
        for entry in ENTRIES:
            if entry["stage"] == "allowed":
                continue
            assert entry["provenance"].strip(), f"{entry['id']} has no provenance"
            assert entry["source_lib"].strip(), f"{entry['id']} names no source library"

    def test_entries_are_tagged_by_source_library(self) -> None:
        """So a reviewer can ask "show me asteval's escapes" and get an answer."""
        libraries = {e["source_lib"] for e in ENTRIES if e["stage"] != "allowed"}
        for expected in ("simpleeval", "asteval", "RestrictedPython", "expr-lang"):
            assert expected in libraries, f"no entry attributed to {expected}"

    def test_the_corpus_is_not_trivially_small(self) -> None:
        assert len(ENTRIES) >= 35, f"corpus has only {len(ENTRIES)} entries"


class TestTheHarnessFailsLoudly:
    """A corpus runner that cannot fail is decoration.

    Each of these breaks the corpus in a way that a careless edit realistically would, and
    asserts the runner notices. Without them, "114 passed" is a number with no meaning behind it.
    """

    def _write(self, tmp_path: Path, *lines: str) -> Path:
        path = tmp_path / "corpus.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_malformed_json_is_reported_not_skipped(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "{not json")
        with pytest.raises(CorpusError, match="not valid JSON"):
            _load(path)

    def test_a_missing_field_is_reported(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, json.dumps({"id": "x", "expression": "1"}))
        with pytest.raises(CorpusError, match="is missing"):
            _load(path)

    def test_an_unknown_stage_is_reported(self, tmp_path: Path) -> None:
        entry = {
            "id": "x",
            "failure_class": "F1",
            "source_lib": "l",
            "provenance": "p",
            "expression": "1",
            "stage": "somewhere",
            "context": "empty",
        }
        path = self._write(tmp_path, json.dumps(entry))
        with pytest.raises(CorpusError, match="unknown stage"):
            _load(path)

    def test_an_unknown_failure_class_is_reported(self, tmp_path: Path) -> None:
        entry = {
            "id": "x",
            "failure_class": "F99",
            "source_lib": "l",
            "provenance": "p",
            "expression": "1",
            "stage": "validate",
            "context": "empty",
        }
        path = self._write(tmp_path, json.dumps(entry))
        with pytest.raises(CorpusError, match="unknown failure class"):
            _load(path)

    def test_an_unknown_context_is_reported(self, tmp_path: Path) -> None:
        """A typo in the context name would otherwise run the attack against `{}` and pass."""
        entry = {
            "id": "x",
            "failure_class": "F1",
            "source_lib": "l",
            "provenance": "p",
            "expression": "1",
            "stage": "validate",
            "context": "typo",
        }
        path = self._write(tmp_path, json.dumps(entry))
        with pytest.raises(CorpusError, match="unknown context"):
            _load(path)

    def test_a_duplicate_id_is_reported(self, tmp_path: Path) -> None:
        entry = {
            "id": "same",
            "failure_class": "F1",
            "source_lib": "l",
            "provenance": "p",
            "expression": "1",
            "stage": "validate",
            "context": "empty",
        }
        path = self._write(tmp_path, json.dumps(entry), json.dumps(entry))
        with pytest.raises(CorpusError, match="duplicate"):
            _load(path)

    def test_an_empty_corpus_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "corpus.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(CorpusError, match="empty"):
            _load(path)

    def test_an_entry_rejected_at_the_wrong_stage_fails(self) -> None:
        """The check that gives the corpus its value. `x.__class__` is rejected at validate; an
        entry claiming the parser catches it is testing something that is not true."""
        entry = {
            "id": "wrong-stage",
            "failure_class": "F2",
            "source_lib": "l",
            "provenance": "p",
            "expression": "x.__class__",
            "stage": "parse",
            "context": "data",
        }
        with pytest.raises(AssertionError, match="wrong stage"):
            test_corpus_entry(entry)

    def test_an_entry_that_is_not_rejected_at_all_fails(self) -> None:
        entry = {
            "id": "not-rejected",
            "failure_class": "F2",
            "source_lib": "l",
            "provenance": "p",
            "expression": "1 + 1",
            "stage": "validate",
            "context": "empty",
        }
        with pytest.raises(AssertionError, match="NOT REJECTED"):
            test_corpus_entry(entry)

    def test_an_entry_rejected_for_the_wrong_reason_fails(self) -> None:
        """Right stage, wrong cause. `lambda: 1` is a ValidationError, but not an underscore one,
        and an entry claiming the private-name rule stopped it would be miscrediting the defence.
        """
        entry = {
            "id": "wrong-reason",
            "failure_class": "F2",
            "source_lib": "l",
            "provenance": "p",
            "expression": "lambda: 1",
            "stage": "validate",
            "context": "empty",
            "expect_message": "underscore",
        }
        with pytest.raises(AssertionError, match="wrong reason"):
            test_corpus_entry(entry)

    def test_a_control_that_stops_working_fails(self) -> None:
        entry = {
            "id": "broken-control",
            "failure_class": None,
            "source_lib": "",
            "provenance": "",
            "expression": "x.__class__",
            "stage": "allowed",
            "context": "data",
        }
        with pytest.raises(AssertionError, match="control entry was rejected"):
            test_corpus_entry(entry)

    def test_a_typo_that_becomes_invalid_syntax_is_caught(self) -> None:
        """The positive control. An entry mistyped into a syntax error would still be "rejected",
        and would keep reporting a pass while testing nothing."""
        entry = {
            "id": "typo",
            "failure_class": "F2",
            "source_lib": "l",
            "provenance": "p",
            "expression": "x.__class__ ==",
            "stage": "validate",
            "context": "data",
        }
        with pytest.raises(SyntaxError):
            test_the_expression_is_real_python(entry)
