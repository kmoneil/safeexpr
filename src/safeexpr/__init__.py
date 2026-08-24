"""A safe expression language for Python, with zero runtime dependencies.

    >>> from safeexpr import evaluate
    >>> evaluate('user.plan == "pro" and user.region in ["us", "eu"]',
    ...          {"user": {"plan": "pro", "region": "eu"}})
    True

Two promises come with it. Every failure is a `SafeExprError`, and no error carries a reference
to the data that caused it; see `_errors` for why the second one is harder than it looks.

Functions are opt-in. `Evaluator()` starts with an empty registry and evaluates comparison,
arithmetic, field access and indexing; `standard_registry()` adds the collections tier, and with
it pipes:

    >>> from safeexpr import Evaluator, standard_registry
    >>> rules = Evaluator(registry=standard_registry())
    >>> rules.evaluate('metrics | where(_.value > 10) | first',
    ...                {"metrics": [{"value": 4}, {"value": 40}]})
    {'value': 40}

Opting in rather than defaulting is deliberate, because a registered name is reserved on the
right of a `|`. See `_stdlib` for the argument.
"""

from __future__ import annotations

from ._errors import (
    EvaluationError,
    InternalError,
    ParseError,
    SafeExprError,
    SourceTooLongError,
    ValidationError,
)
from ._eval import Evaluator, evaluate
from ._registry import Function, FunctionError
from ._stdlib import standard_registry

__all__ = [
    "EvaluationError",
    "Evaluator",
    "Function",
    "FunctionError",
    "InternalError",
    "ParseError",
    "SafeExprError",
    "SourceTooLongError",
    "ValidationError",
    "__version__",
    "evaluate",
    "standard_registry",
]

# Pre-release. The first PyPI upload should carry a real, working surface rather than a
# placeholder: PEP 541 lists "package has no functionality or is empty" as an invalid project
# subject to removal, which is the one way a held name is actually at risk.
__version__ = "0.0.1.dev0"
