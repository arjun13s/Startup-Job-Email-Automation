# Outlook drafts sync (no sending)

This lets you push drafts from `data/final/drafts.csv` into your **Outlook Drafts** folder. The script **only creates drafts**; it does not send any email.

## One-time setup

1. **Azure app**
   - Go to [Azure Portal](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**.
   - Name (e.g. "Startup Job Drafts"), support "Accounts in any organizational directory and personal Microsoft accounts".
   - Register.

2. **Allow public client**
   - Open your app → **Authentication** → **Add a platform** → **Mobile and desktop applications**.
   - Check "Public client/native" and save.

3. **Client ID**
   - In the app, open **Overview** and copy **Application (client) ID**.
   - In your project `.env` add:
     ```env
     MICROSOFT_CLIENT_ID=your-client-id-here
     ```

4. **First run**
   - Install deps: `pip install -r requirements.txt`
   - Run: `python src/sync_drafts_to_outlook.py`
   - A device code and URL will be printed; open the URL, sign in with your Outlook/Microsoft account, and enter the code.
   - After that, a token is cached in `data/outlook_token_cache.json` and future runs reuse it.

## Optional: TO address

The CSV can include a **to_email** (or **contact_email** or **email**) column. If present, each draft will have that address in the **To:** field. If not, drafts are created with subject and body only; you can add the recipient in Outlook before sending.

## Run

```powershell
.\.venv\Scripts\python.exe src/sync_drafts_to_outlook.py
```

Only drafts are created; nothing is sent.
