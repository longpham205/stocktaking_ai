"""Test API web POS qua HTTP thật (server chạy luồng nền, cổng ngẫu nhiên, bộ suy luận GIẢ, không cần model)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np

from backend.catalog import DbCatalog
from backend.config import Settings
from backend.inference import Detection, FakeExecutor, InferenceOutput
from backend.server import PosServer
from backend.service import App

PRODUCTS = [
    {"product_id": "1", "product_name": "Xit Chong Nang UV", "barcode": "8931000001372"},
    {"product_id": "2", "product_name": "But Chi Chan May (BR641)", "barcode": ""},
    {"product_id": "3", "product_name": "Phấn Má Hồng", "barcode": ""},
    {"product_id": "7", "product_name": "Hop Ngoai ABA", "barcode": ""},
    {"product_id": "8", "product_name": "Hop Ngoai ABC", "barcode": ""},
    {"product_id": "12", "product_name": "Kem Duong", "barcode": "8931000009999"},
]
STAFF_PW, ADMIN_PW = "staffpass123", "adminpass123"


def _seed_catalog(db_path: Path) -> None:
    """Catalog trong app.db (như sau migrate): PRODUCTS + cặp dễ nhầm 7/8 bắt buộc OCR + màu BE203."""
    from src.catalog.db import ColorReference, Product, ProductEvidence, Session, create_all, make_engine

    eng = make_engine(db_path)
    try:
        create_all(eng)
        with Session(eng) as s:
            if s.get(Product, "1") is not None:
                return
            for p in PRODUCTS:
                s.add(Product(product_id=p["product_id"], product_name=p["product_name"], barcode=p["barcode"] or None,
                              gallery_folder=f"f{p['product_id']}"))
            s.flush()
            for pid, typ, val in [("7", "force_evidence", ["ocr"]), ("7", "ocr_keywords", ["ABA"]), ("7", "confusable_with", ["8"]),
                                  ("8", "force_evidence", ["ocr"]), ("8", "ocr_keywords", ["ABC"]), ("8", "confusable_with", ["7"]),
                                  ("2", "color_code", "BR641")]:
                s.add(ProductEvidence(product_id=pid, evidence_type=typ, value_json=json.dumps(val)))
            s.add(ColorReference(color_code="BE203", r=199, g=161, b=148, hex="#C7A194", source="seed"))
            s.commit()
    finally:
        eng.dispose()


def _settings(tmp: Path, **over) -> Settings:
    _seed_catalog(over.get("db_path", tmp / "db" / "app.db"))
    front = tmp / "frontend"
    front.mkdir(exist_ok=True)
    (front / "index.html").write_text("<html>POS</html>", encoding="utf-8")
    (front / "app.js").write_text("//js", encoding="utf-8")
    base = dict(host="127.0.0.1", port=0, max_upload_bytes=2 * 1024 * 1024, allowed_origins=[],
                token_ttl_seconds=3600, max_failed_attempts=3, lockout_seconds=60, queue_max=5,
                job_timeout_seconds=60, idempotency_window_seconds=5, db_path=tmp / "db" / "app.db",
                media_root=tmp / "media", media_url_ttl=600, media_retention_days=30, thumb_width=120,
                allow_checkout_without_price=False, tz_offset_hours=7, pipeline_config=tmp / "cfg.yaml",
                catalog_db_path=tmp / "db" / "app.db", ocr_min_length=3, frontend_dir=front,
                seed_prices_path=None, jwt_secret="test-secret-xyz", seed_staff_password=STAFF_PW,
                seed_admin_password=ADMIN_PW)
    base.update(over)
    return Settings(**base)


def _jpeg(color=(40, 120, 200)) -> bytes:
    img = np.full((240, 320, 3), 90, np.uint8)
    cv2.rectangle(img, (30, 30), (150, 200), color, -1)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _dets_default(_path: str) -> InferenceOutput:
    d = lambda pid, st, box=(20, 20, 160, 210): Detection(pid, box, st)  # noqa: E731
    return InferenceOutput([d("7", "accepted"), d("7", "accepted"), d("8", "accepted"), d("7", "uncertain")], 123.0)


class Client:
    def __init__(self, base: str) -> None:
        self.base, self.token = base, None

    def call(self, method, path, body=None, token="_self", raw=None, headers=None):
        url = self.base + path
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        tok = self.token if token == "_self" else token
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        if raw is not None:
            req.add_header("Content-Type", "image/jpeg")
        elif data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                payload, status, ctype = r.read(), r.status, r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            payload, status, ctype = e.read(), e.code, e.headers.get("Content-Type", "")
        if "json" in ctype:
            return status, json.loads(payload)
        return status, payload

    def login(self, user, pw, set_token=True):
        st, body = self.call("POST", "/api/auth/login", {"username": user, "password": pw}, token=None)
        if st == 200 and set_token:
            self.token = body["token"]
        return st, body


@contextmanager
def running(tmp: Path, executor=None, **over):
    settings = _settings(tmp, **over)
    catalog = DbCatalog(settings.catalog_db_path)
    app = App(settings, catalog, executor or FakeExecutor([], fn=_dets_default))
    httpd = PosServer(("127.0.0.1", 0), app)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield Client(f"http://127.0.0.1:{httpd.server_address[1]}"), app
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()


def _capture(c: Client, oid: int, key=None, img=None):
    h = {"Idempotency-Key": key} if key else None
    return c.call("POST", f"/api/orders/{oid}/captures", raw=img or _jpeg(), headers=h)


def _wait_job(c: Client, jid: str, want=("done", "error"), timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, body = c.call("GET", f"/api/jobs/{jid}")
        assert st == 200, body
        if body["status"] in want:
            return body
        time.sleep(0.05)
    raise AssertionError("job không xong kịp")


def _order_with_items(c: Client):
    st, order = c.call("POST", "/api/orders")
    assert st == 200
    st, job = _capture(c, order["id"])
    assert st == 202, job
    return order["id"], _wait_job(c, job["job_id"])["order"]


# --------------------------------------------------------------------------- xác thực
def test_health_public_and_others_need_token(tmp_path):
    with running(tmp_path) as (c, _):
        assert c.call("GET", "/api/health", token=None) == (200, {"status": "ready"})
        st, body = c.call("GET", "/api/me", token=None)
        assert st == 401 and body["error"]["code"] == "AUTH_INVALID"
        st, body = c.call("GET", "/api/me", token="rác.rác")
        assert st == 401 and body["error"]["code"] == "AUTH_INVALID"


def test_login_generic_error_and_lockout(tmp_path):
    with running(tmp_path) as (c, _):
        st, b1 = c.login("staff", "sai-mat-khau")
        st2, b2 = c.login("khong-ton-tai", "x")
        assert st == st2 == 401 and b1["error"]["message"] == b2["error"]["message"]  # không lộ tài khoản có tồn tại
        c.login("staff", "sai")
        c.login("staff", "sai")
        st, body = c.login("staff", STAFF_PW)  # đã bị khoá dù đúng mật khẩu
        assert st == 429 and body["error"]["code"] == "RATE_LIMITED" and body["error"]["retry_after"] > 0


def test_second_login_kicks_first_session(tmp_path):
    with running(tmp_path) as (c1, app):
        c2 = Client(c1.base)
        assert c1.login("staff", STAFF_PW)[0] == 200
        assert c1.call("GET", "/api/me")[0] == 200
        assert c2.login("staff", STAFF_PW)[0] == 200
        st, body = c1.call("GET", "/api/me")
        assert st == 401 and body["error"]["code"] == "SESSION_INVALID"
        assert c2.call("GET", "/api/me")[0] == 200


def test_logout_idempotent_and_invalidates(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        tok = c.token
        assert c.call("POST", "/api/auth/logout")[0] == 200
        assert c.call("POST", "/api/auth/logout")[0] == 200  # lần hai vẫn OK
        assert c.call("POST", "/api/auth/logout", token=None)[0] == 200  # không token vẫn OK
        assert c.call("GET", "/api/me", token=tok)[1]["error"]["code"] == "SESSION_INVALID"


def test_expired_token(tmp_path):
    with running(tmp_path, token_ttl_seconds=-5) as (c, _):
        c.login("staff", STAFF_PW)
        st, body = c.call("GET", "/api/me")
        assert st == 401 and body["error"]["code"] == "AUTH_EXPIRED"


def test_staff_forbidden_on_admin_routes(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        for method, path in (("GET", "/api/admin/reports"), ("GET", "/api/admin/products"),
                             ("GET", "/api/admin/users"), ("GET", "/api/admin/orders")):
            st, body = c.call(method, path)
            assert st == 403 and body["error"]["code"] == "FORBIDDEN", path
        assert c.call("PATCH", "/api/admin/products/1", {"price": 1})[0] == 403
        assert c.call("PATCH", "/api/admin/settings", {"similarity_threshold": 0.5})[0] == 403


# --------------------------------------------------------------------------- chụp và giỏ hàng
def test_capture_flow_merge_rule_and_thumbnails(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = _order_with_items(c)
        rows = [(i["product_id"], i["quantity"], i["flagged"]) for i in order["items"]]
        # accepted cùng SKU gộp một dòng; uncertain giữ riêng dù trùng SKU 7
        assert rows == [("7", 2, False), ("8", 1, False), ("7", 1, True)]
        assert order["flagged_count"] == 1 and order["item_count"] == 4
        url = order["items"][0]["thumbnail_url"]
        assert url and url.startswith("/api/media/")
        st, img = c.call("GET", url, token=None)  # URL ký nên không cần token
        assert st == 200 and img[:2] == b"\xff\xd8"
        assert c.call("GET", url[:-3] + "AAA", token=None)[0] == 403  # sai chữ ký
        assert c.call("GET", "/api/media/1/x.jpg?exp=1&sig=abc", token=None)[0] == 403
        assert c.call("GET", "/api/media/../../etc/passwd?exp=9999999999&sig=x", token=None)[0] in (403, 404)


def test_capture_more_accumulates_on_confirmed_line(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        oid, order = _order_with_items(c)
        _, job = _capture(c, oid)
        order2 = _wait_job(c, job["job_id"])["order"]
        by = {(i["product_id"], i["flagged"]): i["quantity"] for i in order2["items"]}
        assert by[("7", False)] == 4 and by[("8", False)] == 2  # cộng dồn
        assert sum(1 for i in order2["items"] if i["flagged"]) == 2  # uncertain: mỗi lần thêm một dòng riêng


def test_idempotency_key_returns_same_job(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        s1, j1 = _capture(c, order["id"], key="abc-123")
        s2, j2 = _capture(c, order["id"], key="abc-123")
        assert s1 == 202 and s2 == 202 and j1["job_id"] == j2["job_id"] and j2["duplicate"] is True
        _wait_job(c, j1["job_id"])
        _, o = c.call("GET", f"/api/orders/{order['id']}")
        assert o["item_count"] == 4  # chỉ xử lý một lần


def test_bad_image_and_too_large(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        st, body = c.call("POST", f"/api/orders/{order['id']}/captures", raw=b"day khong phai anh" * 50)
        assert st == 400 and body["error"]["code"] == "IMAGE_DECODE_ERROR"
        st, body = c.call("POST", f"/api/orders/{order['id']}/captures", raw=b"\x00" * (2 * 1024 * 1024 + 10))
        assert st == 413 and body["error"]["code"] == "IMAGE_TOO_LARGE"
        assert c.call("GET", "/api/me")[0] == 200  # kết nối/server vẫn sống sau lỗi


def test_pipeline_error_reported_and_logged(tmp_path):
    def boom(_p):
        raise RuntimeError("CUDA out of memory. Tried to allocate")
    with running(tmp_path, executor=FakeExecutor([], fn=boom)) as (c, app):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        _, job = _capture(c, order["id"])
        body = _wait_job(c, job["job_id"])
        assert body["status"] == "error" and body["error"]["code"] == "GPU_OOM"
        with app.db.read() as conn:
            assert conn.execute("SELECT status FROM captures").fetchone()["status"] == "error"
        assert c.call("GET", f"/api/orders/{order['id']}")[1]["items"] == []


def test_queue_full(tmp_path):
    gate = threading.Event()

    def slow(_p):
        gate.wait(10)
        return InferenceOutput([], 1.0)
    with running(tmp_path, executor=FakeExecutor([], fn=slow), queue_max=1) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        oid = order["id"]
        _, j1 = _capture(c, oid)
        _wait_job(c, j1["job_id"], want=("processing",))
        assert _capture(c, oid)[0] == 202  # nằm trong hàng đợi (size 1)
        st, body = _capture(c, oid)
        assert st == 503 and body["error"]["code"] == "QUEUE_FULL"
        gate.set()


def test_other_users_order_and_job_are_hidden(tmp_path):
    with running(tmp_path) as (c, app):
        c.login("staff", STAFF_PW)
        oid, _ = _order_with_items(c)
        _, job = _capture(c, oid)
        admin_c = Client(c.base)
        admin_c.login("admin", ADMIN_PW)
        app.admin_create_user(app.authenticate(admin_c.token), {"username": "staff2", "password": "password222"})
        other = Client(c.base)
        other.login("staff2", "password222")
        assert other.call("GET", f"/api/orders/{oid}")[0] == 404
        assert other.call("POST", f"/api/orders/{oid}/void")[0] == 404
        assert other.call("GET", f"/api/jobs/{job['job_id']}")[0] == 404
        assert admin_c.call("GET", f"/api/orders/{oid}")[0] == 200  # admin xem được


# --------------------------------------------------------------------------- sửa dòng, giá, thanh toán
def test_edit_items_confirm_change_product_merge(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        oid, order = _order_with_items(c)
        flagged = next(i for i in order["items"] if i["flagged"])
        st, o = c.call("PATCH", f"/api/orders/{oid}/items/{flagged['id']}", {"product_id": "8"})  # đổi sang SKU 8
        assert st == 200
        by = {(i["product_id"], i["flagged"]): i["quantity"] for i in o["items"]}
        assert by == {("7", False): 2, ("8", False): 2}  # gộp vào dòng 8 đã xác nhận, hết cờ vàng
        it7 = next(i for i in o["items"] if i["product_id"] == "7")
        assert c.call("PATCH", f"/api/orders/{oid}/items/{it7['id']}", {"quantity": 0})[0] == 422
        assert c.call("PATCH", f"/api/orders/{oid}/items/{it7['id']}", {"quantity": 5000})[0] == 422
        assert c.call("PATCH", f"/api/orders/{oid}/items/{it7['id']}", {"quantity": "3"})[0] == 422
        assert c.call("PATCH", f"/api/orders/{oid}/items/{it7['id']}", {"product_id": "999"})[0] == 422
        st, o = c.call("PATCH", f"/api/orders/{oid}/items/{it7['id']}", {"quantity": 3})
        assert next(i for i in o["items"] if i["id"] == it7["id"])["quantity"] == 3
        assert c.call("DELETE", f"/api/orders/{oid}/items/{it7['id']}")[0] == 200
        assert c.call("DELETE", f"/api/orders/{oid}/items/{it7['id']}")[0] == 404


def test_confirm_flagged_without_changing_product(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        oid, order = _order_with_items(c)
        flagged = next(i for i in order["items"] if i["flagged"])
        _, o = c.call("PATCH", f"/api/orders/{oid}/items/{flagged['id']}", {"confirm": True})
        assert o["flagged_count"] == 0


def test_add_item_manually_and_search_barcode(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        st, found = c.call("GET", "/api/catalog/products?barcode=8931000009999")
        assert st == 200 and [p["id"] for p in found["items"]] == ["12"]
        assert c.call("GET", "/api/catalog/products?barcode=0000")[1]["items"] == []
        # tìm không phân biệt dấu tiếng Việt
        assert [p["id"] for p in c.call("GET", "/api/catalog/products?search=phan%20ma%20hong")[1]["items"]] == ["3"]
        c.call("POST", f"/api/orders/{order['id']}/items", {"product_id": "12", "quantity": 2})
        st, o = c.call("POST", f"/api/orders/{order['id']}/items", {"product_id": "12"})
        assert st == 200 and o["items"][0]["quantity"] == 3 and len(o["items"]) == 1
        assert c.call("POST", f"/api/orders/{order['id']}/items", {"product_id": "nope"})[0] == 422


def _admin(c: Client):
    a = Client(c.base)
    assert a.login("admin", ADMIN_PW)[0] == 200
    return a


def test_checkout_blocked_without_price_then_manual_price_then_pay(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        oid, order = _order_with_items(c)
        for it in order["items"]:
            assert it["price_missing"] is True  # chưa có giá nào
        st, body = c.call("POST", f"/api/orders/{oid}/checkout", {"method": "cash", "cash_given": 999999})
        assert st == 409 and body["error"]["code"] == "PRICE_MISSING_BLOCKED"
        admin = _admin(c)
        assert admin.call("PATCH", "/api/admin/products/7", {"price": 10000})[0] == 200
        assert admin.call("PATCH", "/api/admin/products/8", {"price": 25000})[0] == 200
        for it in c.call("GET", f"/api/orders/{oid}")[1]["items"]:  # dòng flagged (7) cũng có giá 7
            assert it["price_missing"] is False
        # thêm SKU chưa có giá, nhập giá tay
        _, o = c.call("POST", f"/api/orders/{oid}/items", {"product_id": "12"})
        assert o["missing_price_count"] == 1
        iid = next(i["id"] for i in o["items"] if i["product_id"] == "12")
        _, o = c.call("PATCH", f"/api/orders/{oid}/items/{iid}", {"manual_price": 5000})
        assert o["missing_price_count"] == 0
        assert o["total"] == 10000 * 2 + 25000 + 10000 + 5000  # 7x2 + 8 + 7(flagged) + 12
        assert c.call("POST", f"/api/orders/{oid}/checkout", {"method": "cash", "cash_given": 1000})[0] == 422  # thiếu tiền
        assert c.call("POST", f"/api/orders/{oid}/checkout", {"method": "bitcoin"})[0] == 422
        st, paid = c.call("POST", f"/api/orders/{oid}/checkout", {"method": "cash", "cash_given": 100000})
        assert st == 200 and paid["status"] == "paid" and paid["total"] == 60000 and paid["change_given"] == 40000
        # sau khi thanh toán: đơn giá bị đóng băng, admin đổi giá không làm đổi hoá đơn cũ
        admin.call("PATCH", "/api/admin/products/7", {"price": 99999})
        assert c.call("GET", f"/api/orders/{oid}")[1]["total"] == 60000
        assert c.call("PATCH", f"/api/orders/{oid}/items/{iid}", {"quantity": 2})[1]["error"]["code"] == "ORDER_NOT_OPEN"
        assert c.call("POST", f"/api/orders/{oid}/checkout", {"method": "qr"})[1]["error"]["code"] == "ORDER_NOT_OPEN"
        assert c.call("POST", f"/api/orders/{oid}/void")[0] == 403  # staff không huỷ được đơn đã thanh toán
        assert admin.call("POST", f"/api/orders/{oid}/void")[1]["status"] == "void"


def test_allow_checkout_without_price_setting(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        oid, _ = _order_with_items(c)
        _admin_c = _admin(c)
        assert _admin_c.call("PATCH", "/api/admin/settings", {"allow_checkout_without_price": True})[0] == 200
        assert c.call("GET", "/api/settings")[1]["allow_checkout_without_price"] is True
        st, paid = c.call("POST", f"/api/orders/{oid}/checkout", {"method": "qr"})
        assert st == 200 and paid["total"] == 0  # thiếu giá tính 0đ khi được cho phép


def test_checkout_empty_order_rejected_and_qr_ok(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        assert c.call("POST", f"/api/orders/{order['id']}/checkout", {"method": "qr"})[0] == 422
        _admin(c).call("PATCH", "/api/admin/products/12", {"price": 7000})
        c.call("POST", f"/api/orders/{order['id']}/items", {"product_id": "12"})
        st, paid = c.call("POST", f"/api/orders/{order['id']}/checkout", {"method": "qr"})
        assert st == 200 and paid["change_given"] is None and paid["payment_method"] == "qr"


def test_history_and_void_open_order(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        _admin(c).call("PATCH", "/api/admin/products/12", {"price": 7000})
        _, o1 = c.call("POST", "/api/orders")
        c.call("POST", f"/api/orders/{o1['id']}/items", {"product_id": "12"})
        c.call("POST", f"/api/orders/{o1['id']}/checkout", {"method": "qr"})
        _, o2 = c.call("POST", "/api/orders")
        assert c.call("POST", f"/api/orders/{o2['id']}/void")[1]["status"] == "void"
        st, h = c.call("GET", "/api/history?range=today")
        assert st == 200 and [x["id"] for x in h["items"]] == [o2["id"], o1["id"]]
        assert h["items"][1]["total"] == 7000 and h["items"][1]["item_count"] == 1
        assert c.call("GET", "/api/history?range=lung-tung")[0] == 422


# --------------------------------------------------------------------------- admin
def test_admin_products_update_validation_and_changelog(tmp_path):
    with running(tmp_path) as (c, app):
        a = _admin(c)
        st, body = a.call("GET", "/api/admin/products")
        assert body["total"] == 6 and body["missing_price"] == 6
        assert a.call("PATCH", "/api/admin/products/2", {"price": 15000, "barcode": "89310000777"})[0] == 200
        assert a.call("PATCH", "/api/admin/products/3", {"barcode": "89310000777"})[1]["error"]["code"] == "BARCODE_DUPLICATE"
        assert a.call("PATCH", "/api/admin/products/3", {"barcode": "8931000001372"})[0] == 409  # trùng barcode có sẵn trong catalog
        assert a.call("PATCH", "/api/admin/products/3", {"barcode": "a b!"})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3", {"price": -5})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3", {"price": 1.5})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3", {"foo": "x"})[0] == 422  # chỉ sửa price/barcode/name
        assert a.call("PATCH", "/api/admin/products/3", {"name": "  "})[0] == 422
        assert a.call("PATCH", "/api/admin/products/404", {"price": 1})[0] == 404
        # xoá giá về null, xoá barcode
        a.call("PATCH", "/api/admin/products/2", {"price": None, "barcode": ""})
        _, one = a.call("GET", "/api/admin/products?search=chan%20may")
        assert one["items"][0]["price"] is None and one["items"][0]["barcode"] == ""
        assert a.call("GET", "/api/admin/products?filter=missing_barcode")[1]["missing_barcode"] == 4
        with app.db.read() as conn:  # nguyên tắc 4: mọi thay đổi dữ liệu nghiệp vụ đều có log
            fields = [r["field_name"] for r in conn.execute("SELECT field_name FROM change_log WHERE table_name='product'")]
        assert fields.count("price") == 2 and fields.count("barcode") == 2


def test_admin_reports_and_staff_management(tmp_path):
    with running(tmp_path) as (c, _):
        a = _admin(c)
        st, rep = a.call("GET", "/api/admin/reports")
        assert st == 200 and rep["orders_today"] == 0 and rep["products_total"] == 6 and rep["active_shifts"] == 1
        st, u = a.call("POST", "/api/admin/users", {"username": "Thu.Ngan1", "password": "matkhau123", "role": "staff"})
        assert st == 200 and u["username"] == "thu.ngan1"
        assert a.call("POST", "/api/admin/users", {"username": "thu.ngan1", "password": "matkhau123"})[0] == 409
        assert a.call("POST", "/api/admin/users", {"username": "x", "password": "matkhau123"})[0] == 422
        assert a.call("POST", "/api/admin/users", {"username": "okuser", "password": "ngan"})[0] == 422
        assert a.call("POST", "/api/admin/users", {"username": "okuser", "password": "matkhau123", "role": "boss"})[0] == 422
        s = Client(c.base)
        assert s.login("thu.ngan1", "matkhau123")[0] == 200
        assert a.call("PATCH", f"/api/admin/users/{u['id']}", {"is_active": False})[0] == 200  # khoá => văng phiên
        assert s.call("GET", "/api/me")[0] == 401
        assert s.login("thu.ngan1", "matkhau123")[0] == 401
        me = a.call("GET", "/api/me")[1]["user"]["id"]
        assert a.call("PATCH", f"/api/admin/users/{me}", {"is_active": False})[0] == 409  # không tự khoá mình


def test_seed_prices_loaded_once_and_never_overwritten(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"1": 12000, "2": None, "7": 5000, "999": 1}), encoding="utf-8")
    with running(tmp_path, seed_prices_path=seed) as (c, app):
        with app.db.read() as conn:
            got = {r["product_id"]: r["price"] for r in conn.execute("SELECT * FROM product_prices")}
        assert got == {"1": 12000, "7": 5000}  # bỏ null và SKU không có trong catalog
        _admin(c).call("PATCH", "/api/admin/products/1", {"price": 13000})
    with running(tmp_path, seed_prices_path=seed) as (c, app):  # khởi động lại: không ghi đè giá admin đã sửa
        with app.db.read() as conn:
            assert conn.execute("SELECT price FROM product_prices WHERE product_id='1'").fetchone()["price"] == 13000


def test_similarity_threshold_reaches_executor(tmp_path):
    seen = []

    class Spy(FakeExecutor):
        def infer(self, image_path, similarity_threshold=None, min_confidence_accept=None):
            seen.append(similarity_threshold)
            return InferenceOutput([], 1.0)
    with running(tmp_path, executor=Spy([])) as (c, _):
        c.login("staff", STAFF_PW)
        _, o = c.call("POST", "/api/orders")
        _wait_job(c, _capture(c, o["id"])[1]["job_id"])
        a = _admin(c)
        assert a.call("PATCH", "/api/admin/settings", {"similarity_threshold": 0.7})[0] == 200
        assert a.call("PATCH", "/api/admin/settings", {"similarity_threshold": 5})[0] == 422
        _wait_job(c, _capture(c, o["id"])[1]["job_id"])
        assert seen == [None, 0.7]  # đổi ngưỡng có hiệu lực ngay ở lần chụp kế, không cần restart


# --------------------------------------------------------------------------- tĩnh và bảo mật
def test_static_and_traversal_and_unknown_routes(tmp_path):
    (tmp_path / "secret.txt").write_text("BI MAT", encoding="utf-8")
    with running(tmp_path) as (c, _):
        st, body = c.call("GET", "/", token=None)
        assert st == 200 and b"POS" in body
        assert c.call("GET", "/app.js", token=None)[0] == 200
        assert c.call("GET", "/dang-nhap", token=None)[0] == 200  # định tuyến phía client -> index
        assert c.call("GET", "/khong-co.css", token=None)[0] == 404
        st, body = c.call("GET", "/..%2Fsecret.txt", token=None)
        assert b"BI MAT" not in (body if isinstance(body, bytes) else json.dumps(body).encode())
        assert c.call("GET", "/api/khong-co", token=None)[0] in (401, 404)
        assert c.call("PUT", "/api/health", token=None)[0] in (404, 405)
        assert c.call("GET", "/docs", token=None)[0] in (200, 404) and b"swagger" not in c.call("GET", "/docs", token=None)[1].lower()


def test_bad_json_bodies(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        st, body = c.call("POST", "/api/orders", raw=None)
        _, order = c.call("POST", "/api/orders")
        req = urllib.request.Request(c.base + f"/api/orders/{order['id']}/items", data=b"{khong phai json", method="POST")
        req.add_header("Authorization", f"Bearer {c.token}")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("phải lỗi")
        except urllib.error.HTTPError as e:
            assert e.code == 400 and json.loads(e.read())["error"]["code"] == "VALIDATION_ERROR"
        assert c.call("POST", f"/api/orders/{order['id']}/items", [1, 2])[0] == 422  # không phải object


# --------------------------------------------------------------------------- cầu nối pipeline thật (dùng module giả)
def test_evidence_with_numpy_values_does_not_break_capture(tmp_path):
    def fn(_p):
        ev = {"color": {"delta_e": np.float32(3.25), "vec": np.array([1, 2])}, "ocr": {"text": "ABA"}}
        return InferenceOutput([Detection("7", (20, 20, 160, 210), "uncertain", ev)], 10.0)
    with running(tmp_path, executor=FakeExecutor([], fn=fn)) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        body = _wait_job(c, _capture(c, order["id"])[1]["job_id"])
        assert body["status"] == "done", body
        ev = body["order"]["items"][0]["evidence"]
        assert ev["color"]["delta_e"] == 3.25 and ev["color"]["vec"] == [1, 2] and ev["ocr"]["text"] == "ABA"


def test_local_executor_maps_pipeline_result(tmp_path):
    """LocalExecutor chỉ được gọi InferenceRunner.run_single và chuyển InventoryItem -> Detection (bỏ 'rejected')."""
    import sys
    import types
    from types import SimpleNamespace as NS

    calls = {}

    class FakeRunner:
        def __init__(self, cfg):
            calls["cfg"] = cfg

        def run_single(self, path, similarity_threshold=None):
            calls["args"] = (path, similarity_threshold)
            box = lambda a: NS(x1=a, y1=a + 1, x2=a + 50, y2=a + 60)  # noqa: E731
            return NS(items=[NS(product_id=5, bbox=box(10), status="accepted", plugin_evidence={"barcode": {"hit": 1}}),
                             NS(product_id="6", bbox=box(70), status="uncertain", plugin_evidence=None),
                             NS(product_id="9", bbox=box(0), status="rejected", plugin_evidence={})])
    names = ["src", "src.core", "src.core.config", "src.inference", "src.inference.infer"]
    saved = {n: sys.modules.get(n) for n in names}
    try:
        for n in names:
            sys.modules[n] = types.ModuleType(n)
        sys.modules["src.core.config"].build_config = lambda p, overrides=None: ("cfg", p)
        sys.modules["src.inference.infer"].InferenceRunner = FakeRunner
        from backend.inference import LocalExecutor
        ex = LocalExecutor(tmp_path / "c.yaml")
        out = ex.infer("/x/a.jpg", 0.66)
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
    assert calls["args"] == ("/x/a.jpg", 0.66) and calls["cfg"][0] == "cfg"
    assert [(d.product_id, d.status, d.bbox) for d in out.detections] == [
        ("5", "accepted", (10, 11, 60, 70)), ("6", "uncertain", (70, 71, 120, 130))]  # id ép về chuỗi, bỏ rejected
    assert out.detections[0].plugin_evidence == {"barcode": {"hit": 1}} and out.detections[1].plugin_evidence == {}
    assert out.processing_time_ms >= 0


# --------------------------------------------------------------------------- P0 sau web MVP: timeout cứng, chồng lấp, min_confidence, sao lưu
def test_overlap_warning_is_returned_to_client(tmp_path):
    flag = {"v": True}
    fn = lambda _p: InferenceOutput([Detection("7", (20, 20, 160, 210), "accepted")], 5.0, has_overlap=flag["v"])  # noqa: E731
    with running(tmp_path, executor=FakeExecutor([], fn=fn)) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        assert _wait_job(c, _capture(c, order["id"])[1]["job_id"])["warnings"] == [{"type": "overlap_detected"}]
        flag["v"] = False
        assert _wait_job(c, _capture(c, order["id"])[1]["job_id"])["warnings"] == []


def test_hard_timeout_returns_error_quickly_then_recovers(tmp_path):
    calls = {"n": 0}

    def fn(_p):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(1.5)  # lượt đầu "treo"
        return InferenceOutput([Detection("7", (20, 20, 160, 210), "accepted")], 5.0)
    with running(tmp_path, executor=FakeExecutor([], fn=fn), job_timeout_seconds=0.4) as (c, app):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        t0 = time.time()
        body = _wait_job(c, _capture(c, order["id"])[1]["job_id"])
        assert body["status"] == "error" and body["error"]["code"] == "PIPELINE_TIMEOUT"
        assert time.time() - t0 < 1.2  # báo lỗi ngay, không chờ hết 1.5s
        with app.db.read() as conn:
            assert conn.execute("SELECT status FROM captures").fetchone()["status"] == "timeout"
        time.sleep(1.4)  # luồng treo chạy xong -> hệ thống tự hồi phục
        body = _wait_job(c, _capture(c, order["id"])[1]["job_id"], timeout=8)
        assert body["status"] == "done" and body["order"]["item_count"] == 1


def test_min_confidence_accept_setting_reaches_executor_and_is_validated(tmp_path):
    seen = []

    class Spy(FakeExecutor):
        def infer(self, image_path, similarity_threshold=None, min_confidence_accept=None):
            seen.append((similarity_threshold, min_confidence_accept))
            return InferenceOutput([], 1.0)
    with running(tmp_path, executor=Spy([])) as (c, _):
        c.login("staff", STAFF_PW)
        _, o = c.call("POST", "/api/orders")
        _wait_job(c, _capture(c, o["id"])[1]["job_id"])
        a = _admin(c)
        assert a.call("PATCH", "/api/admin/settings", {"min_confidence_accept": 0.1})[0] == 422
        assert a.call("PATCH", "/api/admin/settings", {"min_confidence_accept": 1.5})[0] == 422
        assert a.call("PATCH", "/api/admin/settings", {"min_confidence_accept": 0.55, "similarity_threshold": 0.7})[0] == 200
        _wait_job(c, _capture(c, o["id"])[1]["job_id"])
        assert a.call("PATCH", "/api/admin/settings", {"min_confidence_accept": None})[0] == 200
        _wait_job(c, _capture(c, o["id"])[1]["job_id"])
        assert seen == [(None, None), (0.7, 0.55), (0.7, None)]


def test_db_is_backed_up_on_restart_and_rotated(tmp_path):
    def start():
        s = _settings(tmp_path)
        App(s, DbCatalog(s.catalog_db_path), FakeExecutor([]), start_worker=False).close()
    start()
    backups = tmp_path / "backups"
    # DB đã có catalog (migrate chạy trước web) -> sao lưu ngay từ lần khởi động đầu
    assert len(list(backups.glob("app-*.db"))) == 1
    start()
    assert len(list(backups.glob("app-*.db"))) == 2
    for _ in range(9):
        start()
    files = sorted(backups.glob("app-*.db"))
    assert len(files) == 7  # giữ 7 bản mới nhất
    import sqlite3
    conn = sqlite3.connect(str(files[-1]))
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2  # bản sao lưu hợp lệ, có dữ liệu
    conn.close()


# --------------------------------------------------------------------------- web đợt 2: khôi phục đơn, báo cáo, nhật ký, cờ cài đặt
def test_open_order_resume_and_empty_order_reuse(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        assert c.call("GET", "/api/orders/open")[1] == {"order": None}
        _, o1 = c.call("POST", "/api/orders")
        _, again = c.call("POST", "/api/orders")
        assert again["id"] == o1["id"]  # đơn rỗng được dùng lại, không sinh đơn mồ côi
        c.call("POST", f"/api/orders/{o1['id']}/items", {"product_id": "12", "quantity": 2})
        st, cur = c.call("GET", "/api/orders/open")
        assert st == 200 and cur["order"]["id"] == o1["id"] and cur["order"]["item_count"] == 2
        _, o2 = c.call("POST", "/api/orders")  # o1 đã có hàng -> đơn mới thật sự
        assert o2["id"] != o1["id"] and c.call("GET", "/api/orders/open")[1]["order"]["id"] == o2["id"]
        # đăng nhập lại (ca mới): đơn dở dang vẫn còn để khôi phục
        c2 = Client(c.base)
        c2.login("staff", STAFF_PW)
        assert c2.call("GET", "/api/orders/open")[1]["order"]["id"] == o2["id"]
        assert c2.call("POST", "/api/orders")[1]["id"] == o2["id"]  # đơn rỗng được gắn sang ca mới
        # người khác không thấy đơn mở của staff
        a = _admin(c)
        assert a.call("GET", "/api/orders/open")[1] == {"order": None}


def test_open_order_none_after_payment_and_void(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        _admin(c).call("PATCH", "/api/admin/products/12", {"price": 5000})
        _, o = c.call("POST", "/api/orders")
        c.call("POST", f"/api/orders/{o['id']}/items", {"product_id": "12"})
        c.call("POST", f"/api/orders/{o['id']}/checkout", {"method": "qr"})
        assert c.call("GET", "/api/orders/open")[1] == {"order": None}


def _pay(c: Client, admin: Client, items: dict[str, int], price: dict[str, int]):
    for pid, p in price.items():
        admin.call("PATCH", f"/api/admin/products/{pid}", {"price": p})
    _, o = c.call("POST", "/api/orders")
    for pid, q in items.items():
        c.call("POST", f"/api/orders/{o['id']}/items", {"product_id": pid, "quantity": q})
    st, paid = c.call("POST", f"/api/orders/{o['id']}/checkout", {"method": "qr"})
    assert st == 200
    return paid


def test_reports_range_daily_and_top_products(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        a = _admin(c)
        _pay(c, a, {"12": 3, "7": 1}, {"12": 10000, "7": 2000})  # 32.000
        _pay(c, a, {"12": 1}, {})  # 10.000
        _pay(c, a, {"8": 5}, {"8": 1000})  # 5.000
        st, rep = a.call("GET", "/api/admin/reports?range=7d")
        assert st == 200 and rep["range"] == "7d" and len(rep["daily"]) == 7
        assert rep["daily"][-1]["orders"] == 3 and rep["daily"][-1]["revenue"] == 47000
        assert all(d["orders"] == 0 for d in rep["daily"][:-1])  # ngày không có đơn vẫn có mặt (biểu đồ liên tục)
        assert rep["range_orders"] == 3 and rep["range_revenue"] == 47000
        tops = {t["product_id"]: t for t in rep["top_products"]}
        assert tops["12"]["quantity"] == 4 and tops["12"]["revenue"] == 40000 and tops["8"]["quantity"] == 5
        assert [t["product_id"] for t in rep["top_products"]][:2] == ["8", "12"]  # xếp theo số lượng giảm dần
        assert rep["orders_today"] == 3 and rep["revenue_today"] == 47000  # KPI hôm nay giữ nguyên ý nghĩa
        assert len(a.call("GET", "/api/admin/reports?range=30d")[1]["daily"]) == 30
        assert len(a.call("GET", "/api/admin/reports")[1]["daily"]) == 1
        assert a.call("GET", "/api/admin/reports?range=1y")[0] == 422
        assert c.call("GET", "/api/admin/reports?range=7d")[0] == 403


def test_void_removes_order_from_reports(tmp_path):
    with running(tmp_path) as (c, _):
        c.login("staff", STAFF_PW)
        a = _admin(c)
        paid = _pay(c, a, {"12": 2}, {"12": 10000})
        assert a.call("GET", "/api/admin/reports?range=7d")[1]["range_revenue"] == 20000
        a.call("POST", f"/api/orders/{paid['id']}/void")
        rep = a.call("GET", "/api/admin/reports?range=7d")[1]
        assert rep["range_revenue"] == 0 and rep["top_products"] == []


def test_change_log_listing_and_revert_chain(tmp_path):
    with running(tmp_path) as (c, _):
        a = _admin(c)
        a.call("PATCH", "/api/admin/products/2", {"price": 1000})
        a.call("PATCH", "/api/admin/products/2", {"price": 2000, "barcode": "89310000555"})
        st, log = a.call("GET", "/api/admin/change-log?table=product&record=2")
        items = log["items"]
        assert st == 200 and [i["field"] for i in items] == ["barcode", "price", "price"]  # mới nhất trước
        assert items[0]["by"] == "admin" and items[0]["name"].startswith("But Chi")
        price_new, price_old = items[1], items[2]
        assert (price_new["old"], price_new["new"]) == ("1000", "2000")
        # hoàn tác thay đổi giá mới nhất: 2000 -> 1000, việc hoàn tác cũng được ghi log
        assert a.call("POST", f"/api/admin/change-log/{price_new['id']}/revert")[0] == 200
        assert a.call("GET", "/api/admin/products?search=chan%20may")[1]["items"][0]["price"] == 1000
        assert len(a.call("GET", "/api/admin/change-log?table=product&record=2")[1]["items"]) == 4
        # thay đổi cũ "None -> 1000": giá hiện tại đang 1000 nên hoàn tác được -> None
        assert a.call("POST", f"/api/admin/change-log/{price_old['id']}/revert")[0] == 200
        assert a.call("GET", "/api/admin/products?search=chan%20may")[1]["items"][0]["price"] is None
        # hoàn tác lần nữa dòng "1000 -> 2000" khi giá không còn là 2000 -> lỗi thời, từ chối
        st, body = a.call("POST", f"/api/admin/change-log/{price_new['id']}/revert")
        assert st == 409 and body["error"]["code"] == "CHANGE_STALE"
        # hoàn tác barcode
        assert a.call("POST", f"/api/admin/change-log/{items[0]['id']}/revert")[0] == 200
        assert a.call("GET", "/api/admin/products?search=chan%20may")[1]["items"][0]["barcode"] == ""
        assert a.call("POST", "/api/admin/change-log/99999/revert")[0] == 404


def test_revert_blocked_by_barcode_conflict_and_settings_revert(tmp_path):
    with running(tmp_path) as (c, _):
        a = _admin(c)
        a.call("PATCH", "/api/admin/products/2", {"barcode": "89310000555"})
        a.call("PATCH", "/api/admin/products/2", {"barcode": ""})
        _, log = a.call("GET", "/api/admin/change-log?table=product&record=2")
        clear_entry = log["items"][0]  # "89310000555" -> ""
        a.call("PATCH", "/api/admin/products/3", {"barcode": "89310000555"})  # mã cũ đã được SKU khác dùng
        st, body = a.call("POST", f"/api/admin/change-log/{clear_entry['id']}/revert")
        assert st == 409 and body["error"]["code"] in ("BARCODE_DUPLICATE", "CHANGE_STALE")
        # cài đặt: đổi rồi hoàn tác
        a.call("PATCH", "/api/admin/settings", {"similarity_threshold": 0.8})
        _, slog = a.call("GET", "/api/admin/change-log?table=settings")
        entry = next(i for i in slog["items"] if i["field"] == "similarity_threshold")
        assert a.call("POST", f"/api/admin/change-log/{entry['id']}/revert")[0] == 200
        assert a.call("GET", "/api/settings")[1]["similarity_threshold"] is None
        # staff không xem/hoàn tác được
        s = Client(c.base)
        s.login("staff", STAFF_PW)
        assert s.call("GET", "/api/admin/change-log")[0] == 403
        assert s.call("POST", f"/api/admin/change-log/{entry['id']}/revert")[0] == 403


def test_new_pos_flags_and_validation(tmp_path):
    with running(tmp_path) as (c, _):
        a = _admin(c)
        base = a.call("GET", "/api/settings")[1]
        assert base["tilt_block_capture"] is False and base["auto_print_receipt"] is False
        assert a.call("PATCH", "/api/admin/settings", {"tilt_block_capture": True, "auto_print_receipt": True})[0] == 200
        got = c.call("GET", "/api/settings", token=a.token)[1]
        assert got["tilt_block_capture"] is True and got["auto_print_receipt"] is True
        assert a.call("PATCH", "/api/admin/settings", {"tilt_block_capture": "yes"})[0] == 422
        assert a.call("PATCH", "/api/admin/settings", {"auto_print_receipt": 1})[0] == 422


# --------------------------------------------------------------------------- vật detector thấy nhưng không nhận ra
def test_unrecognized_objects_warning(tmp_path):
    cfg = {"n": None}
    one = [Detection("7", (20, 20, 160, 210), "accepted")]
    fn = lambda _p: InferenceOutput(one, 5.0, has_overlap=False, detected_count=cfg["n"])  # noqa: E731
    with running(tmp_path, executor=FakeExecutor([], fn=fn)) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        cfg["n"] = 6  # thấy 6 vật, chỉ nhận ra 1 -> báo 5 vật chưa nhận diện
        assert _wait_job(c, _capture(c, order["id"])[1]["job_id"])["warnings"] == [{"type": "unrecognized_objects", "count": 5}]
        cfg["n"] = 1  # thấy đúng 1 vật -> không cảnh báo
        assert _wait_job(c, _capture(c, order["id"])[1]["job_id"])["warnings"] == []
        cfg["n"] = None  # pipeline chưa báo cáo detected_count (chưa áp dụng bản vá) -> không cảnh báo, không lỗi
        assert _wait_job(c, _capture(c, order["id"])[1]["job_id"])["warnings"] == []


def test_overlap_and_unrecognized_can_coexist(tmp_path):
    fn = lambda _p: InferenceOutput([Detection("7", (20, 20, 160, 210), "accepted")], 5.0, True, 3)  # noqa: E731
    with running(tmp_path, executor=FakeExecutor([], fn=fn)) as (c, _):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        w = _wait_job(c, _capture(c, order["id"])[1]["job_id"])["warnings"]
        assert {x["type"] for x in w} == {"overlap_detected", "unrecognized_objects"}
        assert next(x for x in w if x["type"] == "unrecognized_objects")["count"] == 2


# --------------------------------------------------------------------------- Phase 1B giai đoạn C: catalog trong DB
class _CountingExecutor(FakeExecutor):
    def __init__(self):
        super().__init__([], fn=_dets_default)
        self.reloads = 0

    def reload_catalog(self):
        self.reloads += 1


def test_barcode_and_name_live_in_catalog_and_reload_pipeline(tmp_path):
    ex = _CountingExecutor()
    with running(tmp_path, executor=ex) as (c, app):
        a = _admin(c)
        assert a.call("PATCH", "/api/admin/products/3", {"barcode": "89310000333"})[0] == 200
        assert app.catalog.repo.get_product("3")["barcode"] == "89310000333"  # web đọc lại catalog ngay
        assert ex.reloads == 1  # pipeline cũng được nạp lại
        with app.db.read() as conn:
            assert conn.execute("SELECT barcode FROM product WHERE product_id='3'").fetchone()[0] == "89310000333"
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='product_overrides'").fetchone()
        assert a.call("PATCH", "/api/admin/products/3", {"price": 5000})[0] == 200
        assert ex.reloads == 1  # giá thuộc web, không nạp lại pipeline
        st, body = a.call("PATCH", "/api/admin/products/3", {"name": "Phấn má hồng mới"})
        assert st == 200 and body["name"] == "Phấn má hồng mới" and body["needs_naming"] is False
        log = a.call("GET", "/api/admin/change-log?table=product&record=3")[1]["items"]
        assert log[0]["field"] == "name"
        assert a.call("POST", f"/api/admin/change-log/{log[0]['id']}/revert")[0] == 200
        assert app.catalog.get("3")["name"] == "Phấn Má Hồng"


def test_evidence_edit_requires_confirm_validates_and_is_symmetric(tmp_path):
    ex = _CountingExecutor()
    with running(tmp_path, executor=ex) as (c, app):
        a = _admin(c)
        st, ev = a.call("GET", "/api/admin/products/7/evidence")
        assert st == 200 and ev["evidence"]["confusable_with"] == ["8"] and ev["confirm_text"]
        assert a.call("PATCH", "/api/admin/products/3/evidence", {"ocr_keywords": ["OR210"]})[1]["error"]["code"] == "CONFIRM_REQUIRED"
        # chuẩn hoá: chữ hoa, bỏ trùng
        st, ev = a.call("PATCH", "/api/admin/products/3/evidence", {"ocr_keywords": ["or210", "OR210"], "confirm": True})
        assert st == 200 and ev["evidence"]["ocr_keywords"] == ["OR210"]
        assert app.catalog.repo.ocr_keywords("3") == ("OR210",) and ex.reloads == 1
        # cặp dễ nhầm bắt buộc OCR không được trùng token
        st, err = a.call("PATCH", "/api/admin/products/8/evidence", {"ocr_keywords": ["ABA"], "confirm": True})
        assert st == 422 and err["error"]["code"] == "EVIDENCE_INVALID" and err["error"]["errors"]
        assert a.call("PATCH", "/api/admin/products/3/evidence", {"ocr_keywords": ["AB"], "confirm": True})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3/evidence", {"force_evidence": ["sam2"], "confirm": True})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3/evidence", {"confusable_with": ["3"], "confirm": True})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3/evidence", {"confusable_with": ["99"], "confirm": True})[0] == 422
        # thêm cặp 3<->12: ghi hai chiều; gỡ 7<->8: gỡ cả hai chiều
        a.call("PATCH", "/api/admin/products/3/evidence", {"confusable_with": ["12"], "confirm": True})
        assert app.catalog.repo.evidence("12", "confusable_with") == ("3",)
        a.call("PATCH", "/api/admin/products/7/evidence", {"confusable_with": [], "force_evidence": [], "confirm": True})
        assert frozenset({"7", "8"}) not in app.catalog.repo.confusable_pairs()
        assert app.catalog.repo.evidence("8", "confusable_with") is None
        # hoàn tác dòng gỡ cặp của SKU 7 -> cặp quay lại ở cả hai chiều
        log = a.call("GET", "/api/admin/change-log?table=product_evidence&record=7")[1]["items"]
        row = next(r for r in log if r["field"] == "confusable_with")
        assert a.call("POST", f"/api/admin/change-log/{row['id']}/revert")[0] == 200
        assert frozenset({"7", "8"}) in app.catalog.repo.confusable_pairs()
        assert a.call("POST", f"/api/admin/change-log/{row['id']}/revert")[1]["error"]["code"] == "CHANGE_STALE"
        assert c.call("GET", "/api/admin/products/7/evidence", token=None)[0] == 401


def test_color_references_crud_flags_and_revert(tmp_path):
    with running(tmp_path) as (c, app):
        a = _admin(c)
        items = a.call("GET", "/api/admin/colors")[1]["items"]
        assert next(x for x in items if x["code"] == "BR641") == {"code": "BR641", "hex": None, "used_by": ["2"], "missing": True}
        prod2 = a.call("GET", "/api/admin/products?search=chan%20may")[1]["items"][0]
        assert prod2["missing_color_reference"] is True
        assert a.call("PATCH", "/api/admin/colors/BR641", {"hex": "#5A3C2D"})[1]["error"]["code"] == "CONFIRM_REQUIRED"
        assert a.call("PATCH", "/api/admin/colors/BR641", {"hex": "zz", "confirm": True})[0] == 422
        st, col = a.call("PATCH", "/api/admin/colors/br641", {"hex": "5a3c2d", "confirm": True})
        assert st == 200 and col["hex"] == "#5A3C2D" and col["r"] == 90 and not col["missing"]
        assert app.catalog.repo.color_references()["BR641"] == (90, 60, 45)
        assert a.call("GET", "/api/admin/products?search=chan%20may")[1]["items"][0]["missing_color_reference"] is False
        log = a.call("GET", "/api/admin/change-log?table=color_reference")[1]["items"]
        assert a.call("POST", f"/api/admin/change-log/{log[0]['id']}/revert")[0] == 200
        assert "BR641" not in app.catalog.repo.color_references()


def test_legacy_barcode_overrides_merged_on_start_and_db_must_be_shared(tmp_path):
    import sqlite3
    s = _settings(tmp_path)
    conn = sqlite3.connect(str(s.db_path))
    conn.execute("CREATE TABLE product_overrides(product_id TEXT PRIMARY KEY, barcode TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO product_overrides VALUES('3','89310000444','x')")
    conn.commit()
    conn.close()
    app = App(s, DbCatalog(s.catalog_db_path), FakeExecutor([]), start_worker=False)
    try:
        assert app.catalog.get("3")["barcode"] == "89310000444"
        with app.db.read() as c:
            assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='product_overrides'").fetchone()
    finally:
        app.close()
    other = tmp_path / "other" / "app.db"
    _seed_catalog(other)
    import pytest
    with pytest.raises(ValueError, match="chung một file"):
        App(s, DbCatalog(other), FakeExecutor([]), start_worker=False)


# --------------------------------------------------------------------------- ảnh kết quả + bbox trên hoá đơn
def test_order_view_has_capture_image_and_boxes_linked_to_items(tmp_path):
    with running(tmp_path) as (c, app):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        _, j = _capture(c, order["id"])
        _wait_job(c, j["job_id"])
        _, o = c.call("GET", f"/api/orders/{order['id']}")
        assert len(o["captures"]) == 1
        cap = o["captures"][0]
        assert (cap["width"], cap["height"]) == (320, 240) and len(cap["boxes"]) == 4
        items = {i["id"]: i for i in o["items"]}
        acc7 = [b for b in cap["boxes"] if b["product_id"] == "7" and b["status"] == "accepted"]
        assert len(acc7) == 2 and acc7[0]["item_id"] == acc7[1]["item_id"]  # 2 vật gộp chung 1 dòng
        assert items[acc7[0]["item_id"]]["quantity"] == 2 and not items[acc7[0]["item_id"]]["flagged"]
        unc = [b for b in cap["boxes"] if b["status"] == "uncertain"]
        assert len(unc) == 1 and items[unc[0]["item_id"]]["flagged"]
        assert all(b["item_id"] in items for b in cap["boxes"]) and all(len(b["bbox"]) == 4 for b in cap["boxes"])
        img = urllib.request.urlopen(c.base + cap["image_url"])  # ảnh gốc qua URL ký
        assert img.status == 200 and len(img.read()) > 500
        # chụp thêm: accepted cùng SKU cộng dồn vào dòng cũ -> bbox lượt 2 trỏ cùng item_id
        _, j2 = _capture(c, order["id"], key="k2")
        _wait_job(c, j2["job_id"])
        _, o2 = c.call("GET", f"/api/orders/{order['id']}")
        assert len(o2["captures"]) == 2
        b2 = [b for b in o2["captures"][1]["boxes"] if b["product_id"] == "7" and b["status"] == "accepted"]
        assert b2[0]["item_id"] == acc7[0]["item_id"]


def test_old_db_gets_detections_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE captures(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, status TEXT NOT NULL, "
                 "item_count INTEGER NOT NULL DEFAULT 0, processing_time_ms REAL, image_path TEXT, error TEXT, created_at TEXT NOT NULL)")
    conn.commit()
    conn.close()
    from backend.db import Database

    Database(db)
    conn = sqlite3.connect(str(db))
    assert "detections_json" in {r[1] for r in conn.execute("PRAGMA table_info(captures)")}
    conn.close()


# --------------------------------------------------------------------------- thiết lập NÂNG CAO (pipeline)
_DEMO_CFG = Path(__file__).resolve().parents[1] / "configs" / "config.demo.yaml"
ADV_PW = "advpass12345"


def test_advanced_config_view_and_apply_with_advanced_password(tmp_path):
    ex = FakeExecutor([], fn=_dets_default)
    with running(tmp_path, executor=ex, pipeline_config=_DEMO_CFG, seed_advanced_password=ADV_PW) as (c, app):
        a = _admin(c)
        st, cfg = a.call("GET", "/api/admin/config")
        assert st == 200 and cfg["advanced_password_set"] and cfg["config_error"] is None
        by = {i["key"]: i for i in cfg["items"]}
        assert by["retrieval.backend"]["tier"] == "readonly" and by["retrieval.backend"]["value"] == "mock_visual_embedding"
        thr = by["detection.confidence_threshold"]
        assert thr["tier"] == "reload" and thr["value"] == thr["default"] and not thr["overridden"]
        ch = {"detection.confidence_threshold": 0.6}
        assert a.call("POST", "/api/admin/config/apply", {"changes": ch, "advanced_password": ADV_PW})[1]["error"]["code"] == "CONFIRM_REQUIRED"
        assert a.call("POST", "/api/admin/config/apply", {"changes": ch, "advanced_password": STAFF_PW, "confirm": True})[1]["error"]["code"] == "ADVANCED_PASSWORD_INVALID"
        ok = {"advanced_password": ADV_PW, "confirm": True}
        assert a.call("POST", "/api/admin/config/apply", {"changes": {"retrieval.backend": "x"}, **ok})[0] == 422  # chỉ xem
        assert a.call("POST", "/api/admin/config/apply", {"changes": {"detection.confidence_threshold": 2}, **ok})[0] == 422
        assert a.call("POST", "/api/admin/config/apply", {"changes": {"plugins.ocr.enabled": "yes"}, **ok})[0] == 422
        assert a.call("POST", "/api/admin/config/apply", {"changes": {"khong.co": 1}, **ok})[0] == 422
        st, res = a.call("POST", "/api/admin/config/apply", {"changes": {**ch, "plugins.ocr.enabled": True}, **ok})
        assert st == 200 and res["applied"] and ex.overrides == {"detection.confidence_threshold": 0.6, "plugins.ocr.enabled": True}
        by = {i["key"]: i for i in res["items"]}
        assert by["detection.confidence_threshold"]["value"] == 0.6 and by["detection.confidence_threshold"]["overridden"]
        # nạp lỗi -> báo lỗi, DB + executor giữ thiết lập cũ
        ex.fail_reload = True
        st, err = a.call("POST", "/api/admin/config/apply", {"changes": {"retrieval.top_k": 7}, **ok})
        assert st == 500 and err["error"]["code"] == "RELOAD_FAILED"
        ex.fail_reload = False
        assert "retrieval.top_k" not in app._config_overrides()
        # đặt lại bằng giá trị gốc -> bỏ ghi đè
        st, res = a.call("POST", "/api/admin/config/apply", {"changes": {"plugins.ocr.enabled": None}, **ok})
        assert st == 200 and "plugins.ocr.enabled" not in app._config_overrides()
        # hoàn tác cần mật khẩu nâng cao
        log = a.call("GET", "/api/admin/change-log?table=config")[1]["items"]
        row = next(r for r in log if r["field"] == "detection.confidence_threshold")
        assert a.call("POST", f"/api/admin/change-log/{row['id']}/revert")[0] == 403
        assert a.call("POST", f"/api/admin/change-log/{row['id']}/revert", {"advanced_password": ADV_PW})[0] == 200
        assert app._config_overrides() == {} and ex.overrides == {}
        # staff không được xem/sửa
        c.login("staff", STAFF_PW)
        assert c.call("GET", "/api/admin/config")[0] == 403


def test_advanced_password_rate_limited_and_job_reports_reloading(tmp_path):
    with running(tmp_path, pipeline_config=_DEMO_CFG, seed_advanced_password=ADV_PW) as (c, app):
        a = _admin(c)
        body = {"changes": {"retrieval.top_k": 6}, "advanced_password": "sai-mat-khau", "confirm": True}
        codes = [a.call("POST", "/api/admin/config/apply", body)[0] for _ in range(4)]
        assert codes[:3] == [403, 403, 403] and codes[3] == 429


def test_advanced_password_missing_is_explicit(tmp_path):
    with running(tmp_path, pipeline_config=_DEMO_CFG) as (c, app):
        a = _admin(c)
        st, err = a.call("POST", "/api/admin/config/apply", {"changes": {"retrieval.top_k": 6}, "advanced_password": "x", "confirm": True})
        assert st == 409 and err["error"]["code"] == "ADVANCED_PASSWORD_NOT_SET"


def test_local_executor_reload_pipeline_and_rollback():
    """LocalExecutor thật trên config demo (backend mock, CPU): nạp lại với ghi đè; nạp lỗi -> quay về thiết lập cũ."""
    import pytest

    if not (Path(__file__).resolve().parents[1] / "data_demo" / "db" / "app.db").is_file():
        pytest.skip("chưa có data_demo (catalog DB)")
    from backend.inference import LocalExecutor

    img = str(Path(__file__).resolve().parents[1] / "data_demo" / "query" / "query_01.jpg")
    ex = LocalExecutor(_DEMO_CFG, {"retrieval.top_k": 3})
    assert ex._runner._pipeline._config.retrieval.top_k == 3
    ex.reload_pipeline({"retrieval.top_k": 6, "detection.confidence_threshold": 0.6})
    cfg = ex._runner._pipeline._config
    assert cfg.retrieval.top_k == 6 and cfg.detection.confidence_threshold == 0.6
    with pytest.raises(ValueError):
        ex.reload_pipeline({"decision.uncertain_band": "khong-phai-so"})
    assert ex._runner._pipeline._config.retrieval.top_k == 6  # đã quay về thiết lập trước
    assert ex.infer(img).detections is not None  # vẫn nhận diện được sau rollback


# --------------------------------------------------------------------------- ảnh gallery, thử bằng chứng, kiểm định
def _gallery_cfg(tmp: Path) -> Path:
    """Config pipeline tối thiểu chỉ để web tìm thư mục gallery; SKU 7 (thư mục f7) có 2 ảnh lớn."""
    g = tmp / "gal" / "f7"
    g.mkdir(parents=True)
    for i in range(2):
        img = np.full((3000, 2000, 3), 40 * (i + 1), np.uint8)
        cv2.imencode(".jpg", img)[1].tofile(str(g / f"{i}.jpg"))
    cfg = tmp / "configs" / "cfg.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("paths:\n  gallery_dir: gal\n", encoding="utf-8")
    return cfg


def test_gallery_images_signed_and_resized(tmp_path):
    with running(tmp_path, pipeline_config=_gallery_cfg(tmp_path)) as (c, app):
        a = _admin(c)
        ev = a.call("GET", "/api/admin/products/7/evidence")[1]
        assert len(ev["gallery"]) == 2 and a.call("GET", "/api/admin/products/8/evidence")[1]["gallery"] == []
        r = urllib.request.urlopen(c.base + ev["gallery"][1])
        img = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
        assert r.status == 200 and max(img.shape[:2]) == 1024  # thu nhỏ từ 3000px
        bad = ev["gallery"][0].replace("sig=", "sig=x")
        try:
            urllib.request.urlopen(c.base + bad)
            raise AssertionError("chữ ký sai phải bị từ chối")
        except urllib.error.HTTPError as e:
            assert e.code == 403


def test_evidence_test_reports_ocr_hits_color_and_barcode(tmp_path):
    def fn(_p):
        ev = {"ocr": {"text": "hop ngoai ABA 200g"}, "color": {"dominant_rgb": [198, 160, 150]},
              "barcode": {"barcodes": [{"data": "8931000001372"}]}}
        return InferenceOutput([Detection("7", (10, 10, 100, 120), "uncertain", ev)], 50.0, detected_count=2)
    with running(tmp_path, executor=FakeExecutor([], fn=fn)) as (c, app):
        a = _admin(c)
        st, r = a.call("POST", "/api/admin/evidence-test", raw=_jpeg())
        assert st == 200 and r["detected_count"] == 2
        it = r["items"][0]
        assert it["ocr_keyword_hits"] == ["7"] and it["color_nearest"]["code"] == "BE203"
        assert it["color_hex"] == "#C6A096" and it["barcode_skus"] == ["1"] and set(it["plugins"]) == {"ocr", "color", "barcode"}
        assert a.call("POST", "/api/admin/evidence-test", raw=b"khong phai anh" * 10)[0] == 400


def test_ocr_keywords_normalized_like_reranker_and_non_latin_warning(tmp_path):
    with running(tmp_path) as (c, app):
        a = _admin(c)
        st, ev = a.call("PATCH", "/api/admin/products/3/evidence", {"ocr_keywords": ["or-210", "フォルミング"], "confirm": True})
        assert st == 200 and ev["evidence"]["ocr_keywords"] == ["OR210", "フォルミング"]
        assert any("Latin" in w for w in ev["warnings"])


def test_validation_job_blocks_captures_and_reports_result(tmp_path):
    ex = FakeExecutor([], delay=0.6, fn=_dets_default)
    with running(tmp_path, executor=ex, seed_advanced_password=ADV_PW) as (c, app):
        a = _admin(c)
        assert a.call("POST", "/api/admin/validation", {"advanced_password": ADV_PW})[1]["error"]["code"] == "CONFIRM_REQUIRED"
        assert a.call("POST", "/api/admin/validation", {"confirm": True, "advanced_password": "sai"})[0] == 403
        st, v = a.call("POST", "/api/admin/validation", {"confirm": True, "advanced_password": ADV_PW})
        assert st == 200 and v["status"] == "running"
        assert a.call("POST", "/api/admin/validation", {"confirm": True, "advanced_password": ADV_PW})[0] == 409
        s = Client(c.base)
        s.login("staff", STAFF_PW)
        _, order = s.call("POST", "/api/orders")
        st, err = _capture(s, order["id"])
        assert st == 503 and err["error"]["code"] == "SYSTEM_BUSY"
        t0 = time.time()
        while a.call("GET", "/api/admin/validation")[1]["status"] == "running" and time.time() - t0 < 10:
            time.sleep(0.05)
        v = a.call("GET", "/api/admin/validation")[1]
        assert v["status"] == "done" and v["result"] == {"f1": 0.5, "fusion_accuracy": 0.6}
        assert _capture(s, order["id"])[0] == 202  # xong kiểm định -> chụp lại bình thường


def test_capture_stores_rejected_boxes_without_item(tmp_path):
    def fn(_p):
        return InferenceOutput([Detection("7", (10, 10, 100, 120), "accepted")], 40.0, detected_count=3,
                               rejected_bboxes=[(150, 20, 200, 90), (210, 30, 300, 200)])
    with running(tmp_path, executor=FakeExecutor([], fn=fn)) as (c, app):
        c.login("staff", STAFF_PW)
        _, order = c.call("POST", "/api/orders")
        _, j = _capture(c, order["id"])
        body = _wait_job(c, j["job_id"])
        assert any(w["type"] == "unrecognized_objects" and w["count"] == 2 for w in body["warnings"])
        cap = c.call("GET", f"/api/orders/{order['id']}")[1]["captures"][0]
        rej = [b for b in cap["boxes"] if b["status"] == "rejected"]
        assert len(rej) == 2 and all(b["item_id"] is None and b["product_id"] is None for b in rej)
        assert rej[0]["bbox"] == [150, 20, 200, 90]
