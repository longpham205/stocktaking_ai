"""Băm mật khẩu (scrypt), token có chữ ký HMAC, URL ảnh ký, khoá tạm khi đăng nhập sai. Chỉ thư viện chuẩn."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time


class TokenError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # AUTH_INVALID | AUTH_EXPIRED


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def make_token(secret: str, payload: dict, ttl_seconds: int) -> str:
    body = dict(payload, exp=int(time.time()) + ttl_seconds)
    raw = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify_token(secret: str, token: str) -> dict:
    try:
        raw, sig = token.split(".")
        expect = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            raise TokenError("AUTH_INVALID")
        body = json.loads(_unb64(raw))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise TokenError("AUTH_INVALID") from exc
    if int(body.get("exp", 0)) < time.time():
        raise TokenError("AUTH_EXPIRED")
    return body


def sign_media(secret: str, rel_path: str, expires: int) -> str:
    return _b64(hmac.new(secret.encode(), f"{rel_path}|{expires}".encode(), hashlib.sha256).digest())


def verify_media(secret: str, rel_path: str, expires: int, sig: str) -> bool:
    if expires < time.time():
        return False
    return hmac.compare_digest(sig, sign_media(secret, rel_path, expires))


class LoginLimiter:
    """Khoá tạm theo (username, IP) sau N lần sai liên tiếp."""

    def __init__(self, max_failed: int, lockout_seconds: int) -> None:
        self.max_failed, self.lockout = max_failed, lockout_seconds
        self._state: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: tuple[str, str]) -> int:
        """Trả số giây còn bị khoá (0 nếu không khoá)."""
        with self._lock:
            now = time.time()
            fails = [t for t in self._state.get(key, []) if now - t < self.lockout]
            self._state[key] = fails
            if len(fails) >= self.max_failed:
                return int(self.lockout - (now - fails[0])) + 1
            return 0

    def fail(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._state.setdefault(key, []).append(time.time())

    def reset(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._state.pop(key, None)
