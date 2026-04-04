"""Authentication for the API layer.

Uses a shared secret (API_SECRET env var) as a Bearer token.
The SvelteKit dashboard injects this server-side so the secret
never reaches the browser.

If API_SECRET is not set, auth is disabled (development mode).
"""

import os

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_security = HTTPBearer(auto_error=False)


def _get_api_secret() -> str | None:
    """Get the API secret from environment. None = auth disabled."""
    return os.getenv("API_SECRET")


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_security),
) -> str | None:
    """FastAPI dependency that validates the Bearer token.

    - If API_SECRET is not set: auth is disabled (dev mode), returns None.
    - If API_SECRET is set: requires a valid Bearer token, raises 401/403.
    """
    secret = _get_api_secret()

    # Dev mode: no secret configured, skip auth
    if not secret:
        return None

    # Secret is configured but no credentials provided
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate the token
    if credentials.credentials != secret:
        raise HTTPException(
            status_code=403,
            detail="Invalid API secret",
        )

    return credentials.credentials
