r"""`matches`, and the static gate that decides which patterns are allowed to run.

**Input-length caps are not the mitigation, and that is measured rather than assumed.** `^(a+)+$`
against a *29-character* input takes seven seconds, identically on every supported interpreter,
because catastrophic backtracking is driven by the pattern's structure and not by the size of the
subject. Each character added roughly doubles the time. There is no input cap short enough to
help and long enough to be useful, so the pattern itself has to be refused before it ever runs.

**The step budget cannot help either.** It counts nodes evaluated, and one `matches` call is one
node however many minutes it spends inside `re`. This is the one place in the package where work
happens outside the counter, which is exactly why the gate is static.

So patterns are parsed with the standard library's own regex parser and checked against two rules
before they are compiled. Both are fitted to measurement rather than to taxonomy: a corpus of
catastrophic and benign patterns was timed against tailored adversarial input, and the rules are
what separate them.

    1. **Nested backtrackable repeats.** A repeat is backtrackable when it has a choice, so
       `a{2}` is not and `a{1,20}` is. Depth above one is refused. This is the classic shape and
       it covers `^(a+)+$`, `(x+x+)+y`, `^(\w+\s?)*$`, `((a)*)*$`, `(\d+)*$` and `(a|a?)*$`.

       Bounded repeats count. `^(a{1,20}){1,20}$` has no unbounded quantifier anywhere and still
       takes measurably long, which the "unbounded repeats only" rule the research proposed would
       have let straight through.

    2. **A backtrackable repeat over an alternation with two identical branches.** `(a|a)*$`
       takes 0.03 seconds where every benign pattern takes none, and rule 1 scores it safe
       because neither branch contains a repeat. This is the third classic shape, and the
       research flagged its absence as the one thing not to ship without.

**A third rule was written, measured and removed**, which is worth recording because the taxonomy
says it should exist. "A repeat whose body can match the empty string" is a real ReDoS class in
other engines, and here it caught nothing: every genuinely slow pattern it flagged (`(a|a?)*$`,
`((a)*)*$`) already contains a nested backtrackable repeat and is refused by rule 1, while
everything it *uniquely* flagged was fast on all four supported interpreters (`(a|)*$`,
`(a|b|)*$`, `(a*+)*$`, `(x?y?)*$`, `(\b)*$`). CPython's engine breaks out of an empty-match loop
by itself. A rule whose only unique effect is refusing safe patterns is worse than no rule, so it
is not here.

**Rule 2 is narrower than the obvious version, and deliberately.** Rejecting branches that merely
share a first character would refuse `(foo|bar|baz)+$`, and rejecting one branch that prefixes
another would refuse `(a|ab)*$`. Both were measured and both are fast, so both stay allowed.

**Atomic groups and possessive quantifiers reset the nesting depth**, because they cannot
backtrack: `^(?>a+)+$` and `^(a++)+$` are instant where `^(a+)+$` is not, measured. The original
decision ruled them out as a mitigation because the floor was 3.10 and they did not exist there.
The floor is 3.11 now and they do, so a pattern author who knows what they are doing can write a
nested repeat and have it accepted, which is a capability the old rule could not offer.

**The gate is conservative and says so.** `(.*)*$` measures fast on this interpreter because `re`
optimises it, and it is refused anyway: it is the textbook shape, the optimisation is an
implementation detail, and a gate that refuses a pattern which happens to be fast today is worth
much more than one that accepts a pattern which is slow tomorrow.

**`re._parser` is a private standard-library API**, renamed from `sre_parse` in 3.11. If it ever
moves, this module fails closed: every pattern is refused with a message saying the checker is
unavailable, rather than patterns being compiled unchecked. `tests/test_regex.py` carries the
canary that fails loudly in CI on every supported interpreter.
"""

from __future__ import annotations

import re
from typing import Any

from ._guards import text
from ._registry import Function, FunctionError

# The standard library's own regex parser, under whichever name this interpreter has for it.
#
# **Fails closed rather than failing to import.** Losing the parser must not stop `import
# safeexpr` working, and it must not quietly turn the gate off either, so the module loads with
# no parser and `matches` refuses everything until somebody notices. The canary in the tests is
# what makes "somebody notices" happen in CI rather than in production.
_PARSER: Any = None
_PARSER_NAME = ""
try:
    # A private standard-library module, so the type checker has never heard of it. The
    # import is guarded and the failure path is tested; silencing the checker here is the
    # honest alternative to pretending the attribute is public.
    from re import _parser as _re_parser  # type: ignore[attr-defined]  # 3.11 and later

    _PARSER, _PARSER_NAME = _re_parser, "re._parser"
except ImportError:  # pragma: no cover - only on an interpreter that has moved it
    try:
        import sre_parse as _sre_parse

        _PARSER, _PARSER_NAME = _sre_parse, "sre_parse"
    except ImportError:
        _PARSER, _PARSER_NAME = None, ""

# How deeply a pattern may nest. Checked first and iteratively, which is what makes the recursive
# analysis below safe: nothing legitimate nests twenty groups deep, and a pattern that does is
# refused before anything walks it.
_MAX_NESTING = 20

# Defence in depth, and labelled as such. Neither of these is the mitigation: the pattern gate is.
# A pattern longer than this is not a rule, and a subject longer than this is not a field.
_MAX_PATTERN_LENGTH = 512
_MAX_SUBJECT_LENGTH = 65_536

# How many compiled patterns to remember. Compiling and checking is the expensive part and a rule
# runs the same pattern once per row, so the cache is what makes `matches` usable at all. Bounded
# because the pattern can come from the context and therefore vary per row; the whole cache is
# dropped when it fills, which is cheap and needs no ordering bookkeeping.
_MAX_CACHE = 256
_CACHE: dict[str, re.Pattern[str]] = {}

# Opcode names, compared as strings so a renamed constant object does not silently stop matching.
_REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT"})
_NO_BACKTRACK = frozenset({"POSSESSIVE_REPEAT", "ATOMIC_GROUP"})


def _op(item: tuple[Any, Any]) -> str:
    return str(item[0])


def _body(item: tuple[Any, Any]) -> Any:
    """The subpattern inside a node, or `None` for a leaf."""
    name, argument = _op(item), item[1]
    if name in _REPEATS or name == "POSSESSIVE_REPEAT":
        return argument[2]
    if name == "ATOMIC_GROUP":
        return argument
    if name == "SUBPATTERN":
        return argument[3]
    return None


def _branches(item: tuple[Any, Any]) -> list[Any] | None:
    return list(item[1][1]) if _op(item) == "BRANCH" else None


def _is_backtrackable(item: tuple[Any, Any]) -> bool:
    """Whether a repeat has a choice to make, and so something to backtrack over.

    `a{2}` has none: it matches exactly twice or fails. `a{1,20}`, `a+`, `a*` and `a?` all do.
    """
    if _op(item) not in _REPEATS:
        return False
    low, high = item[1][0], item[1][1]
    return bool(high > low)


def _too_deep(parsed: Any) -> bool:
    """Whether the pattern nests past the cap, walked with an explicit stack.

    Iterative because it runs *before* the recursive analysis and is what makes that recursion
    safe. A recursive depth check would be the thing it is checking for.
    """
    stack: list[tuple[Any, int]] = [(parsed, 0)]
    while stack:
        sequence, depth = stack.pop()
        if depth > _MAX_NESTING:
            return True
        for item in sequence:
            inner = _body(item)
            if inner is not None:
                stack.append((inner, depth + 1))
            for branch in _branches(item) or []:
                stack.append((branch, depth + 1))
    return False


def _canonical(sequence: Any) -> tuple[Any, ...]:
    """A comparable form of a subpattern, with group numbers dropped.

    Dropping the group number is what makes `(a)|(a)` as detectable as `(a|a)`: they are
    different groups and the same language, and it is the language that decides whether the
    alternation is ambiguous.
    """
    shape: list[Any] = []
    for item in sequence:
        name = _op(item)
        if name == "BRANCH":
            shape.append((name, tuple(_canonical(branch) for branch in item[1][1])))
        elif name in _REPEATS or name == "POSSESSIVE_REPEAT":
            shape.append((name, item[1][0], item[1][1], _canonical(item[1][2])))
        else:
            inner = _body(item)
            shape.append((name, _canonical(inner)) if inner is not None else (name, repr(item[1])))
    return tuple(shape)


def _has_twin_branches(sequence: Any) -> bool:
    """Whether an alternation anywhere in here offers the same language twice."""
    for item in sequence:
        branches = _branches(item)
        if branches is not None:
            shapes = [_canonical(branch) for branch in branches]
            if len(shapes) != len(set(shapes)):
                return True
            if any(_has_twin_branches(branch) for branch in branches):
                return True
        inner = _body(item)
        if inner is not None and _has_twin_branches(inner):
            return True
    return False


def _objection(sequence: Any, depth: int = 0) -> str | None:
    """Return why this subpattern is refused, or `None` if it is allowed.

    Args:
        sequence: A parsed subpattern.
        depth: How many backtrackable repeats enclose it.

    Returns:
        The reason, or `None`.
    """
    for item in sequence:
        name = _op(item)
        # Atomic groups and possessive quantifiers cannot give back what they matched, so the
        # nesting above them cannot make them backtrack. Depth restarts inside.
        inner_depth = 0 if name in _NO_BACKTRACK else depth
        if _is_backtrackable(item):
            inner_depth = depth + 1
            if inner_depth > 1:
                return (
                    "nests one repeat inside another, which is the shape that backtracks "
                    "exponentially; make the inner one atomic with `(?>...)` or possessive "
                    "with `+`, or bound it with `{m}`"
                )
            if _has_twin_branches(item[1][2]):
                return (
                    "repeats a choice whose options can match the same text, so every "
                    "combination has to be tried"
                )
        for branch in _branches(item) or []:
            found = _objection(branch, inner_depth)
            if found is not None:
                return found
        body = _body(item)
        if body is not None:
            found = _objection(body, inner_depth)
            if found is not None:
                return found
    return None


def check(pattern: str) -> str | None:
    """Return why `pattern` is refused, or `None` if it may be compiled.

    Args:
        pattern: The regular expression source.

    Returns:
        The reason it is refused, or `None`.
    """
    if _PARSER is None:  # pragma: no cover - only on an interpreter that has moved the parser
        return (
            "cannot be checked for catastrophic backtracking on this interpreter, and an "
            "unchecked pattern is not run"
        )
    # **The length cap lives here rather than at the call site**, so this function is the whole
    # gate and nothing can consult it without also getting the bound. It is also what makes the
    # parse below provably safe to attempt: 512 characters cannot nest more than 256 groups, and
    # the standard library's parser handles more than 400 without giving out, measured on every
    # supported interpreter. There is no `RecursionError` to catch because no input is short
    # enough to pass this line and deep enough to cause one.
    if len(pattern) > _MAX_PATTERN_LENGTH:
        return f"is {len(pattern):,} characters, over the limit of {_MAX_PATTERN_LENGTH:,}"
    try:
        parsed = _PARSER.parse(pattern)
    except re.error:
        return "is not a valid regular expression"
    if _too_deep(parsed):
        return f"nests more than {_MAX_NESTING} levels deep"
    return _objection(parsed)


def _compiled(pattern: str) -> re.Pattern[str]:
    """Compile `pattern` if the gate allows it, remembering the result.

    Args:
        pattern: The regular expression source.

    Returns:
        The compiled pattern.

    Raises:
        FunctionError: If the gate refuses it.
    """
    found = _CACHE.get(pattern)
    if found is not None:
        return found
    objection = check(pattern)
    if objection is not None:
        raise FunctionError(f"this pattern {objection}")
    built = re.compile(pattern)
    if len(_CACHE) >= _MAX_CACHE:
        # Dropped wholesale rather than by age. The pattern can come from the context and vary
        # per row, so the cache has to be bounded; keeping the bookkeeping for an eviction order
        # would cost more than the occasional rebuild it saves.
        _CACHE.clear()
    _CACHE[pattern] = built
    return built


def _matches(value: Any, pattern: Any) -> bool:
    """Whether a regular expression is found anywhere in the text.

    Searching rather than requiring the whole subject to match, so `^` and `$` mean what they say
    and an author who wants a whole-string match can ask for one.
    """
    subject = text(value)
    source = text(pattern, "a pattern as text")
    if len(subject) > _MAX_SUBJECT_LENGTH:
        # Defence in depth, and not the mitigation: no useful input cap exists, because the
        # pattern gate is what actually bounds the work. This only stops a very large field from
        # making an allowed pattern slow by sheer length.
        raise FunctionError(
            f"text is {len(subject):,} characters, over the limit of {_MAX_SUBJECT_LENGTH:,}"
        )
    return _compiled(source).search(subject) is not None


REGEX: dict[str, Function] = {
    # Dearer than any other entry, because this is the one function whose work happens outside
    # the step budget entirely: the counter sees one call and `re` does the rest in C.
    "matches": Function("matches", _matches, arity=(2, 2), cost=10),
}
