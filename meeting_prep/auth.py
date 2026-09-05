"""SPIFFE and Agent Identity authentication provider for Meeting Prep Copilot.

Source of truth: docs/hld.md §12A
- Agent identity based on SPIFFE, never hard-coded application credentials.
- Resolves credentials via SPIFFE Workload API, SPIFFE JWT SVID, or Workload Identity Federation (WIF).
- Supports user-delegated credentials via ToolContext (Agent Identity Auth Manager).
"""

import json
import logging
import os
from typing import Any, Optional
import requests
from requests import Session

logger = logging.getLogger(__name__)


class SpiffeCredentialsSession(Session):
    """Requests Session authenticated via a SPIFFE-derived or delegated bearer token."""

    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.headers.update({"Authorization": f"Bearer {token}"})


def _exchange_spiffe_jwt_with_sts(
    spiffe_token: str,
    project_number: str,
    pool_id: str,
    provider_id: str,
    scopes: Optional[list[str]] = None,
) -> Optional[str]:
    """Exchange a SPIFFE JWT token for a Google Cloud access token via STS (RFC 8693)."""
    sts_url = "https://sts.googleapis.com/v1/token"
    audience = f"//iam.googleapis.com/projects/{project_number}/locations/global/workloadIdentityPools/{pool_id}/providers/{provider_id}"
    scope_str = " ".join(scopes or ["https://www.googleapis.com/auth/drive.file"])

    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "audience": audience,
        "scope": scope_str,
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "subject_token": spiffe_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
    }

    try:
        resp = requests.post(sts_url, data=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("access_token")
    except Exception as e:
        logger.error("SPIFFE STS token exchange failed: %s", e)
        return None


def get_drive_session(tool_context: Optional[Any] = None) -> Any:
    """Resolve an authenticated session for Google Drive based on SPIFFE or delegated identity.

    Precedence:
    1. Delegated user credential from Agent Identity Auth Manager / ToolContext.
    2. SPIFFE JWT / SVID token via environment (SPIFFE_TOKEN / SPIFFE_SVID_PATH) exchanged via STS.
    3. Workload Identity Federation (WIF) / external account configuration via GOOGLE_APPLICATION_CREDENTIALS.
    4. Fallback: Google Cloud Application Default Credentials (for local testing when SPIFFE daemon is simulated).
    """
    # 1. Delegated credential from ToolContext (Agent Identity Auth Manager)
    if tool_context:
        # Check tool_context credential methods
        if hasattr(tool_context, "get_credential"):
            try:
                for key in ("google_drive_oauth", "google_drive", "drive"):
                    cred = tool_context.get_credential(key)
                    if not cred:
                        continue
                    if hasattr(cred, "http") and cred.http and hasattr(cred.http, "credentials"):
                        token = getattr(cred.http.credentials, "token", None)
                        if token:
                            logger.info("Using delegated user credential from Agent Identity Auth Manager (HTTP scheme)")
                            return SpiffeCredentialsSession(token)
                    if hasattr(cred, "oauth2") and cred.oauth2:
                        token = getattr(cred.oauth2, "access_token", None)
                        if token:
                            logger.info("Using delegated user credential from Agent Identity Auth Manager (OAuth2 scheme)")
                            return SpiffeCredentialsSession(token)
                    if hasattr(cred, "token") and cred.token:
                        logger.info("Using delegated user credential from Agent Identity Auth Manager")
                        return SpiffeCredentialsSession(cred.token)
            except Exception as err:
                logger.debug("tool_context.get_credential check: %s", err)

        # Check session state for auth token
        if hasattr(tool_context, "state") and tool_context.state:
            token = tool_context.state.get("delegated_drive_token")
            if token:
                logger.info("Using delegated drive token from session state")
                return SpiffeCredentialsSession(token)

    # 2. Explicit Delegated User Token or SPIFFE Token via environment
    drive_token = os.getenv("DRIVE_USER_TOKEN") or os.getenv("SPIFFE_TOKEN")
    if not drive_token and os.getenv("SPIFFE_SVID_PATH"):
        svid_path = os.getenv("SPIFFE_SVID_PATH")
        if os.path.isfile(svid_path):
            with open(svid_path, "r", encoding="utf-8") as f:
                drive_token = f.read().strip()

    if drive_token:
        project_number = os.getenv("GCP_PROJECT_NUMBER")
        wif_pool = os.getenv("SPIFFE_WIF_POOL", "agent-identity-pool")
        wif_provider = os.getenv("SPIFFE_WIF_PROVIDER", "spiffe-jwt-provider")
        if project_number and drive_token.startswith("ey"):
            access_token = _exchange_spiffe_jwt_with_sts(
                spiffe_token=drive_token,
                project_number=project_number,
                pool_id=wif_pool,
                provider_id=wif_provider,
            )
            if access_token:
                logger.info("Successfully exchanged SPIFFE JWT for Google Drive access token")
                return SpiffeCredentialsSession(access_token)
        logger.info("Using delegated user token as bearer token for Google Drive")
        return SpiffeCredentialsSession(drive_token)

    # 3. Local Delegated User Token File (.drive_user_token.json or DRIVE_CREDENTIALS_FILE)
    token_file = os.getenv("DRIVE_CREDENTIALS_FILE", ".drive_user_token.json")
    if os.path.isfile(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                token_data = json.load(f)
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import AuthorizedSession

            creds = Credentials(
                token=token_data.get("access_token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
                scopes=["https://www.googleapis.com/auth/drive.file"],
            )
            logger.info("Using delegated user credentials from %s", token_file)
            return AuthorizedSession(creds)
        except Exception as err:
            logger.warning("Failed to load drive credentials from %s: %s", token_file, err)

    # 3. Workload Identity Federation / External Account Credentials
    gac_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if gac_path and os.path.isfile(gac_path):
        try:
            with open(gac_path, "r", encoding="utf-8") as f:
                cred_info = json.load(f)
            if cred_info.get("type") == "external_account":
                logger.info("Loading SPIFFE Workload Identity Federation credentials from %s", gac_path)
                from google.auth import load_credentials_from_dict
                from google.auth.transport.requests import AuthorizedSession

                creds, _ = load_credentials_from_dict(
                    cred_info,
                    scopes=["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"],
                )
                return AuthorizedSession(creds)
        except Exception as e:
            logger.warning("Failed to load external account credential from %s: %s", gac_path, e)

    # 4. Standard AuthorizedSession fallback
    try:
        from google.auth import default
        from google.auth.transport.requests import AuthorizedSession

        try:
            creds, _ = default(scopes=["https://www.googleapis.com/auth/drive.file"])
        except Exception:
            creds, _ = default(scopes=["https://www.googleapis.com/auth/drive"])
        return AuthorizedSession(creds)
    except Exception as e:
        logger.error("Unable to resolve credentials for Google Drive: %s", e)
        raise RuntimeError(
            f"No valid SPIFFE or delegated credentials found for Google Drive API. "
            f"Set SPIFFE_TOKEN, configure WIF credentials, or run in stub mode (DRIVE_CLIENT_MODE=stub). Error: {e}"
        ) from e
