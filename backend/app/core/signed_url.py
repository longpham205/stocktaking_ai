"""HMAC-signed, expiring media URLs. An `<img>` cannot send a Bearer token, so capture and gallery
images are served to whoever holds a link the API signed. Same scheme as the legacy web: the
signature covers `<relative path>|<expiry>`."""

import base64
import hashlib
import hmac
import time


def sign(secret: str, rel_path: str, expires: int) -> str:
    if not secret:
        raise ValueError("MEDIA_URL_SECRET is not set: media URLs cannot be signed")
    digest = hmac.new(secret.encode(), f"{rel_path}|{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def verify(secret: str, rel_path: str, expires: int, signature: str, now: float | None = None) -> bool:
    if expires < (time.time() if now is None else now):
        return False
    # bytes: compare_digest raises on a non-ASCII str, and the signature comes from the query string
    return hmac.compare_digest(signature.encode(), sign(secret, rel_path, expires).encode())


def signed_query(secret: str, rel_path: str, ttl_seconds: int, now: float | None = None) -> str:
    """The `exp=...&sig=...` query string for a media path, valid for `ttl_seconds`."""
    expires = int((time.time() if now is None else now) + ttl_seconds)
    return f"exp={expires}&sig={sign(secret, rel_path, expires)}"
