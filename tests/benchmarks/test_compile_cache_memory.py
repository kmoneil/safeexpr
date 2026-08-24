"""Allocation ceilings for the compile cache.

The cache is bounded because of what it can hold, not because of how often it is hit. Measured by
`scripts/limits.py`, which prints these numbers beside every other limit:

    a typical rule, 49 bytes             4.1 KiB per tree     0.52 MiB at the 128-entry bound
    a 2,046-byte flat literal          262.6 KiB per tree    32.82 MiB at the same bound

The second row is the reason the bound exists. Without it a host that accepts expression text from
an untrusted source holds an unbounded allocation keyed by attacker-chosen input, which is the
denial of service this package exists not to have. The bound turns "unbounded" into 33 MiB, and 33
MiB is a number a reader can weigh.

**The two instruments disagree, and it is worth knowing which answers which question.** The
figures above are `tracemalloc`, which counts retained Python objects. memray, which is what
enforces the ceilings below, counts the process's peak live heap at the allocator level, and it
under-reports small-object retention badly: a full cache of 128 ordinary rules shows **28.8 KiB**
here against tracemalloc's 525 KiB, because CPython serves 4 KiB of AST nodes out of pools it is
already holding and never asks the OS for more.

So the ordinary-rules ceiling is a loose tripwire rather than a measurement. The widest-source
ceiling is the real one: 16 maximum-width sources allocate **2.3 MiB** of peak live heap, which is
large enough that the allocator has to go and get it, and it is the row the bound was set from.

Ceilings rather than targets, set at roughly three times the measurement so that an interpreter's
own overhead does not fail the suite. What they catch is a change that starts holding something
per entry it did not hold before.

**Only enforced under `--memray`.** Without that flag the marker is inert and these read as
ordinary assertions, which is why the numbers are recorded here rather than left implicit in a run
nobody re-reads.
"""

from __future__ import annotations

from typing import Any

import pytest

from safeexpr import Evaluator, standard_registry
from safeexpr._eval import MAX_COMPILE_CACHE
from safeexpr._parse import MAX_SOURCE_BYTES

# A rule shaped like the design's first canonical use case, one per cache slot.
ORDINARY = [
    f'user.plan == "tier{n}" and user.region in ["us", "eu"]' for n in range(MAX_COMPILE_CACHE)
]

# The widest source the cap admits, which is what sets the bound. Sixteen of them rather than a
# full cache: the shape is what is being measured and 128 would spend eight times the time to say
# the same thing.
WIDEST_COUNT = 16
# Every entry is four digits wide, so the sources differ from each other without differing in
# length. A naive `str(n)` would make the tenth source longer than the first and the sixteenth
# longer again, and past 2,048 bytes the source cap refuses it: the widest input is a fixed
# quantity and the test has to build exactly it.
#
# Sized from `MAX_SOURCE_BYTES` rather than typed, so raising the cap widens these rather than
# quietly leaving them measuring something narrower than the worst case.
_WIDEST_ELEMENTS = (MAX_SOURCE_BYTES - 2) // 6
WIDEST = [
    "[" + ", ".join(["1000"] * (_WIDEST_ELEMENTS - 1) + [str(1000 + n)]) + "]"
    for n in range(WIDEST_COUNT)
]

CONTEXT: dict[str, Any] = {"user": {"plan": "tier0", "region": "eu"}}


def _entries(evaluator: Evaluator) -> int:
    """How many sources the evaluator has compiled. Reached once, so the tests read plainly."""
    return len(evaluator._cache)  # noqa: SLF001


@pytest.mark.limit_memory("1 MB")
def test_a_full_cache_of_ordinary_rules_stays_under_its_ceiling() -> None:
    """128 distinct rules, which is the cache full of what a real host puts in it.

    Measured at 28.8 KiB of peak live heap, and the ceiling is 1 MB rather than three times that,
    because the gap between what memray sees here and what the cache actually retains is arena
    reuse rather than headroom. Read this as a tripwire against a change that starts allocating
    per entry, not as a measurement of the cache's size; `scripts/limits.py` is where that number
    comes from.
    """
    evaluator = Evaluator(registry=standard_registry())
    for source in ORDINARY:
        evaluator.evaluate(source, CONTEXT)
    assert _entries(evaluator) == MAX_COMPILE_CACHE


@pytest.mark.limit_memory("8 MB")
def test_the_widest_entries_are_what_the_bound_is_set_from() -> None:
    """Sixteen maximum-width sources: the worst case per entry, at an eighth of the bound.

    Measured at 2.3 MiB of peak live heap; the ceiling is 8 MB. Scale by eight for a full cache.
    This is the row that decided 128 rather than the pattern cache's 256, and unlike the test above
    it is large enough that the allocator has to ask for the memory, so memray can see all of it.
    """
    evaluator = Evaluator(registry=standard_registry())
    for source in WIDEST:
        evaluator.evaluate(source, {})
    assert _entries(evaluator) == WIDEST_COUNT
