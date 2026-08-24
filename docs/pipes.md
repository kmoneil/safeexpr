# Pipes and `_`

The one piece of syntax in this language that does not mean what Python means by it, and the
reason it is worth the trade.

## `|` is a call

With a non-empty registry, `x | f(a)` is rewritten to `f(x, a)` before evaluation, and `x | f` to
`f(x)`. That is the whole rule.

```text
[1, 2, 3] | len                                     => 3
[3, 1, 2] | sort_by(_) | take(2)                    => [1, 2]
[1, 2, 3] | where(_ > 1) | map(_ * 10) | sum        => 50
```

Written out, the last one is `sum(map(where([1, 2, 3], _ > 1), _ * 10))`, which is the same
expression read inside out. The pipe form reads in the order the data moves, which is why a
config author who has never written Python can follow it.

The rewrite happens **once, at parse time, from the registry alone**. It never looks at your data,
so an expression means the same thing whatever it is evaluated against. That property is what the
[reserved names](#reserved-names) section below is paying for.

## `_` is the item

Functions that take a **key expression** receive it unevaluated and run it once per item, with `_`
bound to that item:

```text
where([{"n": 1}, {"n": 9}], _.n > 5)                => [{'n': 9}]
map([1, 2, 3], _ * 2)                               => [2, 4, 6]
sort_by([{"n": 2}, {"n": 1}], _.n)                  => [{'n': 1}, {'n': 2}]
```

Nine functions take one: `where`, `map`, `group_by`, `unique_by`, `sort_by`, `max_by`, `min_by`,
`any_` and `all_`.

**This is why there is no lambda.** `where(_.price > 10)` is not a string that gets re-parsed per
item, and it is not a callable smuggled in from the host: the function is handed the comparison
node itself and asks it for a value once per item. Parse once, evaluate N times, with no syntax
for a user-defined function anywhere in the language.

`_` exists **only** inside such an argument. Outside one it is an error rather than a name that
happens to be unset:

```text
_ > 1                                               => !EvaluationError
```

That is also why `orders | first | _.id` does not work: `first` takes no key expression, so there
is no item in scope on its right. Reach into the result with ordinary syntax instead:

```text
(([{"id": "a"}] | first)).id                        => 'a'
first([{"id": "a"}]).id                             => 'a'
```

## Nesting: `_1`, `_2`

Key expressions nest, and each level shadows the one outside it. `_` and `_1` are the innermost
item; `_2` is one level out, `_3` two, and so on:

```text
map([{"rows": [1, 2, 3]}], where(_.rows, _1 > 1))   => [[2, 3]]
```

Reading that from the inside: `_1` is an element of `rows`, and `_` at the point where `.rows` is
read is the outer mapping. A concrete pair of levels:

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())
rules.evaluate(
    'teams | map({"team": _.name, "active": len(where(_.members, _1.active))})',
    {
        "teams": [
            {"name": "core", "members": [{"active": True}, {"active": False}]},
            {"name": "ops", "members": [{"active": True}]},
        ]
    },
)
# [{'team': 'core', 'active': 1}, {'team': 'ops', 'active': 1}]
```

Inside the inner `where`, `_1` is a member and `_2` would be the team. Reaching past what is in
scope is an error naming both numbers:

```text
map([1], _2)                                        => !EvaluationError
```

```text
`_2` reaches 2 levels out but only 1 is in scope here
```

Two levels is where readability gives out. If you find yourself writing `_3`, the rule wants
splitting into two.

## Reserved names

Here is the cost of deciding what `|` means from the registry alone.

**A function name on the right of a `|` always wins.** With `first` registered, `flags | first`
calls the function, whatever your context says `first` is. The alternative would be deciding per
evaluation by looking at the data, and then the same expression against two rows could be two
different expressions.

Rather than quietly reading past your key, that collision is refused:

```python
from safeexpr import Evaluator, ReservedNameError, standard_registry

rules = Evaluator(registry=standard_registry())
try:
    rules.evaluate("flags | first", {"flags": [1, 2], "first": "surprise"})
except ReservedNameError as error:
    print(error.name)
    print(error.annotated())
```

```text
first
`first` is both a function and a key in the data, and on the right of a `|` the function always
wins, so the data's `first` cannot be reached here; rename the key, or write `bitor(a, b)` if
bitwise or was meant
  flags | first
          ^
```

**Only the right of a `|` is affected.** A bare name still reads your data, so this is correct and
is not refused, even with `min` registered:

```python
rules.evaluate("metrics | where(_.value > min)", {"metrics": [{"value": 40}], "min": 10})
# [{'value': 40}]
```

And `first(x)` is unambiguous whatever your context holds, because a value from the context can
never be called.

### Checking for collisions at startup

The reserved names are exactly the registry's, plus `bitor`, and the evaluator will tell you:

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())
my_context = {"customer": {}, "first": "surprise", "region": "eu"}

sorted(set(my_context) & rules.function_names)  # empty means no collisions
# ['first']
```

With `standard_registry()` there are forty-two:

```text
all_ any_ bitor bool contains default ends_with extend first float format_date group_by int
is_none join last len lower map matches max max_by merge min min_by parse_iso pluck replace
slugify sort_by split starts_with str strip sum take unique_by upper url_host url_path url_query
where
```

If your data really does have a `first` key and renaming it is not an option, the registry is a
plain dictionary: drop the name, or rename the function.

```python
registry = standard_registry()
registry["head"] = registry.pop("first")
rules = Evaluator(registry=registry)
rules.evaluate("flags | head", {"flags": [1, 2], "first": "kept"})
# 1
```

## `bitor`, for when you meant bitwise or

`|` is a pipe as soon as the registry is non-empty, so bitwise or has a name:

```text
bitor(6, 3)                                         => 7
```

It is reserved even on an evaluator with no registry at all, which is the one name
`Evaluator().function_names` holds.

## When not to use a pipe

Pipes are for a value flowing through steps. When there is no flow, the call form reads better:

```text
len(where([1, 2, 3], _ > 1))                        => 2
```

And a pipe is not a substitute for a name. Four steps is usually the point at which a rule wants
to become two rules with something in between that a person can name.
