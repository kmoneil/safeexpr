"""Collection guards for the benchmark directory.

The `compat` and `corpus` CI lanes build an environment with `pytest` and `hypothesis` and
nothing else, deliberately: they exist to run the suite on four interpreters, and dragging a
benchmark stack onto each row would slow every one of them to measure something that is only
meaningful on a stable machine anyway.

So this directory is skipped rather than failed when its plugins are absent. The alternative is a
collection error on three of the four matrix rows, which would make a green matrix impossible for
a reason that has nothing to do with the package.
"""

from __future__ import annotations

import importlib.util

collect_ignore_glob: list[str] = []

if importlib.util.find_spec("pytest_benchmark") is None:  # pragma: no cover - environment gate
    collect_ignore_glob.append("test_*_bench.py")

if importlib.util.find_spec("pytest_memray") is None:  # pragma: no cover - environment gate
    collect_ignore_glob.append("test_*_memory.py")
