r"""The standard registry: every tier, assembled.

One place that knows which tiers exist, so a host writes `Evaluator(registry=standard_registry())`
once and keeps working as tiers are added. Today that is the collections tier; the types, strings,
dates and URL tiers are added here as they are written.

**It is not the default.** `Evaluator()` still starts with an empty registry, and that is a
decision rather than an oversight: registry membership is what tells the pipe transform a `|` is
a pipe rather than bitwise-or, so **every name added here becomes reserved on the right of a
pipe**. With `first` registered, `flags | first` calls it, whatever the context says `first` is.
Making that happen to a host that only wanted `a == b` would be changing the meaning of their
expressions to pay for functions they did not ask for. Opting in is one argument, and it puts the
cost at the call site where a reader can see it.
"""

from __future__ import annotations

from ._collections import COLLECTIONS
from ._registry import Function


def standard_registry() -> dict[str, Function]:
    """Build a registry holding every standard function.

    A fresh dictionary each call, so a host can add to it, drop names from it, or shadow one with
    their own implementation without any of that reaching another evaluator.

    Returns:
        Name to `Function`, ready to pass as `Evaluator(registry=...)`.
    """
    return dict(COLLECTIONS)
