"""The review gate, asserted against the source rather than against the reviewer.

Every registry function passes a mandatory review: does this perform runtime reflection? A review
is a person reading carefully once, and the thing being guarded against is a person adding a
convenience later. So the review is written down as a test that parses the tiers.

**F1 is why.** `"{0.__class__}".format(x)` performs an attribute lookup from inside a format
string, and no AST check reads a format string. It is the most-repeated escape in the competitive
scan, appearing in Jinja2, RestrictedPython and asteval, and every appearance is the same shape:
something *else* did the attribute lookup at runtime, so the static allowlist that governs the
expression never saw it. One convenient `format`, `getattr` or `type` in a registry function
reopens that without changing a single line the validator can read.

Three checks, and the fourth is what keeps them honest:

- No banned name appears anywhere in a tier.
- No `%` operator at all, since `"%(__class__)s" % d` does its own lookup in C.
- **Each tier imports only what it is allowed to, listed per module rather than pooled.** A
  shared allowlist would let the types tier import `urllib` because the URL tier needed it, and
  the point is that each import is a decision somebody made about that module.
- Every callable in the standard registry must be defined in a module this file scans, so a tier
  added without being listed here fails rather than going unchecked.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from safeexpr import standard_registry
from safeexpr._collections import COLLECTIONS
from safeexpr._dates import DATES
from safeexpr._strings import STRINGS
from safeexpr._types import TYPES
from safeexpr._urls import URLS

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "safeexpr"

# What each tier may import. Per module on purpose: `_types` has no business importing `urllib`
# and `_urls` has none importing `unicodedata`, and a pooled allowlist would permit both.
#
# `_guards` is here as well as the five tiers. It is not a tier, but every tier calls into it, so
# a banned name reaching it would reach all of them.
TIER_IMPORTS: dict[str, frozenset[str]] = {
    "safeexpr._guards": frozenset({"__future__", "collections.abc", "typing", "._registry"}),
    "safeexpr._collections": frozenset(
        {"__future__", "operator", "collections.abc", "typing", "._registry", "._guards"}
    ),
    "safeexpr._types": frozenset({"__future__", "math", "typing", "._registry"}),
    "safeexpr._strings": frozenset(
        {"__future__", "unicodedata", "typing", "._registry", "._guards"}
    ),
    "safeexpr._dates": frozenset({"__future__", "datetime", "typing", "._registry", "._guards"}),
    "safeexpr._urls": frozenset({"__future__", "urllib.parse", "typing", "._registry", "._guards"}),
}

# Modules a registered callable may be defined in. `_guards` is scanned but defines no entry.
TIER_MODULES = tuple(name for name in TIER_IMPORTS if name != "safeexpr._guards")

# The names the card bans, plus the climb they lead to. Anything that reads an attribute, a name
# or a type at runtime, because that is precisely what a static AST allowlist cannot see.
BANNED = frozenset(
    {
        "format",
        "format_map",
        "Formatter",
        "getattr",
        "setattr",
        "delattr",
        "vars",
        "dir",
        "type",
        "reduce",
        "eval",
        "exec",
        "compile",
        "globals",
        "locals",
        "__import__",
        "__getattribute__",
        "__dict__",
        "__class__",
        "__bases__",
        "__mro__",
        "__subclasses__",
        "__globals__",
        "__builtins__",
    }
)


def _tree(dotted: str) -> ast.Module:
    path = SRC_DIR / f"{dotted.rsplit('.', 1)[-1]}.py"
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add("." * node.level + (node.module or ""))
    return found


class TestNoTierFunctionPerformsRuntimeReflection:
    @pytest.mark.parametrize("dotted", TIER_IMPORTS)
    def test_no_banned_name_appears_anywhere_in_the_module(self, dotted: str) -> None:
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(_tree(dotted))
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        assert not (used & BANNED), (
            f"{dotted} references {sorted(used & BANNED)}. A registry function that reflects at "
            f"runtime reopens F1, which no static check on the expression can see."
        )

    @pytest.mark.parametrize("dotted", TIER_IMPORTS)
    def test_no_percent_operator_at_all(self, dotted: str) -> None:
        """`"%(__class__)s" % d` does its own `__getitem__` in C and reads keys the evaluator
        blocks. Modulo has no use in a tier, so the whole operator is absent rather than
        conditionally allowed."""
        assert not [node for node in ast.walk(_tree(dotted)) if isinstance(node, ast.Mod)]

    @pytest.mark.parametrize("dotted", TIER_IMPORTS)
    def test_a_tier_imports_only_what_it_is_allowed_to(self, dotted: str) -> None:
        # `._eval` is imported for typing only, under `if TYPE_CHECKING`, so it is not a runtime
        # dependency and is exempted rather than widening any module's runtime allowlist.
        imported = _imports(_tree(dotted)) - {"._eval"}
        allowed = TIER_IMPORTS[dotted]
        assert imported <= allowed, (
            f"{dotted} imports {sorted(imported - allowed)}, which its allowlist does not permit"
        )

    def test_every_registered_callable_lives_in_a_scanned_module(self) -> None:
        """What stops the scan above from going stale.

        A tier added without being listed would be entirely unchecked while every test here still
        passed. This is the line that fails instead.
        """
        stray = {
            name: function.call.__module__
            for name, function in standard_registry().items()
            if function.call.__module__ not in TIER_MODULES
        }
        assert not stray, f"registered from outside the scanned tiers: {stray}"


class TestFormatDateDoesNotFormatStrings:
    """The card singles this one out, so it gets its own assertion rather than relying on the
    blanket scan.

    `format_date` is the function most likely to be written with `str.format`, because formatting
    a date and formatting a string sound like the same job. They are not: one interprets calendar
    directives and the other interprets attribute paths.
    """

    def test_the_dates_tier_calls_strftime_and_nothing_that_interprets_a_template(self) -> None:
        tree = _tree("safeexpr._dates")
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "strftime" in called, "format_date no longer formats through strftime"
        assert not (called & {"format", "format_map"})


class TestTheTiersAgreeWithEachOther:
    def test_no_two_tiers_claim_the_same_name(self) -> None:
        """They are merged in a fixed order, so a clash would be settled silently by whichever
        tier merges last. Two tiers both defining `contains` is a real question about what the
        language means, not something for import order to answer."""
        tiers = {
            "collections": COLLECTIONS,
            "types": TYPES,
            "strings": STRINGS,
            "dates": DATES,
            "urls": URLS,
        }
        seen: dict[str, str] = {}
        clashes: list[str] = []
        for tier, entries in tiers.items():
            for name in entries:
                if name in seen:
                    clashes.append(f"{name} in both {seen[name]} and {tier}")
                seen[name] = tier
        assert not clashes, f"tiers disagree about a name: {clashes}"

    def test_the_standard_registry_is_exactly_the_union_of_the_tiers(self) -> None:
        union = set(COLLECTIONS) | set(TYPES) | set(STRINGS) | set(DATES) | set(URLS)
        assert set(standard_registry()) == union

    def test_every_entry_is_registered_under_its_own_name(self) -> None:
        for name, function in standard_registry().items():
            assert function.name == name

    def test_every_entry_declares_an_arity(self) -> None:
        """A standard function that left arity undeclared would fall back to the older, vaguer
        error message, and the tier would be inconsistent about how it reports a miscount."""
        undeclared = [name for name, f in standard_registry().items() if not f.checks_arity]
        assert not undeclared, f"standard functions with no declared arity: {undeclared}"
