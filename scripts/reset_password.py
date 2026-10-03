"""Cấp lại mật khẩu cho một tài khoản web POS (dùng khi quên, kể cả admin). Chạy TRÊN MÁY CHỦ.

Ai chạy được lệnh trên máy chủ (có quyền vào thư mục dữ liệu) coi như có quyền quản trị.
Mật khẩu chỉ in ra màn hình MỘT lần, không ghi vào file. Mọi ca đang mở của tài khoản bị đóng
(thiết bị đang đăng nhập phải đăng nhập lại). Tài khoản bị khoá sẽ được mở lại nếu dùng --unlock.

    python scripts/reset_password.py admin                      # data/db/app.db, sinh mật khẩu ngẫu nhiên
    python scripts/reset_password.py staff --data-dir data_demo
    python scripts/reset_password.py admin --prompt             # tự gõ mật khẩu (không hiện trên màn hình)
    python scripts/reset_password.py --list --data-dir data_demo
    python scripts/reset_password.py --advanced                 # mật khẩu NÂNG CAO (áp dụng thiết lập pipeline)
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db import Database, utcnow  # noqa: E402
from backend.security import hash_password  # noqa: E402

MIN_LEN = 8


def reset_password(db_path: Path, username: str, password: str, unlock: bool = False) -> int:
    """Đặt mật khẩu mới, đóng ca đang mở. Trả số ca đã đóng. Lỗi nếu không có tài khoản."""
    if len(password) < MIN_LEN:
        raise ValueError(f"Mật khẩu tối thiểu {MIN_LEN} ký tự")
    db = Database(db_path)
    with db.tx() as c:
        row = c.execute("SELECT id FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        if not row:
            raise LookupError(f"Không có tài khoản '{username}' trong {db_path}")
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), row["id"]))
        if unlock:
            c.execute("UPDATE users SET is_active=1 WHERE id=?", (row["id"],))
        closed = c.execute("UPDATE shifts SET ended_at=? WHERE user_id=? AND ended_at IS NULL", (utcnow(), row["id"])).rowcount
    return closed


def reset_advanced_password(db_path: Path, password: str) -> None:
    """Đặt mật khẩu NÂNG CAO (admin dùng khi áp dụng thiết lập nâng cao của pipeline trên web)."""
    import json

    if len(password) < MIN_LEN:
        raise ValueError(f"Mật khẩu tối thiểu {MIN_LEN} ký tự")
    with Database(db_path).tx() as c:
        c.execute("INSERT INTO settings(key,value) VALUES('advanced_password_hash',?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(hash_password(password)),))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Cấp lại mật khẩu tài khoản web POS (chạy trên máy chủ)")
    ap.add_argument("username", nargs="?", help="tài khoản cần cấp lại, ví dụ admin hoặc staff")
    ap.add_argument("--data-dir", default="data", help="thư mục dữ liệu web (mặc định data; demo: data_demo)")
    ap.add_argument("--prompt", action="store_true", help="tự gõ mật khẩu mới thay vì sinh ngẫu nhiên")
    ap.add_argument("--unlock", action="store_true", help="mở khoá tài khoản nếu đang bị khoá")
    ap.add_argument("--list", action="store_true", help="liệt kê tài khoản rồi thoát")
    ap.add_argument("--advanced", action="store_true", help="cấp lại mật khẩu NÂNG CAO thay vì mật khẩu tài khoản")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    db_path = (data_dir if data_dir.is_absolute() else ROOT / data_dir) / "db" / "app.db"
    if not db_path.is_file():
        print(f"LỖI: không thấy {db_path}", file=sys.stderr)
        return 2
    if args.list:
        with Database(db_path).read() as c:
            for r in c.execute("SELECT username, role, is_active FROM users ORDER BY id"):
                print(f"  {r['username']:<16} {r['role']:<6} {'hoạt động' if r['is_active'] else 'ĐÃ KHOÁ'}")
        return 0
    if not args.username and not args.advanced:
        ap.error("cần tên tài khoản (hoặc --list / --advanced)")

    if args.prompt:
        pw = getpass.getpass("Mật khẩu mới: ")
        if pw != getpass.getpass("Nhập lại: "):
            print("LỖI: hai lần nhập không khớp", file=sys.stderr)
            return 2
    else:
        pw = secrets.token_urlsafe(9)  # 12 ký tự ngẫu nhiên
    if args.advanced:
        try:
            reset_advanced_password(db_path, pw)
        except ValueError as exc:
            print(f"LỖI: {exc}", file=sys.stderr)
            return 2
        print(f"Đã cấp lại mật khẩu NÂNG CAO ({db_path}).")
        if not args.prompt:
            print(f"Mật khẩu nâng cao mới (chỉ hiện MỘT lần, hãy ghi lại): {pw}")
        return 0
    try:
        closed = reset_password(db_path, args.username, pw, unlock=args.unlock)
    except (ValueError, LookupError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    print(f"Đã cấp lại mật khẩu cho '{args.username}' ({db_path}).")
    if not args.prompt:
        print(f"Mật khẩu mới (chỉ hiện MỘT lần, hãy ghi lại): {pw}")
    if closed:
        print(f"Đã đóng {closed} ca đang mở — thiết bị đang đăng nhập tài khoản này phải đăng nhập lại.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
