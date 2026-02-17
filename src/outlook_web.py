"""
Outlook OAuth API for frontend integration: "Connect with Outlook" → sign in → sync drafts.

Run this server so your frontend can:
1. GET /api/outlook/auth-url → get URL to send user to Microsoft sign-in
2. User signs in at Microsoft, is redirected to /auth/outlook/callback
3. GET /auth/outlook/callback?code=... → we store the token, redirect to your frontend
4. POST /api/outlook/sync-drafts → create drafts in Outlook (uses stored token)
5. GET /api/outlook/status → { "connected": true/false }

Requires Azure app with "Web" platform and redirect URI (see OUTLOOK_SETUP.md).
"""

import json
import os
import secrets
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

from flask import Flask, redirect, request, jsonify
from flask_cors import CORS

# Config from env
CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.environ.get("OUTLOOK_REDIRECT_URI", "http://localhost:5000/auth/outlook/callback").strip()
FRONTEND_SUCCESS_URL = os.environ.get("OUTLOOK_FRONTEND_SUCCESS_URL", "http://localhost:3000?outlook=connected").strip()
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["https://graph.microsoft.com/Mail.ReadWrite", "offline_access", "User.Read"]
GRAPH_ME_MESSAGES = "https://graph.microsoft.com/v1.0/me/messages"
WEB_TOKENS_PATH = _root / "data" / "outlook_web_tokens.json"
DRAFTS_CSV = _root / "data" / "final" / "drafts.csv"

# In-memory state for CSRF (use Redis or signed cookie in production)
_pending_states: dict[str, None] = {}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
CORS(app, origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","))


def _get_msal_app():
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )


def _load_web_tokens() -> dict | None:
    if not WEB_TOKENS_PATH.exists():
        return None
    try:
        return json.loads(WEB_TOKENS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_web_tokens(data: dict) -> None:
    WEB_TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEB_TOKENS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_valid_access_token() -> str | None:
    """Return a valid access token, refreshing if needed."""
    data = _load_web_tokens()
    if not data or not data.get("refresh_token"):
        return None
    app_msal = _get_msal_app()
    result = app_msal.acquire_token_by_refresh_token(
        data["refresh_token"], scopes=SCOPES
    )
    if not result or "access_token" not in result:
        return None
    # Optionally save new tokens if refresh returned them
    if "refresh_token" in result:
        _save_web_tokens({
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "expires_on": result.get("expires_on", 0),
        })
    return result["access_token"]


def _create_draft(access_token: str, to_email: str, subject: str, body: str) -> None:
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "subject": subject or "(No subject)",
        "body": {"contentType": "Text", "content": body or ""},
    }
    if to_email and to_email.strip():
        payload["toRecipients"] = [{"emailAddress": {"address": to_email.strip()}}]
    resp = requests.post(GRAPH_ME_MESSAGES, headers=headers, json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Graph API {resp.status_code}: {resp.text[:200]}")


@app.route("/api/outlook/auth-url", methods=["GET"])
def outlook_auth_url():
    """Return the URL to send the user to for Microsoft sign-in. Frontend redirects: window.location = data.auth_url."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return jsonify({"error": "MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET must be set"}), 500
    state = secrets.token_urlsafe(32)
    _pending_states[state] = None
    msal_app = _get_msal_app()
    auth_url = msal_app.get_authorization_request_url(
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )
    return jsonify({"auth_url": auth_url, "state": state})


@app.route("/auth/outlook/callback", methods=["GET"])
def outlook_callback():
    """Microsoft redirects here after sign-in. We exchange code for tokens and redirect to frontend."""
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    if error:
        return redirect(f"{FRONTEND_SUCCESS_URL}?outlook=error&error={error}")
    if not code or state not in _pending_states:
        return redirect(f"{FRONTEND_SUCCESS_URL}?outlook=error&error=invalid_callback")
    _pending_states.pop(state, None)
    msal_app = _get_msal_app()
    result = msal_app.acquire_token_by_authorization_code(
        code, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    if not result or "access_token" not in result:
        return redirect(f"{FRONTEND_SUCCESS_URL}?outlook=error&error=token_failed")
    _save_web_tokens({
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", ""),
        "expires_on": result.get("expires_on", 0),
    })
    return redirect(f"{FRONTEND_SUCCESS_URL}?outlook=connected")


@app.route("/api/outlook/status", methods=["GET"])
def outlook_status():
    """Return whether Outlook is connected (token stored)."""
    token = _get_valid_access_token()
    return jsonify({"connected": token is not None})


@app.route("/api/outlook/sync-drafts", methods=["POST"])
def outlook_sync_drafts():
    """Create drafts in Outlook from request body or from drafts.csv. Returns count created."""
    token = _get_valid_access_token()
    if not token:
        return jsonify({"error": "Not connected. Complete Outlook sign-in first."}), 401

    drafts = request.get_json(silent=True)
    if not drafts and DRAFTS_CSV.exists():
        from src.utils import read_csv
        rows = read_csv(DRAFTS_CSV)
        drafts = [
            {
                "to_email": r.get("to_email") or r.get("contact_email") or r.get("email") or "",
                "subject": r.get("subject", ""),
                "body": r.get("body", ""),
            }
            for r in rows
        ]
    if not drafts:
        return jsonify({"error": "No drafts to sync. Provide JSON body or run draft_emails.py first."}), 400

    ok, err = 0, 0
    for d in drafts:
        try:
            _create_draft(
                token,
                d.get("to_email", "") or d.get("email", ""),
                d.get("subject", ""),
                d.get("body", ""),
            )
            ok += 1
        except Exception:
            err += 1
    return jsonify({"created": ok, "failed": err})


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET in .env for web flow. See OUTLOOK_SETUP.md.")
    port = int(os.environ.get("OUTLOOK_API_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")


if __name__ == "__main__":
    main()
