# Outlook drafts sync (no sending)

This lets you push drafts from `data/final/drafts.csv` into your **Outlook Drafts** folder. The script **only creates drafts**; it does not send any email.

You can use either:
- **CLI**: device code flow (terminal).
- **Web / frontend**: redirect flow ("Connect with Outlook" → sign in in browser → sync).

---

## One-time setup (both CLI and Web)

1. **Azure app**
   - Go to [Azure Portal](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**.
   - Name (e.g. "Startup Job Drafts"), support "Accounts in any organizational directory and personal Microsoft accounts".
   - Register.

2. **Client ID**
   - In the app, open **Overview** and copy **Application (client) ID**.
   - In your project `.env` add:
     ```env
     MICROSOFT_CLIENT_ID=your-client-id-here
     ```

---

## CLI (device code)

1. **Allow public client**
   - In your Azure app → **Authentication** → **Add a platform** → **Mobile and desktop applications**.
   - Check "Public client/native" and save.

2. **First run**
   - Run: `python src/sync_drafts_to_outlook.py`
   - A device code and URL will be printed; open the URL, sign in with your Outlook/Microsoft account, and enter the code.
   - After that, a token is cached in `data/outlook_token_cache.json` and future runs reuse it.

**Run:**
```powershell
.\.venv\Scripts\python.exe src/sync_drafts_to_outlook.py
```

---

## Web / frontend ("Connect with Outlook")

Use this so your website can show "Connect with Outlook", send the user to Microsoft sign-in, then sync drafts from your app.

### Azure: Web platform and redirect URI

1. In your Azure app → **Authentication** → **Add a platform** → **Web**.
2. Under **Redirect URIs** add:
   - Local: `http://localhost:5000/auth/outlook/callback`
   - Production: `https://yourdomain.com/auth/outlook/callback` (when you deploy).
3. Under **Certificates & secrets** → **New client secret** → copy the **Value** (not the ID). Put it in `.env` as `MICROSOFT_CLIENT_SECRET`.

### .env for web flow

```env
MICROSOFT_CLIENT_ID=your-client-id
MICROSOFT_CLIENT_SECRET=your-client-secret-value
OUTLOOK_REDIRECT_URI=http://localhost:5000/auth/outlook/callback
OUTLOOK_FRONTEND_SUCCESS_URL=http://localhost:3000?outlook=connected
```

- `OUTLOOK_REDIRECT_URI`: must match exactly what you added in Azure (where Microsoft redirects after sign-in).
- `OUTLOOK_FRONTEND_SUCCESS_URL`: where we send the user after a successful connection (your frontend URL).

### Run the API server

```powershell
.\.venv\Scripts\python.exe src/outlook_web.py
```

Runs on port 5000 by default. Set `OUTLOOK_API_PORT` in `.env` to change.

### API for your frontend

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/outlook/auth-url` | GET | Returns `{ "auth_url": "https://login.microsoftonline.com/..." }`. Frontend redirects user: `window.location = data.auth_url`. |
| `/auth/outlook/callback` | GET | Microsoft redirects here after sign-in. We store the token and redirect to `OUTLOOK_FRONTEND_SUCCESS_URL?outlook=connected`. |
| `/api/outlook/status` | GET | Returns `{ "connected": true }` or `{ "connected": false }`. Use to show "Connected" vs "Connect Outlook". |
| `/api/outlook/sync-drafts` | POST | Creates drafts in Outlook. Uses stored token. If body is empty, reads from `data/final/drafts.csv`. Or send JSON: `[{ "subject": "...", "body": "...", "to_email": "..." }]`. |

**Frontend flow:**

1. User clicks "Connect with Outlook".
2. Frontend calls `GET /api/outlook/auth-url` (e.g. from your backend or same origin), gets `auth_url`, then `window.location = auth_url`.
3. User signs in at Microsoft and is redirected to `/auth/outlook/callback` on your API server; we save the token and redirect to your frontend (`OUTLOOK_FRONTEND_SUCCESS_URL`).
4. Frontend shows "Connected". "Sync drafts" button calls `POST /api/outlook/sync-drafts` (and optionally sends draft list in the body).

---

## Optional: TO address

The CSV can include a **to_email** (or **contact_email** or **email**) column. If present, each draft will have that address in the **To:** field. If not, drafts are created with subject and body only; you can add the recipient in Outlook before sending.

Only drafts are created; nothing is sent.
