#!/usr/bin/env python
"""The whole journey, over HTTP, against the deployed stack.

    PROJECT_ID=attestor-505506 CONTROL_PLANE_URL=https://... \\
    ATTESTOR_WRITE_TOKEN=... uv run python tools/verify_journey.py --limit 60 --write-proof

Every other harness in this repo starts a review by publishing to Pub/Sub directly, which is
the right way to test the *pipeline* and the wrong way to test the *product*. This one does
what a person does, and only what a person can do:

    POST /uploads          -> a v4 signed URL
    PUT  <signed URL>      -> the file, straight to GCS
    POST /reviews          -> the review record
    POST /reviews/{}/rounds -> intake_document published, 202 returned
    ... wait, reading only the endpoints the browser reads ...
    GET  /reviews/{}/export?format=xlsx  -> the customer's workbook, filled in
    GET  /reviews/{}/export?format=pdf   -> the evidence pack

If any of those five write paths is broken -- a missing IAM binding on the signer, a
content-type mismatch on the signed PUT, a guard that refuses a legitimate call -- the
interface is broken in a way no test over the repositories can see. Two of those three have
already happened on this project once each, on a different endpoint, and both were found by
calling the deployed thing rather than by reading the code.

## What it deliberately does NOT do

It does not touch Firestore. Not one repository import. The point is to exercise exactly the
surface the browser has, so a permission the deployed service lacks fails here the way it
would fail for a user, rather than being papered over by a developer's own credentials -- which
is precisely how the `/registry` 503 survived until it was called from Cloud Run.

The one exception is the guard checks at the end, which need to prove a refusal happens. Those
call the API too, with a deliberately wrong token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"
CLEAN = ROOT / "seed" / "questionnaires" / "clean" / "acme-vendor-review-r1.xlsx"

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: How long to wait for the review to reach a state the export can be taken from. A 312-question
#: run takes ~25 minutes at the concurrency the deployed fleet is set to; a 60-question one
#: takes ~4. The default suits the small run and is raised for the big one.
DEFAULT_WAIT_SECONDS = 1800

#: Reads while waiting. Deliberately slow: this harness is not the UI and hammering the control
#: plane while measuring it would be measuring the harness.
POLL_SECONDS = 15


def _request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> tuple[int, bytes, dict[str, str]]:
    """One HTTP call. Returns (status, body, headers) and never raises on an HTTP error.

    Errors are returned rather than raised because several of the checks below are *expecting*
    a 401 or a 429, and a helper that turned those into exceptions would make the assertion
    read backwards.
    """
    request = urllib.request.Request(url, data=body, method=method)  # noqa: S310
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read(), _lower(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), _lower(exc.headers)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc).encode("utf-8"), {}


def _lower(headers: Any) -> dict[str, str]:
    """Header names, lowercased.

    HTTP header names are case-insensitive and Cloud Run's HTTP/2 frontend returns them
    lowercased, so `dict(response.headers)["Content-Disposition"]` finds nothing. The first
    run of this harness reported every export header as blank for exactly that reason -- the
    headers were present and correct and the harness was looking for the wrong keys, which is
    one debugging session away from someone "fixing" a working service.
    """
    return {k.lower(): v for k, v in (headers or {}).items()}


class Journey:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.steps: list[dict[str, Any]] = []

    def _write_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-Attestor-Token": self.token}

    def step(
        self, name: str, ok: bool, detail: dict[str, Any], seconds: float | None = None
    ) -> bool:
        record = {"step": name, "ok": ok, **detail}
        if seconds is not None:
            record["seconds"] = round(seconds, 2)
        self.steps.append(record)
        mark = "PASS" if ok else "FAIL"
        extra = "  ".join(f"{k}={v}" for k, v in detail.items() if k != "body")
        print(f"  {mark}  {name:<34} {extra}")
        if not ok and "body" in detail:
            print(f"        {str(detail['body'])[:400]}")
        return ok

    # -- the journey -------------------------------------------------------------------

    def sign(self, filename: str) -> dict[str, Any] | None:
        started = time.perf_counter()
        status, payload, _ = _request(
            "POST",
            f"{self.base}/uploads",
            body=json.dumps({"filename": filename, "content_type": XLSX_TYPE}).encode(),
            headers=self._write_headers(),
        )
        ok = status == 201
        body = payload.decode("utf-8", "replace")
        # A 500 here is almost always the signer: on Cloud Run there is no private key, so
        # `generate_signed_url` needs `signBlob` on the account itself. Named in the output
        # because the message google-cloud-storage raises does not mention IAM at all.
        hint = (
            " (check roles/iam.serviceAccountTokenCreator on the control-plane SA, granted on "
            "the account itself)"
            if status >= 500 and "private key" in body
            else ""
        )
        if not self.step(
            "POST /uploads -> signed URL",
            ok,
            {"status": status, "body": body[:300] + hint},
            time.perf_counter() - started,
        ):
            return None
        return dict(json.loads(body))

    def upload(self, url: str, path: Path) -> bool:
        started = time.perf_counter()
        content = path.read_bytes()
        status, payload, _ = _request(
            "PUT", url, body=content, headers={"Content-Type": XLSX_TYPE}, timeout=300.0
        )
        return self.step(
            "PUT -> storage.googleapis.com",
            200 <= status < 300,
            {
                "status": status,
                "bytes": len(content),
                "body": payload.decode("utf-8", "replace")[:300],
            },
            time.perf_counter() - started,
        )

    def create(self, customer: str) -> str | None:
        status, payload, _ = _request(
            "POST",
            f"{self.base}/reviews",
            body=json.dumps(
                {"customer": customer, "framework": "caiq", "residency": "us"}
            ).encode(),
            headers=self._write_headers(),
        )
        body = payload.decode("utf-8", "replace")
        if not self.step("POST /reviews", status == 201, {"status": status, "body": body[:300]}):
            return None
        return str(json.loads(body)["review_id"])

    def start(self, review_id: str, gcs_uri: str) -> dict[str, Any] | None:
        status, payload, _ = _request(
            "POST",
            f"{self.base}/reviews/{review_id}/rounds",
            body=json.dumps({"gcs_uri": gcs_uri, "ordinal": 1}).encode(),
            headers=self._write_headers(),
        )
        body = payload.decode("utf-8", "replace")
        # 202, not 201. The round exists; the answers will not for minutes.
        if not self.step(
            "POST /reviews/{}/rounds", status == 202, {"status": status, "body": body[:300]}
        ):
            return None
        return dict(json.loads(body))

    def watch(self, review_id: str, round_id: str, wait_seconds: int) -> dict[str, Any]:
        """Read what the browser reads until the round settles or the clock runs out.

        Reaching the timeout is recorded as a **failed step**, not as a neutral outcome. The
        first version of this harness returned whatever state it found and then reported PASS
        because every HTTP call had succeeded — on a run that held 309 of 312 answers and never
        assembled. A harness that passes a review which did not finish is worse than no harness,
        because it is the artefact someone quotes.
        """
        started = time.perf_counter()
        print(f"\n  watching (reading only the endpoints the browser reads, every {POLL_SECONDS}s)")
        print("   elapsed  state             answers  cited  held")
        print("  --------  ----------------  -------  -----  ----")
        last = ""
        state = "unknown"
        answers: list[dict[str, Any]] = []
        while time.perf_counter() - started < wait_seconds:
            status, payload, _ = _request("GET", f"{self.base}/reviews/{review_id}", timeout=60)
            if status == 200:
                state = str(json.loads(payload)["state"])
            status, payload, _ = _request(
                "GET", f"{self.base}/rounds/{round_id}/answers", timeout=120
            )
            if status == 200:
                answers = list(json.loads(payload))
            cited = sum(1 for a in answers if a.get("citations"))
            held = sum(1 for a in answers if a.get("status") == "needs_human")
            line = f"{state}{len(answers)}{cited}{held}"
            if line != last:
                elapsed = time.perf_counter() - started
                print(f"  {elapsed:7.0f}s  {state:<16}  {len(answers):7d}  {cited:5d}  {held:4d}")
                last = line
            # `awaiting_human` and `delivered` are both settled: the first is the durable pause
            # working as designed, and an export can be taken from either.
            if state in {"delivered", "awaiting_human", "failed"}:
                break
            time.sleep(POLL_SECONDS)

        settled = state in {"delivered", "awaiting_human", "failed"}
        self.step(
            "the review settles on its own",
            settled,
            {"state": state, "answers": len(answers)},
            time.perf_counter() - started,
        )
        return {
            "state": state,
            "settled": settled,
            "answers": len(answers),
            "cited": sum(1 for a in answers if a.get("citations")),
            "held_for_a_human": sum(1 for a in answers if a.get("status") == "needs_human"),
            "no_evidence": sum(1 for a in answers if a.get("status") == "flagged_no_evidence"),
            "wall_seconds": round(time.perf_counter() - started, 1),
        }

    def export(self, review_id: str, fmt: str, out: Path) -> dict[str, Any]:
        started = time.perf_counter()
        status, payload, headers = _request(
            "GET", f"{self.base}/reviews/{review_id}/export?format={fmt}", timeout=300
        )
        magic = payload[:4]
        expected = b"PK\x03\x04" if fmt == "xlsx" else b"%PDF"
        ok = status == 200 and magic == expected
        if ok:
            out.write_bytes(payload)
        detail = {
            "status": status,
            "bytes": len(payload),
            "magic": magic.decode("latin-1", "replace"),
            "disposition": headers.get("content-disposition", ""),
            "rows": headers.get("x-attestor-rows", ""),
            "sendable": headers.get("x-attestor-sendable", ""),
            "source": headers.get("x-attestor-source", ""),
        }
        if not ok:
            detail["body"] = payload.decode("utf-8", "replace")[:300]
        self.step(f"GET export?format={fmt}", ok, detail, time.perf_counter() - started)
        return detail

    # -- the guard ---------------------------------------------------------------------

    def guard_refuses_without_a_token(self) -> bool:
        status, payload, _ = _request(
            "POST",
            f"{self.base}/reviews",
            body=json.dumps({"customer": "Should Not Exist"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        # 401 when the guard is configured. A 503 means the token is not set on the service,
        # which is also a refusal but a different one, and the distinction matters.
        return self.step(
            "POST /reviews with no token",
            status in {401, 503},
            {"status": status, "body": payload.decode("utf-8", "replace")[:200]},
        )

    def guard_refuses_a_wrong_token(self) -> bool:
        status, payload, _ = _request(
            "POST",
            f"{self.base}/reviews",
            body=json.dumps({"customer": "Should Not Exist"}).encode(),
            headers={"Content-Type": "application/json", "X-Attestor-Token": "not-the-token"},
        )
        return self.step(
            "POST /reviews with a wrong token",
            status in {401, 503},
            {"status": status, "body": payload.decode("utf-8", "replace")[:200]},
        )

    def reads_need_no_token(self) -> bool:
        status, _, _ = _request("GET", f"{self.base}/reviews?limit=1")
        return self.step("GET /reviews with no token", status == 200, {"status": status})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="unused; the file decides the count")
    parser.add_argument("--customer", default="Northwind Traders (journey)")
    parser.add_argument("--wait", type=int, default=DEFAULT_WAIT_SECONDS)
    parser.add_argument(
        "--guard-only", action="store_true", help="the refusal checks, nothing else"
    )
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    base = os.environ.get("CONTROL_PLANE_URL", "").strip()
    token = os.environ.get("ATTESTOR_WRITE_TOKEN", "").strip()
    if not base:
        sys.exit("error: CONTROL_PLANE_URL must be set")
    if not token:
        sys.exit(
            "error: ATTESTOR_WRITE_TOKEN must be set. Read it with:\n"
            "  PROJECT_ID=attestor-505506 bash infra/deploy.sh --print-token"
        )
    if not CLEAN.exists():
        sys.exit(f"error: no questionnaire at {CLEAN}; run `make seed`")

    journey = Journey(base, token)
    print("=" * 78)
    print("THE JOURNEY -- upload, start, watch, export. Over HTTP, as a person would.")
    print("=" * 78)
    print(f"  control plane : {base}")
    print(f"  questionnaire : {CLEAN.name} ({CLEAN.stat().st_size:,} bytes)\n")

    print("  the guard")
    guards = [
        journey.guard_refuses_without_a_token(),
        journey.guard_refuses_a_wrong_token(),
        journey.reads_need_no_token(),
    ]
    if args.guard_only:
        passed = all(guards)
        print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1

    print("\n  the journey")
    signed = journey.sign(CLEAN.name)
    if signed is None:
        return 1
    if not journey.upload(signed["upload_url"], CLEAN):
        return 1
    review_id = journey.create(args.customer)
    if review_id is None:
        return 1
    started = journey.start(review_id, signed["gcs_uri"])
    if started is None:
        return 1

    outcome = journey.watch(review_id, started["round_id"], args.wait)
    print(
        f"\n  settled: {outcome['state']}  answers {outcome['answers']}  "
        f"cited {outcome['cited']}  held {outcome['held_for_a_human']}  "
        f"no evidence {outcome['no_evidence']}  in {outcome['wall_seconds']}s\n"
    )

    print("  the deliverable")
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    xlsx = journey.export(review_id, "xlsx", PROOF_DIR / f"export-{review_id}.xlsx")
    pdf = journey.export(review_id, "pdf", PROOF_DIR / f"export-{review_id}.pdf")

    passed = all(step["ok"] for step in journey.steps)
    report = {
        "case": "journey",
        "control_plane": base,
        "review_id": review_id,
        "round_id": started["round_id"],
        "run_id": started["run_id"],
        "dedup_key": started["dedup_key"],
        "gcs_uri": signed["gcs_uri"],
        "questionnaire_bytes": CLEAN.stat().st_size,
        "outcome": outcome,
        "export": {"xlsx": xlsx, "pdf": pdf},
        "steps": journey.steps,
        "pass": passed,
    }
    print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")

    if args.write_proof:
        out = PROOF_DIR / "journey.json"
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out.relative_to(ROOT)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
