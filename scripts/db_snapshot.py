"""Sao lưu / khôi phục / làm sạch DB của web POS (chỉ dùng thư viện chuẩn).

    python scripts/db_snapshot.py save <tên> [--data-dir data_demo]
    python scripts/db_snapshot.py list [--data-dir data_demo]
    python scripts/db_snapshot.py restore <tên> [--data-dir data_demo] [--port 8000]
    python scripts/db_snapshot.py purge-orders [--data-dir data_demo] [--port 8000]

- `save`: dùng SQLite backup API (an toàn cả khi server đang chạy, không bỏ sót file -wal). Không ghi đè bản
  cùng tên trừ khi thêm `--overwrite`.
- `restore`: TỪ CHỐI khi server còn chạy (kiểm tra cổng); xoá app.db-wal/-shm cũ rồi đặt file mới. Sau khi
  khôi phục mọi phiên đăng nhập cũ vô hiệu (ca làm việc bị thay), người dùng phải đăng nhập lại.
- `purge-orders`: xoá đơn hàng, dòng hàng, lượt chụp, ca làm việc và ảnh giao dịch; GIỮ tài khoản, giá, barcode
  đã chỉnh, cài đặt và nhật ký thay đổi. Dùng trước `save demo_clean` khi đã có đơn thử.
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


class SnapshotError(Exception):
    pass


def layout(data_dir: str | Path) -> tuple[Path, Path, Path]:
    base = Path(data_dir)
    if not base.is_absolute():
        base = ROOT / base
    return base / "db" / "app.db", base / "backups", base / "transactions"


_IN_USE = {errno.EADDRINUSE, 10048}  # 10048 = WSAEADDRINUSE (Windows). KHÔNG gồm 10013/WSAEACCES: cổng nằm trong dải bị Windows
# (Hyper-V/WSL) giữ chỗ cũng báo lỗi đó dù không có tiến trình nào nghe, sẽ gây dương tính giả.


def server_running(port: int) -> bool:
    """True nếu có tiến trình đang giữ/lắng nghe cổng `port` trên máy này.

    Dùng `bind` làm phép thử chính vì nó KHÔNG phụ thuộc vào hàng đợi accept của server (một listener bận
    không accept vẫn bị phát hiện) và không phải chờ: thử *kết nối* không đáng tin ở hai đầu — Windows từ chối
    kết nối tới cổng đóng rất chậm (thử lại SYN, hay vượt 0,5s) nên "hết thời gian chờ" KHÔNG có nghĩa là có server.

    - bind báo "địa chỉ đang dùng" => đang chạy. Trên POSIX đặt SO_REUSEADDR để TIME_WAIT sau khi vừa dừng server
      không bị coi nhầm; trên Windows KHÔNG đặt (nếu đặt sẽ chiếm được cả cổng đang dùng).
    - Windows: bind thành công => chắc chắn không có ai giữ cổng => False ngay.
    - POSIX (một số hệ như macOS cho phép chia sẻ với SO_REUSEADDR): bind thành công thì thử thêm một lần kết nối
      ngắn; kết nối được => đang chạy, còn lại (từ chối/hết giờ) => không.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        bound = True
    except OSError as exc:
        if exc.errno in _IN_USE:
            return True
        bound = False  # lỗi khác (ví dụ không đủ quyền với cổng thấp trên Linux): chưa kết luận
    finally:
        probe.close()
    if bound and os.name == "nt":
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:  # từ chối hoặc hết giờ: không coi là đang chạy
        return False


def _need_stopped(port: int) -> None:
    if server_running(port):
        raise SnapshotError(f"Server còn chạy ở cổng {port}. Hãy dừng server (Ctrl+C) rồi chạy lại lệnh này.")


def _check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise SnapshotError("Tên chỉ gồm chữ, số, '_' hoặc '-' (tối đa 40 ký tự).")
    return name


def save(data_dir, name: str, overwrite: bool = False) -> Path:
    db, backups, _ = layout(data_dir)
    _check_name(name)
    if not db.is_file():
        raise SnapshotError(f"Không thấy DB: {db}")
    dest = backups / f"{name}.db"
    if dest.exists() and not overwrite:
        raise SnapshotError(f"Đã có bản '{name}'. Thêm --overwrite để ghi đè.")
    backups.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    src, dst = sqlite3.connect(str(db)), sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, dest)
    return dest


def list_snapshots(data_dir) -> list[tuple[str, int, str]]:
    _, backups, _ = layout(data_dir)
    rows = []
    for p in sorted(backups.glob("*.db")) if backups.is_dir() else []:
        st = p.stat()
        rows.append((p.stem, st.st_size, datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))
    return rows


def restore(data_dir, name: str, port: int = 8000) -> Path:
    db, backups, _ = layout(data_dir)
    _check_name(name)
    src = backups / f"{name}.db"
    if not src.is_file():
        raise SnapshotError(f"Không thấy bản sao lưu '{name}' trong {backups}")
    _need_stopped(port)
    try:  # bản sao lưu phải là DB hợp lệ trước khi thay DB đang dùng
        conn = sqlite3.connect(str(src))
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        raise SnapshotError(f"Bản sao lưu hỏng: {exc}") from exc
    if ok != "ok":
        raise SnapshotError(f"Bản sao lưu không toàn vẹn: {ok}")
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):  # file WAL cũ sẽ làm sai lệch DB mới
        Path(str(db) + suffix).unlink(missing_ok=True)
    tmp = db.with_suffix(".restore.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, db)
    return db


def purge_orders(data_dir, port: int = 8000) -> dict[str, int]:
    db, _, media = layout(data_dir)
    if not db.is_file():
        raise SnapshotError(f"Không thấy DB: {db}")
    _need_stopped(port)
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    counts = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("order_items", "captures", "orders", "shifts"):  # đúng thứ tự khoá ngoại
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    removed = 0
    if media.is_dir():
        for d in media.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    counts["media_folders"] = removed
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sao lưu / khôi phục / làm sạch DB web POS")
    ap.add_argument("command", choices=["save", "list", "restore", "purge-orders"])
    ap.add_argument("name", nargs="?", help="tên bản sao lưu (save/restore)")
    ap.add_argument("--data-dir", default="data", help="thư mục dữ liệu web (mặc định data; demo: data_demo)")
    ap.add_argument("--port", type=int, default=8000, help="cổng server để kiểm tra đang chạy (mặc định 8000)")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.command == "list":
            rows = list_snapshots(args.data_dir)
            print("\n".join(f"{n:<24} {s / 1024:>9.1f} KB  {t}" for n, s, t in rows) if rows else "(chưa có bản sao lưu)")
        elif args.command == "save":
            if not args.name:
                raise SnapshotError("Thiếu tên: save <tên>")
            print(f"Đã lưu: {save(args.data_dir, args.name, args.overwrite)}")
        elif args.command == "restore":
            if not args.name:
                raise SnapshotError("Thiếu tên: restore <tên>")
            print(f"Đã khôi phục: {restore(args.data_dir, args.name, args.port)}\nMọi phiên đăng nhập cũ đã vô hiệu; hãy đăng nhập lại.")
        else:
            print("Đã xoá:", ", ".join(f"{k}={v}" for k, v in purge_orders(args.data_dir, args.port).items()))
    except SnapshotError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
