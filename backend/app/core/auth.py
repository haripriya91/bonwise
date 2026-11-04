from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.core.config import get_settings


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID


@lru_cache
def signing_keys():
    return jwt.PyJWKClient(
        get_settings().supabase_jwks_url,
        cache_jwk_set=True,
        lifespan=300,
        timeout=10,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    unauthorised = HTTPException(
        status_code=401,
        detail="A valid user access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorised

    settings = get_settings()
    token = credentials.credentials

    try:
        algorithm = jwt.get_unverified_header(token).get("alg")

        if algorithm not in {"ES256", "RS256"}:
            raise unauthorised

        key = signing_keys().get_signing_key_from_jwt(token)

        claims = jwt.decode(
            token,
            key.key,
            algorithms=["ES256", "RS256"],
            issuer=settings.supabase_jwt_issuer,
            audience=settings.supabase_jwt_audience,
            options={
                "require": ["exp", "iat", "sub", "iss", "aud"],
            },
        )

        if claims.get("role") != "authenticated":
            raise unauthorised

        return CurrentUser(id=UUID(claims["sub"]))

    except jwt.PyJWKClientConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Authentication service temporarily unavailable.",
        ) from None

    except (jwt.PyJWTError, ValueError, TypeError):
        raise unauthorised from None