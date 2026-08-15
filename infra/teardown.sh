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
engines="$(
    python - "$PROJECT_ID" "$REGION" <<'PY' || true
import sys
import agentplatform

project, region = sys.argv[1], sys.argv[2]
client = agentplatform.Client(project=project, location=region)
for engine in client.agent_engines.list():
    resource = engine.api_resource
    print(f"{resource.name}\t{getattr(resource, 'display_name', '')}")
PY
)"

if [[ -z "$engines" ]]; then
    printf '  none found\n'
else
    while IFS=$'\t' read -r name display; do
        [[ -z "$name" ]] && continue
        printf '  found: %s (%s)\n' "$display" "$name"
        if (( DRY_RUN )); then
            printf '  %sDRY-RUN%s would delete %s\n' "$Y" "$R" "$name"
        else
            python - "$PROJECT_ID" "$REGION" "$name" <<'PY'
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
