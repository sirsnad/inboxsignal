"""Google OAuth for the installed-app flow.

Setup (one time, on the owner's machine):
1. Google Cloud Console -> new project -> enable the Gmail API.
2. OAuth consent screen: External, add yourself as a test user. The consent
   text should note the app reads mail; label/send scopes arrive in Phase 2.
3. Credentials -> Create credentials -> OAuth client ID -> Desktop app.
   Download the JSON as credentials.json in the repo root (gitignored).
4. Run `python -m app.sync --backfill`; a browser window handles consent and
   token.json (gitignored) is written next to it.
"""

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .. import config


def get_credentials() -> Credentials:
    creds = None
    if config.TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(
            str(config.TOKEN_PATH), config.GMAIL_SCOPES
        )
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        config.TOKEN_PATH.write_text(creds.to_json())
    if not creds or not creds.valid:
        if not config.CREDENTIALS_PATH.exists():
            raise RuntimeError(
                "credentials.json not found in the repo root. See app/gmail/auth.py "
                "for the one-time Google Cloud setup steps."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(config.CREDENTIALS_PATH), config.GMAIL_SCOPES
        )
        creds = flow.run_local_server(port=0)
        config.TOKEN_PATH.write_text(creds.to_json())
    return creds
