"""Test scripts/db_snapshot.py (chỉ thư viện chuẩn)."""

from __future__ import annotations

import importlib.util
import socket
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from backend.db import Database, utcnow

_SPEC = importlib.util.spec_from_file_location("db_snapshot", Path(__file__).resolve().parents[1] / "scripts" / "db_snapshot.py")
ds = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ds)


@contextmanager
def _listener(accept: bool = True, backlog: int = 16):
    """Giả lập một server đang chạy. accept=True: chấp nhận kết nối nền như server thật;
    accept=False + backlog nhỏ: listener BẬN không bao giờ accept (hàng đợi đầy sau lần thăm dò đầu)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(backlog)
    stop = threading.Event()
    conns: list[socket.socket] = []

    def loop():
        srv.settimeout(0.1)
        while not stop.is_set():
            try:
                conns.append(srv.accept()[0])
            except OSError:
                pass
    t = None
    if accept:
        t = threading.Thread(target=loop, daemon=True)
        t.start()
    try:
        yield srv.getsockname()[1]
    finally:
        stop.set()
        if t:
            t.join(1)
        for c in conns:
            c.close()
        srv.close()


def _make(tmp: Path):
    db = Database(tmp / "db" / "app.db")
    with db.tx() as c:
        c.execute("INSERT INTO users(username,password_hash,created_at) VALUES('staff','h',?)", (utcnow(),))
        c.execute("INSERT INTO shifts(user_id,started_at) VALUES(1,?)", (utcnow(),))
        c.execute("INSERT INTO orders(shift_id,cashier_id,created_at) VALUES(1,1,?)", (utcnow(),))
        c.execute("INSERT INTO order_items(order_id,product_id,created_at) VALUES(1,'7',?)", (utcnow(),))
        c.execute("INSERT INTO captures(order_id,status,created_at) VALUES(1,'success',?)", (utcnow(),))
        c.execute("INSERT INTO product_prices VALUES('7',12000,?)", (utcnow(),))
    (tmp / "transactions" / "1").mkdir(parents=True)
    (tmp / "transactions" / "1" / "t.jpg").write_bytes(b"x")
    return db


def _count(db: Database, table: str) -> int:
    with db.read() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_save_list_and_no_overwrite(tmp_path):
    _make(tmp_path)
    p = ds.save(tmp_path, "demo_clean")
    assert p.is_file() and [r[0] for r in ds.list_snapshots(tmp_path)] == ["demo_clean"]
    try:
        ds.save(tmp_path, "demo_clean")
        raise AssertionError("phải từ chối ghi đè")
    except ds.SnapshotError:
        pass
    ds.save(tmp_path, "demo_clean", overwrite=True)
    for bad in ("", "a b", "../x", "x" * 41):
        try:
            ds.save(tmp_path, bad)
            raise AssertionError(bad)
        except ds.SnapshotError:
            pass


def test_restore_replaces_db_and_removes_stale_wal(tmp_path):
    db = _make(tmp_path)
    ds.save(tmp_path, "snap")
    with db.tx() as c:
        c.execute("UPDATE product_prices SET price=99999 WHERE product_id='7'")
        c.execute("INSERT INTO users(username,password_hash,created_at) VALUES('extra','h',?)", (utcnow(),))
    (tmp_path / "db" / "app.db-wal").write_bytes(b"rac-cu")  # giả lập WAL cũ còn sót
    ds.restore(tmp_path, "snap", port=1)  # cổng 1: chắc chắn không có server
    db2 = Database(tmp_path / "db" / "app.db")
    with db2.read() as c:
        assert c.execute("SELECT price FROM product_prices").fetchone()[0] == 12000
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert not (tmp_path / "db" / "app.db-shm").exists()


def test_restore_refuses_when_server_running_or_snapshot_missing_or_corrupt(tmp_path):
    _make(tmp_path)
    ds.save(tmp_path, "snap")
    with _listener() as port:  # server thật: accept liên tục
        try:
            ds.restore(tmp_path, "snap", port=port)
            raise AssertionError("phải từ chối khi server còn chạy")
        except ds.SnapshotError as e:
            assert "còn chạy" in str(e)
        try:
            ds.purge_orders(tmp_path, port=port)
            raise AssertionError("purge cũng phải từ chối")
        except ds.SnapshotError:
            pass
    try:
        ds.restore(tmp_path, "khong-co", port=1)
        raise AssertionError
    except ds.SnapshotError:
        pass
    (tmp_path / "backups" / "hong.db").write_bytes(b"day khong phai sqlite" * 100)
    try:
        ds.restore(tmp_path, "hong", port=1)
        raise AssertionError
    except ds.SnapshotError:
        pass
    assert _count(Database(tmp_path / "db" / "app.db"), "users") == 1  # DB hiện tại còn nguyên


def test_purge_orders_keeps_users_prices_and_clears_media(tmp_path):
    db = _make(tmp_path)
    counts = ds.purge_orders(tmp_path, port=1)
    assert counts["orders"] == 1 and counts["order_items"] == 1 and counts["captures"] == 1 and counts["shifts"] == 1
    for t in ("orders", "order_items", "captures", "shifts"):
        assert _count(db, t) == 0
    assert _count(db, "users") == 1 and _count(db, "product_prices") == 1
    assert not (tmp_path / "transactions" / "1").exists()


def test_cli_returns_error_code_instead_of_traceback(tmp_path):
    assert ds.main(["restore", "khong-co", "--data-dir", str(tmp_path), "--port", "1"]) == 1
    assert ds.main(["save", "--data-dir", str(tmp_path)]) == 1
    assert ds.main(["list", "--data-dir", str(tmp_path)]) == 0


def test_server_running_is_stable_for_busy_listener_that_never_accepts():
    """Hồi quy: listener bận (hàng đợi accept đầy) không được bị coi là 'không có server' ở các lần thăm dò sau."""
    with _listener(accept=False, backlog=0) as port:
        assert [ds.server_running(port) for _ in range(4)] == [True, True, True, True]


def test_server_running_false_for_free_port_and_true_after_bind():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # cổng vừa trả: không có ai giữ
    assert ds.server_running(port) is False
    assert ds.server_running(port) is False  # lặp lại vẫn ổn định


def test_server_running_does_not_depend_on_slow_connect_refusal():
    """Hồi quy (Windows): từ chối kết nối tới cổng đóng có thể chậm quá thời gian chờ. Giả lập: MỌI lần kết nối đều
    hết giờ. Cổng trống vẫn phải là False; listener bận (không accept) vẫn phải là True."""
    real = ds.socket.create_connection

    def always_timeout(*a, **k):
        raise TimeoutError("giả lập Windows từ chối chậm")
    ds.socket.create_connection = always_timeout
    try:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()
        assert [ds.server_running(free_port) for _ in range(3)] == [False, False, False]
        assert ds.server_running(1) is False  # cổng thấp, không có server
        with _listener(accept=False, backlog=0) as port:
            assert [ds.server_running(port) for _ in range(4)] == [True, True, True, True]
    finally:
        ds.socket.create_connection = real
