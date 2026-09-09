"""Google sign-in via Supabase. One shared workspace — the email is a marker,
not a permission. Everyone who gets in sees exactly the same data."""
import os
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException

from app import config  # noqa: F401 — imported for its side effect: loads .env

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
# Set to "0" only for local development without a Supabase project.
REQUIRE_AUTH = os.environ.get("PV_REQUIRE_AUTH", "1") != "0"


@lru_cache(maxsize=1)
def _jwks():
    if not SUPABASE_URL:
        return None
    return jwt.PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")


def _decode(token: str) -> dict:
    """Asymmetric keys first (current Supabase), shared secret as the fallback."""
    last = None
    client = _jwks()
    if client is not None:
        try:
            key = client.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["ES256", "RS256"],
                              audience="authenticated")
        except Exception as exc:  # noqa: BLE001 — fall through to the secret
            last = exc
    if JWT_SECRET:
        try:
            return jwt.decode(token, JWT_SECRET, algorithms=["HS256"],
                              audience="authenticated")
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise HTTPException(401, f"Could not verify that sign-in ({last}).")


def actor(authorization: str = Header(default="")) -> str:
    """The email behind this request. Used only to stamp who did the work."""
    if not REQUIRE_AUTH:
        return os.environ.get("PV_LOCAL_ACTOR", "local@parentveda.in")

    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in with Google to continue.")

    claims = _decode(authorization.split(" ", 1)[1].strip())
    email = (claims.get("email")
             or (claims.get("user_metadata") or {}).get("email"))
    if not email:
        raise HTTPException(401, "That sign-in carried no email address.")
    return email.lower()


def public_config() -> dict:
    """What the browser needs to start a Google sign-in."""
    return {
        "supabase_url": SUPABASE_URL,
        "anon_key": os.environ.get("SUPABASE_ANON_KEY", ""),
        "require_auth": REQUIRE_AUTH,
    }
