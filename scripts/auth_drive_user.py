"""Local helper to authorize a separate Google Drive account.

Decouples the Drive user identity from the GCP service/runtime identity:
- GCP Account (in gcloud ADC) continues to be used for Vertex AI Gemini LLMs.
- Drive Account (authorized here) is saved to .drive_user_token.json and used for Google Docs.
"""

import json
import logging
import os
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("auth_drive_user")

REDIRECT_PORT = 8085
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"
SCOPES = "https://www.googleapis.com/auth/drive.file"


def get_oauth_client_info():
    """Retrieve client_id and client_secret from local files or gcloud ADC."""
    for fname in ["client_secret.json", "client_secrets.json"]:
        if os.path.isfile(fname):
            with open(fname, "r", encoding="utf-8") as f:
                data = json.load(f)
                info = data.get("installed") or data.get("web") or data
                if info.get("client_id") and info.get("client_secret"):
                    return info["client_id"], info["client_secret"]

    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    if os.path.isfile(adc_path):
        with open(adc_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if data.get("client_id") and data.get("client_secret"):
                return data["client_id"], data["client_secret"]

    return None, None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """
            <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1 style="color: #1a73e8;">✓ Drive Account Authorized!</h1>
                <p>Google Drive authorization was successful. You can close this window and return to your terminal.</p>
            </body></html>
            """
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed or denied.")

    def log_message(self, format, *args):
        pass


def main():
    client_id, client_secret = get_oauth_client_info()
    if not client_id or not client_secret:
        logger.error(
            "❌ Could not find OAuth client credentials.\n"
            "Please ensure client_secret.json is in the current directory or gcloud ADC is configured."
        )
        sys.exit(1)

    auth_params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "select_account consent",
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(auth_params)}"

    print("=" * 70)
    print("🔑 Authorize Separate Google Drive Account")
    print("=" * 70)
    print("Sign in with the Google Account that HAS Google Drive access enabled.\n")
    print("Opening browser for authorization...")
    print(f"URL: {auth_url}\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    server = HTTPServer(("localhost", REDIRECT_PORT), OAuthCallbackHandler)
    while not OAuthCallbackHandler.auth_code:
        server.handle_request()

    code = OAuthCallbackHandler.auth_code
    print("Received authorization code. Exchanging for tokens...")

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post(token_url, data=payload, timeout=15)
    if not resp.ok:
        print(f"❌ Token exchange failed: {resp.status_code} - {resp.text}")
        sys.exit(1)

    token_data = resp.json()
    token_data["client_id"] = client_id
    token_data["client_secret"] = client_secret
    token_data["token_uri"] = token_url

    token_file = ".drive_user_token.json"
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    print(f"✅ Successfully saved Drive credentials to {token_file}!")
    print("\nYou can now run live Drive tests:")
    print("    DRIVE_CLIENT_MODE=drive .venv/bin/python scripts/run_phase3.py\n")


if __name__ == "__main__":
    main()
