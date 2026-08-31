# Attestor

> An enterprise agent fleet that answers vendor security questionnaires from your own
> documents, cites every claim, refuses what it cannot support, and holds the rest for a
> person.

Built for the **All Things Agentic Hackathon — Track 3: The Fortified Enterprise Fleet**.

**Live:** https://attestor-web-elrhl52mkq-uc.a.run.app · **Write-up:** [`docs/SUBMISSION.md`](docs/SUBMISSION.md)

---

## The run of record

> **150 questions · 136 cited (91%) · 79 checked by a separate agent identity · 63 held for
> a person · 13 minutes 26 seconds · 613 audit events.**

From [`docs/proof/demo-run.json`](docs/proof/demo-run.json). Every figure in this repository
traces to a file in [`docs/proof/`](docs/proof/); nothing here is asserted that was not
measured. Older figures elsewhere name the run they came from — the 312-question fixture and
the 43%, 48% and 84% citation rates are earlier runs, not this one.

---

## The problem

Sell software to any company larger than five people and you get sent a vendor security
review: a 200–400 question spreadsheet (SOC 2 / ISO 27001 / CAIQ), a DPA, a subprocessor
list, a data-residency questionnaire. It takes 20–40 hours of archaeology through internal
policy documents. Every answer has to be evidenced. Every answer has to be consistent with
what you told that same customer three weeks ago. Then round two arrives.

Attestor takes the questionnaire — from an email nobody read, or from a file dropped in a
browser — routes each question to the department agent that owns that domain, drafts an
answer from your own corpus with citations, has a **separate agent identity** check the
draft against the passages it cites, blocks the prompt injections hidden in the customer's
own spreadsheet, remembers what was committed to in earlier rounds, and escalates only the
answers it will not stand behind.

**What it will not do:** answer a question your documents do not support. There is a test
that keeps it off the web ([`tests/unit/test_no_web_answers.py`](tests/unit/test_no_web_answers.py))
— a fluent, well-cited answer sourced from a competitor's trust page, returned under your
company's name, is a worse outcome than a blank.

---

## Architecture

![Attestor architecture](docs/architecture.svg)

Every box in that diagram is a resource deployed in `attestor-505506` / `us-central1`.

---

## Spin-up

### Run it locally — three commands

Requires **Python 3.12+**, [**uv**](https://docs.astral.sh/uv/), **Node 20+** with
[**pnpm**](https://pnpm.io/) (the console is type-checked as part of the gate), and a Google
Cloud project with billing enabled. `gcloud auth application-default login` first.

Verified by cloning this repository into an empty directory and running exactly what is
below.

```bash
make setup                                        # sync the uv workspace, install the pre-commit hook
cp .env.example .env                              # edit PROJECT_ID if yours differs
make check                                        # lint + mypy --strict + 750 tests + layering + copy
```

That gets you a green repository with no cloud resources. To answer actual questions you
need the corpus and the datastores, which is one more command:

```bash
make seed                                         # corpus to GCS, three Vertex AI Search datastores, Firestore fixtures
make run                                          # the authoritative 312-question run, in-process
```

`make seed` is idempotent — re-running it costs nothing and changes nothing.

### Deploy it

```bash
make bootstrap                                    # enable APIs, create buckets, topics, service accounts, Model Armor templates
uv run python services/runtime/deploy_fleet.py    # bundle and publish the seven Agent Runtime engines
make deploy                                       # build and deploy the three Cloud Run services, wire Eventarc
```

`make bootstrap` and `make deploy` are both idempotent and safe to re-run. `make teardown`
removes every billable resource.

### Connect a mailbox (optional)

```bash
uv run python tools/gmail_authorize.py --client-secrets ~/Downloads/client_secret_*.json
uv run python tools/gmail_watch.py --apply --label Attestor
```

One consent screen, once, by the person who owns the mailbox. The watch is **scoped to a
single Gmail label** — nothing outside it ever produces a notification, and the mailbox
owner controls what that label catches with an ordinary Gmail filter. See
[`docs/decisions/`](docs/decisions/) for why a service account cannot do this.

---

## What each `make` target does

| Target | What it does |
|---|---|
| `make setup` | `uv sync --all-packages`, installs `.git/hooks/pre-commit → make check` |
| `make check` | The gate. lint + types + test + layering + type-drift + copy |
| `make lint` / `make fmt` | ruff check / ruff format |
| `make types` | `mypy --strict` over every Python source root |
| `make test` | pytest |
| `make cov` | Branch coverage on `state/` and `policy/`, which must stay at 100% |
| `make layering` | Enforces the package dependency invariant below |
| `make copy` | Fails the build on design rationale rendered as product copy |
| `make types-check` | Fails if `services/web/lib/types/generated.ts` has drifted from `attestor_core.protocol` |
| `make seed` | Corpus to GCS, three datastores, Firestore fixtures. Idempotent |
| `make recall` | Retrieval recall@5 over 63 hand-labelled pairs. Gate: 0.85 |
| `make run` | The authoritative 312-question run, in-process |
| `make verify` | Every defence proof: IAM denial, corpus poisoning, round-to-round consistency |
| `make bootstrap` / `make deploy` / `make teardown` | Cloud lifecycle. All idempotent |

---

## For a judge with four minutes

1. **[`docs/SUBMISSION.md`](docs/SUBMISSION.md)** — features, stack, data sources, and what
   was learned. The last section is the interesting one.
2. **The live console** — https://attestor-web-elrhl52mkq-uc.a.run.app. Open the top review
   and expand the **VerifierAgent** post: a second agent identity's verdict distribution
   over the first's work, with the separation-of-duties check beside it.
3. **[`docs/proof/the-24-percent.md`](docs/proof/the-24-percent.md)** — a diagnosis, from
   symptom to named platform quota to minute-by-minute correlation to the fix, ending in a
   run that went from 24% to 91%.
4. **[`docs/proof/`](docs/proof/)** — the artefact behind every number in this README.
5. **[`PROGRESS.md`](PROGRESS.md)** — eleven phases, what was built and how it was verified,
   including what did not work.

---

## Repository layout

```
packages/attestor-core       pure domain: model, state machine, deny/ask/allow policy
packages/attestor-platform   every Google Cloud edge behind one boundary
packages/attestor-fleet      the ADK agents; bundled to Agent Runtime
services/control-plane       FastAPI on Cloud Run
services/dispatcher          Eventarc/Pub-Sub push subscriber on Cloud Run
services/runtime             the Agent Runtime (reasoningEngine) bundle
services/web                 Next.js 15 console on Cloud Run
infra/                       bootstrap, deploy, teardown, IAM, Model Armor config
seed/                        the synthetic Kestrel Data corpus and questionnaires
tools/                       measurement harnesses; every docs/proof artefact names one
docs/proof/                  measured evidence for every phase exit criterion
docs/decisions/              the decisions, and what was rejected
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
framework. Enforced by [`tools/check_layering.py`](tools/check_layering.py) in `make check`,
not by convention.

## License

See [`LICENSE`](LICENSE).
