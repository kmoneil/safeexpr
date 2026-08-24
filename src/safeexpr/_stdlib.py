r"""The standard registry: every tier, assembled.

One place that knows which tiers exist, so a host writes `Evaluator(registry=standard_registry())`
once and keeps working as tiers are added. Today that is collections, types, strings, dates and
URL; `matches` is still to come and lands here too.

**The tiers must not disagree about a name.** They are merged in a fixed order and a later tier
would silently win, so `tests/test_tiers.py` asserts the names are disjoint rather than trusting
the merge: two tiers both defining `contains` would be a real question about what the language
means, not something to settle by import order.

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
from ._dates import DATES
from ._registry import Function
from ._strings import STRINGS
from ._types import TYPES
from ._urls import URLS


def standard_registry() -> dict[str, Function]:
    """Build a registry holding every standard function.

    A fresh dictionary each call, so a host can add to it, drop names from it, or shadow one with
    their own implementation without any of that reaching another evaluator.

    Returns:
        Name to `Function`, ready to pass as `Evaluator(registry=...)`.
    """
    return {**COLLECTIONS, **TYPES, **STRINGS, **DATES, **URLS}
