"""Login, shifts and the per-request session check. Ported from the legacy suite
(src_legacy/tests/test_backend_api.py), plus the pieces that are new here (JWT, entrypoint)."""

import pytest
from fastapi import Depends, FastAPI

from app.modules.auth.deps import require_admin
from app.modules.auth.limiter import LoginLimiter
from app.modules.auth.passwords import hash_password, verify_password
from app.modules.auth.ports import CurrentUser
from app.modules.auth.tokens import TokenClaims, TokenError, make_token, verify_token
from entrypoints.reset_password import reset_password
from tests.api.conftest import ADMIN_PASSWORD, STAFF_PASSWORD, http, login, seed_accounts, start_app, token_for

SECRET = "unit-test-secret-0123456789abcdef-0123456789"

# ---------------------------------------------------------------- over HTTP


async def test_health_is_public_and_everything_else_needs_a_token(db_app: FastAPI) -> None:
    async with http(db_app) as anonymous:
        assert (await anonymous.get("/api/health")).json()["status"] == "ready"
        response = await anonymous.get("/api/me")
    assert response.status_code == 401 and response.json()["code"] == "AUTH_INVALID"
    async with http(db_app, token="rac.rac") as garbage:
        response = await garbage.get("/api/me")
    assert response.status_code == 401 and response.json()["code"] == "AUTH_INVALID"


async def test_login_returns_token_and_user_and_me_returns_the_shift(db_app: FastAPI) -> None:
    response = await login(db_app, "  STAFF ", STAFF_PASSWORD)  # trimmed, case-insensitive
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "id": body["user"]["id"],
        "username": "staff",
        "full_name": "Thu ngân demo",
        "role": "staff",
        "has_seen_onboarding": False,
    }
    async with http(db_app, body["token"]) as client:
        me = (await client.get("/api/me")).json()
    assert me["user"] == body["user"]
    assert me["shift"]["total_collected"] == 0 and me["shift"]["started_at"].endswith("+00:00")


async def test_wrong_password_and_unknown_account_look_the_same_then_lockout(db_app: FastAPI) -> None:
    wrong, unknown = await login(db_app, "staff", "sai-mat-khau"), await login(db_app, "khong-ton-tai", "x")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # does not reveal whether the account exists
    await login(db_app, "staff", "sai")
    await login(db_app, "staff", "sai")
    locked = await login(db_app, "staff", STAFF_PASSWORD)  # locked even with the right password
    assert locked.status_code == 429
    assert locked.json()["code"] == "RATE_LIMITED" and locked.json()["retry_after"] > 0
    # another account from the same address is not affected
    assert (await login(db_app, "admin", ADMIN_PASSWORD)).status_code == 200


async def test_a_second_login_ends_the_first_session(db_app: FastAPI) -> None:
    first = await token_for(db_app, "staff", STAFF_PASSWORD)
    async with http(db_app, first) as client:
        assert (await client.get("/api/me")).status_code == 200
    second = await token_for(db_app, "staff", STAFF_PASSWORD)
    async with http(db_app, first) as client:
        kicked = await client.get("/api/me")
    assert kicked.status_code == 401 and kicked.json()["code"] == "SESSION_INVALID"
    async with http(db_app, second) as client:
        assert (await client.get("/api/me")).status_code == 200


async def test_logout_is_idempotent_and_ends_the_session(db_app: FastAPI) -> None:
    token = await token_for(db_app, "staff", STAFF_PASSWORD)
    async with http(db_app, token) as client:
        assert (await client.post("/api/auth/logout")).json() == {"ok": True}
        assert (await client.post("/api/auth/logout")).status_code == 200  # again: still fine
        after = await client.get("/api/me")
    async with http(db_app) as anonymous:
        assert (await anonymous.post("/api/auth/logout")).status_code == 200  # no token: still fine
    assert after.status_code == 401 and after.json()["code"] == "SESSION_INVALID"


async def test_an_expired_token_is_reported_as_expired(database_url: str) -> None:
    app = await start_app(database_url, token_ttl_minutes=-1)
    async with app.router.lifespan_context(app):
        await seed_accounts(app)
        token = await token_for(app, "staff", STAFF_PASSWORD)
        async with http(app, token) as client:
            response = await client.get("/api/me")
    assert response.status_code == 401 and response.json()["code"] == "AUTH_EXPIRED"


async def test_login_body_validation_uses_the_common_error_shape(db_app: FastAPI) -> None:
    async with http(db_app) as client:
        empty = await client.post("/api/auth/login", json={"username": "", "password": ""})
        malformed = await client.post("/api/auth/login", json={"username": {"a": 1}, "password": "x"})
    assert empty.status_code == 422 and empty.json()["code"] == "VALIDATION_ERROR"
    assert malformed.status_code == 422 and malformed.json()["code"] == "VALIDATION_ERROR"
    assert malformed.json()["errors"][0]["loc"] == ["body", "username"]


async def test_a_deactivated_account_cannot_log_in_and_loses_its_session(db_app: FastAPI) -> None:
    token = await token_for(db_app, "staff", STAFF_PASSWORD)
    engine = db_app.state.backends.engine
    async with engine.begin() as conn:
        await conn.exec_driver_sql("UPDATE users SET is_active = false WHERE username = 'staff'")
    async with http(db_app, token) as client:
        assert (await client.get("/api/me")).json()["code"] == "AUTH_INVALID"
    refused = await login(db_app, "staff", STAFF_PASSWORD)
    assert refused.status_code == 401 and refused.json()["detail"] == "Sai tài khoản hoặc mật khẩu"


async def test_onboarding_is_remembered_per_account(db_app: FastAPI) -> None:
    token = await token_for(db_app, "staff", STAFF_PASSWORD)
    async with http(db_app, token) as client:
        assert (await client.post("/api/me/onboarding-seen")).json() == {"ok": True}
        assert (await client.get("/api/me")).json()["user"]["has_seen_onboarding"] is True
    again = await login(db_app, "staff", STAFF_PASSWORD)
    assert again.json()["user"]["has_seen_onboarding"] is True
    assert (await login(db_app, "admin", ADMIN_PASSWORD)).json()["user"]["has_seen_onboarding"] is False


async def test_require_admin_lets_admins_through_and_forbids_staff(db_app: FastAPI) -> None:
    @db_app.get("/api/_admin_only")
    async def admin_only(admin: CurrentUser = Depends(require_admin)) -> dict[str, str]:
        return {"username": admin.username}

    async with http(db_app, await token_for(db_app, "staff", STAFF_PASSWORD)) as staff:
        forbidden = await staff.get("/api/_admin_only")
    async with http(db_app, await token_for(db_app, "admin", ADMIN_PASSWORD)) as admin:
        allowed = await admin.get("/api/_admin_only")
    async with http(db_app) as anonymous:
        unauthenticated = await anonymous.get("/api/_admin_only")
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN"
    assert allowed.status_code == 200 and allowed.json() == {"username": "admin"}
    assert unauthenticated.status_code == 401


async def test_the_api_refuses_to_start_without_a_jwt_secret(database_url: str) -> None:
    app = await start_app(database_url, jwt_secret="")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        async with app.router.lifespan_context(app):
            pass


# ---------------------------------------------------------------- the reset-password entrypoint


async def test_reset_password_creates_resets_and_logs_devices_out(db_app: FastAPI) -> None:
    repo = db_app.state.backends.auth.repo
    old_token = await token_for(db_app, "staff", STAFF_PASSWORD)

    assert await reset_password(repo, "staff", "mat-khau-moi-1") == "reset, 1 shift(s) closed"
    async with http(db_app, old_token) as client:
        assert (await client.get("/api/me")).json()["code"] == "SESSION_INVALID"
    assert (await login(db_app, "staff", STAFF_PASSWORD)).status_code == 401
    assert (await login(db_app, "staff", "mat-khau-moi-1")).status_code == 200

    assert await reset_password(repo, "Thu-Ngan-2", "mat-khau-moi-2", create_role="staff") == "created"
    assert (await login(db_app, "thu-ngan-2", "mat-khau-moi-2")).json()["user"]["role"] == "staff"

    with pytest.raises(LookupError, match="khong-co"):
        await reset_password(repo, "khong-co", "mat-khau-moi-3")
    with pytest.raises(ValueError, match="tối thiểu"):
        await reset_password(repo, "staff", "ngan")


async def test_reset_password_can_unlock_a_deactivated_account(db_app: FastAPI) -> None:
    repo = db_app.state.backends.auth.repo
    async with db_app.state.backends.engine.begin() as conn:
        await conn.exec_driver_sql("UPDATE users SET is_active = false WHERE username = 'staff'")
    await reset_password(repo, "staff", "mat-khau-moi-1")
    assert (await login(db_app, "staff", "mat-khau-moi-1")).status_code == 401  # still locked
    await reset_password(repo, "staff", "mat-khau-moi-2", unlock=True)
    assert (await login(db_app, "staff", "mat-khau-moi-2")).status_code == 200


# ---------------------------------------------------------------- units (no database)


def test_passwords_use_the_legacy_scrypt_format() -> None:
    stored = hash_password("bí-mật-123")
    scheme, salt, digest = stored.split("$")
    assert (scheme, len(salt), len(digest)) == ("scrypt", 32, 64)
    assert verify_password("bí-mật-123", stored) and not verify_password("bí-mật-124", stored)
    assert hash_password("bí-mật-123") != stored  # salted
    for broken in ("", "plain", "bcrypt$aa$bb", "scrypt$zz$zz"):
        assert verify_password("x", broken) is False
    # a hash written by the legacy web (hashlib.scrypt n=2**14, r=8, p=1, dklen=32)
    legacy = "scrypt$000102030405060708090a0b0c0d0e0f$f1f4d1d9a3b9e3c0e9d2c1d5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5"
    assert verify_password("anything", legacy) is False  # well-formed, wrong password: no exception


def test_tokens_round_trip_expire_and_reject_tampering() -> None:
    claims = TokenClaims(user_id=7, shift_id=42, role="admin")
    token = make_token(SECRET, claims, ttl_seconds=60)
    assert verify_token(SECRET, token) == claims
    with pytest.raises(TokenError) as wrong_secret:
        verify_token(SECRET[::-1], token)
    assert wrong_secret.value.code == "AUTH_INVALID"
    with pytest.raises(TokenError) as expired:
        verify_token(SECRET, make_token(SECRET, claims, ttl_seconds=-1))
    assert expired.value.code == "AUTH_EXPIRED"
    for garbage in ("", "a.b", "a.b.c", token[:-2] + "xx"):
        with pytest.raises(TokenError):
            verify_token(SECRET, garbage)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        make_token("", claims, ttl_seconds=60)


def test_limiter_locks_after_max_failures_and_forgets_after_the_lockout() -> None:
    now = [1_000.0]
    limiter = LoginLimiter(max_failed=3, lockout_seconds=300, clock=lambda: now[0])
    key = ("staff", "10.0.0.5")
    for _ in range(2):
        limiter.fail(key)
    assert limiter.retry_after(key) == 0
    limiter.fail(key)
    assert limiter.retry_after(key) == 301
    assert limiter.retry_after(("staff", "10.0.0.6")) == 0  # another address
    now[0] += 200
    assert limiter.retry_after(key) == 101
    now[0] += 101
    assert limiter.retry_after(key) == 0  # the lockout has passed
    limiter.fail(key)
    limiter.reset(key)  # a successful login clears the count
    assert limiter.retry_after(key) == 0
