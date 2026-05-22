"""OIDC authentication backend for incoming requests."""

import json
from typing import List
from urllib.request import urlopen

import jwt
import structlog
from jwt import PyJWKClient
from starlette.authentication import AuthCredentials, AuthenticationError, SimpleUser
from starlette.requests import HTTPConnection, Request

from agent_leasing.api.auth.oidc.models import JWTPayload
from agent_leasing.settings import settings

logger = structlog.getLogger()


class OIDCAuthenticationBackend:
    """OIDC JWT Bearer token authentication backend."""

    def __init__(self):
        """Initialize the OIDC authentication backend."""
        self._jwks_client = self._initialize_jwks_client()

    def _initialize_jwks_client(self) -> PyJWKClient:
        """Initialize the JWKS client for token validation."""
        oidc_discover_doc = f"{settings.unified_login_authority}/.well-known/openid-configuration"
        logger.info(f"Retrieving JWKS URI from {oidc_discover_doc}")

        try:
            with urlopen(oidc_discover_doc) as response:
                config_data = json.loads(response.read())

            jwks_uri = config_data["jwks_uri"]
            logger.info(f"Found JWKS URI: {jwks_uri}")

            return PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)

        except (KeyError, OSError, ValueError) as exc:
            logger.error(f"Failed to initialize JWKS client: {exc}", exc_info=True)
            raise RuntimeError(f"Failed to initialize JWKS client from {oidc_discover_doc}") from exc

    def _validate_token(self, token: str) -> JWTPayload:
        """Validate JWT token and return payload."""
        try:
            unverified_header = jwt.get_unverified_header(token)
            alg = unverified_header.get("alg")
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

            payload_dict = jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg] if alg else None,
                audience=settings.unified_login_audiences,
                issuer=settings.unified_login_authority,
                options={"verify_exp": True, "verify_aud": True, "verify_iss": True},
            )

            return JWTPayload.from_dict(payload_dict)

        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            raise AuthenticationError(f"Invalid token: {e}") from e

    def _validate_scopes(self, token_scopes: List[str]) -> None:
        """Validate that token contains required scopes."""
        if not settings.required_scopes:
            return

        required_set = set(settings.unified_login_scopes)
        token_set = set(token_scopes)

        if settings.require_all_scopes:
            if not required_set.issubset(token_set):
                logger.warning(f"Token missing required scopes. Required: {required_set}, Token: {token_set}")
                raise AuthenticationError("Insufficient scopes")
        else:
            if not required_set.intersection(token_set):
                logger.warning(f"Token has none of the required scopes. Required: {required_set}, Token: {token_set}")
                raise AuthenticationError("Insufficient scopes")

    async def authenticate(self, conn: HTTPConnection):
        """Authenticate the request using Bearer token."""
        request = Request(conn.scope)
        auth_header = request.headers.get("authorization")

        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise AuthenticationError("Missing or invalid Authorization header")

        token = auth_header.split(" ", 1)[1]
        payload = self._validate_token(token)

        # Validate scopes
        self._validate_scopes(payload.scopes)

        return AuthCredentials(scopes=payload.scopes), SimpleUser(username=payload.username)
