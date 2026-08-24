#!/usr/bin/env python
"""Fuzz the evaluator with an audit hook watching, and fail if anything fires.

    python scripts/audit_fuzz.py                 # the default run
    python scripts/audit_fuzz.py --seed 7        # reproducible, any seed
    python scripts/audit_fuzz.py --rounds 20000  # a longer run

**Why an audit hook rather than more assertions.** Every other test here checks something
somebody thought of: the corpus lists escapes that have been published, the differential
generator compares against a subset somebody wrote down. `sys.addaudithook` (PEP 578) is the only
mechanism that watches for things nobody thought of. It observes `exec`, `compile`, `import`,
`open`, `os.system` and the subprocess events **process-wide**, below the level any expression
could reach, so an escape trips it whether or not there is a test for that escape.

**Exactly one event is expected**, measured rather than assumed: `compile`, raised by `ast.parse`
turning the source into a tree. It is allowed only when its argument is the very source that was
passed in, so a `compile` of anything else is a finding rather than noise. Every other event is a
finding. A real escape raises `exec`, `open` or `import`, all of which are distinct.

**This is a tripwire, not a defence, and the distinction is not a technicality.** Audit hooks
observe, they do not block; they fire process-wide, so a host installing one pays for every
audited operation in its process; and once installed a hook cannot be removed, so a broken
sandbox could use one as a target rather than a shield. Nothing here ships in the package. The
README says so.

**The run reports what it reached, not only what it found.** A fuzzer whose inputs are all
rejected at the parser finds nothing and proves nothing, which is the same failure the
differential generator has when it shrinks toward atoms. The counts below are what make that
visible, and `tests/test_audit_fuzz.py` asserts floors on them.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from safeexpr import Evaluator, SafeExprError, standard_registry

CORPUS = ROOT / "corpus" / "escapes-v1.jsonl"

# Past the package's own source cap, so over-length input is exercised too rather than
# being generated forever without ever reaching the parser.
MAX_FUZZ_SOURCE = 2_500

# Shapes that have broken other sandboxes, written out so the fuzzer starts somewhere sharp
# rather than from random characters.
ESCAPE_SEEDS: tuple[str, ...] = (
    "x.__class__",
    "x.__class__.__mro__[1].__subclasses__()",
    '"{0.__class__}".format(x)',
    '"%(__class__)s" % x',
    'x["__class__"]',
    'x["__cl" + "ass__"]',
    "danger(1)",
    "x | danger",
    "__import__",
    "__builtins__",
    "open",
    "eval",
    "exec",
    "compile",
    "globals()",
    "locals()",
    "().__class__.__bases__[0].__subclasses__()",
    "[].__class__",
    "type(x)",
    "getattr(x, 'x')",
    "x.__init__.__globals__",
    "lambda: 1",
    "[y for y in x]",
    "f'{x}'",
    "x.__dict__",
    "vars(x)",
    "help",
    "breakpoint()",
    "x.__reduce__()",
    "x.__getattribute__('x')",
)

# Expressions that are *meant* to work. Without them almost everything is refused at validation
# and the hook watches an evaluator that never evaluates: the run reports zero findings because
# nothing ran, which is the same emptiness as a generator that shrinks to atoms.
BENIGN_SEEDS: tuple[str, ...] = (
    "1 + 1",
    "y * 2",
    "x",
    "items",
    "items[0]",
    "items | first",
    "items | len",
    "items | map(_)",
    "items | where(_ == 1)",
    'x.k if y else "n"',
    "y > 0 and y < 10",
    'str(y) + "!"',
    "items | sort_by(_)",
    'lower("ABC")',
    '"a,b" | split(",") | join("-")',
    'matches("abc", "^a")',
    "items | take(1)",
    'merge({"a": 1}, {"b": 2})',
    "y in items",
    "[y, y, y]",
)

WRAPPERS: tuple[str, ...] = (
    "({})",
    "1 + ({})",
    "[{}]",
    "{{'k': {}}}",
    "({}) if 1 else 2",
    "not ({})",
    "({}) and 1",
    "items | where(({}) == 1)",
    "items | map({})",
    "-({})",
    "({})[0]",
    "len({})",
)

OPERATORS: tuple[str, ...] = (" + ", " - ", " * ", " | ", " and ", " or ", " == ", " in ", ", ")


class _Probe:
    """A host object with something worth stealing on it."""

    api_key = "sk-live-must-not-be-reachable"
    colour = "red"

    def method(self) -> str:  # pragma: no cover - reaching this is the failure
        return self.api_key


def contexts() -> tuple[dict[str, Any], ...]:
    """The data the fuzzer evaluates against, chosen to give an escape something to reach."""
    return (
        {},
        {"x": {"k": 1, "__class__": "REACHED"}, "items": [1, 2, 3], "y": 2},
        {"x": _Probe(), "items": [1, 2], "y": 0},
        {"x": "text", "items": ["a"], "danger": lambda n: n, "y": 1},
        {"x": [1, [2, [3]]], "items": [{"k": 1}], "y": -1},
    )


def corpus_seeds() -> list[str]:
    """Every expression in the escape corpus."""
    if not CORPUS.is_file():  # pragma: no cover - only if the corpus is deleted
        return []
    found = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("//"):
            found.append(json.loads(stripped)["expression"])
    return found


def _drop_a_character(source: str, rng: random.Random) -> str:
    """Delete one character."""
    if not source:
        return source
    cut = rng.randrange(len(source))
    return source[:cut] + source[cut + 1 :]


def _insert_a_character(source: str, rng: random.Random) -> str:
    """Insert one printable character."""
    at = rng.randrange(len(source) + 1)
    return source[:at] + rng.choice(string.printable[:70]) + source[at:]


def _repeat_a_character(source: str, rng: random.Random) -> str:
    """Repeat one character a few times."""
    if not source:
        return source
    at = rng.randrange(len(source))
    return source[:at] + source[at] * rng.randrange(2, 5) + source[at + 1 :]


def _wrap(source: str, rng: random.Random) -> str:
    """Bury the expression inside a valid one, so a refusal has to survive nesting."""
    return rng.choice(WRAPPERS).format(source)


def _append_an_escape(source: str, rng: random.Random) -> str:
    """Join it to something escape-shaped, in either order."""
    other = rng.choice(ESCAPE_SEEDS)
    operator = rng.choice(OPERATORS)
    return f"{source}{operator}{other}" if rng.getrandbits(1) else f"{other}{operator}{source}"


def _double_a_token(source: str, rng: random.Random) -> str:
    """Double a character that means something to the grammar."""
    target = rng.choice(["_", ".", "(", '"'])
    return source.replace(target, target * 2)


MUTATIONS = (
    _drop_a_character,
    _insert_a_character,
    _repeat_a_character,
    _wrap,
    _append_an_escape,
    _double_a_token,
)


def mutate(source: str, rng: random.Random) -> str:
    """Return a mutation of `source`.

    Character-level edits produce mostly-invalid syntax, which is not a waste: the parser is a
    barrier worth hammering. The structural mutations are the ones that reach past it.

    Args:
        source: The expression to mutate.
        rng: The seeded generator, so a run is reproducible.

    Returns:
        The mutated expression.
    """
    return rng.choice(MUTATIONS)(source, rng)


class Watcher:
    """Records audit events, but only while armed.

    Armed narrowly on purpose. A hook cannot be removed once installed and fires for every
    audited operation in the process, so recording outside the evaluation window would collect
    the fuzzer's own reading and printing and drown the signal.
    """

    def __init__(self) -> None:
        self.armed = False
        self.source = b""
        self.findings: list[tuple[str, str]] = []

    def hook(self, event: str, args: tuple[Any, ...]) -> None:
        """Record one audit event, if armed and not the parse this package performs itself."""
        if not self.armed:
            return
        # `ast.parse` raises `compile` with the source it was handed. Allowed only when it is
        # *our* source: a compile of anything else means something built code from somewhere.
        if event == "compile" and args and args[0] == self.source:
            return
        self.findings.append((event, repr(args)[:200]))

    def watch(self, source: str) -> None:
        """Arm the recorder for one evaluation of `source`."""
        self.armed = True
        self.source = source.encode("utf-8", "surrogatepass")


def run(rounds: int, seed: int) -> dict[str, Any]:
    """Fuzz and report.

    Args:
        rounds: How many expressions to try.
        seed: The random seed, so a run is reproducible.

    Returns:
        Counts of what was reached, and any findings.
    """
    # **Warnings become errors for the fuzz run**, which turns noise into signal twice over.
    # Printing a warning makes CPython open the source file to show the offending line, and that
    # `open` is an audit event with nothing to do with an escape; and a warning raised as an
    # exception during evaluation either arrives as a `SafeExprError` or is recorded as a
    # finding, which is exactly the check worth making. This is a dedicated process, so changing
    # a process-global filter here costs nothing.
    warnings.simplefilter("error")

    rng = random.Random(seed)
    watcher = Watcher()
    sys.addaudithook(watcher.hook)

    seeds = [*corpus_seeds(), *ESCAPE_SEEDS, *BENIGN_SEEDS, *BENIGN_SEEDS]
    every_context = contexts()
    evaluator = Evaluator(registry=standard_registry())
    bare = Evaluator()

    counts = {"tried": 0, "parsed": 0, "evaluated": 0, "refused": 0}
    for round_number in range(rounds):
        source = rng.choice(seeds)
        for _ in range(rng.randrange(3)):
            source = mutate(source, rng)
        if len(source) > MAX_FUZZ_SOURCE:
            source = source[:MAX_FUZZ_SOURCE]
        context = every_context[round_number % len(every_context)]
        chosen = evaluator if round_number % 2 else bare
        counts["tried"] += 1
        watcher.watch(source)
        try:
            chosen.evaluate(source, context)
            counts["evaluated"] += 1
            counts["parsed"] += 1
        except SafeExprError as refused:
            counts["refused"] += 1
            # "Parsed" means it got past the parser, which is what makes an input worth having:
            # a fuzzer whose inputs all die at the parser is hammering one barrier.
            if type(refused).__name__ not in {"ParseError", "SourceTooLongError"}:
                counts["parsed"] += 1
        except BaseException as escaped:  # anything that is not ours is itself a finding
            watcher.findings.append(
                ("not-a-safeexpr-error", f"{type(escaped).__name__}: {source!r}")
            )
        finally:
            watcher.armed = False

    return {"seed": seed, **counts, "findings": watcher.findings}


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--json", action="store_true")
    parsed = parser.parse_args()

    result = run(parsed.rounds, parsed.seed)
    if parsed.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"seed {result['seed']}, {result['tried']:,} expressions")
        print(f"  past the parser   {result['parsed']:,}")
        print(f"  evaluated         {result['evaluated']:,}")
        print(f"  refused           {result['refused']:,}")
        if result["findings"]:
            print(f"\n{len(result['findings'])} AUDIT FINDINGS:")
            for event, args in result["findings"][:20]:
                print(f"  {event}: {args}")
        else:
            print("\nno audit events beyond parsing this package's own source")
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
