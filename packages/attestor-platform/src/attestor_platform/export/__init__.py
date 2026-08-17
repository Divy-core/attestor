"""Export: a finished review leaves the system in the shape it arrived in.

A vendor security review ends when the completed spreadsheet goes back to the customer.
Everything before this module produces answers in Firestore, which is where the work is
*done* and not where the work is *delivered*. Two formats, because security reviewers ask
for two things:

- ``workbook`` — the customer's own file with answers written into it. Same sheets, same
  rows, same order, so it is directly returnable rather than something they have to
  reconcile against what they sent.
- ``evidence_pack`` — a PDF of every answer with its citations, sections and relevance
  scores. This is the artefact a reviewer actually reads when they want to check a claim.

## Why this lives in ``attestor_platform`` and not ``attestor_fleet``

The Phase 3 plan put ``export.py`` under ``attestor_fleet``. It is here instead, and the
reason is the dependency graph rather than taste: the *control plane* serves the download,
``services/control-plane`` depends on core and platform only, and adding ``attestor_fleet``
to it would pull google-adk and vertexai into the one service a browser can reach. That
service is deliberately small. openpyxl already lives in this package, so a document-format
adapter is not out of place next to the GCS and Firestore adapters — all three are
"translate between our models and someone else's format".

## Nothing here decides anything

Release state is read from ``Answer.status``, never inferred. A draft is labelled a draft.
The one editorial decision this module makes is that *only* ``APPROVED`` counts as cleared
for release, and it makes that decision identically in both formats.
"""

from __future__ import annotations

from attestor_platform.export.evidence_pack import build_evidence_pack
from attestor_platform.export.model import (
    RELEASE_RULE,
    ExportBundle,
    ExportRow,
    ReleaseState,
    build_bundle,
)
from attestor_platform.export.workbook import fill_workbook

__all__ = [
    "RELEASE_RULE",
    "ExportBundle",
    "ExportRow",
    "ReleaseState",
    "build_bundle",
    "build_evidence_pack",
    "fill_workbook",
]
