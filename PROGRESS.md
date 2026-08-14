# PROGRESS

What was built, how it was verified, and every deviation with its reason.
Measured, not asserted — each entry names the command that proved it.

---

## Phase 0 — Foundations & Proof of Life (Days 1–2, 14–15 Aug 2026)

### Prerequisites (Track A remainder)

| Item | State | How verified |
|---|---|---|
| gcloud CLI installed | | |
| `gcloud auth login` | | |
| `gcloud auth application-default login` | | |
| Project set to `attestor-505506` | | |
| uv installed | DONE | `uv --version` → `uv 0.12.4 (77803aa22 2026-08-13 x86_64-pc-windows-msvc)` |
| Python 3.12 installed | DONE | `uv python install 3.12` → `cpython-3.12.13-windows-x86_64-none`; interpreter reports `Python 3.12.13` |
| Python 3.12 pinned | DONE | `uv python pin 3.12` → `.python-version` contains `3.12` |

**Deviation — gcloud install method.** The documented Windows installer is interactive.
Installed instead from the official versionless archive
`https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64-bundled-python.zip`
extracted to `%LOCALAPPDATA%\gcloud\google-cloud-sdk`, which needs no elevation and no
prompts. The first URL tried (`.../channels/rapid/google-cloud-cli-windows-x86_64.zip`)
returns HTTP 404 — the versionless Windows archives live under `/downloads/`.

### Section 1 — Environment verification

_Pending._

### Section 2 — Discovery

_Pending._ Output goes to `docs/proof/PHASE-0-DISCOVERY.md`.

### Section 3 — APIs and cost guardrails

_Pending._

### Section 4 — Repo skeleton

Created at repo root, before any cloud work, since it has no dependency on discovery:

- `.gitignore` committed **first**, covering `.env`, `.env.*` (except `.env.example`),
  service-account JSON patterns, `__pycache__`, `.venv`, `node_modules`, `.next`,
  `*.log`, `/tmp`.
- `pyproject.toml` — uv workspace root, members only, `package = false`.
- `ruff.toml`, `mypy.ini` (`strict = True`), `.python-version` (3.12), `.env.example`.
- `Makefile` — `setup`, `lint`, `fmt`, `types`, `test`, `layering`, `check`,
  `bootstrap`, `deploy`, `teardown`.
- Three packages and four services, each with `pyproject.toml` and a `src/` layout;
  empty directories carry `.gitkeep`.
- `.github/workflows/ci.yml` — uv sync, ruff, mypy --strict, pytest, layering.

**Deviation — `services/runtime` has no `src/` layout.** It is the bundle root handed to
`agent_engines.create()`, so `app.py` and `deploy.py` sit at its top level, matching the
locked repo-structure doc. `tools/check_layering.py` zones it accordingly.

### Section 5 — Layering checker

`tools/check_layering.py` parses imports with `ast` (not regex) and enforces:

```
attestor_core      -> stdlib + pydantic only
attestor_platform  -> attestor_core (+ google/GCP SDKs)
attestor_fleet     -> attestor_core, attestor_platform (+ google-adk)
                      never fastapi/uvicorn/starlette/flask/django/... , never a service
services/*         -> packages/*, never another service
packages/*         -> never a service
```

`tests/unit/test_check_layering.py` builds deliberate violations in temp trees and asserts
each is caught, including the load-bearing case (`attestor_fleet` importing `fastapi`).

_Verification pending — see the `make check` run below._

### Section 6 — Agent Runtime deploy

_Pending._

### Section 7 — Model Armor

_Pending._

### Section 8 — Cloud Run hello-world

_Pending._

### Section 9 — GO/NO-GO

_Pending._ Goes to `docs/proof/PHASE-0-GATE.md`.
