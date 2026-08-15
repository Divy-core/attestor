#!/usr/bin/env python3
"""Deploy Firestore security rules and prove they deny what they claim to.

gcloud has no Firestore rules command -- rules live behind the Firebase Rules API
(`firebaserules.googleapis.com`). This does the two-step the Firebase CLI does:

    1. Create a ruleset from the source file.
    2. Point the `cloud.firestore` release at it.

Then it runs the API's own test endpoint against the deployed source, so the denial is
*measured* rather than asserted. The load-bearing cases are the append-only ones: an
update or delete against `audit_events` must be denied, because an audit trail that can
be rewritten is not an audit trail.

    PROJECT_ID=attestor-505506 uv run python infra/firestore/deploy_rules.py
    PROJECT_ID=attestor-505506 uv run python infra/firestore/deploy_rules.py --test-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests

HOST = "https://firebaserules.googleapis.com/v1"
RULES_FILE = Path(__file__).parent / "firestore.rules"
RELEASE_NAME = "cloud.firestore"
TIMEOUT = 30.0


def token() -> str:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]
    return str(credentials.token)


def call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{HOST}/{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return dict(json.loads(response.read().decode("utf-8") or "{}"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} on {method} {path}: {detail[:500]}") from exc


#: Each case: (label, request, expect_allow). `request` follows the Rules API TestCase
#: shape. Expectation is expressed as ALLOW/DENY so the intent is readable.
TEST_CASES: list[tuple[str, str, str, bool]] = [
    # (label, method, path, expect_allow)
    (
        "audit_events: create denied to clients",
        "create",
        "/databases/(default)/documents/audit_events/e1",
        False,
    ),
    (
        "audit_events: UPDATE denied",
        "update",
        "/databases/(default)/documents/audit_events/e1",
        False,
    ),
    (
        "audit_events: DELETE denied",
        "delete",
        "/databases/(default)/documents/audit_events/e1",
        False,
    ),
    (
        "armor_events: UPDATE denied",
        "update",
        "/databases/(default)/documents/armor_events/e1",
        False,
    ),
    (
        "armor_events: DELETE denied",
        "delete",
        "/databases/(default)/documents/armor_events/e1",
        False,
    ),
    (
        "commitments: UPDATE denied",
        "update",
        "/databases/(default)/documents/commitments/c1",
        False,
    ),
    (
        "commitments: DELETE denied",
        "delete",
        "/databases/(default)/documents/commitments/c1",
        False,
    ),
    (
        "reviews: client write denied",
        "update",
        "/databases/(default)/documents/reviews/rev1",
        False,
    ),
    ("answers: client write denied", "update", "/databases/(default)/documents/answers/a1", False),
    (
        "unlisted collection: write denied",
        "create",
        "/databases/(default)/documents/anything/x1",
        False,
    ),
    (
        "unlisted collection: read denied",
        "get",
        "/databases/(default)/documents/anything/x1",
        False,
    ),
]


def build_test_suite() -> dict[str, Any]:
    cases = []
    for label, method, path, expect_allow in TEST_CASES:
        cases.append(
            {
                "expectation": "ALLOW" if expect_allow else "DENY",
                "request": {
                    "auth": {"uid": "attacker", "token": {}},
                    "path": path,
                    "method": method,
                },
                "functionMocks": [],
                # Carried through so failures name the case rather than an index.
                "pathEncoding": "URL_ENCODED",
                "expressionReportLevel": "NONE",
                "_label": label,
            }
        )
    return {"testCases": cases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-only", action="store_true", help="test without deploying")
    args = parser.parse_args()

    project_id = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        sys.exit("error: PROJECT_ID must be set")

    source = RULES_FILE.read_text(encoding="utf-8")
    print(f"rules file: {RULES_FILE}  ({len(source.splitlines())} lines)\n")

    ruleset_source = {
        "files": [{"name": "firestore.rules", "content": source}],
    }

    # ---- test first: never deploy rules that fail their own suite ---------------------
    suite = build_test_suite()
    labels = [case.pop("_label") for case in suite["testCases"]]

    print("== testing rules ==")
    result = call(
        "POST",
        f"projects/{project_id}:test",
        {"source": ruleset_source, "testSuite": suite},
    )

    issues = result.get("issues", [])
    if issues:
        print("  RULES DID NOT COMPILE:")
        for issue in issues[:5]:
            print(f"    {issue}")
        return 1

    results = result.get("testResults", [])
    failures = 0
    for label, outcome in zip(labels, results, strict=False):
        state = outcome.get("state", "?")
        ok = state == "SUCCESS"
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            for err in outcome.get("errorPosition", []):
                print(f"        {err}")

    print(f"\n  {len(results) - failures}/{len(results)} rule expectations held")
    if failures:
        print("  refusing to deploy: the rules do not deny what they claim to")
        return 1

    if args.test_only:
        print("\n--test-only: not deploying")
        return 0

    # ---- deploy ----------------------------------------------------------------------
    print("\n== deploying ==")
    ruleset = call("POST", f"projects/{project_id}/rulesets", {"source": ruleset_source})
    ruleset_name = ruleset["name"]
    print(f"  ruleset: {ruleset_name}")

    release_path = f"projects/{project_id}/releases/{RELEASE_NAME}"
    body = {"name": release_path, "rulesetName": ruleset_name}
    try:
        call("PUT", release_path, body)
        print(f"  release updated: {release_path}")
    except RuntimeError as exc:
        if "404" in str(exc):
            call("POST", f"projects/{project_id}/releases", body)
            print(f"  release created: {release_path}")
        else:
            raise

    live = call("GET", release_path)
    print(f"\n  live ruleset: {live.get('rulesetName')}")
    print(f"  updated at  : {live.get('updateTime')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
