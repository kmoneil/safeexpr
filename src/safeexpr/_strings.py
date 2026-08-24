r"""The strings tier: the operations a rule actually performs on text.

**Nothing here coerces.** `lower(user.age)` is a mistake in the rule, and answering it with
`"30"` hides the mistake until the day the field arrives missing rather than numeric. Every
function takes text and says so when it does not get it, which is the same argument the
collections tier makes for refusing to iterate a string.

**Three of these can turn a short expression into a large value**, and all three are capped:
`replace` can multiply length by the ratio of its arguments, `join` sums a whole collection, and
`upper` can grow text on its own because a few characters expand when uppercased. The cap is
checked on the *predicted* size wherever the size can be predicted, so the allocation does not
happen and then get complained about. That matters because the step budget cannot see this: it
counts nodes evaluated, and the expression that allocates a megabyte is three nodes long.

`slugify` normalises before it filters, which is why `café` slugs to `cafe` rather than `caf`,
and which also means **the result can be longer than the input**: a ligature decomposes into its
letters. The cap is therefore checked on the normalised text rather than on what arrived.

`slugify` is ASCII in core and lossy about it, deliberately. Text is normalised so that accented
Latin letters keep their base form, `café` becoming `cafe`, but a script with no ASCII
approximation has none to fall back to and its characters are dropped: a title written entirely
in Greek or Japanese slugifies to nothing at all. That is a real limitation rather than a corner
case, it is documented in the README as well as here, and transliteration is what the `unicode`
extra is for. Core stays stdlib-only.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from ._guards import MAX_RESULT_SIZE, sequence, text, within_size
from ._registry import Function, FunctionError, describe_type

# Characters a slug keeps as themselves. Everything else becomes a separator or disappears.
_SLUG_KEEP = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def _lower(value: Any) -> str:
    """Text in lower case."""
    return text(value).lower()


def _upper(value: Any) -> str:
    """Text in upper case.

    Capped, because uppercasing can grow text: German sharp s becomes two characters and a few
    ligatures become three, so the result is not bounded by the input's length.
    """
    result = text(value).upper()
    within_size(len(result), "text")
    return result


def _strip(value: Any) -> str:
    """Text with leading and trailing whitespace removed."""
    return text(value).strip()


def _split(value: Any, separator: Any = None) -> list[str]:
    """Split text into a list.

    With no separator, splits on runs of whitespace and drops empty pieces, which is what
    splitting a sentence into words means. With one, splits on exactly that text.
    """
    subject = text(value)
    if separator is None:
        return subject.split()
    piece = text(separator, "a separator")
    if not piece:
        raise FunctionError("needs a separator that is not empty")
    return subject.split(piece)


def _join(items: Any, separator: Any) -> str:
    """Join a list of text with a separator between the pieces.

    Reads in pipe order: `parts | join(", ")`.
    """
    pieces = sequence(items)
    piece = text(separator, "a separator")
    total = len(piece) * max(0, len(pieces) - 1)
    for index, item in enumerate(pieces):
        if not isinstance(item, str):
            raise FunctionError(
                f"needs a list of text, and item {index} is `{describe_type(item)}`"
            )
        total += len(item)
        within_size(total, "text")
    return piece.join(pieces)


def _replace(value: Any, old: Any, new: Any) -> str:
    """Replace every occurrence of one piece of text with another.

    **The predicted size is checked first.** `replace(x, "a", "aaaaaaaa")` multiplies length by
    eight, and nesting the calls multiplies again; three nodes of expression can ask for a
    gigabyte. Counting occurrences costs one pass and refuses before the allocation rather than
    after it.
    """
    subject = text(value)
    target = text(old, "the text to replace")
    replacement = text(new, "the replacement text")
    if not target:
        raise FunctionError("needs text to replace that is not empty")
    occurrences = subject.count(target)
    within_size(len(subject) + occurrences * (len(replacement) - len(target)), "text")
    return subject.replace(target, replacement)


def _starts_with(value: Any, prefix: Any) -> bool:
    """Whether text begins with a prefix."""
    return text(value).startswith(text(prefix, "a prefix"))


def _ends_with(value: Any, suffix: Any) -> bool:
    """Whether text ends with a suffix."""
    return text(value).endswith(text(suffix, "a suffix"))


def _contains(value: Any, needle: Any) -> bool:
    """Whether text contains another piece of text.

    The function form of `in` for text, so a pipe can ask the question:
    `user.email | contains("@")`.
    """
    return text(needle, "the text to look for") in text(value)


def _slugify(value: Any) -> str:
    """Reduce text to lowercase ASCII words joined by hyphens.

    Lossy for anything with no ASCII form; see this module's docstring.
    """
    subject = text(value)
    within_size(len(subject), "text")
    # Decompose first, so an accented Latin letter separates into a base letter and a combining
    # mark and the base letter survives. Without this, `café` slugs to `caf`.
    folded = unicodedata.normalize("NFKD", subject).lower()
    # **Normalising can make text longer**, so the input's length is not a bound on the output's.
    # A ligature decomposes into its letters and a compatibility numeral into several: `ﬁ` is one
    # character and normalises to two, `Ⅻ` is one and normalises to three, and Unicode allows
    # worse. Found by a property test asserting a slug never grows, which is simply false.
    within_size(len(folded), "text")
    pieces: list[str] = []
    current: list[str] = []
    for character in folded:
        if character in _SLUG_KEEP:
            current.append(character)
        elif unicodedata.category(character).startswith("M"):
            # A combining mark belongs to the letter before it and is not a word boundary.
            # Treating it as one is how `Héllo` slugs to `he-llo`: NFKD splits the `é` and the
            # leftover accent then reads as punctuation. Measured, not theorised.
            continue
        elif current:
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))
    return "-".join(pieces)


STRINGS: dict[str, Function] = {
    "lower": Function("lower", _lower, arity=(1, 1)),
    "upper": Function("upper", _upper, arity=(1, 1)),
    "strip": Function("strip", _strip, arity=(1, 1)),
    "split": Function("split", _split, arity=(1, 2)),
    "join": Function("join", _join, arity=(2, 2)),
    "replace": Function("replace", _replace, arity=(3, 3)),
    "starts_with": Function("starts_with", _starts_with, arity=(2, 2)),
    "ends_with": Function("ends_with", _ends_with, arity=(2, 2)),
    "contains": Function("contains", _contains, arity=(2, 2)),
    "slugify": Function("slugify", _slugify, arity=(1, 1)),
}

# Re-exported so the size cap has one name a reader can find from either side.
__all__ = ["MAX_RESULT_SIZE", "STRINGS"]
