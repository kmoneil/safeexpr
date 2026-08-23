"""A safe expression language for Python, with zero runtime dependencies.

    >>> from safeexpr import evaluate
    >>> evaluate('user.plan == "pro" and user.region in ["us", "eu"]',
    ...          {"user": {"plan": "pro", "region": "eu"}})
    True

Two promises come with it. Every failure is a `SafeExprError`, and no error carries a reference
to the data that caused it; see `_errors` for why the second one is harder than it looks.

Pipes, lazy arguments and the function registry are still being built, so `where`, `map` and
friends are not available yet. What works today is comparison, arithmetic, field access and
indexing over ordinary data.
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

__all__ = [
    "EvaluationError",
    "Evaluator",
    "InternalError",
    "ParseError",
    "SafeExprError",
    "SourceTooLongError",
    "ValidationError",
    "__version__",
    "evaluate",
]

# Pre-release. The first PyPI upload should carry a real, working surface rather than a
# placeholder: PEP 541 lists "package has no functionality or is empty" as an invalid project
# subject to removal, which is the one way a held name is actually at risk.
__version__ = "0.0.1.dev0"
