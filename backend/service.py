"""Logic nghiệp vụ POS: đăng nhập, đơn hàng, chụp + hàng đợi suy luận, thanh toán, quản trị.

Tầng HTTP (`server.py`) chỉ định tuyến và dịch lỗi; mọi quy tắc nằm ở đây để kiểm thử không cần mạng.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import queue
import re
import shutil
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import JsonCatalog
from .config import Settings
from .db import Database, utcnow
from .inference import Executor
from .mapper import merge_detections, save_thumbnail
from .security import (LoginLimiter, TokenError, hash_password, make_token, sign_media, verify_password,
                       verify_token)

log = logging.getLogger("backend")

MAX_QTY = 999
MAX_PRICE = 100_000_000
_BARCODE_RE = re.compile(r"^[0-9A-Za-z\-]{4,32}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{3,32}$")
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra


def _err(status: int, code: str, message: str = "", **extra) -> ApiError:
    return ApiError(status, code, message, **extra)


@dataclass
class Session:
    user_id: int
    role: str
    shift_id: int
    username: str
    full_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def fold(text: str) -> str:
    """Bỏ dấu tiếng Việt + hạ chữ thường để tìm kiếm không phân biệt dấu."""
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _as_int(value, name: str, lo: int = 0, hi: int = MAX_PRICE) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise _err(422, "VALIDATION_ERROR", f"'{name}' phải là số nguyên")
    v = int(value)
    if not lo <= v <= hi:
        raise _err(422, "VALIDATION_ERROR", f"'{name}' phải trong khoảng {lo}..{hi}")
    return v


def _evidence_json(evidence: dict | None) -> str | None:
    """Tuần tự hoá bằng chứng plugin an toàn: chấp nhận số numpy/đối tượng lạ, cắt nếu quá dài (không làm hỏng lượt chụp)."""
    if not evidence:
        return None

    def default(o):
        if hasattr(o, "tolist"):  # numpy: cả số đơn lẫn mảng (`.item()` lỗi với mảng nhiều phần tử)
            return o.tolist()
        if hasattr(o, "item"):
            return o.item()
        return str(o)
    try:
        text = json.dumps(evidence, default=default, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return text if len(text) <= 8000 else None


class App:
    def __init__(self, settings: Settings, catalog: JsonCatalog, executor: Executor, start_worker: bool = True) -> None:
        self.s, self.catalog, self.executor = settings, catalog, executor
        existed = settings.db_path.is_file() and settings.db_path.stat().st_size > 0
        self.db = Database(settings.db_path)
        if existed:
            self._backup_on_start()
        self.limiter = LoginLimiter(settings.max_failed_attempts, settings.lockout_seconds)
        self._queue: queue.Queue = queue.Queue(maxsize=settings.queue_max)
        self._jobs: dict[str, dict] = {}
        self._idem: dict[tuple, tuple[str, float]] = {}
        self._lock = threading.Lock()
        # Một luồng suy luận duy nhất: giữ tuần tự GPU và cho phép đặt timeout cứng mà không chạy hai lượt song song.
        self._infer_pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="infer")
        settings.media_root.mkdir(parents=True, exist_ok=True)
        self._seed()
        self._cleanup_media()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(target=self._worker_loop, name="inference-worker", daemon=True)
            self._worker.start()

    # ------------------------------------------------------------------ khởi tạo
    def _seed(self) -> None:
        with self.db.tx() as c:
            for username, pw, role, name in (("staff", self.s.seed_staff_password, "staff", "Thu ngân demo"),
                                             ("admin", self.s.seed_admin_password, "admin", "Quản trị")):
                if not c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    c.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES(?,?,?,?,?)",
                              (username, hash_password(pw), name, role, utcnow()))
            if self.s.seed_prices_path and not c.execute("SELECT 1 FROM product_prices LIMIT 1").fetchone():
                prices = json.loads(self.s.seed_prices_path.read_text(encoding="utf-8"))
                n = 0
                for pid, price in prices.items():
                    if price is not None and self.catalog.get(pid):
                        c.execute("INSERT INTO product_prices VALUES(?,?,?)", (pid, int(price), utcnow()))
                        n += 1
                log.info("Đã nạp %d giá seed từ %s", n, self.s.seed_prices_path)

    def _backup_on_start(self, keep: int = 7) -> None:
        """Sao lưu DB mỗi lần khởi động (giữ `keep` bản mới nhất) vào <data-dir>/backups. Lỗi sao lưu không chặn server."""
        try:
            folder = self.s.db_path.parent.parent / "backups"
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.db.backup(folder / f"app-{stamp}.db")
            for old in sorted(folder.glob("app-*.db"))[:-keep]:
                old.unlink(missing_ok=True)
        except Exception:
            log.exception("Không sao lưu được DB lúc khởi động (bỏ qua)")

    def _cleanup_media(self) -> None:
        cutoff = time.time() - self.s.media_retention_days * 86400
        for d in self.s.media_root.iterdir() if self.s.media_root.is_dir() else []:
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    # ------------------------------------------------------------------ cài đặt runtime
    def get_setting(self, key: str, default):
        with self.db.read() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def public_settings(self) -> dict:
        return {
            "allow_checkout_without_price": bool(self.get_setting("allow_checkout_without_price",
                                                                  self.s.allow_checkout_without_price)),
            "similarity_threshold": self.get_setting("similarity_threshold", None),
            "min_confidence_accept": self.get_setting("min_confidence_accept", None),
            "tilt_block_capture": bool(self.get_setting("tilt_block_capture", False)),
            "auto_print_receipt": bool(self.get_setting("auto_print_receipt", False)),
        }

    def update_settings(self, sess: Session, body: dict) -> dict:
        self._need_admin(sess)
        changes: dict = {}
        for key in ("allow_checkout_without_price", "tilt_block_capture", "auto_print_receipt"):
            if key in body:
                if not isinstance(body[key], bool):
                    raise _err(422, "VALIDATION_ERROR", f"{key} phải là true/false")
                changes[key] = body[key]
        for key, lo, hi in (("similarity_threshold", 0.3, 0.95), ("min_confidence_accept", 0.3, 0.99)):
            if key in body:
                v = body[key]
                if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi):
                    raise _err(422, "VALIDATION_ERROR", f"{key} phải trong {lo:.2f}..{hi:.2f} hoặc null")
                changes[key] = v
        if not changes:
            raise _err(422, "VALIDATION_ERROR", "Không có thiết lập hợp lệ nào")
        old = self.public_settings()
        with self.db.tx() as c:
            for k, v in changes.items():
                c.execute("INSERT INTO change_log(table_name,record_id,field_name,old_value,new_value,changed_by,changed_at)"
                          " VALUES('settings',?,?,?,?,?,?)", (k, k, json.dumps(old.get(k)), json.dumps(v), sess.user_id, utcnow()))
                c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (k, json.dumps(v)))
        return self.public_settings()

    # ------------------------------------------------------------------ xác thực
    def login(self, username: str, password: str, ip: str) -> dict:
        username = str(username or "").strip().lower()
        if not username or not password or not isinstance(password, str):
            raise _err(422, "VALIDATION_ERROR", "Thiếu tài khoản hoặc mật khẩu")
        key = (username, ip)
        wait = self.limiter.check(key)
        if wait:
            raise _err(429, "RATE_LIMITED", "Đăng nhập sai quá nhiều lần, thử lại sau", retry_after=wait)
        with self.db.read() as c:
            row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)  # thời gian đồng đều
        if not row or not ok or not row["is_active"]:
            self.limiter.fail(key)
            raise _err(401, "AUTH_INVALID", "Sai tài khoản hoặc mật khẩu")
        self.limiter.reset(key)
        with self.db.tx() as c:  # một ca mở mỗi tài khoản: đăng nhập nơi khác đóng ca cũ
            c.execute("UPDATE shifts SET ended_at=? WHERE user_id=? AND ended_at IS NULL", (utcnow(), row["id"]))
            sid = c.execute("INSERT INTO shifts(user_id,started_at) VALUES(?,?)", (row["id"], utcnow())).lastrowid
        token = make_token(self.s.jwt_secret, {"uid": row["id"], "sid": sid, "role": row["role"]}, self.s.token_ttl_seconds)
        return {"token": token, "user": self._user_view(row)}

    @staticmethod
    def _user_view(r) -> dict:
        return {"id": r["id"], "username": r["username"], "full_name": r["full_name"], "role": r["role"],
                "has_seen_onboarding": bool(r["has_seen_onboarding"])}

    def authenticate(self, token: str | None) -> Session:
        if not token:
            raise _err(401, "AUTH_INVALID", "Cần đăng nhập")
        try:
            body = verify_token(self.s.jwt_secret, token)
        except TokenError as exc:
            raise _err(401, exc.code, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn") from exc
        with self.db.read() as c:
            user = c.execute("SELECT * FROM users WHERE id=?", (body.get("uid"),)).fetchone()
            shift = c.execute("SELECT * FROM shifts WHERE id=?", (body.get("sid"),)).fetchone()
        if not user or not user["is_active"]:
            raise _err(401, "AUTH_INVALID", "Tài khoản không hợp lệ")
        if not shift or shift["ended_at"] is not None or shift["user_id"] != user["id"]:
            raise _err(401, "SESSION_INVALID", "Tài khoản đã đăng nhập ở thiết bị khác hoặc ca đã kết thúc")
        return Session(user["id"], user["role"], shift["id"], user["username"], user["full_name"])

    def logout(self, token: str | None) -> dict:
        """Idempotent: token hỏng hoặc ca đã đóng vẫn trả OK."""
        try:
            body = verify_token(self.s.jwt_secret, token or "")
            with self.db.tx() as c:
                c.execute("UPDATE shifts SET ended_at=? WHERE id=? AND ended_at IS NULL", (utcnow(), body.get("sid")))
        except TokenError:
            pass
        return {"ok": True}

    def me(self, sess: Session) -> dict:
        with self.db.read() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (sess.user_id,)).fetchone()
            shift = c.execute("SELECT * FROM shifts WHERE id=?", (sess.shift_id,)).fetchone()
        return {"user": self._user_view(row), "shift": {"id": shift["id"], "started_at": shift["started_at"],
                                                        "total_collected": shift["total_collected"]},
                "settings": self.public_settings()}

    def mark_onboarding_seen(self, sess: Session) -> dict:
        with self.db.tx() as c:
            c.execute("UPDATE users SET has_seen_onboarding=1 WHERE id=?", (sess.user_id,))
        return {"ok": True}

    @staticmethod
    def _need_admin(sess: Session) -> None:
        if not sess.is_admin:
            raise _err(403, "FORBIDDEN", "Chỉ quản trị viên được thực hiện")

    # ------------------------------------------------------------------ catalog
    def _prices(self, c) -> dict[str, int]:
        return {r["product_id"]: r["price"] for r in c.execute("SELECT product_id, price FROM product_prices")}

    def _barcodes(self, c) -> dict[str, str]:
        over = {r["product_id"]: r["barcode"] for r in c.execute("SELECT product_id, barcode FROM product_overrides")}
        return {pid: over.get(pid, p["barcode"]) for pid, p in self.catalog.all().items()}

    def _product_view(self, pid: str, prices: dict, barcodes: dict) -> dict:
        p = self.catalog.get(pid)
        return {"id": pid, "name": p["name"] if p else f"SKU {pid}", "barcode": barcodes.get(pid, ""),
                "price": prices.get(pid)}

    def list_products(self, search: str = "", barcode: str = "", limit: int = 50) -> list[dict]:
        with self.db.read() as c:
            prices, barcodes = self._prices(c), self._barcodes(c)
        out = []
        q = fold(search.strip())
        for pid in self.catalog.all():
            if barcode:
                if barcodes.get(pid) != barcode.strip():
                    continue
            elif q and q not in fold(self.catalog.get(pid)["name"]) and q != pid:
                continue
            out.append(self._product_view(pid, prices, barcodes))
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ đơn hàng
    def _order_row(self, c, sess: Session, oid: int):
        row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not row or (not sess.is_admin and row["cashier_id"] != sess.user_id):
            raise _err(404, "NOT_FOUND", "Không tìm thấy đơn hàng")  # không lộ đơn của người khác
        return row

    def _thumb_url(self, rel: str | None) -> str | None:
        if not rel:
            return None
        exp = int(time.time()) + self.s.media_url_ttl
        return f"/api/media/{rel}?exp={exp}&sig={sign_media(self.s.jwt_secret, rel, exp)}"

    def _order_view(self, c, row) -> dict:
        paid = row["status"] == "paid"
        prices = self._prices(c)
        items, total, missing = [], 0, 0
        for it in c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (row["id"],)):
            price = it["manual_price"] if it["manual_price"] is not None else (
                it["unit_price"] if paid else prices.get(it["product_id"]))
            line = None if price is None else price * it["quantity"]
            if price is None:
                missing += 1
            else:
                total += line
            p = self.catalog.get(it["product_id"])
            items.append({"id": it["id"], "product_id": it["product_id"],
                          "product_name": p["name"] if p else f"SKU {it['product_id']}",
                          "quantity": it["quantity"], "unit_price": price, "price_missing": price is None,
                          "manual_price": it["manual_price"] is not None, "line_total": line,
                          "flagged": bool(it["flagged"]), "thumbnail_url": self._thumb_url(it["thumb_path"]),
                          "evidence": json.loads(it["evidence_json"]) if it["evidence_json"] else None})
        return {"id": row["id"], "status": row["status"], "created_at": row["created_at"], "paid_at": row["paid_at"],
                "payment_method": row["payment_method"], "cash_given": row["cash_given"],
                "change_given": row["change_given"], "items": items,
                "item_count": sum(i["quantity"] for i in items),
                "total": row["total_amount"] if paid else total, "missing_price_count": missing,
                "flagged_count": sum(1 for i in items if i["flagged"])}

    def create_order(self, sess: Session) -> dict:
        """Tạo đơn mới; nếu người dùng đang có đơn mở RỖNG (chưa có dòng hàng/lượt chụp) thì dùng lại, tránh đơn mồ côi."""
        with self.db.tx() as c:
            row = c.execute("SELECT o.id FROM orders o WHERE o.cashier_id=? AND o.status='open' "
                            "AND NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id=o.id) "
                            "AND NOT EXISTS (SELECT 1 FROM captures k WHERE k.order_id=o.id) ORDER BY o.id DESC LIMIT 1",
                            (sess.user_id,)).fetchone()
            if row:
                oid = row["id"]
                c.execute("UPDATE orders SET shift_id=? WHERE id=?", (sess.shift_id, oid))
            else:
                oid = c.execute("INSERT INTO orders(shift_id,cashier_id,created_at) VALUES(?,?,?)",
                                (sess.shift_id, sess.user_id, utcnow())).lastrowid
        return self.get_order(sess, oid)

    def open_order(self, sess: Session) -> dict:
        """Đơn đang mở gần nhất của người dùng (để khôi phục sau khi tải lại trang / đăng nhập lại)."""
        with self.db.read() as c:
            row = c.execute("SELECT * FROM orders WHERE cashier_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                            (sess.user_id,)).fetchone()
            return {"order": self._order_view(c, row) if row else None}

    def get_order(self, sess: Session, oid: int) -> dict:
        with self.db.read() as c:
            return self._order_view(c, self._order_row(c, sess, oid))

    def _open_order(self, c, sess: Session, oid: int):
        row = self._order_row(c, sess, oid)
        if row["status"] != "open":
            raise _err(409, "ORDER_NOT_OPEN", "Đơn hàng đã thanh toán hoặc đã huỷ")
        return row

    def _merge_or_insert(self, c, oid: int, pid: str, qty: int, thumb: str | None = None, evidence: dict | None = None) -> None:
        """Dòng đã xác nhận (không flagged) cùng SKU thì cộng dồn; ngược lại thêm dòng mới."""
        row = c.execute("SELECT id, quantity FROM order_items WHERE order_id=? AND product_id=? AND flagged=0 "
                        "AND manual_price IS NULL ORDER BY id LIMIT 1", (oid, pid)).fetchone()
        if row:
            c.execute("UPDATE order_items SET quantity=? WHERE id=?", (min(MAX_QTY, row["quantity"] + qty), row["id"]))
        else:
            c.execute("INSERT INTO order_items(order_id,product_id,quantity,thumb_path,evidence_json,created_at) "
                      "VALUES(?,?,?,?,?,?)", (oid, pid, qty, thumb, _evidence_json(evidence), utcnow()))

    def add_item(self, sess: Session, oid: int, body: dict) -> dict:
        pid = str(body.get("product_id", "")).strip()
        if not self.catalog.get(pid):
            raise _err(422, "VALIDATION_ERROR", "Sản phẩm không tồn tại")
        qty = _as_int(body.get("quantity", 1), "quantity", 1, MAX_QTY)
        with self.db.tx() as c:
            self._open_order(c, sess, oid)
            self._merge_or_insert(c, oid, pid, qty)
        return self.get_order(sess, oid)

    def update_item(self, sess: Session, oid: int, iid: int, body: dict) -> dict:
        with self.db.tx() as c:
            self._open_order(c, sess, oid)
            it = c.execute("SELECT * FROM order_items WHERE id=? AND order_id=?", (iid, oid)).fetchone()
            if not it:
                raise _err(404, "NOT_FOUND", "Không thấy dòng hàng")
            if "quantity" in body:
                c.execute("UPDATE order_items SET quantity=? WHERE id=?", (_as_int(body["quantity"], "quantity", 1, MAX_QTY), iid))
            if "manual_price" in body:
                mp = body["manual_price"]
                c.execute("UPDATE order_items SET manual_price=? WHERE id=?", (None if mp is None else _as_int(mp, "manual_price"), iid))
            if body.get("confirm") is True:
                c.execute("UPDATE order_items SET flagged=0 WHERE id=?", (iid,))
            if "product_id" in body:
                pid = str(body["product_id"]).strip()
                if not self.catalog.get(pid):
                    raise _err(422, "VALIDATION_ERROR", "Sản phẩm không tồn tại")
                c.execute("UPDATE order_items SET product_id=?, flagged=0, manual_price=NULL, evidence_json=NULL WHERE id=?", (pid, iid))
                other = c.execute("SELECT id, quantity FROM order_items WHERE order_id=? AND product_id=? AND flagged=0 "
                                  "AND manual_price IS NULL AND id<>? ORDER BY id LIMIT 1", (oid, pid, iid)).fetchone()
                if other:  # gộp vào dòng đã có của SKU vừa chọn
                    cur = c.execute("SELECT quantity FROM order_items WHERE id=?", (iid,)).fetchone()["quantity"]
                    c.execute("UPDATE order_items SET quantity=? WHERE id=?", (min(MAX_QTY, other["quantity"] + cur), other["id"]))
                    c.execute("DELETE FROM order_items WHERE id=?", (iid,))
        return self.get_order(sess, oid)

    def delete_item(self, sess: Session, oid: int, iid: int) -> dict:
        with self.db.tx() as c:
            self._open_order(c, sess, oid)
            if not c.execute("DELETE FROM order_items WHERE id=? AND order_id=?", (iid, oid)).rowcount:
                raise _err(404, "NOT_FOUND", "Không thấy dòng hàng")
        return self.get_order(sess, oid)

    def checkout(self, sess: Session, oid: int, body: dict) -> dict:
        method = body.get("method")
        if method not in ("cash", "qr"):
            raise _err(422, "VALIDATION_ERROR", "Phương thức phải là 'cash' hoặc 'qr'")
        allow_missing = bool(self.public_settings()["allow_checkout_without_price"])
        with self.db.tx() as c:
            row = self._open_order(c, sess, oid)
            view = self._order_view(c, row)
            if not view["items"]:
                raise _err(422, "VALIDATION_ERROR", "Đơn hàng chưa có sản phẩm")
            if view["missing_price_count"] and not allow_missing:
                raise _err(409, "PRICE_MISSING_BLOCKED", "Còn sản phẩm chưa có giá, hãy nhập giá tay",
                           missing=view["missing_price_count"])
            total = view["total"]  # dòng thiếu giá tính 0đ khi được cho phép
            cash_given = change = None
            if method == "cash":
                cash_given = _as_int(body.get("cash_given"), "cash_given", 0, MAX_PRICE * 10)
                if cash_given < total:
                    raise _err(422, "VALIDATION_ERROR", "Tiền khách đưa chưa đủ")
                change = cash_given - total
            for it in view["items"]:  # đóng băng đơn giá tại thời điểm bán
                c.execute("UPDATE order_items SET unit_price=? WHERE id=?", (it["unit_price"] or 0, it["id"]))
            c.execute("UPDATE orders SET status='paid', payment_method=?, cash_given=?, change_given=?, total_amount=?, "
                      "paid_at=? WHERE id=?", (method, cash_given, change, total, utcnow(), oid))
            c.execute("UPDATE shifts SET total_collected=total_collected+? WHERE id=?", (total, sess.shift_id))
        return self.get_order(sess, oid)

    def void_order(self, sess: Session, oid: int) -> dict:
        with self.db.tx() as c:
            row = self._order_row(c, sess, oid)
            if row["status"] == "void":
                pass
            elif row["status"] == "paid" and not sess.is_admin:
                raise _err(403, "FORBIDDEN", "Chỉ quản trị viên được huỷ đơn đã thanh toán")
            else:
                if row["status"] == "paid":
                    c.execute("UPDATE shifts SET total_collected=MAX(0,total_collected-?) WHERE id=?",
                              (row["total_amount"] or 0, row["shift_id"]))
                c.execute("UPDATE orders SET status='void' WHERE id=?", (oid,))
        return self.get_order(sess, oid)

    # ------------------------------------------------------------------ lịch sử
    def _range_start(self, rng: str) -> str | None:
        off = timedelta(hours=self.s.tz_offset_hours)
        now = datetime.now(timezone.utc)
        if rng == "all":
            return None
        days = {"today": 0, "7d": 6, "30d": 29}.get(rng)
        if days is None:
            raise _err(422, "VALIDATION_ERROR", "range phải là today|7d|30d|all")
        local_midnight = (now + off).replace(hour=0, minute=0, second=0, microsecond=0)
        return (local_midnight - timedelta(days=days) - off).isoformat(timespec="seconds")

    def history(self, sess: Session, rng: str = "today", all_users: bool = False, limit: int = 200) -> list[dict]:
        if all_users:
            self._need_admin(sess)
        start = self._range_start(rng)
        sql = ("SELECT o.*, u.full_name AS cashier_name, (SELECT COALESCE(SUM(quantity),0) FROM order_items WHERE order_id=o.id) "
               "AS n FROM orders o JOIN users u ON u.id=o.cashier_id WHERE o.status IN ('paid','void')")
        args: list = []
        if not all_users:
            sql += " AND o.cashier_id=?"
            args.append(sess.user_id)
        if start:
            sql += " AND o.created_at>=?"
            args.append(start)
        sql += " ORDER BY o.id DESC LIMIT ?"
        args.append(limit)
        with self.db.read() as c:
            return [{"id": r["id"], "status": r["status"], "created_at": r["created_at"], "paid_at": r["paid_at"],
                     "total": r["total_amount"] or 0, "item_count": r["n"], "payment_method": r["payment_method"],
                     "cashier": r["cashier_name"]} for r in c.execute(sql, args)]

    # ------------------------------------------------------------------ chụp + hàng đợi suy luận
    def submit_capture(self, sess: Session, oid: int, data: bytes, idem_key: str | None) -> dict:
        import cv2
        import numpy as np

        with self.db.read() as c:
            row = self._order_row(c, sess, oid)
        if row["status"] != "open":
            raise _err(409, "ORDER_NOT_OPEN", "Đơn hàng đã thanh toán hoặc đã huỷ")
        idem_key = (idem_key or "").strip()[:64] or None
        with self._lock:
            if idem_key:
                hit = self._idem.get((oid, idem_key))
                if hit and time.time() - hit[1] < self.s.idempotency_window_seconds and hit[0] in self._jobs:
                    return {"job_id": hit[0], "duplicate": True}
        if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:  # không tin Content-Type
            raise _err(400, "IMAGE_DECODE_ERROR", "Không giải mã được ảnh")
        ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        folder = self.s.media_root / str(oid)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"capture_{uuid.uuid4().hex[:10]}{ext}"  # tên do server đặt
        path.write_bytes(data)
        job = {"id": uuid.uuid4().hex, "order_id": oid, "user_id": sess.user_id, "path": str(path),
               "status": "queued", "created": time.time(), "error": None, "added": 0, "warnings": []}
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            path.unlink(missing_ok=True)
            raise _err(503, "QUEUE_FULL", "Hệ thống đang bận, thử lại sau ít giây") from None
        with self._lock:
            self._jobs[job["id"]] = job
            if idem_key:
                self._idem[(oid, idem_key)] = (job["id"], time.time())
            for jid in [k for k, v in self._jobs.items() if time.time() - v["created"] > 3600]:
                self._jobs.pop(jid, None)
        return {"job_id": job["id"], "duplicate": False}

    def job_view(self, sess: Session, jid: str) -> dict:
        with self._lock:
            job = self._jobs.get(jid)
            if not job or (job["user_id"] != sess.user_id and not sess.is_admin):
                raise _err(404, "NOT_FOUND", "Không thấy tác vụ")
            pos = sum(1 for j in self._jobs.values() if j["status"] == "queued" and j["created"] < job["created"])
            snap = dict(job)
        out: dict = {"status": snap["status"], "position": pos if snap["status"] == "queued" else 0}
        if snap["status"] == "done":
            out["added"] = snap["added"]
            out["warnings"] = snap["warnings"]
            out["order"] = self.get_order(sess, snap["order_id"])
        if snap["status"] == "error":
            out["error"] = snap["error"]
        return out

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._run_job(job)
            except Exception:  # không để luồng chết
                log.exception("Lỗi không lường trước trong worker")
                job["status"], job["error"] = "error", {"code": "PIPELINE_ERROR", "message": "Lỗi xử lý ảnh"}

    def _run_job(self, job: dict) -> None:
        import cv2

        job["status"] = "processing"
        oid = job["order_id"]
        t0 = time.perf_counter()
        fut = self._infer_pool.submit(self.executor.infer, job["path"], self.get_setting("similarity_threshold", None),
                                      self.get_setting("min_confidence_accept", None))
        try:
            out = fut.result(timeout=self.s.job_timeout_seconds)
        except cf.TimeoutError:
            # Không kill được luồng đang chạy; lượt kế tiếp tự xếp hàng sau nó (cùng một luồng suy luận).
            log.error("Job %s quá %.0fs, trả lỗi cho người dùng (luồng suy luận vẫn chạy nốt)", job["id"], self.s.job_timeout_seconds)
            self._record_capture(oid, "timeout", 0, self.s.job_timeout_seconds * 1000, job["path"], "timeout")
            job["status"], job["error"] = "error", {"code": "PIPELINE_TIMEOUT", "message": "Xử lý quá lâu, hãy chụp lại"}
            return
        except Exception as exc:
            log.exception("Pipeline lỗi")
            code = "GPU_OOM" if "out of memory" in str(exc).lower() else "PIPELINE_ERROR"
            self._record_capture(oid, "error", 0, (time.perf_counter() - t0) * 1000, job["path"], str(exc)[:300])
            job["status"], job["error"] = "error", {"code": code, "message": "Không xử lý được ảnh, hãy chụp lại"}
            return
        elapsed_ms = out.processing_time_ms or (time.perf_counter() - t0) * 1000
        warnings = []
        if out.has_overlap:
            warnings.append({"type": "overlap_detected"})
        if out.detected_count is not None and out.detected_count > len(out.detections):
            # pipeline loại âm thầm các vật 'rejected'; báo cho thu ngân biết còn vật chưa nhận ra
            warnings.append({"type": "unrecognized_objects", "count": out.detected_count - len(out.detections)})
        job["warnings"] = warnings
        img = cv2.imread(job["path"])
        lines = merge_detections(out.detections)
        added = 0
        with self.db.tx() as c:
            status = c.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()
            if not status or status["status"] != "open":
                job["status"], job["error"] = "error", {"code": "ORDER_NOT_OPEN", "message": "Đơn hàng đã đóng"}
                return
            for ln in lines:
                thumb = None
                if img is not None:
                    rel = f"{oid}/t_{uuid.uuid4().hex[:10]}.jpg"
                    if save_thumbnail(img, ln["bbox"], self.s.media_root / rel, self.s.thumb_width):
                        thumb = rel
                if ln["flagged"]:
                    c.execute("INSERT INTO order_items(order_id,product_id,quantity,flagged,thumb_path,evidence_json,created_at) "
                              "VALUES(?,?,?,1,?,?,?)", (oid, ln["product_id"], 1, thumb,
                                                       _evidence_json(ln["evidence"]), utcnow()))
                else:
                    self._merge_or_insert(c, oid, ln["product_id"], ln["quantity"], thumb, ln["evidence"])
                added += ln["quantity"]
            c.execute("INSERT INTO captures(order_id,status,item_count,processing_time_ms,image_path,created_at) "
                      "VALUES(?,?,?,?,?,?)", (oid, "success", added, elapsed_ms, job["path"], utcnow()))
        job["added"], job["status"] = added, "done"

    def _record_capture(self, oid: int, status: str, n: int, ms: float, path: str, error: str | None) -> None:
        with self.db.tx() as c:
            c.execute("INSERT INTO captures(order_id,status,item_count,processing_time_ms,image_path,error,created_at) "
                      "VALUES(?,?,?,?,?,?,?)", (oid, status, n, ms, path, error, utcnow()))

    # ------------------------------------------------------------------ quản trị: sản phẩm
    def admin_products(self, sess: Session, search: str = "", flt: str = "", page: int = 1, size: int = 50) -> dict:
        self._need_admin(sess)
        with self.db.read() as c:
            prices, barcodes = self._prices(c), self._barcodes(c)
        q = fold(search.strip())
        rows = []
        for pid, p in self.catalog.all().items():
            if q and q not in fold(p["name"]) and q not in pid and q not in barcodes.get(pid, ""):
                continue
            if flt == "missing_price" and pid in prices:
                continue
            if flt == "missing_barcode" and barcodes.get(pid):
                continue
            rows.append(self._product_view(pid, prices, barcodes))
        page, size = max(1, page), max(1, min(200, size))
        return {"total": len(rows), "page": page, "size": size, "items": rows[(page - 1) * size: page * size],
                "missing_price": sum(1 for pid in self.catalog.all() if pid not in prices),
                "missing_barcode": sum(1 for pid in self.catalog.all() if not barcodes.get(pid))}

    def admin_update_product(self, sess: Session, pid: str, body: dict) -> dict:
        self._need_admin(sess)
        if not self.catalog.get(pid):
            raise _err(404, "NOT_FOUND", "Không thấy sản phẩm")
        if not ({"price", "barcode"} & set(body)):
            raise _err(422, "VALIDATION_ERROR", "Chỉ sửa được 'price' và 'barcode'")
        with self.db.tx() as c:
            prices, barcodes = self._prices(c), self._barcodes(c)
            now = utcnow()

            def log_change(field: str, old, new) -> None:
                c.execute("INSERT INTO change_log(table_name,record_id,field_name,old_value,new_value,changed_by,changed_at) "
                          "VALUES('product',?,?,?,?,?,?)", (pid, field, None if old is None else str(old),
                                                            None if new is None else str(new), sess.user_id, now))
            if "price" in body:
                new = None if body["price"] is None else _as_int(body["price"], "price")
                if new != prices.get(pid):
                    log_change("price", prices.get(pid), new)
                    if new is None:
                        c.execute("DELETE FROM product_prices WHERE product_id=?", (pid,))
                    else:
                        c.execute("INSERT INTO product_prices VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET "
                                  "price=excluded.price, updated_at=excluded.updated_at", (pid, new, now))
                    prices = self._prices(c)
            if "barcode" in body:
                new = str(body["barcode"] or "").strip()
                if new and not _BARCODE_RE.match(new):
                    raise _err(422, "VALIDATION_ERROR", "Barcode gồm 4–32 ký tự chữ, số hoặc dấu gạch ngang")
                if new and any(b == new for k, b in barcodes.items() if k != pid):
                    raise _err(409, "BARCODE_DUPLICATE", "Barcode đã thuộc sản phẩm khác")
                if new != barcodes.get(pid, ""):
                    log_change("barcode", barcodes.get(pid, ""), new)
                    c.execute("INSERT INTO product_overrides VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET "
                              "barcode=excluded.barcode, updated_at=excluded.updated_at", (pid, new, now))
                    barcodes = self._barcodes(c)
            return self._product_view(pid, prices, barcodes)

    # ------------------------------------------------------------------ quản trị: báo cáo, nhân viên
    def admin_reports(self, sess: Session, rng: str = "today") -> dict:
        self._need_admin(sess)
        if rng not in ("today", "7d", "30d"):
            raise _err(422, "VALIDATION_ERROR", "range phải là today|7d|30d")
        start = self._range_start("today")
        with self.db.read() as c:
            paid = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total_amount),0) t FROM orders WHERE status='paid' AND created_at>=?", (start,)).fetchone()
            cap = c.execute("SELECT COUNT(*) n, SUM(status='error') e, AVG(CASE WHEN status='success' THEN processing_time_ms END) a "
                            "FROM captures WHERE created_at>=?", (start,)).fetchone()
            active = c.execute("SELECT COUNT(*) FROM shifts WHERE ended_at IS NULL").fetchone()[0]
            staff = c.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
            prices, barcodes = self._prices(c), self._barcodes(c)
        n = cap["n"] or 0
        extra = self._range_report(rng)
        return {**extra, "orders_today": paid["n"], "revenue_today": paid["t"], "captures_today": n,
                "error_rate": round((cap["e"] or 0) / n, 4) if n else 0.0,
                "avg_processing_ms": round(cap["a"], 1) if cap["a"] else None,
                "active_shifts": active, "active_staff": staff, "queue_size": self._queue.qsize(),
                "products_total": len(self.catalog.all()),
                "products_missing_price": sum(1 for p in self.catalog.all() if p not in prices),
                "products_missing_barcode": sum(1 for p in self.catalog.all() if not barcodes.get(p))}

    def _range_report(self, rng: str) -> dict:
        """Doanh thu theo ngày (giờ địa phương, đủ cả ngày không có đơn) và top 5 sản phẩm bán chạy trong khoảng."""
        start = self._range_start(rng)
        off = timedelta(hours=self.s.tz_offset_hours)
        n_days = {"today": 1, "7d": 7, "30d": 30}[rng]
        with self.db.read() as c:
            rows = c.execute("SELECT paid_at, total_amount FROM orders WHERE status='paid' AND paid_at>=?", (start,)).fetchall()
            tops = c.execute("SELECT i.product_id pid, SUM(i.quantity) q, SUM(i.quantity*COALESCE(i.unit_price,0)) r "
                             "FROM order_items i JOIN orders o ON o.id=i.order_id WHERE o.status='paid' AND o.paid_at>=? "
                             "GROUP BY i.product_id ORDER BY q DESC, r DESC, i.product_id LIMIT 5", (start,)).fetchall()
        today = (datetime.now(timezone.utc) + off).date()
        daily = {(today - timedelta(days=k)).isoformat(): [0, 0] for k in range(n_days - 1, -1, -1)}
        for r in rows:
            day = (datetime.fromisoformat(r["paid_at"]) + off).date().isoformat()
            if day in daily:
                daily[day][0] += 1
                daily[day][1] += r["total_amount"] or 0
        return {"range": rng, "range_orders": sum(v[0] for v in daily.values()), "range_revenue": sum(v[1] for v in daily.values()),
                "daily": [{"date": d, "orders": v[0], "revenue": v[1]} for d, v in daily.items()],
                "top_products": [{"product_id": t["pid"], "name": (self.catalog.get(t["pid"]) or {}).get("name", f"SKU {t['pid']}"),
                                  "quantity": t["q"], "revenue": t["r"]} for t in tops]}

    # ------------------------------------------------------------------ quản trị: nhật ký thay đổi + hoàn tác
    def admin_change_log(self, sess: Session, table: str = "", record: str = "", limit: int = 100) -> list[dict]:
        self._need_admin(sess)
        sql = ("SELECT l.*, u.username FROM change_log l LEFT JOIN users u ON u.id=l.changed_by WHERE 1=1")
        args: list = []
        if table:
            sql += " AND l.table_name=?"
            args.append(table)
        if record:
            sql += " AND l.record_id=?"
            args.append(record)
        sql += " ORDER BY l.id DESC LIMIT ?"
        args.append(max(1, min(500, limit)))
        with self.db.read() as c:
            return [{"id": r["id"], "table": r["table_name"], "record_id": r["record_id"], "field": r["field_name"],
                     "old": r["old_value"], "new": r["new_value"], "by": r["username"], "at": r["changed_at"],
                     "name": (self.catalog.get(r["record_id"]) or {}).get("name") if r["table_name"] == "product" else None}
                    for r in c.execute(sql, args)]

    def admin_revert_change(self, sess: Session, lid: int) -> dict:
        """Hoàn tác MỘT thay đổi (giá/barcode/cài đặt) bằng cách ghi lại giá trị cũ; việc hoàn tác cũng được ghi log.
        Từ chối (409) nếu giá trị hiện tại không còn bằng giá trị mới của dòng log (đã bị đổi sau đó)."""
        self._need_admin(sess)
        with self.db.read() as c:
            row = c.execute("SELECT * FROM change_log WHERE id=?", (lid,)).fetchone()
            prices, barcodes = self._prices(c), self._barcodes(c)
        if not row:
            raise _err(404, "NOT_FOUND", "Không thấy dòng nhật ký")
        table, field, rid = row["table_name"], row["field_name"], row["record_id"]
        if table == "product" and field in ("price", "barcode"):
            if not self.catalog.get(rid):
                raise _err(404, "NOT_FOUND", "Sản phẩm không còn trong catalog")
            if field == "price":
                current = str(prices[rid]) if rid in prices else None
                expected, target = row["new_value"], (None if row["old_value"] is None else int(row["old_value"]))
            else:
                current, expected, target = barcodes.get(rid, "") or "", row["new_value"] or "", row["old_value"] or ""
            if current != expected:
                raise _err(409, "CHANGE_STALE", "Giá trị đã được thay đổi sau lần sửa này, không thể hoàn tác")
            self.admin_update_product(sess, rid, {field: target})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        if table == "settings":
            current, expected = self.public_settings().get(field), json.loads(row["new_value"])
            if current != expected:
                raise _err(409, "CHANGE_STALE", "Cài đặt đã được thay đổi sau lần sửa này, không thể hoàn tác")
            self.update_settings(sess, {field: json.loads(row["old_value"])})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        raise _err(422, "VALIDATION_ERROR", "Loại thay đổi này không hoàn tác được")

    def admin_users(self, sess: Session) -> list[dict]:
        self._need_admin(sess)
        with self.db.read() as c:
            return [{"id": r["id"], "username": r["username"], "full_name": r["full_name"], "role": r["role"],
                     "is_active": bool(r["is_active"]),
                     "online": bool(c.execute("SELECT 1 FROM shifts WHERE user_id=? AND ended_at IS NULL", (r["id"],)).fetchone())}
                    for r in c.execute("SELECT * FROM users ORDER BY id")]

    def admin_create_user(self, sess: Session, body: dict) -> dict:
        self._need_admin(sess)
        username = str(body.get("username", "")).strip().lower()
        password, role = str(body.get("password", "")), body.get("role", "staff")
        if not _USERNAME_RE.match(username):
            raise _err(422, "VALIDATION_ERROR", "Tài khoản 3–32 ký tự (chữ, số, . _ -)")
        if len(password) < 8:
            raise _err(422, "VALIDATION_ERROR", "Mật khẩu tối thiểu 8 ký tự")
        if role not in ("staff", "admin"):
            raise _err(422, "VALIDATION_ERROR", "Vai trò phải là staff hoặc admin")
        try:
            with self.db.tx() as c:
                uid = c.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES(?,?,?,?,?)",
                                (username, hash_password(password), str(body.get("full_name", ""))[:80], role, utcnow())).lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise _err(409, "USER_EXISTS", "Tài khoản đã tồn tại") from exc
            raise
        return next(u for u in self.admin_users(sess) if u["id"] == uid)

    def admin_update_user(self, sess: Session, uid: int, body: dict) -> dict:
        self._need_admin(sess)
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                raise _err(404, "NOT_FOUND", "Không thấy nhân viên")
            if "password" in body:
                if len(str(body["password"])) < 8:
                    raise _err(422, "VALIDATION_ERROR", "Mật khẩu tối thiểu 8 ký tự")
                c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(str(body["password"])), uid))
                c.execute("UPDATE shifts SET ended_at=? WHERE user_id=? AND ended_at IS NULL", (utcnow(), uid))
            if "full_name" in body:
                c.execute("UPDATE users SET full_name=? WHERE id=?", (str(body["full_name"])[:80], uid))
            if "is_active" in body:
                if not isinstance(body["is_active"], bool):
                    raise _err(422, "VALIDATION_ERROR", "is_active phải là true/false")
                if uid == sess.user_id and not body["is_active"]:
                    raise _err(409, "VALIDATION_ERROR", "Không thể tự khoá tài khoản của mình")
                c.execute("UPDATE users SET is_active=? WHERE id=?", (int(body["is_active"]), uid))
                if not body["is_active"]:
                    c.execute("UPDATE shifts SET ended_at=? WHERE user_id=? AND ended_at IS NULL", (utcnow(), uid))
        return next(u for u in self.admin_users(sess) if u["id"] == uid)

    # ------------------------------------------------------------------ vòng đời
    def close(self) -> None:
        self._infer_pool.shutdown(wait=False, cancel_futures=True)
        if self._worker:
            self._queue.put(None)
            self._worker.join(timeout=5)
