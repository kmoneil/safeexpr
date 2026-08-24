"""Alert thresholds the on-call rota can edit, without giving them a shell.

    python examples/alert_rules.py

The job: whoever gets woken up should be able to change what wakes them, and a bad threshold
should be a refused rule rather than a paging storm at 3am.

What this shows.

**Rules are strings, in whatever store you already have.** Nothing here is compiled into the
service; the same list could come from a database row or a config map.

**A rule that breaks is reported, not raised.** The loop catches per rule. A rule referring to a
metric that stopped being collected is a config bug, and an alerting loop that lets it escape
stops evaluating every rule after it, which is the failure mode where nobody gets paged at all.

**The severity ladder is host policy.** The expression answers a question; whether the answer
pages someone is decided in Python, where it can be tested and reviewed.
"""

from safeexpr import Evaluator, SafeExprError, standard_registry

ALERTS = [
    {"name": "error-rate", "when": "metrics.error_rate > 0.05", "page": True},
    {"name": "slow-p99", "when": "metrics.p99_ms > 800 and metrics.rpm > 100", "page": True},
    {"name": "disk-pressure", "when": "any_(hosts, _.disk_pct > 90)", "page": False},
    {"name": "all-hosts-degraded", "when": "all_(hosts, _.healthy == False)", "page": True},
    {"name": "queue-backing-up", "when": 'queues | pluck("depth") | max > 10000', "page": False},
    {"name": "stale-metric", "when": "metrics.gc_pause_ms > 500", "page": False},
]

SNAPSHOT = {
    "metrics": {"error_rate": 0.07, "p99_ms": 410, "rpm": 950},
    "hosts": [
        {"name": "web-1", "disk_pct": 62, "healthy": True},
        {"name": "web-2", "disk_pct": 94, "healthy": False},
    ],
    "queues": [{"name": "ingest", "depth": 4}, {"name": "reindex", "depth": 40000}],
}

RULES = Evaluator(registry=standard_registry())


def main() -> None:
    print("== the snapshot ==\n")
    for key, value in SNAPSHOT.items():
        print(f"  {key:<9} {value}")

    print("\n== evaluating every rule ==\n")
    firing, broken = [], []
    for alert in ALERTS:
        try:
            hit = RULES.evaluate(alert["when"], SNAPSHOT)
        except SafeExprError as error:
            broken.append((alert["name"], error.message))
            print(f"  BROKEN  {alert['name']:<20} {error.message}")
            continue
        if hit:
            firing.append(alert)
            page = "PAGE" if alert["page"] else "ticket"
            print(f"  FIRING  {alert['name']:<20} -> {page}")
        else:
            print(f"  quiet   {alert['name']:<20}")

    print(f"\n  {len(firing)} firing, {len(broken)} broken, {len(ALERTS)} evaluated.")

    print("\n== the broken one, shown the way its author would see it ==\n")
    for alert in ALERTS:
        try:
            RULES.evaluate(alert["when"], SNAPSHOT)
        except SafeExprError as error:
            for line in error.annotated().splitlines():
                print(f"  {line}")

    print(
        "\n  `stale-metric` is not wrong about anything except reality: nothing collects\n"
        "  gc_pause_ms any more. That is the failure a rule store grows on its own, and it is\n"
        "  why every rule is evaluated against a sample when the config loads. See\n"
        "  examples/rules_from_config.py."
    )

    print("\n== the rule shape people get wrong first ==\n")
    for source in [
        "queues | max_by(_.depth) | _.depth > 10000",
        "(queues | max_by(_.depth)).depth > 10000",
        'queues | pluck("depth") | max > 10000',
    ]:
        try:
            print(f"  {source:<44} -> {RULES.evaluate(source, SNAPSHOT)!r}")
        except SafeExprError as error:
            print(f"  {source:<44} !! {error.message}")

    print(
        "\n  `_` exists only inside an argument that takes an expression, and `max_by` is not one\n"
        "  of those on its *left*. Reach into a result with ordinary syntax instead: wrap the\n"
        "  pipeline in brackets and read the field, or pull the column out and take its max.\n"
        "  The third form needs no brackets because `|` binds tighter than `>`."
    )


if __name__ == "__main__":
    main()
