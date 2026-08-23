r"""What a registry entry is.

The registry is the only thing an expression can call, so its entries carry more than a callable.
A `Function` declares **which argument positions are lazy**: positions the evaluator must *not*
evaluate, handing the function the unevaluated expression instead so it can run that expression
once per item.

That declaration is what makes `where(_.price > 10)` work without a lambda, and it is the reason
this package needs no lambda. `where` receives the comparison itself, not a value.

**Why the declaration lives here rather than in the tree.** The design hoisted lazy arguments into
a side table keyed by synthetic names (`__lazy_0` and friends) which the evaluator then resolved
like any other name. Built and attacked, that leaks: the synthetic names live in the same
namespace the user writes into, so naming one returns a live AST subtree, which is the F8
precondition (asteval GHSA-vp47-9734-prjw). Declaring lazy positions on the *function* instead
means the evaluator can simply skip those arguments. There is no table to name and no name to
collide with, so the hole is absent rather than defended.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Function:
    """One callable an expression may invoke.

    Args:
        name: The name the expression uses.
        call: The implementation. Lazy positions arrive as `LazyExpr`, everything else as values.
        lazy: Zero-based positional indices the evaluator must not evaluate. A function taking a
            lazy argument is responsible for calling `LazyExpr.evaluate(item)` itself, once per
            item, which is what keeps "parse once, evaluate N times" true.
    """

    name: str
    call: Callable[..., Any]
    lazy: frozenset[int] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if any(index < 0 for index in self.lazy):
            message = f"{self.name}: lazy positions are zero-based indices, got {sorted(self.lazy)}"
            raise ValueError(message)


def as_function(name: str, entry: Function | Callable[..., Any]) -> Function:
    """Normalise a registry entry.

    A bare callable is a function with no lazy arguments, which is the common case and not worth
    a wrapper at every call site.

    Args:
        name: The name the entry is registered under.
        entry: Either a `Function` or a plain callable.

    Returns:
        A `Function`.
    """
    if isinstance(entry, Function):
        return entry
    return Function(name=name, call=entry)
