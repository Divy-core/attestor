# Phase 0 — Discovery

> Everything here was **measured on this project**, not taken from documentation or memory.
> Every row names the command that produced it. Build against this file, not against the plan's
> assumptions — several of those turned out to be wrong, and they are called out explicitly.
>
> Project `attestor-505506` · account `divy.ds.x@gmail.com` · 14 Aug 2026
> gcloud `580.0.0` · `gcloud services list --available` returned **12,557** services.

---

## 2.1 Which APIs actually exist

Command:

```bash
gcloud services list --available --project attestor-505506 \
  --format="value(config.name,config.title)" > all-apis.txt
wc -l all-apis.txt        # 12557
```

Then grepped per capability. Results — **exact** service names:

| Capability | Exact service name | Title as returned | Found |
|---|---|---|---|
| Agent Runtime / Agent Engine | `aiplatform.googleapis.com` | **Agent Platform API** | YES |
| Agent Registry | `agentregistry.googleapis.com` | Agent Registry API | YES |
| Agent Identity | `agentidentity.googleapis.com` | Agent Identity API | YES |
| Agent Identity (credentials) | `agentidentitycredentials.googleapis.com` | Agent Identity Credentials API | YES |
| Model Armor | `modelarmor.googleapis.com` | Model Armor API | YES |
| Vertex AI Search | `discoveryengine.googleapis.com` | Discovery Engine API | YES |
| Gemini API (AI Studio surface) | `generativelanguage.googleapis.com` | Gemini API | YES |
| Firestore | `firestore.googleapis.com` | Cloud Firestore API | YES |
| Cloud Run | `run.googleapis.com` | Cloud Run Admin API | YES |
| Eventarc | `eventarc.googleapis.com` | Eventarc API | YES |
| Pub/Sub | `pubsub.googleapis.com` | Cloud Pub/Sub API | YES |
| Secret Manager | `secretmanager.googleapis.com` | Secret Manager API | YES |
| Cloud Trace | `cloudtrace.googleapis.com` | Cloud Trace API | YES |
| Artifact Registry | `artifactregistry.googleapis.com` | Artifact Registry API | YES |
| Cloud Tasks | `cloudtasks.googleapis.com` | Cloud Tasks API | YES |
| Cloud Build | `cloudbuild.googleapis.com` | Cloud Build API | YES |
| IAM | `iam.googleapis.com` | Identity and Access Management (IAM) API | YES |
| IAM SA credentials | `iamcredentials.googleapis.com` | IAM Service Account Credentials API | YES |
| GCS | `storage.googleapis.com` | Cloud Storage API | YES |
| Telemetry | `telemetry.googleapis.com` | Telemetry API | YES |

### Three findings that change the plan

**1. `aiplatform.googleapis.com` is now titled "Agent Platform API", not "Vertex AI API".**
This settles the open question in the build prompt: Agent Runtime is surfaced under
`aiplatform.googleapis.com` as `reasoningEngine` resources. There is no separate
"agentruntime" service.

**2. Agent Registry and Agent Identity are separate, first-class APIs.**
The locked plan assumed both were implicit in `aiplatform`. They are not —
`agentregistry.googleapis.com`, `agentidentity.googleapis.com`, and
`agentidentitycredentials.googleapis.com` each exist and must be enabled in their own
right. `infra/bootstrap.sh` enables all three. If auto-registration in the Registry does
not appear to work after the Section 6 deploy, an unenabled `agentregistry` API is the
first thing to check.

**3. There is no `memorybank` service.** Memory Bank is part of the Agent Platform surface
under `aiplatform.googleapis.com`, not a separately enableable API. Nothing to enable for it.

### Services already enabled on the project before we touched it

```
analyticshub · bigquery (+6 bigquery-*) · cloudapis · cloudtrace · dataform · dataplex
datastore · logging · monitoring · servicemanagement · serviceusage · sql-component
storage-api · storage-component · storage · telemetry
```

Notable: `cloudtrace`, `storage`, and `telemetry` were **already on** (project defaults).
`aiplatform`, `modelarmor`, `discoveryengine`, `firestore`, `run`, `pubsub`, `eventarc`,
`secretmanager`, `artifactregistry`, `agentregistry`, and `agentidentity` were **not**.

Also notable: `datastore.googleapis.com` is enabled but `firestore.googleapis.com` is not,
and no Firestore database exists yet. The database must be created in **Native mode** —
see Section 3.

---

## 2.2 Which regions

### Model Armor supported locations — `us-central1` IS supported

The plan's highest-risk regional assumption holds. Queried the service's own locations
endpoint rather than trusting docs:

```bash
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://modelarmor.googleapis.com/v1/projects/attestor-505506/locations?pageSize=200"
```

**19 locations returned:**

```
asia-northeast1  asia-northeast3  asia-south1   asia-southeast1  australia-southeast2
eu               europe-southwest1 europe-west1 europe-west2     europe-west3
europe-west4     europe-west9      global       northamerica-northeast2
us               us-central1       us-east1     us-east4         us-west1
```

`us-central1 present: True`. **No region split is needed.** Everything stays pinned to
`us-central1` as the locked plan requires.

Note the multi-region aliases `us`, `eu`, and `global` are also offered — relevant later
if a floor setting needs to apply above a single region.

A `gcloud model-armor` command group also exists (confirmed via `gcloud model-armor --help`),
so floor settings and templates can be managed from the CLI rather than raw REST.

---

## 2.3 ADK version and its deploy surface

Commands:

```bash
uv run python -c "import google.adk, google.genai; print(google.adk.__version__, google.genai.__version__)"
uv pip list | grep -Ei "google|vertex|adk|genai"
```

| Package | Version installed |
|---|---|
| `google-adk` | **2.7.0** |
| `google-genai` | 2.18.1 |
| `google-cloud-aiplatform` | 1.164.0 |
| `agentplatform` | 1.164.0 (ships with `google-cloud-aiplatform`) |
| `vertexai` | 1.164.0 |
| `google-cloud-firestore` | 2.28.1 |
| `google-cloud-storage` | 3.13.1 |
| `google-cloud-trace` | 1.20.0 |

**Deviation from the locked plan: ADK is 2.7.0, not 2.6.x.** The stack table in the
Master Architecture doc names 2.6.x. 2.7.0 is what resolves today; we build against it.

### Agent Identity — the plan's import path is WRONG

The build prompt (and the locked plan) say:

```python
from google.genai import types
config={"identity_type": types.IdentityType.AGENT_IDENTITY}
```

Measured result:

```bash
$ uv run python -c "from google.genai import types; print([x for x in dir(types) if 'Identity' in x])"
[]

$ uv run python -c "from google.genai import types; print(list(types.IdentityType))"
AttributeError: module 'google.genai.types' has no attribute 'IdentityType'
```

**`IdentityType` does not exist in `google.genai.types`.** It actually lives in two places,
both under a private `_genai` module:

```bash
$ uv run python -c "from agentplatform._genai import types as t; print([e.value for e in t.IdentityType])"
['IDENTITY_TYPE_UNSPECIFIED', 'SERVICE_ACCOUNT', 'AGENT_IDENTITY']

$ uv run python -c "from vertexai._genai import types as t; print([e.value for e in t.IdentityType])"
['IDENTITY_TYPE_UNSPECIFIED', 'SERVICE_ACCOUNT', 'AGENT_IDENTITY']
```

There is **no public re-export** — both `agentplatform.types` and `vertexai.types` raise
`ModuleNotFoundError`.

#### The complete accepted value set, and where it is defined

Read from the installed package (reading is fine; it is *importing at runtime* we avoid).
Defined identically in two places, both at **line 261**:

- `.venv/Lib/site-packages/agentplatform/_genai/types/common.py:261`
- `.venv/Lib/site-packages/vertexai/_genai/types/common.py:261`

```python
class IdentityType(_common.CaseInSensitiveEnum):
    """The identity type to use for the Reasoning Engine. ..."""

    IDENTITY_TYPE_UNSPECIFIED = "IDENTITY_TYPE_UNSPECIFIED"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"
    AGENT_IDENTITY = "AGENT_IDENTITY"
```

**The complete set of accepted strings is exactly:**
`"IDENTITY_TYPE_UNSPECIFIED"`, `"SERVICE_ACCOUNT"`, `"AGENT_IDENTITY"`.

Two things in the docstrings that matter for the deploy:

1. The base class is `CaseInSensitiveEnum`, so casing is forgiving — but we pass the
   exact upper-case string anyway.
2. **`AGENT_IDENTITY` carries a hard constraint: _"Use Agent Identity. The
   `service_account` field must not be set."_** Setting both `identity_type=AGENT_IDENTITY`
   and `service_account` is a deploy error. `AgentEngineConfig` exposes both fields
   (`['service_account', 'identity_type']`), so this is easy to get wrong.

The create surface:

```bash
$ uv run python -c "import inspect; from agentplatform._genai import agent_engines as ae; print(inspect.signature(ae.AgentEngines.create))"
(self, *, agent_engine: Any = None, agent: Any = None,
 config: AgentEngineConfig | AgentEngineConfigDict | None = None) -> AgentEngine

$ uv run python -c "from agentplatform._genai import types as t; print([k for k in t.AgentEngineConfig.model_fields if 'ident' in k or 'account' in k])"
['service_account', 'identity_type']
```

**Decision for `services/runtime/deploy.py`:** `create()` accepts `AgentEngineConfigDict`,
so pass the enum **value as a plain string** rather than importing from a private module:

```python
config={"identity_type": "AGENT_IDENTITY"}
```

This is the documented enum value (`AGENT_IDENTITY`), verified above, and it avoids taking
a dependency on `agentplatform._genai`, which is private and free to move between patch
releases. The private import is recorded here as the fallback if the string form is
rejected at deploy time.

_`adk --help` / `adk deploy agent_engine --help` flag capture: pending, folded into
Section 6._

---

## 2.4 Which Gemini models are available to this project

```python
from google import genai
c = genai.Client(vertexai=True, project="attestor-505506", location="us-central1")
for m in c.models.list():
    print(m.name)
```

127 models listed; 26 Gemini entries. **All three target strings are present and exact —
no substitution needed:**

| Plan's intended use | Model string | Available |
|---|---|---|
| Reasoning / drafting | `gemini-3.5-flash` | **YES** |
| High-volume triage classification | `gemini-3.5-flash-lite` | **YES** |
| Tested swap ("3.5 or newer") | `gemini-3.6-flash` | **YES** |

Full Gemini list as returned (prefix `publishers/google/models/`):

```
gemini-2.5-computer-use-preview-10-2025   gemini-2.5-flash         gemini-2.5-flash-image
gemini-2.5-flash-lite                     gemini-2.5-flash-tts     gemini-2.5-pro
gemini-2.5-pro-tts                        gemini-3-flash-preview   gemini-3-pro-image
gemini-3-pro-preview                      gemini-3.1-flash-image   gemini-3.1-flash-image-preview
gemini-3.1-flash-lite                     gemini-3.1-flash-lite-image
gemini-3.1-flash-lite-preview             gemini-3.1-flash-tts-preview
gemini-3.1-pro-preview                    gemini-3.5-flash         gemini-3.5-flash-lite
gemini-3.6-flash                          gemini-3.7-flash         gemini-embedding-001
gemini-embedding-2                        gemini-live-2.5-flash-native-audio
gemini-omni-flash-preview                 gemini-robotics-er-2-preview-info
```

**Unanticipated: `gemini-3.7-flash` is also available**, newer than anything the locked
plan contemplated. We stay on `gemini-3.5-flash` as primary regardless — the hackathon
brief names 3.5 Flash explicitly and matching the brief is worth more than chasing the
newest string. 3.6 and 3.7 are recorded as available swaps.

**Decision (unchanged from plan, now evidence-backed):**
`MODEL_REASONING=gemini-3.5-flash`, `MODEL_CLASSIFY=gemini-3.5-flash-lite`.

---

## 2.5 Model Armor request/response shape

_Pending — Section 7. Phase 2's `screen_long_text()` chunker is built directly against
whatever is recorded here, including the real token/byte limit on the prompt injection
filter (the plan assumes ~512 tokens)._
