"""OIDC data models and types."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List


@dataclass
class TokenInfo:
    """Information about an access token."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    scope: str | None = None
    issued_at: datetime | None = None

    @property
    def is_expired(self) -> bool:
        """Check if the token is expired."""
        if not self.issued_at or not self.expires_in:
            return False
        expiry = self.issued_at + timedelta(seconds=self.expires_in)
        return datetime.now() >= expiry

    @property
    def scopes(self) -> List[str]:
        """Get scopes as a list."""
        if not self.scope:
            return []
        return self.scope.split()


@dataclass
class JWTPayload:
    """JWT token payload data."""

    sub: str | None = None
    client_id: str | None = None
    scope: str | None = None
    aud: List[str] | None = None
    iss: str | None = None
    exp: int | None = None
    iat: int | None = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JWTPayload":
        """Create from dictionary."""
        return cls(
            sub=data.get("sub"),
            client_id=data.get("client_id"),
            scope=data.get("scope"),
            aud=data.get("aud"),
            iss=data.get("iss"),
            exp=data.get("exp"),
            iat=data.get("iat"),
        )

    @property
    def scopes(self) -> List[str]:
        """Get scopes as a list."""
        if not self.scope:
            return []
        if isinstance(self.scope, str):
            return self.scope.split()
        return self.scope if isinstance(self.scope, list) else []

    @property
    def username(self) -> str | None:
        """Get the username from client_id or sub."""
        return self.client_id or self.sub
