## What changed, and why

<!-- The why matters more than the what; the diff already says the what. -->

## Registry review gate

**Required for any change that adds or edits a registry function.** Delete this section if the
change touches no registry entry.

- [ ] **No runtime reflection.** No `format`, `format_map`, `%` on text, `string.Formatter`,
      `getattr`, `setattr`, `vars`, `dir`, `type`, `reduce`, `eval`, `exec`. A static AST
      allowlist cannot see an attribute lookup a function performs at runtime, so one convenient
      reflective call here reopens the climb the validator exists to close.
- [ ] **Nothing recursive over host data**, or the recursion is depth-guarded and detects cycles.
      Data comes from the host and can be nested or self-referential.
- [ ] **Arity and lazy positions declared**, and the lazy positions are the ones that genuinely
      take an expression.
- [ ] **A new tier module is listed in `TIER_MODULES`** in `tests/test_collections.py`, so the
      reflection scan covers it. The test that every registered callable is defined in a scanned
      module should fail if it is not; check that it did.

## Checks

- [ ] `python scripts/lanes.py gates format types fast corpus`
- [ ] Coverage holds at 90% line / 85% branch: `pytest --cov=src --cov-branch --cov-fail-under=90`
- [ ] Hot paths benchmarked if touched: `pytest tests/benchmarks --benchmark-only`
      (needs `uv sync --frozen --group measure`)
- [ ] Every bug fix has a regression test that failed before the fix
