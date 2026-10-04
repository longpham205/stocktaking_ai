"""Test scripts/reset_password.py (DB tạm)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.db import Database, utcnow
from backend.security import hash_password, verify_password

_SPEC = importlib.util.spec_from_file_location("reset_password", Path(__file__).resolve().parents[1] / "scripts" / "reset_password.py")
rp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rp)


@pytest.fixture()
def data_dir(tmp_path):
    db = Database(tmp_path / "db" / "app.db")
    with db.tx() as c:
        c.execute("INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES('admin',?,'admin',0,?)", (hash_password("old-admin"), utcnow()))
        uid = c.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        c.execute("INSERT INTO shifts(user_id,started_at) VALUES(?,?)", (uid, utcnow()))
    return tmp_path


def _hash(tmp, user="admin"):
    with Database(tmp / "db" / "app.db").read() as c:
        return c.execute("SELECT password_hash, is_active FROM users WHERE username=?", (user,)).fetchone()


def test_generated_password_works_closes_shifts_and_unlocks(data_dir, capsys):
    assert rp.main(["admin", "--data-dir", str(data_dir), "--unlock"]) == 0
    out = capsys.readouterr().out
    new_pw = out.split("ghi lại): ")[1].split()[0]
    row = _hash(data_dir)
    assert verify_password(new_pw, row["password_hash"]) and not verify_password("old-admin", row["password_hash"])
    assert row["is_active"] == 1
    assert "Đã đóng 1 ca" in out
    with Database(data_dir / "db" / "app.db").read() as c:
        assert c.execute("SELECT COUNT(*) FROM shifts WHERE ended_at IS NULL").fetchone()[0] == 0


def test_errors(data_dir):
    assert rp.main(["khongco", "--data-dir", str(data_dir)]) == 2
    assert rp.main(["admin", "--data-dir", str(data_dir / "nope")]) == 2
    with pytest.raises(ValueError):
        rp.reset_password(data_dir / "db" / "app.db", "admin", "ngan")


def test_list_does_not_print_hashes(data_dir, capsys):
    assert rp.main(["--list", "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "admin" in out and "ĐÃ KHOÁ" in out and "scrypt" not in out.lower() and "$" not in out


def test_reset_advanced_password(data_dir, capsys):
    import json

    assert rp.main(["--advanced", "--data-dir", str(data_dir)]) == 0
    pw = capsys.readouterr().out.split("ghi lại): ")[1].split()[0]
    with Database(data_dir / "db" / "app.db").read() as c:
        stored = json.loads(c.execute("SELECT value FROM settings WHERE key='advanced_password_hash'").fetchone()[0])
    assert verify_password(pw, stored)
