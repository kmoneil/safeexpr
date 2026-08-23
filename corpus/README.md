# Escape corpus

The versioned, documented record of sandbox escapes this package rejects, and the artifact a
security reviewer asks for.

**Empty until the format is defined and the entries are ported.** It is in the sdist
include list already (`tests/test_packaging.py` asserts that) because the corpus is the security
argument: a distribution shipping the code without the tests that prove it is shipping an
unverifiable claim.

Planned schema, one JSON object per line in `escapes-v1.jsonl`:

| Field | Meaning |
| --- | --- |
| `expression` | the source to evaluate |
| `expected` | `rejected_at_parse` or `rejected_at_eval` |
| `python_versions_verified` | where the outcome was actually observed |
| `provenance` | CVE or advisory URL |
| `failure_class` | the failure class this entry proves unreachable |
| `source_lib` | which project's disclosure this came from |
