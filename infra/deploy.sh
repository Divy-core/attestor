#!/usr/bin/env bash
#
# Bring the deployed stack up from nothing: two Cloud Run services with their own service
# accounts, and the Eventarc push subscription that drives every review.
#
#   PROJECT_ID=attestor-505506 bash infra/deploy.sh
#   PROJECT_ID=attestor-505506 bash infra/deploy.sh --services-only
#
# The engines are deployed separately by `services/runtime/deploy_fleet.py`, because they
# take four minutes each and are rebuilt far less often than the services.
#
# ## Why push rather than pull
#
# Phase 5 session two ran the full 312-question review through a local pull loop and it
# stalled: the harness pulled one message at a time and dispatched synchronously, so the
# three drafting partitions could not overlap, and two of them were never seen again. A
# push subscription removes both halves of that -- there is no client-side prefetch buffer
# whose ack deadlines expire while a 269-second partition is being worked, and there is no
# single-threaded loop to serialise the partitions. Cloud Run starts one instance per
# concurrent message.
#
# ## The ordering that matters
#
# Ack deadline 600s (the Pub/Sub maximum), lease 900s. A redelivery arriving at 600s while
# a handler is still drafting finds a live claim and is refused with 409 instead of
# drafting the same 123 questions a second time. See `docs/proof/ack-deadline-margin.md`.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set}"
REGION="${REGION:-us-central1}"
SERVICES_ONLY=0

DISPATCHER_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --services-only)   SERVICES_ONLY=1 ;;
        # The dispatcher changes far more often than the control plane, and rebuilding
        # both doubles a five-minute cycle for nothing.
        --dispatcher-only) DISPATCHER_ONLY=1 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

if [[ -t 1 ]]; then B=$'\033[1m'; Y=$'\033[33m'; R=$'\033[0m'; else B=""; Y=""; R=""; fi
section() { printf '\n%s==> %s%s\n' "$B" "$1" "$R"; }

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
WORK_TOPIC="attestor.work"
DEADLETTER_TOPIC="attestor.deadletter"
PUSH_SUBSCRIPTION="attestor.work.push"

DISPATCHER_SA="attestor-dispatcher@${PROJECT_ID}.iam.gserviceaccount.com"
CONTROL_SA="attestor-control-plane@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="attestor-pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

printf '%sAttestor deploy%s  project=%s region=%s\n' "$B" "$R" "$PROJECT_ID" "$REGION"

# ---------------------------------------------------------------------------------
# 1. Service accounts -- one per service, plus one Pub/Sub uses to call the dispatcher
# ---------------------------------------------------------------------------------
section "Service accounts"
ensure_sa() {
    local account="$1" display="$2"
    if gcloud iam service-accounts describe "$account" --project "$PROJECT_ID" >/dev/null 2>&1; then
        printf '  exists: %s\n' "$account"
    else
        gcloud iam service-accounts create "${account%%@*}" \
            --display-name="$display" --project "$PROJECT_ID"
        printf '  created: %s\n' "$account"
    fi
}
ensure_sa "$DISPATCHER_SA" "Attestor dispatcher"
ensure_sa "$CONTROL_SA"    "Attestor control plane"
ensure_sa "$INVOKER_SA"    "Attestor Pub/Sub push invoker"

# The dispatcher reads and writes review state, publishes the next stage, screens through
# Model Armor, and calls the deployed engines. It does NOT get corpus access: retrieval
# happens on the department engines, under their own identities, which is the entire point
# of moving drafting there.
section "Roles"
grant() {
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$1" --role="$2" --condition=None --format=none --quiet
    printf '  %s -> %s\n' "${1%%@*}" "$2"
}
# `modelarmor.user` is not optional and its absence is not obvious. Model Armor fails
# CLOSED by design -- `execution_failed` maps to DENY -- so a dispatcher that cannot call
# `sanitizeUserPrompt` does not error, it quarantines every question it is given. The
# first deployed run delivered a review with twelve quarantined answers and zero
# citations, reported `delivered`, and the only sign was a 403 in the logs.
for role in roles/datastore.user roles/pubsub.publisher roles/aiplatform.user \
            roles/storage.objectViewer roles/cloudtrace.agent roles/logging.logWriter \
            roles/modelarmor.user roles/discoveryengine.viewer; do
    grant "$DISPATCHER_SA" "$role"
done
# `aiplatform.user` on the control plane is for ONE endpoint: `GET /registry`, which reads
# the live Agent Registry. Found by calling the deployed endpoint rather than by reading
# this file -- it returned
#
#     503  agent registry unreachable at https://agentregistry.googleapis.com:
#          HTTPError: HTTP Error 403: Forbidden
#
# because the registry read had only ever been exercised by `tools/verify_registry.py`,
# which runs under a developer's own credentials. `/registry` is on the never-cut list and
# is the video's second beat.
#
# Worth noting what the endpoint did NOT do: it did not return `[]`. "No agents are
# registered" would have been a lie told in a demo, and the 503 is why this was a
# five-minute fix instead of a mystery.
for role in roles/datastore.user roles/pubsub.publisher roles/storage.objectAdmin \
            roles/cloudtrace.agent roles/logging.logWriter roles/aiplatform.user; do
    grant "$CONTROL_SA" "$role"
done

# ---------------------------------------------------------------------------------
# 2. The two services
# ---------------------------------------------------------------------------------
REPO="attestor"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"

section "Artifact Registry"
if gcloud artifacts repositories describe "$REPO" --location "$REGION" \
        --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf '  exists: %s\n' "$REGISTRY"
else
    gcloud artifacts repositories create "$REPO" --repository-format=docker \
        --location "$REGION" --project "$PROJECT_ID" --quiet
    printf '  created: %s\n' "$REGISTRY"
fi

if (( ! DISPATCHER_ONLY )); then
section "Cloud Run: control plane"
gcloud builds submit . \
    --project "$PROJECT_ID" --region "$REGION" \
    --config infra/cloudrun/cloudbuild.control-plane.yaml \
    --substitutions "_IMAGE=${REGISTRY}/control-plane:latest" \
    --quiet

gcloud run deploy attestor-control-plane \
    --image "${REGISTRY}/control-plane:latest" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --service-account "$CONTROL_SA" \
    --min-instances 0 \
    --max-instances 4 \
    --memory 1Gi \
    --timeout 3600 \
    --allow-unauthenticated \
    --set-env-vars "PROJECT_ID=${PROJECT_ID},VERTEX_LOCATION=${REGION}" \
    --quiet
fi

section "Cloud Run: dispatcher"

# Which engine owns which partition, resolved BEFORE the deploy so it can go in the same
# `--set-env-vars`. It used to be applied afterwards with a separate `services update`,
# and that left a window -- one whole revision -- in which the dispatcher was live,
# receiving pushes, and had no engine names. `--set-env-vars` REPLACES the variable set
# rather than merging into it, so every redeploy re-opened that window. Session three hit
# it twice.
ENGINE_VARS=""
if [[ -f docs/proof/fleet-deployment.json ]]; then
    ENGINE_VARS="$(uv run python - <<'PY'
import json
from pathlib import Path

record = json.loads(Path("docs/proof/fleet-deployment.json").read_text(encoding="utf-8"))
names = {"security": "SECURITY", "legal": "LEGAL", "engineering": "ENGINEERING"}
pairs = [
    f"ATTESTOR_ENGINE_{names[e['role']]}={e['resource_name']}"
    for e in record["engines"]
    if e["role"] in names
]
# Memory Bank is scoped per engine, so this names the engine holding the commitments --
# the orchestrator, because commitments are review-scoped rather than department-scoped.
orchestrator = next(e for e in record["engines"] if e["role"] == "orchestrator")
pairs.append(f"AGENT_ENGINE_ID={orchestrator['resource_name'].rsplit('/', 1)[-1]}")
print(",".join(pairs))
PY
)"
    printf '  %s\n' "${ENGINE_VARS//,/$'\n  '}"
else
    printf '  %sWARNING%s docs/proof/fleet-deployment.json not found -- the dispatcher\n' "$Y" "$R"
    printf '          will start with no engine names and every draft will fail.\n'
fi

gcloud builds submit . \
    --project "$PROJECT_ID" --region "$REGION" \
    --config infra/cloudrun/cloudbuild.dispatcher.yaml \
    --substitutions "_IMAGE=${REGISTRY}/dispatcher:latest" \
    --quiet

# Concurrency 1: a drafting partition already fans out over eight threads inside the
# handler, so a second concurrent message on the same instance would contend for the same
# pool and make the measured concurrency a fiction. One message, one instance, up to ten.
#
# Timeout 3600 rather than the 600 ack deadline: the request must outlive the message's
# first delivery, or Cloud Run kills the handler at the exact moment Pub/Sub redelivers
# and the lease is left holding work nobody is doing.
gcloud run deploy attestor-dispatcher \
    --image "${REGISTRY}/dispatcher:latest" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --service-account "$DISPATCHER_SA" \
    --min-instances 0 \
    --max-instances 10 \
    --concurrency 1 \
    --cpu 2 \
    --memory 2Gi \
    --timeout 3600 \
    --no-allow-unauthenticated \
    --set-env-vars "PROJECT_ID=${PROJECT_ID},VERTEX_LOCATION=${REGION},ATTESTOR_FLEET_RUNNER=agent_runtime${ENGINE_VARS:+,${ENGINE_VARS}}" \
    --quiet

DISPATCHER_URL="$(gcloud run services describe attestor-dispatcher \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
CONTROL_URL="$(gcloud run services describe attestor-control-plane \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
printf '\n  dispatcher    : %s\n' "$DISPATCHER_URL"
printf '  control plane : %s\n' "$CONTROL_URL"

if (( SERVICES_ONLY )); then
    printf '\n%sservices deployed; skipping subscription wiring%s\n' "$B" "$R"
    exit 0
fi

# ---------------------------------------------------------------------------------
# 4. The push subscription
# ---------------------------------------------------------------------------------
section "Eventarc / Pub-Sub push"

# Pub/Sub mints the OIDC token as this account, and Cloud Run checks it. The dispatcher is
# deliberately --no-allow-unauthenticated: the endpoint that advances every review must
# not be callable by anyone who learns its URL.
gcloud run services add-iam-policy-binding attestor-dispatcher \
    --project "$PROJECT_ID" --region "$REGION" \
    --member="serviceAccount:${INVOKER_SA}" --role=roles/run.invoker --quiet

# The Pub/Sub service agent has to be allowed to mint tokens as the invoker.
gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" \
    --project "$PROJECT_ID" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
    --role=roles/iam.serviceAccountTokenCreator --quiet

SUBSCRIPTION_ARGS=(
    --topic "$WORK_TOPIC"
    --push-endpoint "${DISPATCHER_URL}/pubsub/push"
    --push-auth-service-account "$INVOKER_SA"
    # 600 is the Pub/Sub maximum and the number the lease was sized against.
    --ack-deadline 600
    --min-retry-delay 10s
    --max-retry-delay 600s
    --dead-letter-topic "$DEADLETTER_TOPIC"
    # Five, matching DISPATCHER_MAX_ATTEMPTS, so our own dead-lettering -- which writes an
    # audit event -- fires at the same count rather than after the platform has already
    # moved the message silently.
    --max-delivery-attempts 5
    --project "$PROJECT_ID"
    --quiet
)

if gcloud pubsub subscriptions describe "$PUSH_SUBSCRIPTION" --project "$PROJECT_ID" >/dev/null 2>&1
then
    gcloud pubsub subscriptions update "$PUSH_SUBSCRIPTION" \
        --push-endpoint "${DISPATCHER_URL}/pubsub/push" \
        --push-auth-service-account "$INVOKER_SA" \
        --ack-deadline 600 \
        --dead-letter-topic "$DEADLETTER_TOPIC" \
        --max-delivery-attempts 5 \
        --project "$PROJECT_ID" --quiet
    printf '  updated %s\n' "$PUSH_SUBSCRIPTION"
else
    gcloud pubsub subscriptions create "$PUSH_SUBSCRIPTION" "${SUBSCRIPTION_ARGS[@]}"
    printf '  created %s\n' "$PUSH_SUBSCRIPTION"
fi

# Pub/Sub's own service agent needs to publish into the dead-letter topic and to ack on
# the subscription it is dead-lettering from. Without both, exhausted messages are
# redelivered forever and the DLQ stays empty -- a failure that looks like nothing.
gcloud pubsub topics add-iam-policy-binding "$DEADLETTER_TOPIC" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
    --role=roles/pubsub.publisher --project "$PROJECT_ID" --quiet --format=none
gcloud pubsub subscriptions add-iam-policy-binding "$PUSH_SUBSCRIPTION" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
    --role=roles/pubsub.subscriber --project "$PROJECT_ID" --quiet --format=none

# A subscription ON the dead-letter topic. Without one, a dead-lettered message is
# discarded the moment it arrives and the DLQ is a hole rather than a queue -- which is
# precisely why session two's stalled run had nothing to inspect. The drill in
# `tools/drill_deadletter.py` reads from here.
if ! gcloud pubsub subscriptions describe attestor.deadletter.sub \
        --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create attestor.deadletter.sub \
        --topic "$DEADLETTER_TOPIC" --ack-deadline 60 \
        --message-retention-duration 7d --project "$PROJECT_ID" --quiet
    printf '  created attestor.deadletter.sub\n'
else
    printf '  exists: attestor.deadletter.sub\n'
fi

printf '\n%sdeploy complete%s\n' "$B" "$R"
printf '  control plane : %s\n' "$CONTROL_URL"
printf '  dispatcher    : %s\n' "$DISPATCHER_URL"
printf '  subscription  : %s -> %s/pubsub/push\n' "$PUSH_SUBSCRIPTION" "$DISPATCHER_URL"
