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

from backend.catalog import JsonCatalog
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


def _settings(tmp: Path, **over) -> Settings:
    meta = tmp / "metadata"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "products.json").write_text(json.dumps(PRODUCTS), encoding="utf-8")
    front = tmp / "frontend"
    front.mkdir(exist_ok=True)
    (front / "index.html").write_text("<html>POS</html>", encoding="utf-8")
    (front / "app.js").write_text("//js", encoding="utf-8")
    base = dict(host="127.0.0.1", port=0, max_upload_bytes=2 * 1024 * 1024, allowed_origins=[],
                token_ttl_seconds=3600, max_failed_attempts=3, lockout_seconds=60, queue_max=5,
                job_timeout_seconds=60, idempotency_window_seconds=5, db_path=tmp / "db" / "app.db",
                media_root=tmp / "media", media_url_ttl=600, media_retention_days=30, thumb_width=120,
                allow_checkout_without_price=False, tz_offset_hours=7, pipeline_config=tmp / "cfg.yaml",
                metadata_dir=meta, products_filename="products.json", frontend_dir=front,
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
    catalog = JsonCatalog(settings.metadata_dir / "products.json")
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
        assert a.call("PATCH", "/api/admin/products/3", {"barcode": "8931000001372"})[0] == 409  # trùng barcode từ products.json
        assert a.call("PATCH", "/api/admin/products/3", {"barcode": "a b!"})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3", {"price": -5})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3", {"price": 1.5})[0] == 422
        assert a.call("PATCH", "/api/admin/products/3", {"name": "x"})[0] == 422  # chỉ sửa price/barcode
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
        sys.modules["src.core.config"].load_config = lambda p: ("cfg", p)
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
        App(s, JsonCatalog(s.metadata_dir / "products.json"), FakeExecutor([]), start_worker=False).close()
    start()
    backups = tmp_path / "backups"
    assert not backups.exists() or not list(backups.glob("app-*.db"))  # lần chạy đầu: DB mới tạo, chưa có gì để sao lưu
    start()
    assert len(list(backups.glob("app-*.db"))) == 1
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
