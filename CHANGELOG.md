# Changelog

All notable changes to this project are documented here.

## Unreleased

Scaffold only. Nothing in this package evaluates an expression yet.

### Added
- Project scaffold: `pyproject.toml`, `src/safeexpr/` layout, Apache-2.0 licence text, CI matrix
  over Python 3.10 through 3.14.
- Zero-dependency enforcement: `tests/test_zero_deps.py` reads declared metadata, and
  `scripts/check_zero_deps.py` imports a built wheel in an interpreter with nothing else in it.
- `scripts/lanes.py`, the single spelling of how this project runs its checks, with
  `tests/test_lanes.py` asserting every lane is wired into CI.
- Parse boundary: source is capped at 2048 bytes *before* it reaches `ast.parse`, and every
  parser failure surfaces as a `SafeExprError`. The cap is set by Python 3.11, whose parser
  gives out at 2,989 levels of operator nesting against roughly 5,975 on every other supported
  version.
- **Public error hierarchy**, rooted at `SafeExprError` and exported from the package:
  `ParseError`, `ValidationError`, `SourceTooLongError`, `InternalError`. Every failure this
  package produces is one of these, and no error carries a reference to the data that caused it.
  Errors are constructed from scrubbed parts and raised outside the handler that caught the
  cause, because `raise ... from None` leaves `__context__` live and on Python 3.10+ that is a
  reachable handle on the caller's object.
- `SafeExprError.annotated()` renders a message above the offending source with a caret under
  the position.
- **Node allowlist.** The supported language is defined by what is listed rather than by what is
  forbidden, so syntax added by a future Python is rejected until it is reviewed. Rejections name
  the construct ("list comprehensions are not supported") and point at it. Attribute and
  constant-subscript access to underscore-prefixed names is blocked, and only `_`, `_1`, `_2` and
  so on are accepted as names beginning with an underscore.

### Known limitations
- **There is no evaluator.** Nothing here computes the value of an expression. The parse and
  validation layers are private modules; only the error hierarchy is public API.
- Validation catches underscore-prefixed subscript keys only when they are written as literals.
  A computed key such as `x["__cl" + "ass__"]` passes this layer by design and is the
  evaluator's responsibility, so that half of the defence does not exist yet.
- The escape corpus is an empty directory with a schema sketch. Until it ships and passes, the
  package's central security claim is unproven, which is what the `Development Status :: 3 - Alpha`
  classifier is saying.
