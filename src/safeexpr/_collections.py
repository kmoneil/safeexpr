r"""The collections tier: `where`, `map`, `group_by`, `merge` and friends.

This is the capability bar. The gap between simpleeval (an evaluator with no data functions) and
cel-python (real collection semantics at a six-dependency cost) is exactly this file, and `merge`
in particular is the relational join JMESPath cannot express: JMESPath can *build* a new object
and can *project* an existing one, but it cannot combine the two, so adding a computed field to a
record means relisting every field the record already had.

**Every entry here passed the reflection gate, and the gate is an absence rather than a rule.**
`format`, `format_map`, `%` on text, `string.Formatter`, `getattr`, `vars`, `type` and `reduce`
are banned from the tiers regardless of convenience, because a static AST allowlist cannot see an
attribute lookup performed at runtime (F1, the single most important lesson from the competitive
scan). None of those names appears in this module at all, `type` included: naming a value's type
for an error message goes through `_registry.describe_type`, which is reviewed once and returns a
string. `tests/test_collections.py` parses this file and asserts the absence, and asserts that
every registered callable is defined in a module that check covers, so a later tier cannot be
added without being scanned.

Three conventions hold across the whole tier, so that a reader learns them once:

- **A collection is a list or a tuple, and nothing else.** Not a string, whose elements are
  characters; not a mapping, whose elements are keys. Both iterate perfectly well in Python and
  both are almost always a mistake in a rule, so they are rejected with a message rather than
  quietly producing a surprising answer. `len` is the exception, because asking a string its
  length is not a mistake.
- **Empty in, empty out.** A function that returns a collection returns an empty one; a function
  that must pick a single element (`first`, `last`, `min`, `max`, `min_by`, `max_by`) returns
  `None`; `sum` returns `0`. Canonical use case 2 is `metrics | where(...) | first`, which has to
  survive matching nothing, so raising on an empty collection would make the ordinary case the
  error case.
- **Nothing here recurses into data, and the three places Python does it for us are guarded.**
  Sorting, comparing and hashing all walk nested values in C, and all three raise `RecursionError`
  on input a host can genuinely hold: two mutually self-referential lists, or ~10,000 levels of
  nesting, both measured. Unguarded that surfaces as "internal error ... this is a bug in
  safeexpr", which is the wrong answer to a legitimate complaint about the data. This is F4,
  expr-lang's advisory.

  **What the guards cannot reach, stated rather than implied:** `tuplehash` does not use
  `Py_EnterRecursiveCall`, so hashing a tuple nested about 400,000 deep exhausts the C stack and
  segfaults the interpreter with no Python-level exception to catch, here or anywhere else.
  Closing that needs a depth cap applied *before* the value reaches `hash`, which is a general
  guard over host data rather than something a `try` can express, and it is not built. What is
  here covers every path this tier reaches that CPython lets it see.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ._guards import HASHABLE_CONTAINERS, check_depth
from ._guards import sequence as _sequence
from ._registry import Function, FunctionError, describe_type

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from ._eval import LazyExpr

# What a comparison or a hash says when the data defeats it. Both are the same failure wearing
# two exception types, and both mean "your data, not your expression".
_TOO_DEEP = "some of them nest too deeply or refer to themselves"
_NOT_COMPARABLE = "they are not all comparable with each other"


def _key_for(lazy: LazyExpr, item: Any) -> Any:
    """Evaluate a grouping key and check it can be used as one.

    Grouping and de-duplication put keys in a `dict` and a `set`, so an unhashable key is a
    `TypeError` from deep inside a comprehension rather than something the author can act on.
    Asking up front costs one `hash` per item and turns it into a sentence.

    Args:
        lazy: The key expression.
        item: The item to evaluate it against.

    Returns:
        The key.

    Raises:
        FunctionError: If the key cannot be hashed.
    """
    value = lazy.evaluate(item)
    # **Before `hash`, not around it.** A deeply nested tuple does not raise on the way in; it
    # exhausts the C stack and takes the interpreter with it, so there is nothing for the handler
    # below to catch.
    #
    # The `isinstance` is here rather than only inside `check_depth` because this runs once per
    # item and a Python call is not free: measured at 43us over a thousand keys, about 6% of a
    # `group_by`, which the test alone brings to near nothing. `check_depth` repeats the test for
    # callers that are not on a hot path.
    if isinstance(value, HASHABLE_CONTAINERS):
        check_depth(value)
    try:
        hash(value)
    except TypeError:
        failure = FunctionError(
            f"needs a key it can group by, and `{describe_type(value)}` cannot be one"
        )
    except RecursionError:
        failure = FunctionError(f"cannot group by these keys: {_TOO_DEEP}")
    else:
        return value
    raise failure


def _where(items: Any, predicate: LazyExpr) -> list[Any]:
    """Keep the items for which `predicate` is truthy."""
    return [item for item in _sequence(items) if predicate.evaluate(item)]


def _map(items: Any, expression: LazyExpr) -> list[Any]:
    """Evaluate `expression` once per item and collect the results."""
    return [expression.evaluate(item) for item in _sequence(items)]


def _extend(items: Any, other: Any) -> list[Any]:
    """Concatenate two lists.

    The sequence counterpart to `merge`: `merge` combines two mappings, this combines two lists,
    and neither modifies what it was given.
    """
    return [*_sequence(items), *_sequence(other)]


def _group_by(items: Any, key: LazyExpr) -> list[dict[str, Any]]:
    """Group items by a key expression.

    Returns a **list of group records**, `{"key": ..., "items": [...]}`, rather than a mapping of
    key to items. Canonical use case 4 is what decides it:

        orders | where(_.status == "paid") | group_by(_.customer_id) | map(merge(_, {...}))

    A `map` over a mapping would iterate its keys, so the groups would arrive at `merge` as bare
    customer ids with the orders left behind. As records, each group flows into the next stage as
    an ordinary item with `.key` and `.items` on it, and the pipe keeps its shape: a list goes in
    at every stage and a list comes out.

    Groups come back in first-appearance order, and items keep their order within a group, so the
    result is a deterministic function of the input rather than of dict ordering luck.
    """
    groups: dict[Any, list[Any]] = {}
    for item in _sequence(items):
        groups.setdefault(_key_for(key, item), []).append(item)
    return [{"key": key_value, "items": members} for key_value, members in groups.items()]


def _unique_by(items: Any, key: LazyExpr) -> list[Any]:
    """Keep the first item for each distinct key, in the order they first appear."""
    seen: set[Any] = set()
    kept: list[Any] = []
    for item in _sequence(items):
        key_value = _key_for(key, item)
        if key_value not in seen:
            seen.add(key_value)
            kept.append(item)
    return kept


def _sort_by(items: Any, key: LazyExpr, descending: Any = False) -> list[Any]:
    """Sort by a key expression, ascending unless `descending` is truthy.

    The key is evaluated exactly once per item rather than once per comparison, which is what
    keeps a sort over a real collection from re-running the expression O(n log n) times.

    Stable in both directions: `reverse=True` still leaves equal keys in their original order,
    so `sort_by(_.score, True) | take(3)` gives the same three rows every run.
    """
    keyed = [(key.evaluate(item), item) for item in _sequence(items)]
    try:
        keyed.sort(key=operator.itemgetter(0), reverse=bool(descending))
    except TypeError:
        failure = FunctionError(f"cannot sort by these keys: {_NOT_COMPARABLE}")
    except RecursionError:
        failure = FunctionError(f"cannot sort by these keys: {_TOO_DEEP}")
    else:
        return [item for _, item in keyed]
    raise failure


def _pluck(items: Any, field: Any) -> list[Any]:
    """Read one field from every item.

    What this has that `map(_.name)` does not is a field name that is a *value*: the name can
    come from the context, so a host can drive the same rule over a configured column.

    That is also why the underscore rule is repeated here. The validator blocks `x.__class__`
    and `x["__class__"]` because it can read them in the source; a name arriving as a value is
    exactly the case it cannot see, which is the same reason `_eval._subscript` re-checks
    computed keys. Field access is mapping-only either way, so this is defence in depth rather
    than the only barrier, and it stays because the cost is one comparison.
    """
    if not isinstance(field, str):
        raise FunctionError(f"needs a field name as text, got `{describe_type(field)}`")
    if field.startswith("_"):
        raise FunctionError(
            f'cannot read the field "{field}": fields beginning with an underscore are blocked'
        )
    values: list[Any] = []
    for item in _sequence(items):
        if not isinstance(item, Mapping):
            raise FunctionError(f"needs a list of mappings, found `{describe_type(item)}`")
        if field not in item:
            raise FunctionError(f'no field "{field}" on every item')
        values.append(item[field])
    return values


def _extreme_by(items: Any, key: LazyExpr, want_max: bool) -> Any:
    """Return the item with the largest or smallest key, or `None` for an empty list.

    Ties go to the first item, matching `max` and `min` on a plain list.
    """
    values = _sequence(items)
    if not values:
        return None
    keys = [key.evaluate(item) for item in values]
    pick = max if want_max else min
    try:
        chosen = pick(range(len(keys)), key=lambda index: keys[index])
    except TypeError:
        failure = FunctionError(f"cannot compare these keys: {_NOT_COMPARABLE}")
    except RecursionError:
        failure = FunctionError(f"cannot compare these keys: {_TOO_DEEP}")
    else:
        return values[chosen]
    raise failure


def _max_by(items: Any, key: LazyExpr) -> Any:
    """The item with the largest key, or `None` if there are no items."""
    return _extreme_by(items, key, want_max=True)


def _min_by(items: Any, key: LazyExpr) -> Any:
    """The item with the smallest key, or `None` if there are no items."""
    return _extreme_by(items, key, want_max=False)


def _first(items: Any) -> Any:
    """The first item, or `None` if there are none."""
    values = _sequence(items)
    return values[0] if values else None


def _last(items: Any) -> Any:
    """The last item, or `None` if there are none."""
    values = _sequence(items)
    return values[-1] if values else None


def _take(items: Any, count: Any) -> list[Any]:
    """The first `count` items, or all of them if there are fewer.

    `True` is not 1 here. Python would accept it, since `bool` is an `int`, and
    `take(rows, True)` reads as a mistake rather than as a request for one row.
    """
    values = _sequence(items)
    if isinstance(count, bool) or not isinstance(count, int):
        raise FunctionError(f"needs a whole number, got `{describe_type(count)}`")
    if count < 0:
        raise FunctionError(f"needs a count of 0 or more, got {count}")
    return list(values[:count])


def _merge(*mappings: Any) -> dict[Any, Any]:
    """Combine mappings into a new one, later keys winning.

    **Shallow, and deliberately so.** A nested merge would have to walk both inputs, which puts
    it squarely in F4 territory: expr-lang shipped a denial of service because builtins recursed
    over user data with no depth cap, and a merge that recurses needs cycle detection and a depth
    guard that are not built. Shallow needs neither, because it never looks inside a value.

    That is not a reduced version of the capability being claimed. What JMESPath cannot express
    is combining two objects at all: given a record, there is no way to say "this, plus one more
    field" without relisting every field it already has. One level is the whole of that gap.
    `merge(_, {"n": len(_.items)})` is canonical use case 4 and needs no more.

    Nothing is modified: the inputs are read and a new `dict` comes back, so a host's data cannot
    be edited by an expression that merges it.
    """
    merged: dict[Any, Any] = {}
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            raise FunctionError(f"needs mappings, got `{describe_type(mapping)}`")
        merged.update(mapping)
    return merged


def _len(value: Any) -> int:
    """How many items, characters or keys a value has.

    The one function in the tier that takes something other than a list, because `len(user.name)`
    and `len(user.roles)` are both ordinary things to ask.
    """
    try:
        length = len(value)
    except TypeError:
        failure = FunctionError(f"needs something with a length, got `{describe_type(value)}`")
    else:
        return length
    raise failure


def _sum(items: Any) -> Any:
    """Add up a list of numbers. An empty list sums to 0, as it does in arithmetic."""
    values = _sequence(items)
    try:
        total = sum(values)
    except TypeError:
        failure = FunctionError("needs a list of numbers")
    else:
        return total
    raise failure


def _extreme(items: Any, want_max: bool) -> Any:
    """The largest or smallest value in a list, or `None` for an empty list."""
    values = _sequence(items)
    if not values:
        return None
    pick = max if want_max else min
    try:
        chosen = pick(values)
    except TypeError:
        failure = FunctionError(f"cannot compare these values: {_NOT_COMPARABLE}")
    except RecursionError:
        failure = FunctionError(f"cannot compare these values: {_TOO_DEEP}")
    else:
        return chosen
    raise failure


def _max(items: Any) -> Any:
    """The largest value, or `None` if there are none."""
    return _extreme(items, want_max=True)


def _min(items: Any) -> Any:
    """The smallest value, or `None` if there are none."""
    return _extreme(items, want_max=False)


def _any(items: Any, predicate: LazyExpr | None = None) -> bool:
    """Whether any item satisfies `predicate`, or is truthy if no predicate is given.

    Short-circuits: the predicate stops running at the first item that satisfies it.
    """
    values = _sequence(items)
    if predicate is None:
        return any(bool(item) for item in values)
    return any(bool(predicate.evaluate(item)) for item in values)


def _all(items: Any, predicate: LazyExpr | None = None) -> bool:
    """Whether every item satisfies `predicate`, or is truthy if no predicate is given.

    Short-circuits at the first item that fails, and an empty list is vacuously true.
    """
    values = _sequence(items)
    if predicate is None:
        return all(bool(item) for item in values)
    return all(bool(predicate.evaluate(item)) for item in values)


# The tier, as the evaluator sees it.
#
# **Costs are relative and per call, not per item.** A function whose work scales with the
# collection is charged for the collection by the budget itself; what a number here says
# is what one call is worth *on top* of that scan. So `sort_by` is dearer than `where` because a
# comparison sort is superlinear rather than because it touches more items, and `group_by`,
# `unique_by` and `merge` are dearer than a plain scan because each allocates as it goes. The
# ordering is the part that carries meaning. The absolute values will be calibrated against a
# real budget, and until that budget exists nothing reads them.
COLLECTIONS: dict[str, Function] = {
    "where": Function("where", _where, lazy=frozenset({1}), arity=(2, 2)),
    "map": Function("map", _map, lazy=frozenset({1}), arity=(2, 2)),
    "extend": Function("extend", _extend, arity=(2, 2)),
    "group_by": Function("group_by", _group_by, lazy=frozenset({1}), arity=(2, 2), cost=2),
    "unique_by": Function("unique_by", _unique_by, lazy=frozenset({1}), arity=(2, 2), cost=2),
    "sort_by": Function("sort_by", _sort_by, lazy=frozenset({1}), arity=(2, 3), cost=5),
    "pluck": Function("pluck", _pluck, arity=(2, 2)),
    "max_by": Function("max_by", _max_by, lazy=frozenset({1}), arity=(2, 2)),
    "min_by": Function("min_by", _min_by, lazy=frozenset({1}), arity=(2, 2)),
    "first": Function("first", _first, arity=(1, 1)),
    "last": Function("last", _last, arity=(1, 1)),
    "take": Function("take", _take, arity=(2, 2)),
    "merge": Function("merge", _merge, arity=(2, None), cost=2),
    "len": Function("len", _len, arity=(1, 1)),
    "sum": Function("sum", _sum, arity=(1, 1)),
    "min": Function("min", _min, arity=(1, 1)),
    "max": Function("max", _max, arity=(1, 1)),
    "any_": Function("any_", _any, lazy=frozenset({1}), arity=(1, 2)),
    "all_": Function("all_", _all, lazy=frozenset({1}), arity=(1, 2)),
}
