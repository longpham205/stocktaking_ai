"""Login tokens: JWT (HS256) carrying the account, its shift and its role. The token alone does
not authenticate a request: the shift it names must still be open (see `AuthService.authenticate`)."""

import time
from dataclasses import dataclass

import jwt


class TokenError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code  # AUTH_INVALID | AUTH_EXPIRED


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    shift_id: int
    role: str


def make_token(secret: str, claims: TokenClaims, ttl_seconds: int, now: float | None = None) -> str:
    if not secret:
        raise ValueError("JWT_SECRET is not set: login tokens cannot be signed")
    issued = int(time.time() if now is None else now)
    payload = {"uid": claims.user_id, "sid": claims.shift_id, "role": claims.role, "exp": issued + ttl_seconds}
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(secret: str, token: str) -> TokenClaims:
    if not secret:
        raise ValueError("JWT_SECRET is not set: login tokens cannot be verified")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "uid", "sid", "role"]})
        return TokenClaims(user_id=int(payload["uid"]), shift_id=int(payload["sid"]), role=str(payload["role"]))
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("AUTH_EXPIRED") from exc
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise TokenError("AUTH_INVALID") from exc
