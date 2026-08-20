#!/usr/bin/env python
"""Grant Attestor access to one mailbox, once, and put the refresh token in Secret Manager.

    uv run python tools/gmail_authorize.py --client-secrets ~/Downloads/client_secret_....json
    uv run python tools/gmail_authorize.py --client-id ... --client-secret ...   # same thing

A browser opens, the account being used grants consent, and the resulting **refresh
token** is written to the Secret Manager secret `attestor-gmail-oauth`. Nothing else in
the system ever performs this flow: Cloud Run reads the secret and exchanges the refresh
token for access tokens on its own.

## Why a person has to run this and a service account cannot

Gmail's API talks to a *mailbox*, and a mailbox belongs to a user. A service account can
impersonate one only through Workspace domain-wide delegation, which requires a Workspace
domain and a super-admin to authorise the client. Attestor has neither. So consent is
granted once, by hand, by the account that owns the watched inbox -- which is also the
honest description of what this integration is: one mailbox, given deliberately.

## Why the flow is hand-rolled

`google-auth-oauthlib` would do this in four lines and add a dependency, used exactly
once, to every container image that never runs it. The installed-application flow is an
authorisation URL, a loopback redirect, and one token exchange; all three are below and
all three are checkable.

## What it prints, and what it does not

It prints the granted scopes, the mailbox address, and the secret version. It never prints
the refresh token. A refresh token for a mailbox is a long-lived credential, and a
terminal is a place where things get pasted into issues.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from typing import Any

from attestor_platform.gmail import OAUTH_SECRET, SCOPES

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL
USERINFO = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

#: Loopback, not a public redirect. Google treats `http://127.0.0.1:<port>` as a valid
#: redirect for a Desktop client without registering the exact port.
REDIRECT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect the consent screen makes back to us."""

    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CodeCatcher.code = next(iter(query.get("code", [])), None)
        _CodeCatcher.error = next(iter(query.get("error", [])), None)
        body = (
            b"<h2>Attestor: consent recorded. You can close this tab.</h2>"
            if _CodeCatcher.code
            else b"<h2>Attestor: consent was refused.</h2>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default stderr access log."""


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(  # noqa: S310 - a constant https URL
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return dict(json.loads(response.read().decode("utf-8")))


def _client_from_file(path: str) -> tuple[str, str]:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    block = document.get("installed") or document.get("web") or {}
    return str(block.get("client_id", "")), str(block.get("client_secret", ""))


def _write_secret(project: str, payload: dict[str, Any]) -> str:
    """Create the secret if it does not exist, then add a version. Returns the version."""
    from google.api_core import exceptions as gexc
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project}"
    try:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": OAUTH_SECRET,
                "secret": {"replication": {"automatic": {}}},
            }
        )
        print(f"  created secret {OAUTH_SECRET}")
    except gexc.AlreadyExists:
        print(f"  secret {OAUTH_SECRET} already exists; adding a version")
    version = client.add_secret_version(
        request={
            "parent": f"{parent}/secrets/{OAUTH_SECRET}",
            "payload": {"data": json.dumps(payload).encode("utf-8")},
        }
    )
    return str(version.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-secrets", help="the JSON downloaded from the console")
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="do not write Secret Manager; print the payload shape and exit",
    )
    args = parser.parse_args()

    project = os.environ.get("PROJECT_ID", "").strip()
    if not project and not args.print_only:
        sys.exit("error: PROJECT_ID must be set (the project holding the secret)")

    if args.client_secrets:
        client_id, client_secret = _client_from_file(args.client_secrets)
    else:
        client_id, client_secret = args.client_id or "", args.client_secret or ""
    if not client_id or not client_secret:
        sys.exit("error: pass --client-secrets, or both --client-id and --client-secret")

    redirect_uri = f"http://{REDIRECT_HOST}:{args.port}/"
    # PKCE, even though this is a confidential client with a secret. It costs two lines and
    # removes the class of attack where the authorization code is intercepted on the
    # loopback redirect -- which on a shared machine is not hypothetical.
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    state = secrets.token_urlsafe(24)

    url = f"{AUTH_URI}?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # Without both of these Google returns no refresh token on a re-consent, and
            # the failure is silent: an access token that works for an hour and then does
            # not. This is the single most common way this flow is got wrong.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )

    print("=" * 78)
    print("GMAIL CONSENT -- one mailbox, once")
    print("=" * 78)
    print("  scopes:")
    for scope in SCOPES:
        print(f"    {scope}")
    print(f"\n  listening on {redirect_uri}")
    print("  opening the consent screen. Sign in as the mailbox Attestor should watch.\n")

    server = http.server.HTTPServer((REDIRECT_HOST, args.port), _CodeCatcher)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    if not webbrowser.open(url):
        print(f"  could not open a browser. Visit this URL by hand:\n\n{url}\n")
    thread.join(timeout=300)
    server.server_close()

    if _CodeCatcher.error:
        sys.exit(f"error: consent refused: {_CodeCatcher.error}")
    if not _CodeCatcher.code:
        sys.exit("error: no authorization code was received within 5 minutes")

    tokens = _post_form(
        TOKEN_URI,
        {
            "code": _CodeCatcher.code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    refresh_token = str(tokens.get("refresh_token") or "")
    if not refresh_token:
        sys.exit(
            "error: Google returned no refresh token. This happens when the account has "
            "already granted consent; revoke Attestor at "
            "https://myaccount.google.com/permissions and run this again."
        )

    request = urllib.request.Request(
        USERINFO, headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        profile = json.loads(response.read().decode("utf-8"))
    address = str(profile.get("emailAddress", ""))

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "email": address,
        "scopes": list(SCOPES),
    }

    print(f"  mailbox        : {address}")
    print(f"  granted scopes : {tokens.get('scope', '(not reported)')}")
    print(f"  history id     : {profile.get('historyId')}")
    print(f"  messages       : {profile.get('messagesTotal')}")

    if args.print_only:
        print("\n  --print-only: nothing written. Payload keys: " + ", ".join(sorted(payload)))
        return 0

    version = _write_secret(project, payload)
    print(f"\n  wrote {version}")
    print("\n  next: uv run python tools/gmail_watch.py --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
