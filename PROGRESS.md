# PROGRESS

What was built, how it was verified, and every deviation with its reason.
Measured, not asserted — each entry names the command that proved it.

---

## Phase 0 — Foundations & Proof of Life (Days 1–2, 14–15 Aug 2026)

### Prerequisites (Track A remainder)

| Item | State | How verified |
|---|---|---|
| gcloud CLI installed | DONE | `gcloud version` → `Google Cloud SDK 580.0.0`, `bq 2.1.36`, `core 2026.08.07`, `gsutil 5.37` |
| `gcloud auth login` | DONE | `You are now logged in as [divy.ds.x@gmail.com]`; `gcloud auth list` shows it ACTIVE |
| `gcloud auth application-default login` | DONE | `Credentials saved to file: [...\gcloud\application_default_credentials.json]`; quota project set to `attestor-505506` |
| Project set to `attestor-505506` | DONE | `gcloud config get-value project` → `attestor-505506`; `gcloud projects describe` → name `Attestor`, `lifecycleState: ACTIVE`, projectNumber `906988347581` |
| uv installed | DONE | `uv --version` → `uv 0.12.4 (77803aa22 2026-08-13 x86_64-pc-windows-msvc)` |
| Python 3.12 installed | DONE | `uv python install 3.12` → `cpython-3.12.13-windows-x86_64-none`; interpreter reports `Python 3.12.13` |
| Python 3.12 pinned | DONE | `uv python pin 3.12` → `.python-version` contains `3.12` |

**Deviation — gcloud install method.** The documented Windows installer is interactive.
Installed instead from the official versionless archive
`https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64-bundled-python.zip`
extracted to `%LOCALAPPDATA%\gcloud\google-cloud-sdk`, which needs no elevation and no
prompts. The first URL tried (`.../channels/rapid/google-cloud-cli-windows-x86_64.zip`)
returns HTTP 404 — the versionless Windows archives live under `/downloads/`.
gcloud requires `CLOUDSDK_PYTHON` pointed at its bundled interpreter; the system
Python 3.13 is a Microsoft Store alias that gcloud refuses. Set in the user environment.

**Deviation — how the interactive auth prompt was driven.** `gcloud auth login
--no-launch-browser` reads the verification code from stdin, but this shell is
non-interactive. Three failed attempts cost two of the user's verification codes before
the cause was measured rather than guessed:

- Symptom: `ERROR: (gcloud.auth.login) (invalid_grant) Malformed auth code.`
- First hypothesis (wrong): a UTF-8 BOM written into the code file by
  `Set-Content -Encoding utf8`. Fixed it; the error persisted.
- Actual cause, found with an echo-stdin harness: gcloud was receiving
  `'﻿4/0AXEQ...'` — 74 chars for a 73-char code. Reading .NET's
  `Process.StandardInput` sets `AutoFlush = true`, which flushes the UTF-8 encoding
  **preamble** into the pipe before anything is written. Writing raw bytes to
  `BaseStream` does not help because the preamble is already gone, and Windows
  PowerShell 5.1 (.NET Framework) has no `ProcessStartInfo.StandardInputEncoding` to
  suppress it.
- Fix: give gcloud an ordinary OS pipe instead of a .NET writer —
  `python code_waiter.py <codefile> | gcloud auth login --no-launch-browser`, where the
  waiter blocks until the code file appears and decodes it as `utf-8-sig`. Verified
  against the harness (73 chars, no BOM, even with a BOM deliberately in the file)
  before spending another code.

Lesson worth keeping: `invalid_grant / Malformed auth code` blames the credential, but
the fault was entirely in transport. The harness that isolated it cost far less than the
guessing did.

### Section 1 — Environment verification

All abort conditions clear. Run 14 Aug 2026:

| Check | Result |
|---|---|
| `gcloud version` | `Google Cloud SDK 580.0.0` |
| `gcloud auth list` | `divy.ds.x@gmail.com` (ACTIVE) |
| `gcloud config get-value project` | `attestor-505506` |
| `gcloud auth application-default print-access-token` | token returned → **ADC OK** |
| `gcloud billing projects describe attestor-505506` | `billingEnabled: true`, `billingAccountName: billingAccounts/012520-481530-4CCD15` |
| `uv run python --version` | `Python 3.12.13` (project interpreter) |
| `uv --version` | `uv 0.12.4` |
| `node --version` | `v24.13.1` (20+ required) |
| `npm --version` | `11.8.0` |

**Note on Python.** The *system* interpreter is 3.13.3, but the build never uses it —
`.python-version` pins 3.12 and the uv-managed workspace interpreter is 3.12.13, which is
what `make lint/types/test` and CI run against. Recorded rather than silently glossed.

**Note on the account.** Auth is `divy.ds.x@gmail.com`, not the `shauryaopjp.virat@gmail.com`
address on the Claude session. The billing account with the credits is attached to the
former, which is what matters; both gcloud auth and ADC use the same principal.

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
