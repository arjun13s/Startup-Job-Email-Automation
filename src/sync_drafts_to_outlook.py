"""
Push email drafts from drafts.csv into your Outlook Drafts folder (no sending).

One-time setup:
1. Go to https://portal.azure.com → Azure Active Directory → App registrations → New registration.
2. Name it (e.g. "Startup Job Drafts"), choose "Accounts in any org + personal Microsoft accounts".
3. Under Authentication → add platform "Mobile and desktop applications", allow "Public client/native".
4. Copy the Application (client) ID. Put it in .env as MICROSOFT_CLIENT_ID=...
5. Run this script once; it will open a browser/device code flow. Sign in and approve.
6. Token is cached locally; future runs reuse it. This script only creates drafts; it cannot send mail.
"""

import json
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
import msal
import requests

load_dotenv(_root / ".env", override=True)
load_dotenv(Path.cwd() / ".env", override=True)

from src.utils import log, read_csv

DRAFTS_CSV = _root / "data" / "final" / "drafts.csv"
TOKEN_CACHE_PATH = _root / "data" / "outlook_token_cache.json"
SCOPES = ["https://graph.microsoft.com/Mail.ReadWrite", "offline_access", "User.Read"]
GRAPH_ME_MESSAGES = "https://graph.microsoft.com/v1.0/me/messages"
AUTHORITY = "https://login.microsoftonline.com/common"
CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()


def _get_token_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        try:
            cache.deserialize(TOKEN_CACHE_PATH.read_text())
        except Exception:
            pass
    return cache


def _save_token_cache(cache: msal.SerializableTokenCache) -> None:
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if cache.has_state_changed:
        TOKEN_CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")


def _acquire_token() -> str:
    """Get access token via cached refresh or device code flow. Raises if no client ID."""
    if not CLIENT_ID:
        raise ValueError(
            "MICROSOFT_CLIENT_ID is not set. Register an app in Azure Portal and add the client ID to .env. See script docstring."
        )
    cache = _get_token_cache()
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "message" in flow:
            print(flow["message"])
            result = app.acquire_token_by_device_flow(flow)
        else:
            raise RuntimeError("Device flow failed to start.")
    _save_token_cache(cache)
    if not result or "access_token" not in result:
        raise RuntimeError("Failed to get token. Sign in again when prompted.")
    return result["access_token"]


def _create_draft(access_token: str, to_email: str, subject: str, body: str) -> None:
    """Create one draft in Outlook. Does not send."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "subject": subject or "(No subject)",
        "body": {
            "contentType": "Text",
            "content": body or "",
        },
    }
    if to_email and to_email.strip():
        payload["toRecipients"] = [
            {"emailAddress": {"address": to_email.strip()}}
        ]
    resp = requests.post(GRAPH_ME_MESSAGES, headers=headers, json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Graph API error {resp.status_code}: {resp.text[:200]}")


def main() -> None:
    if not DRAFTS_CSV.exists():
        log(f"No drafts file at {DRAFTS_CSV}. Run draft_emails.py first.")
        sys.exit(1)

    rows = read_csv(DRAFTS_CSV)
    if not rows:
        log("No drafts to sync.")
        return

    log("Getting Outlook access (sign in if prompted)...")
    token = _acquire_token()
    log(f"Syncing {len(rows)} draft(s) to Outlook (Drafts folder only; nothing will be sent).")

    ok, err = 0, 0
    for i, row in enumerate(rows):
        to_email = (
            row.get("to_email") or row.get("contact_email") or row.get("email") or ""
        )
        subject = row.get("subject", "")
        body = row.get("body", "")
        company = row.get("company_name", "?")
        try:
            _create_draft(token, to_email, subject, body)
            ok += 1
            log(f"  [{i + 1}/{len(rows)}] Draft created: {company}" + (f" -> {to_email}" if to_email else " (no TO address)"))
        except Exception as e:
            err += 1
            log(f"  [{i + 1}/{len(rows)}] Failed {company}: {e}")

    log(f"Done. Created {ok} draft(s)." + (f" {err} failed." if err else ""))


if __name__ == "__main__":
    main()
