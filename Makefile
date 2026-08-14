SHELL := /bin/bash
.DEFAULT_GOAL := help

PROJECT_ID ?= $(shell gcloud config get-value project 2>/dev/null)
REGION     ?= us-central1

.PHONY: help setup lint types test layering check bootstrap deploy teardown fmt

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Sync the uv workspace and install the pre-commit hook
	uv sync --all-packages
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

check: lint types test layering ## lint + types + test + layering

bootstrap: ## Enable APIs and create project resources (idempotent)
	PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) bash infra/bootstrap.sh

deploy: ## Deploy all services
	PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) bash infra/deploy.sh

teardown: ## Remove every billable resource
	PROJECT_ID=$(PROJECT_ID) REGION=$(REGION) bash infra/teardown.sh
