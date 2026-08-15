#!/usr/bin/env python3
"""Seed Attestor: corpus into Vertex AI Search, fixtures into Firestore.

Idempotent. Running it twice must produce identical state -- every write is keyed on a
deterministic id, and every create is guarded by an existence check.

    PROJECT_ID=attestor-505506 uv run python seed/seed.py
    PROJECT_ID=attestor-505506 uv run python seed/seed.py --check   # report, change nothing

The most important thing it creates is **the backdated review**: a review dated 22 days
ago with round 1 already `delivered`, its answers stored, and its commitments recorded --
including the one that round 2 tries to contradict. Without that, the multi-week resume
demo would be staged rather than real.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.api_core import exceptions as gexc
from google.cloud import discoveryengine_v1 as de
from google.cloud import firestore, storage  # type: ignore[attr-defined]

from attestor_core.domain import (
    Answer,
    AnswerStatus,
    Citation,
    Commitment,
    Confidence,
    Department,
    Framework,
    Question,
    Residency,
    Review,
    Round,
)
from attestor_core.domain.ids import make_question_id
from attestor_core.state import ReviewState

SEED_DIR = Path(__file__).parent
CORPUS_DIR = SEED_DIR / "corpus"

#: How far back the seeded review is dated. The resume demo depends on this being real.
BACKDATE_DAYS = 22

REVIEW_ID = "rev-acme-2026-q3"
ROUND_1_ID = "rnd-acme-r1"
CUSTOMER = "Acme Financial Services, Inc."

SEARCH_LOCATION = "global"

DEPARTMENT_DATASTORES: dict[Department, str] = {
    Department.SECURITY: "attestor-corpus-security",
    Department.LEGAL: "attestor-corpus-legal",
    Department.ENGINEERING: "attestor-corpus-engineering",
}

created: list[str] = []
existing: list[str] = []


def note(kind: str, what: str, *, made: bool) -> None:
    (created if made else existing).append(what)
    print(f"  {'CREATED' if made else 'exists ':<7}  {kind:<12} {what}")


def project() -> str:
    value = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not value:
        sys.exit("error: PROJECT_ID must be set")
    return value


def corpus_documents() -> list[tuple[Department, Path]]:
    """Every corpus document with the department that owns it."""
    docs: list[tuple[Department, Path]] = []
    for department, folder in (
        (Department.SECURITY, "security"),
        (Department.LEGAL, "legal"),
        (Department.ENGINEERING, "engineering"),
    ):
        docs.extend((department, path) for path in sorted((CORPUS_DIR / folder).glob("*.md")))
    return docs


# --------------------------------------------------------------------------------------
# 1. GCS corpus staging
# --------------------------------------------------------------------------------------


def stage_corpus(project_id: str, *, dry_run: bool) -> dict[Department, list[str]]:
    """Upload the corpus to GCS. Returns the uploaded URIs per department.

    Idempotent by content hash: a document whose bytes are unchanged is not re-uploaded,
    so a second run does not churn object generations.
    """
    client = storage.Client(project=project_id)
    bucket_name = f"{project_id}-corpus"
    bucket = client.bucket(bucket_name)
    uris: dict[Department, list[str]] = {d: [] for d in DEPARTMENT_DATASTORES}

    for department, path in corpus_documents():
        object_name = f"{department.value}/{path.name}"
        uri = f"gs://{bucket_name}/{object_name}"
        uris[department].append(uri)

        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()[:16]

        blob = bucket.blob(object_name)
        if blob.exists() and (blob.metadata or {}).get("content_sha256") == digest:
            note("gcs", object_name, made=False)
            continue
        if dry_run:
            note("gcs", f"{object_name} (would upload)", made=True)
            continue
        blob.metadata = {"content_sha256": digest, "department": department.value}
        blob.upload_from_string(body, content_type="text/markdown")
        note("gcs", object_name, made=True)

    return uris


# --------------------------------------------------------------------------------------
# 2. Vertex AI Search datastores
# --------------------------------------------------------------------------------------


def ensure_datastores(project_id: str, *, dry_run: bool) -> None:
    """Create one datastore per department if absent.

    The per-department split is the access boundary: a security agent is pointed at the
    security datastore and therefore *cannot* retrieve legal documents.
    """
    client = de.DataStoreServiceClient()
    parent = f"projects/{project_id}/locations/{SEARCH_LOCATION}/collections/default_collection"

    for department, datastore_id in DEPARTMENT_DATASTORES.items():
        name = f"{parent}/dataStores/{datastore_id}"
        try:
            client.get_data_store(request=de.GetDataStoreRequest(name=name))
            note("datastore", datastore_id, made=False)
            continue
        except gexc.NotFound:
            pass

        if dry_run:
            note("datastore", f"{datastore_id} (would create)", made=True)
            continue

        operation = client.create_data_store(
            request=de.CreateDataStoreRequest(
                parent=parent,
                data_store_id=datastore_id,
                data_store=de.DataStore(
                    display_name=f"Attestor corpus: {department.value}",
                    industry_vertical=de.IndustryVertical.GENERIC,
                    solution_types=[de.SolutionType.SOLUTION_TYPE_SEARCH],
                    content_config=de.DataStore.ContentConfig.CONTENT_REQUIRED,
                ),
            )
        )
        operation.result(timeout=600)  # type: ignore[no-untyped-call]
        note("datastore", datastore_id, made=True)


def import_corpus(project_id: str, uris: dict[Department, list[str]], *, dry_run: bool) -> None:
    """Import the staged corpus into each department datastore.

    Uses `INCREMENTAL` reconciliation keyed on the GCS URI, so re-running updates the
    existing documents rather than duplicating them -- which is what makes `make seed`
    safe to run twice.
    """
    client = de.DocumentServiceClient()
    for department, datastore_id in DEPARTMENT_DATASTORES.items():
        parent = (
            f"projects/{project_id}/locations/{SEARCH_LOCATION}"
            f"/collections/default_collection/dataStores/{datastore_id}"
            f"/branches/default_branch"
        )
        pattern = f"gs://{project_id}-corpus/{department.value}/*.md"
        if dry_run:
            note("import", f"{datastore_id} <- {pattern} (would import)", made=True)
            continue

        operation = client.import_documents(
            request=de.ImportDocumentsRequest(
                parent=parent,
                gcs_source=de.GcsSource(input_uris=[pattern], data_schema="content"),
                reconciliation_mode=de.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
            )
        )
        operation.result(timeout=1800)  # type: ignore[no-untyped-call]
        note("import", f"{datastore_id} ({len(uris[department])} docs)", made=True)


# --------------------------------------------------------------------------------------
# 3. Firestore fixtures -- the backdated review
# --------------------------------------------------------------------------------------

#: Round-1 answers that matter later. The first is THE commitment round 2 attacks.
SEEDED_ANSWERS: list[tuple[str, str, str, list[tuple[str, str, str]]]] = [
    (
        "Do you offer on-premises or self-hosted deployment?",
        "No. Kestrel Insight is a multi-tenant SaaS product only. There is no "
        "single-tenant, private-cloud, air-gapped, or customer-VPC deployment option, "
        "and none is on the roadmap.",
        "LegalAgent",
        [
            (
                "gs://corpus/legal/security-addendum.md",
                "Security Addendum",
                "On-premises or self-hosted deployment is not offered. Kestrel Insight "
                "is a multi-tenant SaaS product only.",
            ),
            (
                "gs://corpus/engineering/infrastructure-architecture-overview.md",
                "Infrastructure Architecture Overview",
                "Kestrel Insight is multi-tenant with logical isolation. There is no "
                "per-customer infrastructure and no single-tenant deployment option.",
            ),
        ],
    ),
    (
        "Do you encrypt customer data at rest?",
        "Yes. All customer data at rest is encrypted using AES-256-GCM across RDS, S3, "
        "EBS, and Snowflake. No customer data is stored unencrypted at any tier.",
        "SecurityAgent",
        [
            (
                "gs://corpus/security/encryption-standard.md",
                "Encryption Standard",
                "All customer data at rest is encrypted using AES-256-GCM.",
            )
        ],
    ),
    (
        "What is your Recovery Time Objective?",
        "4 hours. The full failover exercise on 17 January 2026 achieved 2 hours 41 "
        "minutes against that objective.",
        "EngineeringAgent",
        [
            (
                "gs://corpus/engineering/backup-restore-procedure.md",
                "Backup and Restore Procedure",
                "RTO 4 hours, RPO 15 minutes. The 17 January 2026 exercise achieved "
                "2 hours 41 minutes.",
            )
        ],
    ),
    (
        "Is multi-factor authentication enforced for all personnel with production access?",
        "Yes. Hardware-backed MFA (WebAuthn / FIDO2) is mandatory for every human "
        "account. SMS and TOTP were removed as acceptable factors on 30 June 2025.",
        "SecurityAgent",
        [
            (
                "gs://corpus/security/access-control-standard.md",
                "Access Control Standard",
                "Hardware-backed MFA is mandatory for every human account. Coverage is "
                "100% of the 187 active employee accounts.",
            )
        ],
    ),
    (
        "Have you experienced a customer data breach in the last 3 years?",
        "No. No customer data breach has occurred in the history of the company. 41 "
        "security events were recorded in 2025, of which two reached SEV2 and neither "
        "involved customer data exposure.",
        "SecurityAgent",
        [
            (
                "gs://corpus/security/incident-response-runbook.md",
                "Incident Response Runbook",
                "No customer data breach has occurred in the history of the company.",
            )
        ],
    ),
]

#: Commitments recorded when round 1 closed. The first is the load-bearing one.
SEEDED_COMMITMENTS: list[tuple[str, str]] = [
    (
        "Do you offer on-premises or self-hosted deployment?",
        "Kestrel Data does not offer on-premises or self-hosted deployment. Kestrel "
        "Insight is multi-tenant SaaS only, with no single-tenant, private-cloud, "
        "air-gapped, or customer-VPC option, and none on the roadmap.",
    ),
    (
        "Do you encrypt customer data at rest?",
        "Kestrel Data encrypts all customer data at rest with AES-256-GCM.",
    ),
    (
        "What is your Recovery Time Objective?",
        "Kestrel Data commits to a Recovery Time Objective of 4 hours.",
    ),
    (
        "Is multi-factor authentication enforced for all personnel with production access?",
        "Kestrel Data enforces hardware-backed multi-factor authentication for 100% of "
        "personnel with production access.",
    ),
    (
        "Have you experienced a customer data breach in the last 3 years?",
        "Kestrel Data has never experienced a customer data breach.",
    ),
]


def seed_firestore(project_id: str, *, dry_run: bool) -> None:
    """Write the backdated review, its round 1, answers, and commitments."""
    db = firestore.Client(project=project_id)

    now = datetime.now(UTC)
    created_at = now - timedelta(days=BACKDATE_DAYS)
    delivered_at = created_at + timedelta(days=3)

    review = Review(
        review_id=REVIEW_ID,
        customer=CUSTOMER,
        framework=Framework.CAIQ,
        residency=Residency.US,
        created_at=created_at,
        current_round=1,
        state=ReviewState.DELIVERED,
    )
    round_one = Round(
        round_id=ROUND_1_ID,
        review_id=REVIEW_ID,
        ordinal=1,
        received_at=created_at,
        closed_at=delivered_at,
        state=ReviewState.DELIVERED,
    )

    if dry_run:
        note("review", f"{REVIEW_ID} (would write, dated {created_at.date()})", made=True)
    else:
        db.collection("reviews").document(REVIEW_ID).set(review.model_dump(mode="json"))
        db.collection("rounds").document(ROUND_1_ID).set(round_one.model_dump(mode="json"))
        note("review", f"{REVIEW_ID} dated {created_at.date()} ({BACKDATE_DAYS}d ago)", made=True)

    # --- questions and answers -------------------------------------------------------
    for text, answer_text, author, citations in SEEDED_ANSWERS:
        question = Question.from_text(text, department=_department_for(author))
        answer = Answer(
            question_id=question.question_id,
            round_id=ROUND_1_ID,
            text=answer_text,
            citations=[
                Citation(
                    document_uri=uri,
                    document_title=title,
                    snippet=snippet,
                    retrieval_score=0.91,
                    retrieved_at=delivered_at,
                )
                for uri, title, snippet in citations
            ],
            confidence=Confidence.HIGH,
            status=AnswerStatus.DELIVERED,
            authored_by=author,
            created_at=delivered_at,
        )
        doc_id = f"{ROUND_1_ID}__{question.question_id}"
        if dry_run:
            note("answer", f"{doc_id} (would write)", made=True)
            continue
        payload = question.model_dump(mode="json")
        payload["round_id"] = ROUND_1_ID
        db.collection("questions").document(doc_id).set(payload)
        db.collection("answers").document(doc_id).set(answer.model_dump(mode="json"))
        note("answer", f"{question.question_id}  {text[:44]}", made=True)

    # --- commitments -----------------------------------------------------------------
    for question_text, statement in SEEDED_COMMITMENTS:
        question_id = make_question_id(question_text)
        # Deterministic id: re-running must overwrite, never duplicate.
        commitment_id = hashlib.sha256(
            f"{REVIEW_ID}\x1f{ROUND_1_ID}\x1f{question_id}".encode()
        ).hexdigest()[:16]
        commitment = Commitment(
            commitment_id=commitment_id,
            review_id=REVIEW_ID,
            round_id=ROUND_1_ID,
            question_id=question_id,
            statement=statement,
            made_at=delivered_at,
        )
        if dry_run:
            note("commitment", f"{commitment_id} (would write)", made=True)
            continue
        db.collection("commitments").document(commitment_id).set(commitment.model_dump(mode="json"))
        note("commitment", f"{commitment_id}  {statement[:44]}", made=True)


def _department_for(author: str) -> Department:
    return {
        "SecurityAgent": Department.SECURITY,
        "LegalAgent": Department.LEGAL,
        "EngineeringAgent": Department.ENGINEERING,
    }.get(author, Department.UNASSIGNED)


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only; change nothing")
    parser.add_argument("--skip-search", action="store_true", help="skip datastore create/import")
    args = parser.parse_args()

    project_id = project()
    dry_run = args.check

    print(f"seeding project {project_id}{'  (DRY RUN)' if dry_run else ''}\n")

    print("== GCS corpus ==")
    uris = stage_corpus(project_id, dry_run=dry_run)

    if not args.skip_search:
        print("\n== Vertex AI Search datastores ==")
        ensure_datastores(project_id, dry_run=dry_run)
        print("\n== corpus import ==")
        import_corpus(project_id, uris, dry_run=dry_run)

    print("\n== Firestore fixtures ==")
    seed_firestore(project_id, dry_run=dry_run)

    print("\n== summary ==")
    print(f"  created : {len(created)}")
    print(f"  existing: {len(existing)}")
    total: int = sum(len(v) for v in uris.values())
    print(f"  corpus documents: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
