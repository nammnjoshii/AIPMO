"""FastAPI JWT auth — T-071. Replaces Supabase entirely.

Usage:
    token = create_token(user_id="alice", role="project_manager")
    payload = decode_token(token)  # raises on expired/tampered

JWT_SECRET_KEY must be set in environment (generate with: openssl rand -hex 32).
Token expiry: 8 hours. Role embedded in JWT claims.
For Phase 1 Streamlit demo: auth is skipped. Wire in during Phase 2 Next.js.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TOKEN_EXPIRY_HOURS = 8
_ALGORITHM = "HS256"


def _get_secret() -> str:
    secret = os.environ.get("JWT_SECRET_KEY", "")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. "
            "Generate one with: openssl rand -hex 32 and add to .env"
        )
    return secret


def create_token(user_id: str, role: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """Create a signed JWT for the given user and role.

    Args:
        user_id: User identifier (never logged).
        role: Role string (embedded in 'role' claim).
        extra_claims: Optional additional claims to include.

    Returns:
        Signed JWT string.

    Raises:
        RuntimeError: if JWT_SECRET_KEY is not set.
        ImportError: if python-jose is not installed.
    """
    try:
        from jose import jwt
    except ImportError:
        raise ImportError("python-jose is not installed. Run: pip install python-jose[cryptography]")

    secret = _get_secret()
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=_TOKEN_EXPIRY_HOURS),
    }
    if extra_claims:
        claims.update(extra_claims)

    token = jwt.encode(claims, secret, algorithm=_ALGORITHM)
    logger.debug("JWT created for user (role=%s)", role)
    # Never log user_id or the token value
    return token


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and verify a JWT. Raises on expiry or tampering.

    Args:
        token: JWT string to verify.

    Returns:
        Decoded claims dict with 'sub', 'role', 'iat', 'exp'.

    Raises:
        RuntimeError: if JWT_SECRET_KEY is not set.
        jose.JWTError: on invalid/expired/tampered token.
        ImportError: if python-jose is not installed.
    """
    try:
        from jose import JWTError, jwt
    except ImportError:
        raise ImportError("python-jose is not installed. Run: pip install python-jose[cryptography]")

    secret = _get_secret()
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
        return payload
    except JWTError as exc:
        # Never log the token value
        logger.warning("JWT validation failed: %s", type(exc).__name__)
        raise


def get_role_from_token(token: str) -> str:
    """Extract the role claim from a verified JWT.

    Returns:
        Role string or 'unknown' on failure.
    """
    try:
        payload = decode_token(token)
        return payload.get("role", "unknown")
    except Exception:
        return "unknown"
