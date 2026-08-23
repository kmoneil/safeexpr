"""A safe expression language for Python, with zero runtime dependencies.

The evaluator is still being built. What is public and usable today is the **error hierarchy**,
which is worth exporting ahead of the rest because it is what callers write their `except`
clauses against, and because it is the part with a promise attached:

    every failure this package produces is a `SafeExprError`, and no error carries a
    reference to the data that caused it.

The second half of that is not free. See `_errors` for why `raise ... from None` does not
achieve it.
"""

from __future__ import annotations

from ._errors import (
    InternalError,
    ParseError,
    SafeExprError,
    SourceTooLongError,
    ValidationError,
)

__all__ = [
    "InternalError",
    "ParseError",
    "SafeExprError",
    "SourceTooLongError",
    "ValidationError",
    "__version__",
]

# Pre-release. The first PyPI upload should carry a real, working surface rather than a
# placeholder: PEP 541 lists "package has no functionality or is empty" as an invalid project
# subject to removal, which is the one way a held name is actually at risk.
__version__ = "0.0.1.dev0"
