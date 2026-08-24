"""The refusals, run rather than described.

    python examples/what_is_refused.py

Everything below is a real attempt, evaluated, with the actual refusal printed. Each group maps
to a failure class in THREAT-MODEL.md, which carries the mechanism, the advisories where it has
broken a real project, and the corpus entry proving it is unreachable here.

Two things worth knowing before you read the output.

**Most of these are refused before evaluation starts.** A `ValidationError` comes from the node
allowlist, which walks the parsed tree and refuses anything not explicitly permitted. That is the
opposite of a denylist: a construct nobody thought about is refused by default rather than
allowed by default.

**The ones that look ergonomic are not.** Comprehensions expose `gi_frame`, f-strings call a
value's own `__format__`, and a method call reaches an arbitrary object's attributes. Each of
those has a published escape behind it in another sandbox.

This is defence in depth for a config-authoring surface, not a boundary for genuinely hostile
input. If you must run that, use process isolation. No in-interpreter CPython sandbox, this one
included, should be your only boundary.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())

# A real callable, in the context, which is the F3 precondition: the host handed the expression
# something dangerous as a *value*. Nothing in the language can reach it, because a value from
# the context cannot be called at all, whatever it happens to be.
CONTEXT = {
    "text": "hello",
    "items": [1, 2, 3],
    "user": {"name": "bo"},
    "callback": print,
    "tools": {"run": print},
}

GROUPS = [
    (
        "F1  runtime reflection through formatting",
        [
            'f"{user}"',
            'f"{user!r}"',
            '"{}".format(user)',
            '"{0.__class__}".format(user)',
        ],
    ),
    (
        "F2  attribute traversal to __subclasses__",
        [
            "text.__class__",
            "text.__class__.__mro__",
            "().__class__.__bases__[0].__subclasses__()",
            "user.__dict__",
            "user.__getattribute__",
        ],
    ),
    (
        "F3  a callable smuggled in as a value",
        [
            "callback('escaped')",
            "tools.run('escaped')",
            "items[0]()",
            "text.upper()",
            "callback",
        ],
    ),
    (
        "F4  resource exhaustion",
        [
            "2 ** 100000000",
            '"a" * 999999999',
            'matches(text, "^(a+)+$")',
        ],
    ),
    (
        "F6  stack-frame walking in generators",
        [
            "[x for x in items]",
            "(x for x in items)",
            "{x for x in items}",
            "{k: k for k in items}",
        ],
    ),
    (
        "F7  new syntax as new attack surface",
        [
            "lambda x: x",
            "(y := 1)",
            "await items",
            "items[0] if True else lambda: 1",
        ],
    ),
    (
        "imports and I/O, which have no failure class because they have no route in",
        [
            '__import__("os")',
            "open('/etc/passwd')",
            'eval("1+1")',
            "exec('x=1')",
            "globals()",
            "print(user)",
        ],
    ),
    (
        "underscore names, refused by name before anything else",
        [
            "_secret",
            "__builtins__",
            "user._private",
        ],
    ),
]


def main() -> None:
    total, refused = 0, 0
    for title, sources in GROUPS:
        print(f"== {title} ==\n")
        for source in sources:
            total += 1
            try:
                value = RULES.evaluate(source, CONTEXT)
            except SafeExprError as error:
                refused += 1
                print(f"  {source:<44} {type(error).__name__:<16} {error.message[:70]}")
            else:
                print(f"  {source:<44} {'ALLOWED':<16} {value!r}")
        print()

    print(f"== {refused} of {total} refused ==\n")
    print(
        "  The one that is not refused is the interesting line. A bare `callback` returns the\n"
        "  function object, because the host put it in the context and reading a name is\n"
        "  reading a name. It cannot be called, it cannot be reached through, and it cannot be\n"
        '  formatted into a string: `str` refuses it, `f"{callback}"` does not parse, and every\n'
        "  attribute of it starts with an underscore. That is F3 in one line: a dangerous\n"
        "  callable in scope is still just a value.\n"
        "\n  Do not put one there anyway. The language holding is not a reason to hand it one."
    )

    print("\n== the tripwire that makes this observable rather than intentional ==\n")
    print("  python scripts/audit_fuzz.py")
    print(
        "\n  Fuzzes the evaluator with sys.addaudithook watching, and fails if *any* audit event\n"
        "  fires during evaluation beyond this package parsing its own source. `exec`, `import`,\n"
        "  `open`, `os.system` and the subprocess events are all observed process-wide, so an\n"
        "  escape trips it whether or not anybody wrote a test for that escape.\n"
        "\n  Audit hooks are a test tripwire here and not a defence layer: nothing\n"
        "  installs one at\n"
        "  runtime. They observe rather than block, they fire process-wide so a host would pay\n"
        "  for every audited operation in its process, and a hook cannot be uninstalled once\n"
        "  added, which makes it a target rather than a shield if a sandbox is already broken."
    )


if __name__ == "__main__":
    main()
