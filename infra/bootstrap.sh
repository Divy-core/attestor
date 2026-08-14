#!/usr/bin/env bash
#
# Attestor project bootstrap. Idempotent -- safe to run repeatedly; a second run must
# report everything as already-present and change nothing.
#
# Service names come from docs/proof/PHASE-0-DISCOVERY.md, which was measured against
# this project. They are NOT taken from documentation or memory. In particular:
#   - aiplatform.googleapis.com is titled "Agent Platform API"; Agent Runtime lives
#     there as reasoningEngine resources. There is no separate agentruntime service.
#   - agentregistry / agentidentity / agentidentitycredentials are SEPARATE APIs, not
#     implicit in aiplatform. An unenabled agentregistry is the first thing to suspect
#     if a deployed agent never appears in the Agent Registry.
#
# Usage:
#   PROJECT_ID=attestor-505506 REGION=us-central1 bash infra/bootstrap.sh
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set (no hardcoded project id in this repo)}"
REGION="${REGION:-us-central1}"
AR_REPO="${AR_REPO:-attestor}"

# Colour only when attached to a terminal, so logs stay clean.
if [[ -t 1 ]]; then
    B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; C=$'\033[36m'; R=$'\033[0m'
else
    B=""; G=""; Y=""; C=""; R=""
fi

created=(); existing=()

note_created()  { created+=("$1");  printf '  %sCREATED%s  %s\n' "$G" "$R" "$1"; }
note_existing() { existing+=("$1"); printf '  %sexists %s  %s\n' "$Y" "$R" "$1"; }
section()       { printf '\n%s==> %s%s\n' "$B" "$1" "$R"; }

printf '%sAttestor bootstrap%s\n' "$B" "$R"
printf '  project : %s\n' "$PROJECT_ID"
printf '  region  : %s\n' "$REGION"

# ---------------------------------------------------------------------------------
# 1. APIs
# ---------------------------------------------------------------------------------
SERVICES=(
    aiplatform.googleapis.com                # Agent Platform: Agent Runtime, Sessions, Memory Bank
    agentregistry.googleapis.com             # Agent Registry: discovery + versioning
    agentidentity.googleapis.com             # Agent Identity: zero-trust identity
    agentidentitycredentials.googleapis.com  # Agent Identity: credential issuance
    modelarmor.googleapis.com                # Model Armor: guardrails
    discoveryengine.googleapis.com           # Vertex AI Search
    firestore.googleapis.com                 # domain data
    run.googleapis.com                       # Cloud Run
    cloudbuild.googleapis.com                # required by `gcloud run deploy --source .`
    eventarc.googleapis.com                  # Pub/Sub -> Cloud Run push
    pubsub.googleapis.com                    # async work queue
    cloudtasks.googleapis.com                # follow-up + SLA timers
    secretmanager.googleapis.com             # zero secrets in code
    cloudtrace.googleapis.com                # OTel traces from ADK
    artifactregistry.googleapis.com          # container images
    storage.googleapis.com                   # GCS
    iam.googleapis.com                       # per-agent / per-service SAs
    iamcredentials.googleapis.com            # SA credential minting
)

section "APIs"
enabled_now="$(gcloud services list --enabled --project "$PROJECT_ID" --format='value(config.name)')"

to_enable=()
for svc in "${SERVICES[@]}"; do
    if grep -qx "$svc" <<<"$enabled_now"; then
        note_existing "api $svc"
    else
        to_enable+=("$svc")
    fi
done

if ((${#to_enable[@]})); then
    printf '  enabling %d API(s) in one batch...\n' "${#to_enable[@]}"
    gcloud services enable "${to_enable[@]}" --project "$PROJECT_ID"
    for svc in "${to_enable[@]}"; do note_created "api $svc"; done
else
    printf '  all APIs already enabled\n'
fi

# ---------------------------------------------------------------------------------
# 2. Firestore (Native mode)
# ---------------------------------------------------------------------------------
section "Firestore"
if gcloud firestore databases describe --database='(default)' --project "$PROJECT_ID" >/dev/null 2>&1; then
    db_type="$(gcloud firestore databases describe --database='(default)' \
        --project "$PROJECT_ID" --format='value(type)')"
    note_existing "firestore (default) [$db_type]"
    if [[ "$db_type" != "FIRESTORE_NATIVE" ]]; then
        printf '  %sWARNING%s database is %s, expected FIRESTORE_NATIVE\n' "$Y" "$R" "$db_type"
    fi
else
    gcloud firestore databases create \
        --database='(default)' \
        --location="$REGION" \
        --type=firestore-native \
        --project "$PROJECT_ID"
    note_created "firestore (default) [FIRESTORE_NATIVE] in $REGION"
fi

# ---------------------------------------------------------------------------------
# 3. GCS buckets
# ---------------------------------------------------------------------------------
section "Buckets"
for suffix in uploads corpus exports staging; do
    bucket="gs://${PROJECT_ID}-${suffix}"
    if gcloud storage buckets describe "$bucket" --project "$PROJECT_ID" >/dev/null 2>&1; then
        note_existing "bucket $bucket"
    else
        gcloud storage buckets create "$bucket" \
            --project "$PROJECT_ID" \
            --location="$REGION" \
            --uniform-bucket-level-access \
            --public-access-prevention
        note_created "bucket $bucket"
    fi
done

# ---------------------------------------------------------------------------------
# 4. Artifact Registry
# ---------------------------------------------------------------------------------
section "Artifact Registry"
if gcloud artifacts repositories describe "$AR_REPO" \
        --location="$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
    note_existing "artifact repo $AR_REPO ($REGION)"
else
    gcloud artifacts repositories create "$AR_REPO" \
        --repository-format=docker \
        --location="$REGION" \
        --project "$PROJECT_ID" \
        --description="Attestor container images"
    note_created "artifact repo $AR_REPO ($REGION)"
fi

# ---------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------
section "Summary"
printf '  created : %d\n' "${#created[@]}"
for item in "${created[@]:-}";  do [[ -n "$item" ]] && printf '    + %s\n' "$item"; done
printf '  existing: %d\n' "${#existing[@]}"
for item in "${existing[@]:-}"; do [[ -n "$item" ]] && printf '    = %s\n' "$item"; done
printf '\n%sbootstrap complete%s  project=%s region=%s\n' "$C" "$R" "$PROJECT_ID" "$REGION"
