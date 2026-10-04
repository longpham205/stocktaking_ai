"""Give an account a new password, from the server's command line (for a forgotten password, the
admin's included, and to create the first accounts). Whoever can run this on the server is an admin.

    python -m entrypoints.reset_password admin                       # new random password
    python -m entrypoints.reset_password admin --create --role admin # create the account if missing
    python -m entrypoints.reset_password staff --prompt              # type the password (not echoed)
    python -m entrypoints.reset_password staff --unlock              # also reactivate a locked account
    python -m entrypoints.reset_password --advanced                  # the advanced password (engine settings)

The password is printed once and stored nowhere else. Every open shift of the account is closed:
devices that were logged in must log in again.
"""

import argparse
import asyncio
import getpass
import secrets
import sys

from app.core.config import get_settings
from app.core.db import make_engine
from app.modules.auth.passwords import hash_password
from app.modules.auth.repository import AuthRepository
from app.modules.pos_settings.repository import SettingsRepository
from app.modules.pos_settings.service import ADVANCED_PASSWORD_KEY

MIN_LENGTH = 8


async def reset_password(
    repo: AuthRepository, username: str, password: str, *, create_role: str | None = None, unlock: bool = False
) -> str:
    """Set the password; returns what happened ("created" or "reset, N shift(s) closed")."""
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Mật khẩu tối thiểu {MIN_LENGTH} ký tự")
    username = username.strip().lower()
    user = await repo.user_by_username(username)
    if user is None:
        if create_role is None:
            raise LookupError(f"Không có tài khoản '{username}' (thêm --create --role staff|admin để tạo)")
        await repo.create_user(username, hash_password(password), create_role)
        return "created"
    closed = await repo.set_password(user.id, hash_password(password), unlock=unlock)
    return f"reset, {closed} shift(s) closed"


async def reset_advanced_password(repo: SettingsRepository, password: str) -> str:
    """Replace the advanced password, which guards the engine settings (not a login)."""
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Mật khẩu tối thiểu {MIN_LENGTH} ký tự")
    await repo.put_secret(ADVANCED_PASSWORD_KEY, hash_password(password))
    return "advanced password set"


async def _run(args: argparse.Namespace, password: str) -> str:
    engine = make_engine(get_settings().database_url)
    try:
        if args.advanced:
            return await reset_advanced_password(SettingsRepository(engine), password)
        return await reset_password(
            AuthRepository(engine),
            args.username,
            password,
            create_role=args.role if args.create else None,
            unlock=args.unlock,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Đặt lại mật khẩu một tài khoản web POS.")
    parser.add_argument("username", nargs="?", help="tài khoản (bỏ trống khi dùng --advanced)")
    parser.add_argument("--advanced", action="store_true", help="đặt mật khẩu NÂNG CAO thay vì mật khẩu tài khoản")
    parser.add_argument("--create", action="store_true", help="tạo tài khoản nếu chưa có")
    parser.add_argument("--role", choices=["staff", "admin"], default="staff", help="vai trò khi tạo mới")
    parser.add_argument("--prompt", action="store_true", help="tự gõ mật khẩu thay vì sinh ngẫu nhiên")
    parser.add_argument("--unlock", action="store_true", help="mở lại tài khoản đang bị khoá")
    args = parser.parse_args(argv)
    if bool(args.username) == args.advanced:
        parser.error("cần tên tài khoản, hoặc --advanced (không cả hai)")

    password = getpass.getpass("Mật khẩu mới: ") if args.prompt else secrets.token_urlsafe(12)
    try:
        outcome = asyncio.run(_run(args, password))
    except (LookupError, ValueError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    print(f"{args.username or 'advanced'}: {outcome}")
    if not args.prompt:
        print(f"Mật khẩu mới (chỉ hiện một lần): {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
