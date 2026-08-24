"""Dates and URLs: two tiers that exist because rules keep asking for them.

    python examples/dates_and_urls.py

Three things worth taking from this.

**There is no clock.** Nothing in the language can ask what time it is, which is what makes an
expression a pure function of its context: the same rule against the same data gives the same
answer forever, in a test, in a replay, and in production. A rule about "recent" takes the
current time from the host, as a value in the context.

**`format_date` allows only portable directives.** `%c`, `%x` and `%X` are locale-defined all the
way down, and `%s`, `%-d` and `%e` are platform extensions rather than Python guarantees. A rule
whose output depends on the C library of the machine that ran it is a rule you cannot test.

**A URL with no scheme has no host.** `url_host("example.com/x")` is `None`, because without a
scheme there is no authority to parse and the whole string is a path. An allowlist that forgets
that is an allowlist that passes anything. Nothing here fetches a URL; parsing a string opens
nothing.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

RULES = Evaluator(registry=standard_registry())

NOW = "2026-08-24T12:00:00+00:00"

RECORDS = [
    {
        "id": "r-1",
        "updated": "2026-08-24T09:30:00+00:00",
        "callback": "https://hooks.example.com/v2/in",
    },
    {"id": "r-2", "updated": "2026-06-01T00:00:00+00:00", "callback": "https://evil.test/v2/in"},
    {"id": "r-3", "updated": "2026-08-23T23:59:59+00:00", "callback": "hooks.example.com/v2/in"},
]


def show(source: str, context: dict | None = None) -> None:
    try:
        print(f"  {source:<58} -> {RULES.evaluate(source, context or {})!r}")
    except SafeExprError as error:
        print(f"  {source:<58} !! {error.message}")


def main() -> None:
    print("== dates ==\n")
    show('parse_iso("2026-08-24T13:05:00Z")')
    show('parse_iso("2026-08-24")')
    show('parse_iso("24/08/2026")')
    show('format_date(parse_iso("2026-08-24"), "%Y-%m-%d")')
    show('format_date(parse_iso("2026-08-24"), "%d %B %Y")')
    show('format_date(parse_iso("2026-08-24"), "%c")')
    show('format_date("2026-08-24", "%Y")')

    print("\n== a freshness rule, with the clock passed in ==\n")
    rule = "parse_iso(record.updated) > parse_iso(cutoff)"
    print(f"  now    = {NOW}")
    print("  cutoff = now minus 24 hours, computed in the host")
    print(f"  rule   = {rule}\n")
    cutoff = "2026-08-23T12:00:00+00:00"
    for record in RECORDS:
        fresh = RULES.evaluate(rule, {"record": record, "cutoff": cutoff})
        print(f"  {record['id']}  updated {record['updated']}  fresh={fresh}")
    print(
        "\n  The host owns the clock. That is not a limitation to work around: a rule that could\n"
        "  read the time would give a different answer on a replay of the same data, and the\n"
        "  first thing anybody does with a rule engine is replay data."
    )

    print("\n== urls ==\n")
    show('url_host("https://api.Example.com:8443/v2/items?page=2")')
    show('url_path("https://api.example.com/v2/items?page=2")')
    show('url_query("https://api.example.com/v2/items?page=2&limit=50")')
    show('url_host("https://user:password@api.example.com/x")')
    show('url_host("example.com/x")')
    show('url_query("https://a/b?x=1&x=2")')

    print("\n== an allowlist, and the input that breaks the naive version ==\n")
    allow = (
        'url_host(record.callback) in ["hooks.example.com"]'
        ' and starts_with(url_path(record.callback), "/v2/")'
    )
    deny = 'not (url_host(record.callback) in ["evil.test"])'
    print(f"  allowlist: {allow}")
    print(f"  denylist:  {deny}\n")
    for record in RECORDS:
        context = {"record": record}
        print(
            f"  {record['id']}  {record['callback']:<34}"
            f"  allowlist={RULES.evaluate(allow, context)!s:<5}"
            f"  denylist={RULES.evaluate(deny, context)}"
        )
    print(
        "\n  r-3 has no scheme, so `url_host` is None. The allowlist fails closed and the\n"
        "  denylist fails *open*, on the same input, from the same fact. Write the rule that\n"
        "  fails the safe way when the parse gives you nothing.\n"
        "\n  And note what neither rule does: resolve the name. If the decision matters, pin the\n"
        "  address as well, which is a different problem than this library solves."
    )

    print("\n== query values are always text ==\n")
    show('url_query("https://a/b?page=2")["page"] == 2')
    show('int(url_query("https://a/b?page=2")["page"]) == 2')
    show('int(url_query("https://a/b?page=x")["page"])')


if __name__ == "__main__":
    main()
