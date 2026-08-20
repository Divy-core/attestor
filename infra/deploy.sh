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
WEB_ONLY=0
CONTROL_ONLY=0
PRINT_TOKEN=0

for arg in "$@"; do
    case "$arg" in
        --services-only)   SERVICES_ONLY=1 ;;
        # The dispatcher changes far more often than the control plane, and rebuilding
        # both doubles a five-minute cycle for nothing.
        --dispatcher-only) DISPATCHER_ONLY=1 ;;
        # The UI changes more often than either, and it shares no build context with them.
        --web-only)        WEB_ONLY=1 ;;
        # The control plane on its own: it owns the SSE framing, which the UI depends on, so
        # a streaming change has to ship here before the UI can observe it.
        --control-only)    CONTROL_ONLY=1 ;;
        # Read the write token back and stop. `tools/drill_approval.py` and any manual curl
        # against a write endpoint need it, and it is deliberately not in the repo.
        --print-token)     PRINT_TOKEN=1 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

if [[ -t 1 ]]; then B=$'\033[1m'; Y=$'\033[33m'; R=$'\033[0m'; else B=""; Y=""; R=""; fi
section() { printf '\n%s==> %s%s\n' "$B" "$1" "$R"; }

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
WEB_SA="attestor-web@${PROJECT_ID}.iam.gserviceaccount.com"
WORK_TOPIC="attestor.work"
DEADLETTER_TOPIC="attestor.deadletter"
PUSH_SUBSCRIPTION="attestor.work.push"
# Gmail publishes change notifications here. A SEPARATE topic from the work topic on
# purpose: what Gmail publishes is its own shape, not a WorkEnvelope, and the endpoint
# that reads it has a different ack contract. See ADR-0009.
GMAIL_TOPIC="attestor-gmail"
GMAIL_SUBSCRIPTION="attestor.gmail.push"
# Gmail's own publisher identity. Fixed and documented by Google -- not one of ours.
GMAIL_PUBLISHER="serviceAccount:gmail-api-push@system.gserviceaccount.com"
# Holds the OAuth refresh token for the watched mailbox, written once by
# tools/gmail_authorize.py. Never in this repo and never in an environment variable.
GMAIL_SECRET="attestor-gmail-oauth"

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
ensure_sa "$WEB_SA"        "Attestor web"

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
# `agentregistry.viewer` on the control plane is for ONE endpoint: `GET /registry`, which
# reads the live Agent Registry. Found by calling the deployed endpoint rather than by
# reading this file -- it returned
#
#     503  agent registry unreachable at https://agentregistry.googleapis.com:
#          HTTPError: HTTP Error 403: Forbidden
#
# because the registry read had only ever been exercised by `tools/verify_registry.py`,
# which runs under a developer's own credentials. `/registry` is on the never-cut list and
# is the video's second beat.
#
# The first fix attempted was `aiplatform.user`, on the assumption that Agent Registry sits
# behind the Vertex surface like everything else in this platform does. It does not:
# `agentregistry.googleapis.com` is its own service with its own role family
# (`agentregistry.{admin,editor,user,viewer}`), and a project-level `aiplatform.user` grants
# nothing on it. Worth recording because the same assumption would be made again -- most of
# GEAP does hang off aiplatform, and the Registry being separate is the exception.
#
# Worth noting what the endpoint did NOT do: it did not return `[]`. "No agents are
# registered" would have been a lie told in a demo, and the 503 -- carrying the host and the
# HTTP status verbatim -- is why this was two lookups instead of a mystery.
for role in roles/datastore.user roles/pubsub.publisher roles/storage.objectAdmin \
            roles/cloudtrace.agent roles/logging.logWriter roles/agentregistry.viewer; do
    grant "$CONTROL_SA" "$role"
done

# A v4 signed URL is a signature, and on Cloud Run there is no private key to make one with:
# the credentials come from the metadata server and carry a token, not a key. So
# `blob.generate_signed_url` falls back to the IAM `signBlob` API and signs AS the service
# account -- which means the account needs permission to impersonate itself. Without it the
# upload endpoint fails with
#
#     you need a private key to sign credentials
#
# and it fails ONLY when deployed, because local ADC is a user credential that has one. The
# browser upload path is the whole of Phase 6.5's front door, so this is load-bearing.
#
# Granted on the account rather than at the project level. That is the narrowest form of the
# permission: a project-wide roles/iam.serviceAccountTokenCreator would let the control plane
# impersonate every engine identity the fleet's least-privilege story rests on.
printf '  signBlob (self-impersonation, for v4 signed upload URLs)\n'
gcloud iam service-accounts add-iam-policy-binding "$CONTROL_SA" \
    --project "$PROJECT_ID" \
    --member="serviceAccount:${CONTROL_SA}" \
    --role=roles/iam.serviceAccountTokenCreator --quiet >/dev/null

# ---------------------------------------------------------------------------------
# Inbound email (Phase 7, ADR-0009)
#
# Two grants the dispatcher needs before an email can become work, both narrow:
#
# 1. Read the Gmail refresh token. Scoped to the ONE secret, not project-wide
#    `secretmanager.secretAccessor` -- a service that can read every secret in the project
#    to read one of them is not least privilege, it is a habit.
#
# 2. Write to the uploads bucket. The dispatcher has had `storage.objectViewer` since
#    Phase 4 and that was correct while every questionnaire arrived through a signed URL
#    minted by the control plane. An attachment pulled from an email has to be staged by
#    the dispatcher itself, so it needs create -- granted ON THE UPLOADS BUCKET and nowhere
#    else. In particular not on the corpus bucket: attacker-supplied text one indexing job
#    away from being cited as evidence about our own controls is precisely the shape of the
#    tool-poisoning attack the Armor egress surface exists to stop.
# ---------------------------------------------------------------------------------
section "Inbound email"

if gcloud secrets describe "$GMAIL_SECRET" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "$GMAIL_SECRET" \
        --project "$PROJECT_ID" \
        --member="serviceAccount:${DISPATCHER_SA}" \
        --role=roles/secretmanager.secretAccessor --quiet --format=none
    printf '  %s -> dispatcher may read it\n' "$GMAIL_SECRET"
else
    printf '  %s does NOT exist yet.\n' "$GMAIL_SECRET"
    printf '    Inbound email stays off until it does. Create it with:\n'
    printf '      PROJECT_ID=%s uv run python tools/gmail_authorize.py --client-secrets ...\n' \
        "$PROJECT_ID"
    printf '    then re-run this script to grant the dispatcher access.\n'
fi

UPLOADS_BUCKET="gs://${PROJECT_ID}-uploads"
if gcloud storage buckets describe "$UPLOADS_BUCKET" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud storage buckets add-iam-policy-binding "$UPLOADS_BUCKET" \
        --member="serviceAccount:${DISPATCHER_SA}" \
        --role=roles/storage.objectAdmin --project "$PROJECT_ID" --quiet --format=none
    printf '  %s -> dispatcher may stage attachments\n' "$UPLOADS_BUCKET"
else
    printf '  %s not found; skipping the staging grant\n' "$UPLOADS_BUCKET"
fi

if gcloud pubsub topics describe "$GMAIL_TOPIC" --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf '  exists: %s\n' "$GMAIL_TOPIC"
else
    gcloud pubsub topics create "$GMAIL_TOPIC" --project "$PROJECT_ID" --quiet
    printf '  created: %s\n' "$GMAIL_TOPIC"
fi

# Without this binding `users.watch` returns a 403 naming the topic, and it is the first
# thing that goes wrong every time. Gmail publishes as a fixed system account.
gcloud pubsub topics add-iam-policy-binding "$GMAIL_TOPIC" \
    --member="$GMAIL_PUBLISHER" --role=roles/pubsub.publisher \
    --project "$PROJECT_ID" --quiet --format=none
printf '  %s may publish to %s\n' "gmail-api-push" "$GMAIL_TOPIC"

# ---------------------------------------------------------------------------------
# The write token
#
# Phase 6.5 put an entrance on the interface, so the control plane's public URL now accepts
# "start a 312-question review". `services/control-plane/guard.py` requires a shared token on
# every write, and both services need the same value: the control plane to check it, the web
# service to send it from inside its route handler where the browser cannot read it.
#
# Generated once and persisted OUTSIDE the repo, because a token regenerated on every deploy
# would invalidate a running demo, and one committed to git is not a token. If the file is
# absent a new one is minted; `--print-token` exists so a human can read it back for
# `tools/drill_approval.py`.
#
# It is not authentication and `guard.py` says so in its own docstring. It bounds a
# credit-burn surface; it does not identify anyone.
# ---------------------------------------------------------------------------------
TOKEN_FILE="${ATTESTOR_TOKEN_FILE:-${HOME}/.attestor-write-token}"
if [[ ! -f "$TOKEN_FILE" ]]; then
    umask 077
    head -c 32 /dev/urandom | base64 | tr -d '=+/\n' > "$TOKEN_FILE"
    printf '  minted a new write token at %s\n' "$TOKEN_FILE"
fi
WRITE_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
if [[ -z "$WRITE_TOKEN" ]]; then
    printf 'error: the write token at %s is empty. Delete it and re-run to mint a new one.\n' \
        "$TOKEN_FILE" >&2
    exit 1
fi
printf '  write token: %s… (%d chars, from %s)\n' "${WRITE_TOKEN:0:4}" "${#WRITE_TOKEN}" "$TOKEN_FILE"

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

if (( ! DISPATCHER_ONLY && ! WEB_ONLY )); then
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
    --set-env-vars "PROJECT_ID=${PROJECT_ID},REGION=${REGION},VERTEX_LOCATION=${REGION},ATTESTOR_WRITE_TOKEN=${WRITE_TOKEN}" \
    --quiet
fi

if (( ! WEB_ONLY && ! CONTROL_ONLY )); then
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
# The verifier, named the same way and for the same reason. `docs/` is not copied into the
# container image, so `verifier_engine_name`'s file fallback cannot resolve there -- without
# this variable the deployed dispatcher verifies in-process and honestly says so, which is a
# working fallback and not the intended configuration.
verifier = next((e for e in record["engines"] if e["role"] == "verifier"), None)
if verifier is not None:
    pairs.append(f"ATTESTOR_VERIFIER_ENGINE={verifier['resource_name']}")
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
fi

if (( ! DISPATCHER_ONLY && ! CONTROL_ONLY )); then
section "Cloud Run: web"
# Build context is `services/web`, not the repo root: the UI is not a uv workspace member and
# carries its own lockfile.
gcloud builds submit . \
    --project "$PROJECT_ID" --region "$REGION" \
    --config infra/cloudrun/cloudbuild.web.yaml \
    --substitutions "_IMAGE=${REGISTRY}/web:latest" \
    --quiet

# CONTROL_PLANE_URL is resolved BEFORE the deploy and folded into --set-env-vars, for the same
# reason the engine variables are: --set-env-vars REPLACES rather than merges, so setting it in
# a second call would wipe PROJECT_ID and REGION, and the sidebar would show
# `unknown-project` on a recording.
WEB_CONTROL_URL="$(gcloud run services describe attestor-control-plane \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
printf '  CONTROL_PLANE_URL=%s\n' "$WEB_CONTROL_URL"

gcloud run deploy attestor-web \
    --image "${REGISTRY}/web:latest" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --service-account "$WEB_SA" \
    --min-instances 0 \
    --max-instances 4 \
    --memory 512Mi \
    --timeout 3600 \
    --allow-unauthenticated \
    --set-env-vars "CONTROL_PLANE_URL=${WEB_CONTROL_URL},PROJECT_ID=${PROJECT_ID},REGION=${REGION},ATTESTOR_WRITE_TOKEN=${WRITE_TOKEN}" \
    --quiet
fi

DISPATCHER_URL="$(gcloud run services describe attestor-dispatcher \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
CONTROL_URL="$(gcloud run services describe attestor-control-plane \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
WEB_URL="$(gcloud run services describe attestor-web \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)"
printf '\n  dispatcher    : %s\n' "$DISPATCHER_URL"
printf '  control plane : %s\n' "$CONTROL_URL"
printf '  web           : %s\n' "${WEB_URL:-not deployed}"

# The console URL, applied after the services exist because the dispatcher needs the WEB
# service's address and Cloud Run injects a service's own URL, never another's. It goes into
# the approval-request email as a deep link into the queue -- without it the link is
# relative, which is unhelpful in an inbox. `--update-env-vars` MERGES, unlike the
# `--set-env-vars` used above, so this cannot wipe the engine names.
if [[ -n "${WEB_URL:-}" ]]; then
    gcloud run services update attestor-dispatcher \
        --project "$PROJECT_ID" --region "$REGION" \
        --update-env-vars "ATTESTOR_CONSOLE_URL=${WEB_URL}" \
        --quiet --format=none
    printf '  dispatcher -> ATTESTOR_CONSOLE_URL=%s\n' "$WEB_URL"
fi

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

# The Gmail notification subscription. Same invoker account and the same OIDC posture as
# the work subscription -- the dispatcher stays --no-allow-unauthenticated, so a stranger who
# learns the URL cannot inject a notification and make the fleet re-read a mailbox.
#
# No dead-letter topic, deliberately, and a short ack deadline. A notification is not work:
# it is a pointer at a history delta, it is cheap to recompute, and the *work* it produces
# has its own dead-letter path with its own audit event. Dead-lettering the notification
# would move a message nobody can act on into a queue nobody reads.
GMAIL_SUB_ARGS=(
    --topic "$GMAIL_TOPIC"
    --push-endpoint "${DISPATCHER_URL}/gmail/push"
    --push-auth-service-account "$INVOKER_SA"
    --ack-deadline 60
    --min-retry-delay 10s
    --max-retry-delay 300s
    --message-retention-duration 1d
    --project "$PROJECT_ID"
    --quiet
)
if gcloud pubsub subscriptions describe "$GMAIL_SUBSCRIPTION" \
        --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub subscriptions update "$GMAIL_SUBSCRIPTION" \
        --push-endpoint "${DISPATCHER_URL}/gmail/push" \
        --push-auth-service-account "$INVOKER_SA" \
        --ack-deadline 60 --project "$PROJECT_ID" --quiet
    printf '  updated %s\n' "$GMAIL_SUBSCRIPTION"
else
    gcloud pubsub subscriptions create "$GMAIL_SUBSCRIPTION" "${GMAIL_SUB_ARGS[@]}"
    printf '  created %s\n' "$GMAIL_SUBSCRIPTION"
fi

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
printf '  web           : %s\n' "${WEB_URL:-not deployed}"
printf '  subscription  : %s -> %s/pubsub/push\n' "$PUSH_SUBSCRIPTION" "$DISPATCHER_URL"
printf '  inbound mail  : %s -> %s/gmail/push\n' "$GMAIL_SUBSCRIPTION" "$DISPATCHER_URL"
printf '                  register the watch: PROJECT_ID=%s uv run python tools/gmail_watch.py --apply\n' "$PROJECT_ID"
