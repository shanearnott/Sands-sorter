#!/usr/bin/env python3
"""Run a Google OAuth flow and produce a token JSON file the app can read.

Use cases:

  1. Initial Drive+Gmail token for the deployed Cloud Run service:
        python scripts/auth/get_token.py \\
            --client-secrets ~/Downloads/client_secret.json \\
            --scopes drive,gmail-modify,gmail-send \\
            --output drive_token.json

     Then upload to Secret Manager:
        gcloud secrets versions add drive-token --data-file=drive_token.json

     Or do both in one go:
        python scripts/auth/get_token.py \\
            --client-secrets ~/Downloads/client_secret.json \\
            --scopes drive,gmail-modify,gmail-send \\
            --upload-to-secret drive-token \\
            --gcp-project your-project

  2. Per-account Gmail token for multi-account polling:
        python scripts/auth/get_token.py \\
            --client-secrets ~/Downloads/client_secret.json \\
            --scopes gmail-modify \\
            --output personal_gmail_token.json

The OAuth client must be of type **Desktop app** (Cloud Console → APIs &
Services → Credentials → Create OAuth client → Desktop). That's a different
client from the one your *web app* uses to log users in.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Scope presets — friendly names map to the actual Google scope URLs.
SCOPE_PRESETS = {
    "drive": "https://www.googleapis.com/auth/drive.file",
    "drive-readonly": "https://www.googleapis.com/auth/drive.readonly",
    "gmail-modify": "https://www.googleapis.com/auth/gmail.modify",
    "gmail-readonly": "https://www.googleapis.com/auth/gmail.readonly",
    "gmail-send": "https://www.googleapis.com/auth/gmail.send",
}

# Default scope set covers everything Sands-sorter needs from a single token.
DEFAULT_SCOPES = "drive,gmail-modify,gmail-send"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="get_token.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--client-secrets",
        required=True,
        help="Path to the OAuth client_secret.json from Cloud Console (Desktop type).",
    )
    parser.add_argument(
        "--scopes",
        default=DEFAULT_SCOPES,
        help=(
            "Comma-separated scope names from "
            f"{sorted(SCOPE_PRESETS)}. Default: {DEFAULT_SCOPES}."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("token.json"),
        help="Where to write the token JSON (default: ./token.json).",
    )
    parser.add_argument(
        "--upload-to-secret",
        metavar="SECRET_NAME",
        help=(
            "If set, also upload the resulting JSON as a new version of this "
            "Secret Manager secret. Requires --gcp-project."
        ),
    )
    parser.add_argument(
        "--gcp-project",
        default=os.environ.get("GCP_PROJECT_ID"),
        help="GCP project for Secret Manager. Defaults to $GCP_PROJECT_ID.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port for the OAuth callback (0 = let the OS pick).",
    )
    args = parser.parse_args(argv)

    scopes = _resolve_scopes(args.scopes)
    if not scopes:
        parser.error(
            f"--scopes must include at least one of {sorted(SCOPE_PRESETS)}"
        )

    token_payload = _run_oauth_flow(args.client_secrets, scopes, args.port)

    args.output.write_text(json.dumps(token_payload, indent=2))
    args.output.chmod(0o600)
    print(f"\n✓ Token written to {args.output}")
    print(f"  Scopes granted: {', '.join(token_payload.get('scopes', []))}")

    if args.upload_to_secret:
        if not args.gcp_project:
            parser.error("--upload-to-secret requires --gcp-project (or $GCP_PROJECT_ID)")
        version = _upload_secret(
            args.gcp_project, args.upload_to_secret, args.output.read_bytes()
        )
        print(f"✓ Uploaded to Secret Manager: {version}")

    return 0


def _resolve_scopes(raw: str) -> list[str]:
    scopes: list[str] = []
    for name in raw.split(","):
        name = name.strip()
        if not name:
            continue
        if name not in SCOPE_PRESETS:
            print(
                f"unknown scope '{name}'. Known: {sorted(SCOPE_PRESETS)}",
                file=sys.stderr,
            )
            sys.exit(2)
        scopes.append(SCOPE_PRESETS[name])
    return scopes


def _run_oauth_flow(client_secrets_path: str, scopes: list[str], port: int) -> dict:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        print(
            "google-auth-oauthlib is not installed. `pip install -e .` from the repo root.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, scopes)
    # `prompt='consent'` ensures we always get a refresh_token, even if the
    # user previously consented to the same scopes for this client.
    creds = flow.run_local_server(port=port, prompt="consent", access_type="offline")

    if not creds.refresh_token:
        print(
            "WARNING: no refresh_token returned. The token will only work until "
            "the access_token expires. Re-run with --port 0 and accept fresh "
            "consent in the browser.",
            file=sys.stderr,
        )

    # `Credentials.to_json` produces the dict shape `from_authorized_user_info` expects.
    return json.loads(creds.to_json())


def _upload_secret(project: str, secret_name: str, payload: bytes) -> str:
    try:
        from google.api_core.exceptions import NotFound
        from google.cloud import secretmanager
    except ImportError as exc:
        print(
            "google-cloud-secret-manager is not installed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project}"
    secret_path = f"{parent}/secrets/{secret_name}"

    try:
        client.get_secret(name=secret_path)
    except NotFound:
        print(f"Creating Secret Manager secret '{secret_name}'…")
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_name,
                "secret": {"replication": {"automatic": {}}},
            }
        )

    version = client.add_secret_version(
        request={"parent": secret_path, "payload": {"data": payload}}
    )
    return version.name


if __name__ == "__main__":
    sys.exit(main())
