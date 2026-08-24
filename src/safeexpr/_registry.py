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

An entry also declares its **arity** and its **step cost**. Arity is checked by the evaluator
before the function is called, which is what lets a function's own `TypeError` mean "this value is
wrong" rather than "this argument count is wrong"; the two used to be indistinguishable and the
message guessed. Cost is declared here and charged by the step budget, which is not built yet, so
a tier can be written once and priced later without being rewritten.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class FunctionError(Exception):
    """A registry function rejecting the values it was given.

    **Carries a message and nothing else.** No value, no reference to a caught exception, no
    `args` passed through from one. That is not tidiness: the evaluator catches this *inside* an
    exception handler, so anything reachable from an instance would be reachable through the
    `__context__` of whatever the handler builds. That is the F9 leak shape (RestrictedPython
    CVE-2024-47532) and a string is inert against it.

    **Deliberately not a `SafeExprError`.** A registry function knows what is wrong with its
    input and has no idea where in the source it was called from, so an error raised here would
    have no position. The evaluator catches this and constructs the positioned error, because
    the evaluator is the layer that holds the node.
    """


def describe_type(value: object) -> str:
    """Name a value's type for an error message.

    The type name only. A `repr` of the value would put the caller's data into a string the
    expression author reads, which is the leak R8 exists to prevent.

    **This is why `type` does not appear in the tier modules.** The registry review gate bans
    runtime reflection, `type` included, because a class object is climbable: `__class__` to
    `__mro__` to `__subclasses__` is F1/F2 and the reason a static AST allowlist cannot be the
    only defence. A type's *name* is a string and cannot be climbed. Putting that one call here,
    where it is reviewed once, keeps the ban on the tiers a plain absence check rather than a
    rule carrying an exception, and `tests/test_collections.py` asserts the absence.

    Args:
        value: The value whose type to name.

    Returns:
        The type's bare name.
    """
    return type(value).__name__


@dataclass(frozen=True)
class Function:
    """One callable an expression may invoke.

    Args:
        name: The name the expression uses.
        call: The implementation. Lazy positions arrive as `LazyExpr`, everything else as values.
        lazy: Zero-based positional indices the evaluator must not evaluate. A function taking a
            lazy argument is responsible for calling `LazyExpr.evaluate(item)` itself, once per
            item, which is what keeps "parse once, evaluate N times" true.
        arity: `(minimum, maximum)` argument counts, where `None` as the maximum means variadic.
            The default accepts anything, which is what a bare callable registered without a
            `Function` wrapper gets: unchecked, exactly as before this field existed.
        cost: What one call charges the step budget. A **per-call** figure, so it prices the
            fixed overhead of a function rather than the size of its input; a function whose
            work scales with the collection is charged per item by the budget itself, and this
            number says how much one of those calls is worth on top. Ordering matters more than
            the absolute values, which the budget work will calibrate.
    """

    name: str
    call: Callable[..., Any]
    lazy: frozenset[int] = field(default_factory=frozenset)
    arity: tuple[int, int | None] = (0, None)
    cost: int = 1

    def __post_init__(self) -> None:
        if any(index < 0 for index in self.lazy):
            message = f"{self.name}: lazy positions are zero-based indices, got {sorted(self.lazy)}"
            raise ValueError(message)
        low, high = self.arity
        if low < 0 or (high is not None and high < low):
            message = f"{self.name}: arity {self.arity} is not a (minimum, maximum) pair"
            raise ValueError(message)
        if high is not None and any(index >= high for index in self.lazy):
            message = (
                f"{self.name}: lazy position {max(self.lazy)} is past the last argument "
                f"the arity {self.arity} allows"
            )
            raise ValueError(message)
        if self.cost < 1:
            message = f"{self.name}: cost must be 1 or more, got {self.cost}"
            raise ValueError(message)

    @property
    def checks_arity(self) -> bool:
        """Whether this entry's arity says anything the evaluator can act on.

        `(0, None)` is the default and means "not declared", which is what a bare callable gets.
        The distinction matters for error messages rather than for dispatch: with an informative
        arity that a call already satisfied, a `TypeError` out of the function *cannot* be a
        miscount, so it can be reported as what it is. Without one it might still be either.

        A function that genuinely accepts any number of arguments is indistinguishable from one
        that never said, and is treated as never having said. That is the honest reading: its
        declaration rules nothing out.

        Returns:
            True if the arity is narrower than "anything".
        """
        return self.arity != (0, None)

    def accepts(self, count: int) -> bool:
        """Whether this function may be called with `count` positional arguments.

        Args:
            count: How many arguments the expression passed.

        Returns:
            True if the count is within the declared arity.
        """
        low, high = self.arity
        return low <= count and (high is None or count <= high)

    def arity_text(self) -> str:
        """Describe the accepted argument counts, for an error message.

        Returns:
            A phrase such as "2 arguments", "1 or 2 arguments" or "at least 2 arguments".
        """
        low, high = self.arity
        if high is None:
            return f"at least {low} argument{'' if low == 1 else 's'}"
        if low == high:
            return f"{low} argument{'' if low == 1 else 's'}"
        return f"{low} or {high} arguments" if high == low + 1 else f"{low} to {high} arguments"


def as_function(name: str, entry: Function | Callable[..., Any]) -> Function:
    """Normalise a registry entry.

    A bare callable is a function with no lazy arguments, which is the common case and not worth
    a wrapper at every call site. Its arity is left unchecked, because a plain callable has not
    told us one and guessing from a signature would be reflection.

    Args:
        name: The name the entry is registered under.
        entry: Either a `Function` or a plain callable.

    Returns:
        A `Function`.
    """
    if isinstance(entry, Function):
        return entry
    return Function(name=name, call=entry)
