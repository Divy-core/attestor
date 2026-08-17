#!/usr/bin/env bash
#
# Remove every billable resource. Run after recording the demo -- the hackathon brief
# explicitly says the app need not be live at judging.
#
# Engines, Cloud Run services, and templates are ENUMERATED DYNAMICALLY, never listed by
# hardcoded id. A hardcoded id rots the moment a redeploy mints a new one, and a teardown
# script that silently misses a resource is worse than no teardown script: it reports
# success while the meter runs.
#
# Data is NOT deleted by default. Firestore contents, GCS objects, and the seeded corpus
# survive unless --purge-data is passed, because re-seeding costs an hour and the storage
# costs pennies.
#
# Usage:
#   PROJECT_ID=attestor-505506 bash infra/teardown.sh [--dry-run] [--purge-data]
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set}"
REGION="${REGION:-us-central1}"
DRY_RUN=0
PURGE_DATA=0

for arg in "$@"; do
    case "$arg" in
        --dry-run)    DRY_RUN=1 ;;
        --purge-data) PURGE_DATA=1 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

if [[ -t 1 ]]; then B=$'\033[1m'; Y=$'\033[33m'; R=$'\033[0m'; else B=""; Y=""; R=""; fi

run() {
    if (( DRY_RUN )); then
        printf '  %sDRY-RUN%s %s\n' "$Y" "$R" "$*"
    else
        printf '  running: %s\n' "$*"
        "$@"
    fi
}

section() { printf '\n%s==> %s%s\n' "$B" "$1" "$R"; }

printf '%sAttestor teardown%s  project=%s region=%s dry_run=%s purge_data=%s\n' \
    "$B" "$R" "$PROJECT_ID" "$REGION" "$DRY_RUN" "$PURGE_DATA"

# ---------------------------------------------------------------------------------
# 1. Agent Runtime engines -- the most expensive thing if left running
# ---------------------------------------------------------------------------------
section "Agent Runtime engines"

# `uv run python`, not `python`, and NO `|| true`.
#
# This block used both, and the first dry run of session three showed exactly what the
# header of this file warns about: bare `python` has no `agentplatform` module, the
# traceback was swallowed by `|| true`, `$engines` came back empty, and the script
# cheerfully printed "none found" for the five most expensive resources in the project
# while reporting success. A listing that fails must stop the teardown, because
# "I found nothing" and "I could not look" are not the same statement -- the same
# distinction this codebase enforces everywhere else.
if ! engines="$(
    uv run python - "$PROJECT_ID" "$REGION" <<'PY'
import sys
import agentplatform

project, region = sys.argv[1], sys.argv[2]
client = agentplatform.Client(project=project, location=region)
for engine in client.agent_engines.list():
    resource = engine.api_resource
    print(f"{resource.name}\t{getattr(resource, 'display_name', '')}")
PY
)"; then
    printf '  %sFATAL%s could not list Agent Runtime engines -- refusing to continue.\n' "$Y" "$R"
    printf '        An empty listing here is indistinguishable from a failed one, and\n'
    printf '        reporting a clean teardown while five engines keep billing is the\n'
    printf '        worst outcome this script has.\n'
    exit 1
fi

if [[ -z "$engines" ]]; then
    printf '  none found\n'
else
    while IFS=$'\t' read -r name display; do
        [[ -z "$name" ]] && continue
        printf '  found: %s (%s)\n' "$display" "$name"
        if (( DRY_RUN )); then
            printf '  %sDRY-RUN%s would delete %s\n' "$Y" "$R" "$name"
        else
            uv run python - "$PROJECT_ID" "$REGION" "$name" <<'PY'
import sys
import agentplatform

project, region, name = sys.argv[1], sys.argv[2], sys.argv[3]
client = agentplatform.Client(project=project, location=region)
client.agent_engines.delete(name=name, force=True)
print(f"  deleted {name}")
PY
        fi
    done <<<"$engines"
fi

# ---------------------------------------------------------------------------------
# 2. Cloud Run services
# ---------------------------------------------------------------------------------
section "Cloud Run services"
services="$(gcloud run services list --project "$PROJECT_ID" --region "$REGION" \
    --format='value(metadata.name)' 2>/dev/null || true)"
if [[ -z "$services" ]]; then
    printf '  none found\n'
else
    while read -r svc; do
        [[ -z "$svc" ]] && continue
        run gcloud run services delete "$svc" --project "$PROJECT_ID" --region "$REGION" --quiet
    done <<<"$services"
fi

# ---------------------------------------------------------------------------------
# 2b. Pub/Sub subscriptions, including the push subscription Eventarc drives
# ---------------------------------------------------------------------------------
#
# Subscriptions rather than topics. A subscription with a backlog is the thing that
# accrues storage and keeps redelivering to an endpoint that no longer exists; a topic
# with no subscription holds nothing. Topics are left so that `deploy.sh` restores a
# working system without re-running `bootstrap.sh`.
section "Pub/Sub subscriptions"
subscriptions="$(gcloud pubsub subscriptions list --project "$PROJECT_ID" \
    --format='value(name)' 2>/dev/null || true)"
if [[ -z "$subscriptions" ]]; then
    printf '  none found\n'
else
    while read -r sub; do
        [[ -z "$sub" ]] && continue
        run gcloud pubsub subscriptions delete "$sub" --project "$PROJECT_ID" --quiet
    done <<<"$subscriptions"
fi

# ---------------------------------------------------------------------------------
# 2c. Container images
# ---------------------------------------------------------------------------------
#
# Artifact Registry bills for storage, and the dispatcher image is not small. Deleting
# the repository takes the images with it; `deploy.sh` recreates it.
section "Artifact Registry"
if gcloud artifacts repositories describe attestor --location "$REGION" \
        --project "$PROJECT_ID" >/dev/null 2>&1; then
    run gcloud artifacts repositories delete attestor --location "$REGION" \
        --project "$PROJECT_ID" --quiet
else
    printf '  none found\n'
fi

# ---------------------------------------------------------------------------------
# 3. Model Armor templates (regional endpoint -- see PHASE-0-DISCOVERY.md)
# ---------------------------------------------------------------------------------
section "Model Armor templates"
export CLOUDSDK_API_ENDPOINT_OVERRIDES_MODELARMOR="https://modelarmor.${REGION}.rep.googleapis.com/"
templates="$(gcloud model-armor templates list --location "$REGION" --project "$PROJECT_ID" \
    --format='value(name)' 2>/dev/null || true)"
if [[ -z "$templates" ]]; then
    printf '  none found\n'
else
    while read -r tpl; do
        [[ -z "$tpl" ]] && continue
        run gcloud model-armor templates delete "$tpl" --location "$REGION" \
            --project "$PROJECT_ID" --quiet
    done <<<"$templates"
fi
printf '  NOTE: the project-level floor setting is left in place -- it costs nothing\n'
printf '        and removing it would drop the security baseline.\n'

# ---------------------------------------------------------------------------------
# 4. Data -- opt-in only
# ---------------------------------------------------------------------------------
section "Data"
if (( PURGE_DATA )); then
    printf '  %spurging seeded data%s\n' "$Y" "$R"
    for suffix in uploads corpus exports staging; do
        run gcloud storage rm -r "gs://${PROJECT_ID}-${suffix}/**" --project "$PROJECT_ID" || true
    done
    printf '  Firestore collections are NOT auto-purged: deleting them needs an explicit\n'
    printf '  per-collection command, and an accidental wipe costs a re-seed. Use:\n'
    printf '    gcloud firestore bulk-delete --collection-ids=... --project %s\n' "$PROJECT_ID"
else
    printf '  skipped (pass --purge-data to remove GCS objects)\n'
fi

printf '\n%steardown complete%s\n' "$B" "$R"
