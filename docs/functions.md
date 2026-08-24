# Function reference

Forty-one functions across six tiers, plus one builtin. They arrive together:

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())
```

Every example below is `expression => result`, self-contained, and evaluated by
[`tests/test_docs.py`](../tests/test_docs.py) against the standard registry. A `!` result names
the error class the expression raises.

Two things that hold for every function here:

- **Every argument is checked.** A function given the wrong kind of value raises an
  `EvaluationError` naming the function, what it needed and what it got. Nothing is coerced
  quietly, and nothing returns `None` to mean "that did not work".
- **The pipe form is the same call.** `x | f(a)` is `f(x, a)`, so every function below can be
  written either way. See [Pipes and `_`](pipes.md).

**Jump to:** [Collections](#collections) · [Types](#types) · [Strings](#strings) ·
[Regex](#regex) · [Dates](#dates) · [URLs](#urls) · [Builtin](#builtin)

## Collections

Nineteen functions over lists. The ones taking a **key expression** (`where`, `map`, `group_by`,
`unique_by`, `sort_by`, `max_by`, `min_by`, `any_`, `all_`) receive that argument unevaluated and
run it once per item, with `_` bound to the item. That is the whole reason this language needs no
lambda.

### where

`where(items, predicate)` keeps the items for which the predicate is truthy.

```text
where([1, 2, 3], _ > 1)                             => [2, 3]
where([{"n": 1}, {"n": 9}], _.n > 5)                => [{'n': 9}]
where([], _ > 1)                                    => []
where(5, _ > 1)                                     => !EvaluationError
```

### map

`map(items, expression)` evaluates the expression once per item and collects the results.

```text
map([1, 2, 3], _ * 2)                               => [2, 4, 6]
map([{"n": 1}], {"double": _.n * 2})                => [{'double': 2}]
map([{"a": 1}], merge(_, {"b": 2}))                 => [{'a': 1, 'b': 2}]
```

### pluck

`pluck(items, field)` reads one field from every item. The field name is a value, not an
expression, and every item must have it.

```text
pluck([{"id": "a"}, {"id": "b"}], "id")             => ['a', 'b']
pluck([{"a": 1}, {"b": 2}], "a")                    => !EvaluationError
```

The refusal is the point: a `pluck` that skipped the items without the field would hand you a
shorter list than you asked for and no way to know which ones went missing.

### first

`first(items)` is the first item, or `None` when there are none.

```text
first([1, 2, 3])                                    => 1
first([])                                           => None
```

### last

`last(items)` is the last item, or `None` when there are none.

```text
last([1, 2, 3])                                     => 3
last([])                                            => None
```

### take

`take(items, count)` is the first `count` items, or all of them if there are fewer.

```text
take([1, 2, 3], 2)                                  => [1, 2]
take([1, 2, 3], 10)                                 => [1, 2, 3]
take([1, 2, 3], 0)                                  => []
take([1, 2, 3], -1)                                 => !EvaluationError
```

### len

`len(value)` is how many items, characters or keys a value has.

```text
len([1, 2, 3])                                      => 3
len("hello")                                        => 5
len({"a": 1, "b": 2})                               => 2
len(None)                                           => !EvaluationError
```

### sum

`sum(items)` adds up a list of numbers. An empty list sums to `0`, as it does in arithmetic.

```text
sum([1, 2, 3])                                      => 6
sum([])                                             => 0
sum([1, "a"])                                       => !EvaluationError
```

### min

`min(items)` is the smallest value, or `None` when there are none.

```text
min([3, 1, 2])                                      => 1
min([])                                             => None
```

### max

`max(items)` is the largest value, or `None` when there are none.

```text
max([3, 1, 2])                                      => 3
max([])                                             => None
```

### min_by

`min_by(items, key)` is the item with the smallest key, or `None` for an empty list.

```text
min_by([{"n": 3}, {"n": 1}], _.n)                   => {'n': 1}
min_by([], _.n)                                     => None
```

### max_by

`max_by(items, key)` is the item with the largest key, or `None` for an empty list.

```text
max_by([{"n": 3}, {"n": 1}], _.n)                   => {'n': 3}
max_by([], _.n)                                     => None
```

### sort_by

`sort_by(items, key)` sorts ascending. A third argument sorts descending when it is truthy.

```text
sort_by([{"n": 2}, {"n": 1}], _.n)                  => [{'n': 1}, {'n': 2}]
sort_by([3, 1, 2], _)                               => [1, 2, 3]
sort_by([3, 1, 2], _, True)                         => [3, 2, 1]
```

### group_by

`group_by(items, key)` groups items by a key expression. The result is a list of
`{"key": ..., "items": [...]}` mappings, in the order each key was first seen.

```text
group_by([{"k": "a"}, {"k": "b"}, {"k": "a"}], _.k) => [{'key': 'a', 'items': [{'k': 'a'}, {'k': 'a'}]}, {'key': 'b', 'items': [{'k': 'b'}]}]
```

A list rather than a mapping, because a mapping would put your data's keys where a rule reads
names, and because the order of first appearance is information a mapping would throw away.

### unique_by

`unique_by(items, key)` keeps the first item for each distinct key, in the order they first
appear.

```text
unique_by([{"k": "a"}, {"k": "a"}, {"k": "b"}], _.k) => [{'k': 'a'}, {'k': 'b'}]
unique_by([1, 1, 2], _)                             => [1, 2]
```

### any_

`any_(items)` is whether any item is truthy. `any_(items, predicate)` is whether any item
satisfies the predicate.

```text
any_([0, 1])                                        => True
any_([0, 0])                                        => False
any_([])                                            => False
any_([{"n": 9}], _.n > 5)                           => True
```

The trailing underscore is not decoration: `any` and `all` are Python builtins, and a name that
shadows one is a name a reader has to think about.

### all_

`all_(items)` is whether every item is truthy. `all_(items, predicate)` is whether every item
satisfies the predicate. An empty list satisfies it, as it does in logic.

```text
all_([1, 2])                                        => True
all_([1, 0])                                        => False
all_([])                                            => True
all_([{"n": 9}], _.n > 5)                           => True
```

### merge

`merge(first, second, ...)` combines mappings into a new one, with later keys winning. It takes
at least two, because merging one mapping is a copy and a copy is not a thing a rule needs.

```text
merge({"a": 1}, {"b": 2})                           => {'a': 1, 'b': 2}
merge({"a": 1}, {"a": 2})                           => {'a': 2}
merge({"a": 1})                                     => !EvaluationError
```

### extend

`extend(items, other)` concatenates two lists. `+` does the same thing; this is the name for the
pipe form.

```text
extend([1, 2], [3])                                 => [1, 2, 3]
extend([1], 2)                                      => !EvaluationError
```

## Types

Six conversions, each of which refuses rather than guesses.

### int

`int(value)` converts to a whole number. Text must read as one, and a decimal is truncated toward
zero.

```text
int("42")                                           => 42
int(" 42 ")                                         => 42
int(1.9)                                            => 1
int("x")                                            => !EvaluationError
int("0x10")                                         => !EvaluationError
```

### float

`float(value)` converts to a decimal number.

```text
float("1.5")                                        => 1.5
float(2)                                            => 2.0
float("x")                                          => !EvaluationError
```

### str

`str(value)` converts a **primitive** to text: numbers, booleans and text itself.

```text
str(42)                                             => '42'
str(True)                                           => 'True'
str({"a": 1})                                       => !EvaluationError
str(None)                                           => !EvaluationError
```

That refusal is a security property rather than a limitation. Converting an arbitrary object to
text runs that object's own `__str__`, which is your code, called from inside an expression, on a
value you did not intend to expose. `None` is refused separately, because text reading `None` is
almost never what a rule wanted: use [`default`](#default).

### bool

`bool(value)` is whether a value is truthy.

```text
bool(1)                                             => True
bool(0)                                             => False
bool("")                                            => False
bool([])                                            => False
```

### is_none

`is_none(value)` is whether a value is nothing.

```text
is_none(None)                                       => True
is_none(0)                                          => False
is_none("")                                         => False
```

### default

`default(value, fallback)` is the value unless it is `None`, in which case the fallback.

```text
default(None, "fallback")                           => 'fallback'
default("set", "fallback")                          => 'set'
default(0, "fallback")                              => 0
default("", "fallback")                             => ''
```

**It tests for nothing, not for falsy.** `0` and `""` are values a rule may well have meant, so
they survive. Use `or` when you want the falsy-coalescing behaviour instead.

`default` cannot rescue a **missing field**: `user.absent` raises before `default` is called.
Reach for the value another way when the key itself is optional, such as
`default(user["settings"], {})` over a mapping you control.

## Strings

Ten functions over text. Every one of them refuses a non-text argument by name, so a rule that
runs against a number where it expected a string fails loudly instead of comparing something
surprising.

### lower

```text
lower("ABC")                                        => 'abc'
lower(1)                                            => !EvaluationError
```

### upper

```text
upper("abc")                                        => 'ABC'
```

### strip

`strip(value)` removes leading and trailing whitespace.

```text
strip("  padded  ")                                 => 'padded'
```

### split

`split(value)` splits on runs of whitespace. `split(value, separator)` splits on the separator.

```text
split("a b  c")                                     => ['a', 'b', 'c']
split("a,b,c", ",")                                 => ['a', 'b', 'c']
```

### join

`join(items, separator)` joins a list of text. Every item must already be text.

```text
join(["a", "b"], "-")                               => 'a-b'
join(["a"], "")                                     => 'a'
join([1, 2], "-")                                   => !EvaluationError
```

This is how you build a string, since there is no interpolation. `join([str(count), "items"], " ")`
is the spelling for what an f-string would have done.

### replace

`replace(value, old, new)` replaces every occurrence.

```text
replace("a-b-c", "-", "_")                          => 'a_b_c'
replace("aa", "a", "b")                             => 'bb'
```

### starts_with

```text
starts_with("hello", "he")                          => True
starts_with("hello", "lo")                          => False
```

### ends_with

```text
ends_with("hello", "lo")                            => True
```

### contains

`contains(value, needle)` is substring containment, **for text only**. For list membership, use
`in`.

```text
contains("hello", "ell")                            => True
contains(["a"], "a")                                => !EvaluationError
"a" in ["a"]                                        => True
```

### slugify

`slugify(value)` reduces text to lowercase ASCII words joined by hyphens.

```text
slugify("Hello, World!")                            => 'hello-world'
slugify("  Ünïcode  Test!! ")                       => 'unicode-test'
slugify("日本語")                                     => ''
```

**ASCII in core.** Accented Latin letters decompose to their base letter, and a script with no
ASCII form is dropped rather than transliterated, which is why the third line is empty rather
than wrong. Transliteration needs a Unicode database this package will not depend on.

## Regex

### matches

`matches(value, pattern)` is whether the regular expression is found **anywhere** in the text.
Anchor it with `^` and `$` when you mean the whole string.

```text
matches("abc-123", "^[a-z]+-[0-9]+$")               => True
matches("hello", "ell")                             => True
matches("hello", "^ell")                            => False
matches(1, "a")                                     => !EvaluationError
matches("a", "[")                                   => !EvaluationError
```

**A backslash in a pattern is doubled**, because the expression source is parsed by Python's own
parser before the pattern reaches `re`, and `"\d"` inside a string literal is an unrecognised
escape. Write `\\d` in the expression:

```text
matches("2026-08-24", "^\\d{4}-\\d{2}-\\d{2}$")      => True
matches("a b", "\\s")                               => True
```

In a host that builds the source in Python, a raw string keeps that readable:
`r'matches(x, "^\\d{4}$")'`. A bracketed class such as `[0-9]` or `[^@ ]` needs no escape at all
and is the spelling to prefer where it fits. The dialect is Python's `re`, so POSIX class names
like `[[:digit:]]` are not a thing here: that is a set containing `[`, `:`, `d` and friends.

**Catastrophically backtracking patterns are refused before they compile.** `^(a+)+$` against a
29-character input takes about seven seconds, so no input-length cap helps and the pattern itself
is the problem:

```text
matches("aaa", "^(a+)+$")                           => !EvaluationError
```

A pattern is refused if it nests one backtrackable repeat inside another, or repeats an
alternation whose branches match the same text. **Atomic groups and possessive quantifiers reset
that**, and both are available on every supported Python:

```text
matches("aaa", "^(?>a+)+$")                         => True
matches("aaa", "^(a++)+$")                          => True
matches("aaa", "^(a{3})+$")                         => True
matches("aaa", "^(a{1,3})+$")                       => !EvaluationError
```

The gate is deliberately conservative and refuses a few patterns that happen to be fast. An
**exact** count is the other way through, because it leaves nothing to choose: `^(a{3})+$` is
accepted where `^(a{1,3})+$` and `^(a{2,})+$` are not.

`matches` is the one function in the registry priced above one step, at ten, because an accepted
pattern still runs inside `re` where the step counter cannot follow it.

## Dates

Two functions. Both are ISO 8601 and `strftime`, with no natural-language parsing and no time
zone database, because either would be a dependency.

### parse_iso

`parse_iso(value)` reads an ISO 8601 date or timestamp and returns a datetime.

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())
rules.evaluate('parse_iso("2026-08-24T13:05:00Z")')
# datetime.datetime(2026, 8, 24, 13, 5, tzinfo=datetime.timezone.utc)
```

```text
parse_iso("not a date")                             => !EvaluationError
```

Datetimes compare with each other, which is what makes a freshness rule expressible:
`parse_iso(record.updated) > parse_iso(cutoff)`.

### format_date

`format_date(value, pattern)` renders a date using `strftime` directives.

```text
format_date(parse_iso("2026-08-24"), "%Y-%m-%d")    => '2026-08-24'
format_date(parse_iso("2026-01-02"), "%d/%m/%Y")    => '02/01/2026'
format_date("2026-08-24", "%Y")                     => !EvaluationError
format_date(parse_iso("2026-08-24"), "%c")          => !EvaluationError
```

The directive set is restricted to the portable ones. `%c`, `%x` and `%X` are locale-defined all
the way down, so their output length is unpredictable, and `%s`, `%-d` and `%e` are platform
extensions rather than Python guarantees. The error names every directive that is allowed.

## URLs

Three readers over a URL string. Parsing a URL reads nothing and opens nothing: there is no
request here, and there never will be.

### url_host

`url_host(value)` is the host, lower-cased, with no port and no credentials. `None` when the
string has no host.

```text
url_host("https://api.Example.com/v2/items")        => 'api.example.com'
url_host("https://user:pw@API.example.com:8443/x")  => 'api.example.com'
url_host("example.com/x")                           => None
```

The last line is the one worth internalising: without a scheme there is no authority to parse,
so `example.com/x` is all path. A host allowlist that forgets this passes anything.

### url_path

```text
url_path("https://api.example.com/v2/items")        => '/v2/items'
url_path("https://api.example.com")                 => ''
```

### url_query

`url_query(value)` is the query parameters, as a mapping of name to its **first** value.

```text
url_query("https://a/b?page=2&limit=50")            => {'page': '2', 'limit': '50'}
url_query("https://a/b")                            => {}
url_query("https://a/b?x=1&x=2")                    => {'x': '1'}
```

Values are text, always. `int(url_query(u)["page"])` is the conversion, and it refuses rather
than guessing if the parameter is not a number.

## Builtin

### bitor

`bitor(a, b)` is bitwise or. It exists because `|` between two values means "pipe" as soon as the
registry is non-empty, so this is how you say the other thing.

```text
bitor(6, 3)                                         => 7
bitor(4, 1)                                         => 5
```

It is the one name reserved even on an evaluator with an empty registry, which is why
`Evaluator().function_names` is `{"bitor"}` rather than empty.

## What is not here

- **No aggregation over groups beyond `len` and `sum`.** `group_by(...) | map(sum(pluck(_.items,
  "total")))` is the spelling, and it composes from parts that already exist.
- **No `now()` or any other clock.** The language has no source of time, which is what makes an
  expression a pure function of its context: the same rule against the same data gives the same
  answer forever. Pass the time in as part of the context when a rule needs one.
- **No random, no uuid, no hash.** Same reason.
- **No I/O of any kind.** Not a query language for an external store, and no plan to become one.

Adding your own is one dictionary entry: see
[Custom functions](embedding.md#adding-your-own-functions).
