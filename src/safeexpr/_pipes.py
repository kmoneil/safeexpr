r"""The pipe transform: `items | where(...)` becomes `where(items, ...)`.

`|` is Python's bitwise-or, and this package borrows it for chaining. That borrowing needs a rule
for telling the two apart, and the rule is deliberately narrow:

    the right-hand side is a pipe **if and only if** the function name is in the registry.

**The context is never consulted, and that is the whole argument.** Registry membership is fixed
when the evaluator is built, so the same source always rewrites the same way. A rule that looked
at the context would make an expression mean different things on different data, which cannot be
a compatibility promise. `tests/test_pipes.py` asserts the rewrite is identical across contexts
that disagree about every name in it.

The consequence, stated because it is a real cost: **registry names are reserved in right-of-pipe
position**. With `first` in the registry, `flags | first` is `first(flags)` even if the context
has a variable called `first`.

This also deletes a bug the predecessor had by construction. That implementation split pipes with
a regular expression, `^(\w+)`, which matches digits, so the bitwise-or guard never fired:
`flags | mask` became `mask(flags)` and `flags | 2` became `2(flags)`. Here the decision is a node
type checked against a set, so a `Constant` on the right is not a name and cannot be mistaken for
one.

**The rewrite is iterative.** A 2047-byte source holds a 1023-stage pipe chain, and
`ast.NodeTransformer` recurses, so a recursive rewrite raises `RecursionError` on input this
package accepts. Measured before this module was written, which is the same reason the validator
walks with an explicit stack.
"""

from __future__ import annotations

import ast
from collections.abc import Container

# A node together with where it sits in its parent, which is what makes replacing it possible.
# `slot` is the list index for a node in a list-valued field, or `None` for a plain one.
_Slot = tuple[ast.AST, ast.AST, str, "int | None"]


def _piped_call(node: ast.BinOp, functions: Container[str]) -> ast.Call | None:
    """Return the call `node` should become, or `None` to leave it as bitwise-or.

    Args:
        node: A `BinOp` whose operator is `BitOr`.
        functions: The registered function names.

    Returns:
        The rewritten `Call`, or `None` if this is ordinary bitwise-or.
    """
    right = node.right

    # `x | f(a)` -> `f(x, a)`. The piped value becomes the first argument, so the function reads
    # in the order the pipe was written.
    if (
        isinstance(right, ast.Call)
        and isinstance(right.func, ast.Name)
        and right.func.id in functions
    ):
        return ast.copy_location(
            ast.Call(func=right.func, args=[node.left, *right.args], keywords=right.keywords),
            node,
        )

    # `x | f` -> `f(x)`. Only a bare name, and only one already in the registry.
    if isinstance(right, ast.Name) and right.id in functions:
        return ast.copy_location(ast.Call(func=right, args=[node.left], keywords=[]), node)

    return None


def shadowed_pipes(tree: ast.Expression, names: Container[str]) -> list[ast.BinOp]:
    """Every `|` in `tree` whose right-hand name is one of `names`.

    Read-only, and separate from `transform` on purpose: the transform must never consult the
    context, because a rule that did would make an expression mean different things on different
    data. This does not change any decision, it reports one that has already been made, and it
    runs before the rewrite because afterwards `x | first` and `first(x)` are the same tree.

    Walked with an explicit stack, like everything else here: a 2047-byte source holds a
    1023-stage pipe chain.

    Args:
        tree: A parsed expression, before the rewrite.
        names: The names to look for.

    Returns:
        The offending `BinOp` nodes, in the order found, so a caller can report a position.
    """
    found: list[ast.BinOp] = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            right = node.right
            named = (
                right.id
                if isinstance(right, ast.Name)
                else right.func.id
                if isinstance(right, ast.Call) and isinstance(right.func, ast.Name)
                else None
            )
            if named is not None and named in names:
                found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _slots(tree: ast.Expression) -> list[_Slot]:
    """Every node in `tree`, paired with where it sits in its parent.

    Collected with an explicit stack rather than recursion: a 2047-byte source holds a 1023-stage
    pipe chain, and a recursive walk of one raises `RecursionError`.

    The result is a pre-order list, in which a parent always precedes its descendants. Walking it
    backwards therefore rewrites children before parents, which `a | f | g` needs: the inner
    rewrite has to be in place before the outer one reads its left operand.

    Seeded from the body rather than the root, so every entry has a real parent to be written back
    into and there is no `None` case to carry through the caller's loop.
    """
    found: list[_Slot] = []
    stack: list[_Slot] = [(tree.body, tree, "body", None)]
    while stack:
        entry = stack.pop()
        found.append(entry)
        node = entry[0]
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for position, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        stack.append((item, node, field, position))
            elif isinstance(value, ast.AST):
                stack.append((value, node, field, None))
    return found


def transform(tree: ast.Expression, functions: Container[str]) -> ast.Expression:
    """Rewrite pipe chains in `tree`, in place, returning the same tree.

    The same object is returned rather than a copy, so that validation and evaluation continue to
    see one tree with no window between them.

    Args:
        tree: A parsed expression.
        functions: The registered function names. Membership decides pipe from bitwise-or.

    Returns:
        `tree`, with pipe chains rewritten.
    """
    for node, parent, field, slot in reversed(_slots(tree)):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)):
            continue
        replacement = _piped_call(node, functions)
        if replacement is None:
            continue
        if slot is None:
            setattr(parent, field, replacement)
        else:
            getattr(parent, field)[slot] = replacement

    return tree
