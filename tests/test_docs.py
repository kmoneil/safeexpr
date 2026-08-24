"""The documentation, checked against the code it describes.

Three of this project's own recorded lessons are the same lesson: a number in a comment is a
number nobody checks. `tests/test_readme.py` applies that to the front page. This applies it to
`docs/`, which is larger and makes many more checkable claims.

Two kinds of claim are checked, and both are executed rather than read:

**Expression examples**, written `expression => result` inside a ```` ```text ```` block. Each one
is evaluated against the standard registry with an empty context, and its `repr` compared to the
printed result. A result beginning with `!` names the error class the expression must raise.

**Code blocks**, written as ```` ```python ````. Every block in a document runs, in order, in one
shared namespace, so a block may continue from the one above it. Where a block ends in `# `
comment lines, those are the output it must produce: either what it printed, or the `repr` of its
final expression, which is how the blocks read as a session.

An example that has drifted out of sync with the library is a confident, wrong answer somebody
will copy, which is worse than no example at all.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import re
import warnings

import pytest

import safeexpr
from _docs import EM_DASH, ROOT, anchor_link_targets, read, relative_link_targets, slugs
from safeexpr import Evaluator, SafeExprError, standard_registry
from safeexpr._eval import DEFAULT_STEP_BUDGET
from safeexpr._guards import SIZE_CHARGE_UNIT

DOCS = ROOT / "docs"
DOC_FILES = sorted(DOCS.glob("*.md"))
DOC_NAMES = [path.name for path in DOC_FILES]

EXAMPLE = re.compile(r"^(?P<expression>\S.*?)\s+=>\s+(?P<result>\S.*)$")
TEXT_BLOCK = re.compile(r"^```text\n(.*?)^```", re.M | re.S)
PYTHON_BLOCK = re.compile(r"^```python\n(.*?)^```", re.M | re.S)

# Documents that carry executable expression examples. Listed rather than discovered, so a new
# document with examples has to be added here deliberately and cannot arrive unchecked.
WITH_EXPRESSIONS = ("language.md", "functions.md", "pipes.md")


def _flat(text: str) -> str:
    """One line, single-spaced: these documents wrap, and a reader sees whole sentences."""
    return " ".join(text.split())


def expression_examples(name: str) -> list[tuple[str, str]]:
    """Every `expression => result` line in a document, in order."""
    found = []
    for block in TEXT_BLOCK.findall(read(f"docs/{name}")):
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = EXAMPLE.match(line)
            if match:
                found.append((match.group("expression"), match.group("result").strip()))
    return found


ALL_EXAMPLES = [
    pytest.param(name, expression, result, id=f"{name}:{expression}"[:120])
    for name in WITH_EXPRESSIONS
    for expression, result in expression_examples(name)
]


class TestTheExpressionExamples:
    @pytest.mark.parametrize(("name", "expression", "result"), ALL_EXAMPLES)
    def test_the_example_produces_what_it_says(self, name: str, expression: str, result: str):
        evaluator = Evaluator(registry=standard_registry())
        if result.startswith("!"):
            with pytest.raises(SafeExprError) as caught:
                evaluator.evaluate(expression)
            assert type(caught.value).__name__ == result[1:], (
                f"{name}: {expression} raised {type(caught.value).__name__}, not {result[1:]}"
            )
            return
        assert repr(evaluator.evaluate(expression)) == result, f"{name}: {expression}"

    def test_there_are_examples_to_run(self):
        """A parser that quietly matched nothing would pass every test above."""
        assert len(ALL_EXAMPLES) > 150, f"only {len(ALL_EXAMPLES)} examples found"

    def test_every_document_that_should_have_examples_has_some(self):
        for name in WITH_EXPRESSIONS:
            assert expression_examples(name), f"docs/{name} has no checked examples"

    def test_both_result_shapes_are_exercised(self):
        """A suite with no `!` results would not be checking any of the refusals."""
        results = [result for _, _, result in (param.values for param in ALL_EXAMPLES)]
        assert sum(1 for result in results if result.startswith("!")) > 15
        assert sum(1 for result in results if not result.startswith("!")) > 100


def expected_output(code: str) -> str:
    """The trailing `# ` comment lines of a block, which are the output it claims to produce."""
    tail = []
    for line in reversed(code.rstrip().splitlines()):
        if line == "#":
            tail.append("")
        elif line.startswith("# "):
            tail.append(line[2:])
        else:
            break
    return "\n".join(reversed(tail)).strip()


def _split_trailing_expression(code: str) -> tuple[ast.Module, ast.Expression | None]:
    """Separate a block's final bare expression, so a document can read as a session.

    A block ending in `rules.evaluate(...)` followed by `# 4` is showing a value, not printed
    output. A block ending in `print(...)` is showing printed output. Both spellings appear in
    these documents because both are how people write examples.
    """
    module = ast.parse(code)
    last = module.body[-1] if module.body else None
    if not isinstance(last, ast.Expr):
        return module, None
    call_to_print = (
        isinstance(last.value, ast.Call)
        and isinstance(last.value.func, ast.Name)
        and last.value.func.id == "print"
    )
    if call_to_print:
        return module, None
    module.body = module.body[:-1]
    return module, ast.Expression(last.value)


def run_blocks(name: str) -> list[tuple[str, str]]:
    """Run every python block in a document, returning `(expected, actual)` per block."""
    namespace: dict = {}
    outcomes = []
    for index, code in enumerate(PYTHON_BLOCK.findall(read(f"docs/{name}"))):
        module, trailing = _split_trailing_expression(code)
        printed = io.StringIO()
        origin = f"docs/{name}:block{index}"
        with warnings.catch_warnings():
            # The suite runs with `filterwarnings = error`, and a block must not need an
            # exemption the reader would not get.
            warnings.simplefilter("error")
            with contextlib.redirect_stdout(printed):
                exec(compile(module, origin, "exec"), namespace)  # noqa: S102
                if trailing is not None:
                    value = eval(compile(trailing, origin, "eval"), namespace)  # noqa: S307
                    if value is not None:
                        print(repr(value))
        outcomes.append((expected_output(code), printed.getvalue().strip()))
    return outcomes


class TestTheCodeBlocks:
    @pytest.mark.parametrize("name", DOC_NAMES)
    def test_every_block_runs_and_prints_what_it_claims(self, name: str):
        for index, (expected, actual) in enumerate(run_blocks(name)):
            if expected:
                assert actual == expected, f"docs/{name} block {index}"

    def test_there_are_blocks_to_run(self):
        total = sum(len(PYTHON_BLOCK.findall(read(f"docs/{name}"))) for name in DOC_NAMES)
        assert total > 25, f"only {total} python blocks found across docs/"

    def test_most_blocks_claim_an_output(self):
        """A block with no expected output is only checked for running at all."""
        claimed = sum(1 for name in DOC_NAMES for expected, _ in run_blocks(name) if expected)
        assert claimed > 15, f"only {claimed} blocks pin their own output"


class TestTheDocumentsThemselves:
    @pytest.mark.parametrize("name", DOC_NAMES)
    def test_no_em_dashes(self, name: str):
        assert EM_DASH not in read(f"docs/{name}")

    @pytest.mark.parametrize("name", DOC_NAMES)
    def test_relative_links_resolve(self, name: str):
        for target in relative_link_targets(read(f"docs/{name}")):
            assert (DOCS / target).resolve().exists(), f"docs/{name} links to nothing: {target}"

    @pytest.mark.parametrize("name", DOC_NAMES)
    def test_anchor_links_resolve(self, name: str):
        text = read(f"docs/{name}")
        for anchor in anchor_link_targets(text):
            assert anchor in slugs(text), f"docs/{name} links to #{anchor}, which is not a heading"

    @pytest.mark.parametrize("name", DOC_NAMES)
    def test_cross_document_anchors_resolve(self, name: str):
        """A link into another document's section, which no link checker catches by accident."""
        for link in re.findall(r"\]\(([a-z-]+\.md)#([a-z0-9-_]+)\)", read(f"docs/{name}")):
            target, anchor = link
            assert anchor in slugs(read(f"docs/{target}")), (
                f"docs/{name} links to {target}#{anchor}, which is not a heading there"
            )

    def test_the_index_lists_every_document(self):
        index = read("docs/README.md")
        linked = {target for target in relative_link_targets(index) if target.endswith(".md")}
        present = {name for name in DOC_NAMES if name != "README.md"}
        assert present - linked == set(), (
            f"not listed in docs/README.md: {sorted(present - linked)}"
        )

    def test_the_index_lists_nothing_that_is_missing(self):
        index = read("docs/README.md")
        for target in relative_link_targets(index):
            assert (DOCS / target).resolve().exists(), f"docs/README.md points at nothing: {target}"

    def test_the_examples_directory_is_pointed_at(self):
        assert "../examples/README.md" in read("docs/README.md")


class TestTheFunctionReference:
    """Forty-one functions plus `bitor`, and a reference is only a reference if it is complete."""

    @staticmethod
    def _documented() -> set[str]:
        return set(re.findall(r"^### (\w+)$", read("docs/functions.md"), re.MULTILINE))

    def test_every_registry_function_has_a_section(self):
        missing = set(standard_registry()) - self._documented()
        assert missing == set(), f"undocumented functions: {sorted(missing)}"

    def test_the_reference_documents_nothing_that_does_not_exist(self):
        extra = self._documented() - set(standard_registry()) - {"bitor"}
        assert extra == set(), f"documented but not registered: {sorted(extra)}"

    def test_bitor_is_documented_even_though_it_is_not_in_the_registry(self):
        assert "bitor" in self._documented()
        assert "bitor" not in standard_registry()

    def test_the_count_in_the_prose_is_the_count_in_the_code(self):
        assert "Forty-one functions across six tiers" in _flat(read("docs/functions.md"))
        assert len(standard_registry()) == 41

    def test_every_section_carries_an_example(self):
        """A heading with prose under it and nothing to run is a reference nobody can check."""
        sections = re.split(r"^### ", read("docs/functions.md"), flags=re.MULTILINE)[1:]
        for section in sections:
            name = section.split("\n", 1)[0]
            assert "```" in section, f"`{name}` has no example block"


class TestThePipesDocument:
    def test_the_reserved_names_block_is_what_is_actually_reserved(self):
        block = re.search(r"forty-two:\n\n```text\n(.*?)\n```", read("docs/pipes.md"), re.S)
        assert block is not None, "the reserved-names block is gone"
        listed = set(block.group(1).split())
        reserved = set(Evaluator(registry=standard_registry()).function_names)
        assert listed - reserved == set(), f"listed but not reserved: {sorted(listed - reserved)}"
        assert reserved - listed == set(), f"reserved but not listed: {sorted(reserved - listed)}"

    def test_the_count_in_the_prose_is_the_count_in_the_code(self):
        assert len(Evaluator(registry=standard_registry()).function_names) == 42

    def test_the_nine_lazy_functions_are_named(self):
        lazy = {name for name, entry in standard_registry().items() if entry.lazy}
        text = read("docs/pipes.md")
        assert len(lazy) == 9
        for name in lazy:
            assert f"`{name}`" in text, f"docs/pipes.md does not name the lazy function {name}"


class TestThePerformanceDocument:
    def test_the_published_default_budget_is_the_default(self):
        assert f"{DEFAULT_STEP_BUDGET:,}" in read("docs/performance.md")

    def test_the_limits_table_names_the_error_each_limit_raises(self):
        text = read("docs/performance.md")
        for name in ("SourceTooLongError", "ValidationError", "BudgetExceededError"):
            assert name in text
            assert name in safeexpr.__all__

    def test_the_size_charge_unit_is_the_one_in_force(self):
        assert f"under {SIZE_CHARGE_UNIT} elements is charged nothing" in _flat(
            read("docs/performance.md")
        )


class TestTheEmbeddingDocument:
    def test_the_configuration_table_names_exactly_the_constructor_arguments(self):
        rows = set(re.findall(r"^\| `(\w+)` \|", read("docs/embedding.md"), re.MULTILINE))
        declared = set(inspect.signature(Evaluator.__init__).parameters) - {"self"}
        assert rows == declared

    def test_the_untrusted_input_section_carries_the_scope_statement_verbatim(self):
        """The same sentence as the README and the threat model. Three copies, one meaning."""
        sentence = "No in-interpreter CPython sandbox, this one included, should be your only"
        assert sentence in _flat(read("docs/embedding.md"))
        assert sentence in _flat(read("README.md"))
