SHELL := /bin/bash
.DEFAULT_GOAL := help

PROJECT_ID ?= $(shell gcloud config get-value project 2>/dev/null)
REGION     ?= us-central1

.PHONY: help setup lint types test layering copy check bootstrap deploy teardown fmt types-gen types-check cov seed recall calibrate run verify verify-denial verify-poison verify-consistency

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Sync the uv workspace and install the pre-commit hook
	uv sync --all-packages
	@# The web workspace too. `make check` type-checks the console with tsc, so a clone that
	@# only synced Python fails on `types` with "Command \"tsc\" not found" -- which reads as
	@# a broken toolchain rather than a missing install step. Found by re-cloning the
	@# repository and following the README, which is the only way this class of gap shows up.
	@if [ -f services/web/package.json ]; then \
		cd services/web && pnpm install --frozen-lockfile; \
	fi
	@if [ -d .git ]; then \
		printf '#!/bin/sh\nexec make check\n' > .git/hooks/pre-commit; \
		chmod +x .git/hooks/pre-commit; \
		echo "installed .git/hooks/pre-commit -> make check"; \
	fi

lint: ## ruff check + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## ruff format (writes)
	uv run ruff format .
	uv run ruff check --fix .

types: ## mypy --strict over every Python source root
	uv run mypy --config-file mypy.ini \
		packages/attestor-core/src \
		packages/attestor-platform/src \
		packages/attestor-fleet/src \
		services/control-plane/src \
		services/dispatcher/src \
		tools
	@if [ -f services/web/package.json ]; then \
		cd services/web && pnpm exec tsc --noEmit; \
	else \
		echo "types: services/web not present yet, skipping tsc"; \
	fi

test: ## pytest
	uv run pytest

layering: ## Enforce the package dependency invariant
	uv run python tools/check_layering.py

copy: ## Fail the build on rationale rendered as product copy
	@if [ -f services/web/package.json ]; then \
		cd services/web && node scripts/check-copy.mjs && node scripts/check-tokens.mjs; \
	fi

types-gen: ## Regenerate services/web/lib/types/generated.ts from attestor_core.protocol
	uv run python tools/gen_types.py

types-check: ## Fail if the committed generated.ts is stale
	uv run python tools/gen_types.py --check

cov: ## Branch coverage on state/ and policy/, which must stay at 100%
	uv run pytest tests/unit --cov=attestor_core.state --cov=attestor_core.policy --cov-branch --cov-report=term-missing --cov-fail-under=100

check: lint types test layering types-check copy ## lint + types + test + layering + drift + copy

seed: ## Seed corpus, datastores, and Firestore fixtures (idempotent)
	PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) uv run python seed/seed.py

recall: ## Measure retrieval recall@5, raw vs expanded (gate: 0.85)
	PROJECT_ID=$(PROJECT_ID) uv run python tools/recall_harness.py --no-model --write-proof

calibrate: ## Measure the retrieval-score distribution and derive confidence thresholds
	PROJECT_ID=$(PROJECT_ID) uv run python tools/calibrate_confidence.py --write-proof

run: ## The authoritative 312-question run (clean questionnaire, orchestrated)
	PROJECT_ID=$(PROJECT_ID) uv run python tools/run_review.py \
		--questionnaire clean --orchestrate --write-proof

verify: verify-denial verify-poison verify-consistency ## Every defence proof, in order
	@echo "all defence verifications passed"

verify-denial: ## SecurityAgent reaching for the legal corpus is refused
	PROJECT_ID=$(PROJECT_ID) uv run python tools/verify_defences.py --case denial --write-proof

verify-poison: ## Injection planted in a real corpus document is caught before context
	PROJECT_ID=$(PROJECT_ID) uv run python tools/verify_defences.py --case poison --write-proof

verify-consistency: ## Round 2 cannot contradict round 1, natural and under fault injection
	PROJECT_ID=$(PROJECT_ID) uv run python tools/verify_consistency.py --write-proof
	PROJECT_ID=$(PROJECT_ID) uv run python tools/verify_consistency.py --write-proof --inject-drift

bootstrap: ## Enable APIs and create project resources (idempotent)
	PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) bash infra/bootstrap.sh

deploy: ## Deploy all services
	PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) bash infra/deploy.sh

teardown: ## Remove every billable resource
	PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) bash infra/teardown.sh
