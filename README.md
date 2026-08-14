# Attestor

> An enterprise agent fleet that answers vendor security reviews with evidence, memory,
> and a defensible audit trail.

Built for the **All Things Agentic Hackathon — Track 3: The Fortified Enterprise Fleet**.

> **Status: Phase 0 (Foundations & Proof of Life).** This README is a placeholder.
> The real one — with the architecture diagram and a three-command spin-up — lands in Phase 8.
> For what has actually been built and verified so far, read [`PROGRESS.md`](PROGRESS.md)
> and [`docs/proof/`](docs/proof/).

## The problem

Sell software to any company larger than five people and you get sent a vendor security
review: a 200–400 question spreadsheet (SOC 2 / ISO 27001 / CAIQ), a DPA, a subprocessor
list, and a data-residency questionnaire. It takes 20–40 hours of archaeology through
internal policy docs. Every answer must be evidenced. Every answer must be consistent with
what you told that same customer three weeks ago. Then round two arrives.

Attestor is a fleet of department-scoped agents that ingests the questionnaire, routes each
question to the agent that owns that domain, drafts an evidenced answer with citations,
blocks the prompt injections hidden in the customer's own document, remembers what was
committed to in previous rounds, and escalates only the answers it is not confident about.

## Repository layout

```
packages/attestor-core       pure domain: model, state machine, deny/ask/allow policy
packages/attestor-platform   every Google Cloud edge behind one boundary
packages/attestor-fleet      the ADK agents; bundled to Agent Runtime
services/control-plane       FastAPI on Cloud Run
services/dispatcher          Eventarc/Pub-Sub push subscriber on Cloud Run
services/runtime             the Agent Runtime (reasoningEngine) bundle
services/web                 Next.js 15 UI on Cloud Run
infra/                       bootstrap, deploy, teardown, IAM, Model Armor config
tools/check_layering.py      mechanically enforces the dependency invariant
docs/proof/                  measured evidence for every phase exit criterion
```

## The dependency invariant

```
attestor_core      ->  stdlib + pydantic only
attestor_platform  ->  attestor_core (+ google/GCP SDKs)
attestor_fleet     ->  attestor_core, attestor_platform (+ google-adk)
services/*         ->  packages/*, never another service
packages/*         ->  never a service
```

`attestor_fleet` is bundled and shipped to Agent Runtime, so it must never reach a web
framework. This is enforced in CI by `tools/check_layering.py`, not by convention.

## Development

```bash
make setup   # uv sync + install the pre-commit hook
make check   # lint + types + test + layering
```

## License

Apache-2.0. See [LICENSE](LICENSE).
