# The language

A strict subset of Python's own expression grammar, parsed by the standard library's `ast` and
then checked node by node against an allowlist. If Python cannot parse it, neither can this; if
this package has not explicitly allowed a node type, it is refused with a message naming the
construct.

Every example below is `expression => result`, and
[`tests/test_docs.py`](../tests/test_docs.py) evaluates each one and compares it to the printed
result, so an example that stops being true fails a build.

## Values

```text
42                              => 42
-3                              => -3
1.5                             => 1.5
"text"                          => 'text'
'single quotes too'             => 'single quotes too'
True                            => True
False                           => False
None                            => None
[1, 2, 3]                       => [1, 2, 3]
{"a": 1, "b": 2}                => {'a': 1, 'b': 2}
[]                              => []
```

Booleans are Python's `True` and `False`, not `true` and `false`. Nothing is `None`. Both are
written the way the surrounding language writes them, because the parser is that language's.

## Names read the context

A bare name is a lookup in the mapping you passed as the context, and reaching a name that is not
there is an error rather than `None`:

```python
from safeexpr import evaluate

evaluate("plan", {"plan": "pro"})
# 'pro'
```

Names beginning with an underscore are refused outright, with the single exception of `_`, `_1`,
`_2` and so on, which are the [pipe item](pipes.md):

```text
__import__                      => !ValidationError
```

## Field access

Dots read keys from a mapping. `user.plan` and `user["plan"]` mean the same thing:

```text
{"user": {"plan": "pro"}}["user"]["plan"]     => 'pro'
{"a": {"b": {"c": 1}}}["a"]["b"]["c"]         => 1
```

```python
from safeexpr import evaluate

evaluate("user.address.city", {"user": {"address": {"city": "Berlin"}}})
# 'Berlin'
```

A missing key raises rather than returning nothing, so a typo in a rule is a loud failure and not
a silently false condition:

```python
from safeexpr import SafeExprError, evaluate

try:
    evaluate("user.pln", {"user": {"plan": "pro"}})
except SafeExprError as error:
    print(error.message)
# no field `pln`, did you mean `plan`?
```

Use [`default`](functions.md#default) when a field is genuinely optional.

**Dots do not reach attributes.** On anything that is not a mapping, `.name` is refused unless the
host has registered that type with `attribute_types`, which is the one door this package leaves
closed by default and opens only from your side. See
[Embedding](embedding.md#attribute-access-on-your-own-objects).

## Indexing and slicing

```text
[10, 20, 30][0]                 => 10
[10, 20, 30][-1]                => 30
[10, 20, 30][0:2]               => [10, 20]
[10, 20, 30][1:]                => [20, 30]
[1, 2, 3, 4][0:4:2]             => [1, 3]
"hello"[1:3]                    => 'el'
{"a": 1}["a"]                   => 1
```

## Arithmetic

```text
1 + 2 * 3                       => 7
(1 + 2) * 3                     => 9
10 / 4                          => 2.5
10 // 4                         => 2
10 % 3                          => 1
2 ** 10                         => 1024
"a" + "b"                       => 'ab'
"ab" * 2                        => 'abab'
[1] + [2]                       => [1, 2]
```

Precedence and associativity are Python's, because the parser is Python's. There is nothing to
learn here that you did not already know, and nothing to get subtly different from what the
grammar says.

Mixing types that have no meaning together is an error rather than a coercion:

```text
"a" + 1                         => !EvaluationError
1 / 0                           => !EvaluationError
```

`**` is bounded by the width of the number it would produce, so an expression cannot spend a
process's memory on one operator:

```text
2 ** 100000000                  => !EvaluationError
```

## Comparison

```text
1 < 2                           => True
2 >= 2                          => True
"a" == "a"                      => True
"a" != "b"                      => True
1 < 2 < 3                       => True
None == None                    => True
```

Chained comparison works and means what it does in Python: `1 < 2 < 3` is `1 < 2 and 2 < 3`, with
the middle term evaluated once.

Comparing values with no ordering between them is refused, and the error names the two types:

```text
1 < "a"                         => !EvaluationError
```

That message is the one place this package tells an expression author something about your data,
and it is a type's name rather than a value. [The threat model](../THREAT-MODEL.md) records why
the trade was taken.

## Membership

```text
"x" in ["x", "y"]               => True
"z" not in ["x", "y"]           => True
"a" in {"a": 1}                 => True
"ell" in "hello"                => True
```

`in` over a mapping tests its keys, as it does in Python.

## Boolean logic

```text
True and False                  => False
True or False                   => True
not True                        => False
1 and 2                         => 2
0 or "fallback"                 => 'fallback'
```

`and` and `or` short-circuit and return one of their operands rather than a boolean, again exactly
as Python does. When you want a real boolean, [`bool`](functions.md#bool) is in the types tier.

Truthiness follows Python: `0`, `0.0`, `""`, `[]`, `{}` and `None` are false, everything else is
true.

## Conditionals

```text
"yes" if 1 > 0 else "no"        => 'yes'
"a" if False else "b"           => 'b'
```

The branch not taken is not evaluated, which matters when one side is expensive or would fail:

```python
from safeexpr import Evaluator, standard_registry

rules = Evaluator(registry=standard_registry())
rules.evaluate(
    "first(rows).total if len(rows) > 0 else 0",
    {"rows": []},
)
# 0
```

## Calls

Only names registered with the evaluator can be called, and only with positional arguments:

```text
len([1, 2, 3])                  => 3
```

```text
len(x=1)                        => !ValidationError
```

**Methods on values are not callable.** `text.upper()` is refused, because reaching a method means
reaching an attribute of an arbitrary object, which is where published sandbox escapes start. The
tier functions are the spelling instead: `upper(text)`, or `text | upper`.

```text
"abc".upper()                   => !ValidationError
```

## Pipes

With a non-empty registry, `|` chains a value into the call on its right:

```text
[3, 1, 2] | sort_by(_)          => [1, 2, 3]
[1, 2, 3] | where(_ > 1) | len  => 2
```

`x | f(a)` is exactly `f(x, a)`, and `x | f` is `f(x)`. [Pipes and `_`](pipes.md) covers the item
variable, nesting, and the one collision this creates.

With an **empty** registry there are no pipes and `|` is bitwise or, which is also what `bitor`
does when a name you need is taken:

```text
6 | 3                           => 7
```

## What is deliberately absent

Each of these is refused by the node allowlist with a message naming it, and each is a decision
rather than a gap:

| Construct | Refused because |
| --- | --- |
| `[x for x in items]` | Comprehensions and generators expose `gi_frame`, a published escape elsewhere. Use `map` or `where` |
| `lambda x: x` | Not needed: lazy arguments give `where(_.price > 10)` without one |
| `f"{value}"` | Interpolation calls the value's own `__format__`, `__repr__` or `__str__`. Use `+` or `join` |
| `x := 1` | Assignment of any kind. An expression produces a value and changes nothing |
| `await`, `yield` | No concurrency, no suspension, no I/O |
| `import`, `__import__` | No imports, ever. Underscore names are refused before this is even reached |
| `a.b()` where `a` is a value | Method calls reach an arbitrary object's attributes |
| `x if x else` over statements | There are no statements. One expression, one value |

The absence of iteration is what makes termination structural rather than enforced: there is no
loop to bound, so [the step budget](performance.md#the-step-budget) is a backstop behind a
guarantee rather than the guarantee itself.

## Reading on

- [Function reference](functions.md), which is where the verbs live
- [Pipes and `_`](pipes.md), the one piece of syntax that is not Python's meaning
- [Errors](errors.md), for what each refusal above actually looks like at runtime
