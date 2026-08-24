# Security policy

**A sandbox escape is always a critical bug here.** Not a hardening opportunity, not a
documentation issue, not "working as designed for semi-trusted input". If an expression reaches
something the language does not offer, that is the most serious class of defect this project has,
and it is treated as one.

That sentence is in this file because the alternative has been tried. The competitive scan that
produced [`THREAT-MODEL.md`](THREAT-MODEL.md) turned up a maintainer-behaviour failure as damaging
as any code bug: a sandboxing library whose maintainer declined to treat a sandbox escape as
critical, closed the advisory, shipped a fix without a release or a credit, and left users
uninformed, which is why that escape ended up disclosed through a CTF write-up and a
full-disclosure mailing list instead. For a package whose entire value proposition is safety, the
disclosure process **is** part of the product, so it is written down before it is needed rather
than improvised afterwards.

## Reporting

**Do not open a public issue for a suspected escape.** A public report on a sandbox is a working
exploit published to everyone who has not upgraded yet, and there is no upgrade available at the
moment you file it.

Report privately, either way:

- **GitHub private vulnerability reporting**, which is preferred:
  <https://github.com/kmoneil/safeexpr/security/advisories/new>
- **Email:** kevin@oneil.xyz

If you are unsure whether what you have found is a security issue, report it privately anyway. A
private report that turns out to be an ordinary bug costs one message; a public report that turns
out to be an escape cannot be taken back.

Please include the expression, the context values it needs, the Python version, and what you
reached. A failing corpus entry is the ideal form of report and is not required.

## What is in scope

In scope, and treated as critical:

- Evaluating an expression reaches an attribute, key, module, callable or frame that the language
  does not offer.
- A value from the context is called, or a value's method is called.
- An error carries a reference to the caller's data, through `__cause__`, `__context__`,
  `__notes__`, `args`, or its own message. See F9 in the threat model.
- Anything that is not a `SafeExprError` escapes a public entry point, including a
  `BaseException` subclass.
- A documented limit does not hold: source length, expression nesting, the step budget, result
  size, data nesting or the power cap.

In scope, and triaged on the facts rather than automatically critical:

- Resource exhaustion inside a documented bound. F4 is bounded, not eliminated, and the threat
  model says so.

Out of scope, because they are documented rather than accidental:

- The limits in [`THREAT-MODEL.md`](THREAT-MODEL.md) under "What this does not bound". The step
  budget does not bound regular-expression time, memory amplification is mitigated rather than
  eliminated, `Ctrl-C` does not interrupt an evaluation in progress, and a host that registers a
  type has opted that type back into attribute traversal.
- Anything that requires the host to pass a hostile object into the context *and* register its
  type. Registration is the documented escape hatch, and the host owns what it registers.

If you think something in the "out of scope" list is wrong, that is a valuable report too. Send
it privately and it will be argued on the merits.

## What happens next

| When | What |
| --- | --- |
| Within 3 working days | Acknowledgement that the report arrived, from a human |
| Within 10 working days | An assessment: reproduced or not, and the class it falls into |
| Before any public detail | A fix, a release, and an advisory, published together |

Every accepted escape ships as **a new release**, never as a silent push to the main branch. A fix
that reaches the repository without a release leaves every installed copy vulnerable and tells
nobody, which is the specific failure this policy exists to avoid.

Four things happen in the same change, and none of them is optional:

1. **A release.** Versioned, published, with the advisory referencing it.
2. **A CVE requested**, so the fix is discoverable by tooling that has never heard of this
   project.
3. **The reporter credited**, by the name they choose, or anonymously if they prefer.
4. **A corpus entry added**, in the same change as the fix, tagged with its failure class and
   carrying its provenance.

The fourth is the one that makes the other three durable. `corpus/escapes-v1.jsonl` runs on every
supported interpreter on every change, so an escape that has been fixed once stays fixed, and the
document describing the class it belongs to is checked against those entries by
`tests/test_threat_model.py`. An escape reported here becomes a permanent test, not a patch note.

## Supported versions

**This package has not been released yet.** The current version is a pre-release, and its
`Development Status :: 3 - Alpha` classifier is stated in `pyproject.toml`, in the README's
"Status" section and here, so that the three move together rather than one of them being left
behind.

There is therefore no supported release to report against yet, and no version table that would
not be fiction. Reports against the main branch are welcome now and are handled by the process
above, minus the release step, which becomes real with the first published version.

Once there is a released version, the policy is:

- **The latest release** receives security fixes. Before 1.0 there is no backporting; the fix is
  in the next release and the upgrade path is one version.
- **Python 3.11, 3.12, 3.13 and 3.14** are supported, and a fix is verified on all four before it
  ships. The floor is set so that every interpreter in the matrix stays in upstream security
  support; it moves when one of them leaves.

## Provenance

Releases are published to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/),
so there is no long-lived API token in this repository. Each artifact carries a signed
[PEP 740](https://peps.python.org/pep-0740/) attestation naming the workflow that built it, and
`tests/test_lanes.py` asserts that the workflow still produces one, so this paragraph cannot
quietly stop being true.

Every release also carries a CycloneDX SBOM. **It is nearly empty, which is the whole point:**
this package has no runtime dependencies, and the SBOM is that claim in a form a procurement
process can read without taking our word for it. It is generated from the built wheel rather than
from `pyproject.toml`, so it describes the artifact rather than the intention.

The two answer different questions and neither replaces the other: the attestation says who built
this, the SBOM says what is inside it.

## What this package is not

Read the scope statement in [`THREAT-MODEL.md`](THREAT-MODEL.md) before reporting, because it
bounds what a report can reasonably claim:

> Expressions come from semi-trusted config authors, not anonymous internet users. The sandbox is
> defense in depth for a config-authoring surface. If you must run genuinely hostile input, use
> process isolation. No in-interpreter CPython sandbox, this one included, should be your only
> boundary.

That is a statement about what a host should rely on. It is **not** a reason to downgrade an
escape: the sentence above about critical bugs is the operative one, and "semi-trusted" is not a
defence for a hole. The two coexist because a package can both take its own boundary seriously and
tell you honestly not to make it your only one.
