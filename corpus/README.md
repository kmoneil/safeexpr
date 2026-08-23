# Escape corpus

The versioned record of published sandbox escapes this package rejects, and the artifact a
security reviewer asks for.

`escapes-v1.jsonl` holds one JSON object per line. `tests/test_corpus.py` runs every entry on
every supported interpreter; `python scripts/lanes.py corpus` runs just this.

## What an entry claims

| Field | Meaning |
| --- | --- |
| `id` | Unique, stable, human-readable |
| `failure_class` | `F1` to `F9`, the escape class this entry proves unreachable |
| `source_lib` | Which project's disclosure this came from |
| `provenance` | CVE, advisory ID, or a citation |
| `expression` | The source to run |
| `context` | Named fixture supplying the values, since JSON cannot express a callable |
| `stage` | Where it must be rejected: `parse`, `validate` or `evaluate`, or `allowed` for a control |
| `expect_message` | Substring the error must contain, so an entry cannot pass for the wrong reason |
| `note` | Optional. What the attack is, and why it is interesting |
| `python_min` / `python_max` | Optional. Some attacks only exist where the syntax does |

## Why the `stage` field carries the weight

"The expression was rejected" is a weak claim, because it is also true of a typo. Each entry says
*where* rejection must happen, and the harness fails an entry rejected at the wrong stage even
though it was still rejected. For anything expected past the parser, the harness separately
asserts the expression is valid Python, so an entry mistyped into a syntax error fails rather
than quietly reporting a pass.

Two properties are checked on **every** entry rather than being entries of their own: no error
may carry `__cause__` or `__context__` (F9, checked corpus-wide), and every rejection must be a
`SafeExprError`.

The `allowed` controls matter as much as the rejections. A corpus of nothing but rejections would
pass against a sandbox that refuses everything, which is not a sandbox anybody can use.

## Adding an entry

Write the entry, run the lane, and expect it to fail first. An entry that passes immediately is
worth re-reading: it may be rejected for a reason unrelated to the one it claims, and the
`expect_message` field exists to pin that down.

Three entries here were found by writing this corpus rather than ported from a disclosure. They
are marked in their `note` and attributed to `safeexpr`.
