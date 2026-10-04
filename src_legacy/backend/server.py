"""Tầng HTTP (http.server, chỉ thư viện chuẩn): định tuyến, dịch lỗi, phục vụ giao diện tĩnh và ảnh có ký.

Bảo mật (backend.md §8.3): mọi route trừ /api/health, /api/auth/login (và ảnh có chữ ký) cần token; không có
Swagger; ảnh chỉ phục vụ qua URL ký có hạn + chặn path traversal; giới hạn kích thước body; CORS chỉ nhận
origin cấu hình rõ ràng (mặc định cùng origin nên không cần CORS).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .security import verify_media
from .service import ApiError, App, Session

log = logging.getLogger("backend.http")
MAX_JSON_BYTES = 1024 * 1024
DRAIN_MAX = 64 * 1024 * 1024
_MEDIA_RE = re.compile(r"^\d+/[A-Za-z0-9_.\-]+$")
_SEC_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


class Request:
    def __init__(self, handler: "Handler", query: dict[str, list[str]]) -> None:
        self.h, self.query, self._body = handler, query, None

    def q(self, name: str, default: str = "") -> str:
        return (self.query.get(name) or [default])[0]

    def raw(self, limit: int, too_large_code: str = "PAYLOAD_TOO_LARGE") -> bytes:
        if self._body is not None:
            return self._body
        if "chunked" in (self.h.headers.get("Transfer-Encoding") or "").lower():
            self.h.close_connection = True
            raise ApiError(411, "VALIDATION_ERROR", "Cần Content-Length")
        try:
            n = int(self.h.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "VALIDATION_ERROR", "Content-Length không hợp lệ") from None
        if n > limit:
            # Đọc bỏ (không lưu) phần body thừa để client nhận được 413 rõ ràng thay vì "lỗi mạng"; trên trần
            # DRAIN_MAX thì ngắt kết nối luôn để không bị kẹt băng thông.
            if n <= DRAIN_MAX:
                left = n
                while left > 0:
                    chunk = self.h.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            self.h.close_connection = True
            raise ApiError(413, too_large_code, "Dữ liệu gửi lên quá lớn")
        self._body = self.h.rfile.read(n) if n else b""
        return self._body

    def json(self) -> dict:
        raw = self.raw(MAX_JSON_BYTES)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "VALIDATION_ERROR", "JSON không hợp lệ") from None
        if not isinstance(data, dict):
            raise ApiError(422, "VALIDATION_ERROR", "Body phải là object JSON")
        return data


def _int(text: str) -> int:
    return int(text)


def build_routes(app: App):
    """(method, regex, public, fn(req, sess, *groups)). `sess` là None với route public."""
    R = []

    def route(method: str, pattern: str, public: bool = False):
        def deco(fn):
            R.append((method, re.compile("^" + pattern + "$"), public, fn))
            return fn
        return deco

    @route("GET", r"health", public=True)
    def health(req, s):
        return {"status": "ready"}

    @route("POST", r"auth/login", public=True)
    def login(req, s):
        b = req.json()
        return app.login(b.get("username"), b.get("password"), req.h.client_address[0])

    @route("POST", r"auth/logout", public=True)
    def logout(req, s):
        return app.logout(req.h.bearer())

    @route("GET", r"me")
    def me(req, s):
        return app.me(s)

    @route("POST", r"me/onboarding-seen")
    def onboarding(req, s):
        return app.mark_onboarding_seen(s)

    @route("GET", r"settings")
    def settings(req, s):
        return app.public_settings()

    @route("GET", r"catalog/products")
    def products(req, s):
        return {"items": app.list_products(req.q("search"), req.q("barcode"))}

    @route("POST", r"orders")
    def create_order(req, s):
        return app.create_order(s)

    @route("GET", r"orders/open")
    def open_order(req, s):
        return app.open_order(s)

    @route("GET", r"orders/(\d+)")
    def get_order(req, s, oid):
        return app.get_order(s, _int(oid))

    @route("POST", r"orders/(\d+)/captures")
    def capture(req, s, oid):
        limit = app.s.max_upload_bytes
        data = req.raw(limit, "IMAGE_TOO_LARGE")
        if not data:
            raise ApiError(422, "VALIDATION_ERROR", "Thiếu ảnh")
        return app.submit_capture(s, _int(oid), data, req.h.headers.get("Idempotency-Key"))

    @route("GET", r"jobs/([0-9a-f]{32})")
    def job(req, s, jid):
        return app.job_view(s, jid)

    @route("POST", r"orders/(\d+)/items")
    def add_item(req, s, oid):
        return app.add_item(s, _int(oid), req.json())

    @route("PATCH", r"orders/(\d+)/items/(\d+)")
    def patch_item(req, s, oid, iid):
        return app.update_item(s, _int(oid), _int(iid), req.json())

    @route("DELETE", r"orders/(\d+)/items/(\d+)")
    def del_item(req, s, oid, iid):
        return app.delete_item(s, _int(oid), _int(iid))

    @route("POST", r"orders/(\d+)/checkout")
    def checkout(req, s, oid):
        return app.checkout(s, _int(oid), req.json())

    @route("POST", r"orders/(\d+)/void")
    def void(req, s, oid):
        return app.void_order(s, _int(oid))

    @route("GET", r"history")
    def history(req, s):
        return {"items": app.history(s, req.q("range", "today"))}

    # ---- admin
    @route("GET", r"admin/reports")
    def reports(req, s):
        return app.admin_reports(s, req.q("range", "today"))

    @route("GET", r"admin/change-log")
    def change_log(req, s):
        return {"items": app.admin_change_log(s, req.q("table"), req.q("record"), int(req.q("limit", "100") or 100))}

    @route("POST", r"admin/change-log/(\d+)/revert")
    def revert_change(req, s, lid):
        return app.admin_revert_change(s, _int(lid), req.json())

    @route("GET", r"admin/products")
    def admin_products(req, s):
        return app.admin_products(s, req.q("search"), req.q("filter"), int(req.q("page", "1") or 1), int(req.q("size", "50") or 50))

    @route("PATCH", r"admin/products/([0-9A-Za-z_\-]+)")
    def admin_patch_product(req, s, pid):
        return app.admin_update_product(s, pid, req.json())

    @route("GET", r"admin/products/([0-9A-Za-z_\-]+)/evidence")
    def admin_product_evidence(req, s, pid):
        return app.admin_product_evidence(s, pid)

    @route("PATCH", r"admin/products/([0-9A-Za-z_\-]+)/evidence")
    def admin_patch_evidence(req, s, pid):
        return app.admin_update_evidence(s, pid, req.json())

    @route("GET", r"admin/colors")
    def admin_colors(req, s):
        return {"items": app.admin_colors(s)}

    @route("PATCH", r"admin/colors/([0-9A-Za-z_\-]+)")
    def admin_patch_color(req, s, code):
        return app.admin_update_color(s, code, req.json())

    @route("GET", r"admin/config")
    def admin_config(req, s):
        return app.admin_config(s)

    @route("POST", r"admin/config/apply")
    def admin_apply_config(req, s):
        return app.admin_apply_config(s, req.json())

    @route("POST", r"admin/evidence-test")
    def admin_evidence_test(req, s):
        return app.admin_test_evidence(s, req.raw(app.s.max_upload_bytes))

    @route("GET", r"admin/validation")
    def admin_validation(req, s):
        return app.admin_validation_status(s)

    @route("POST", r"admin/validation")
    def admin_start_validation(req, s):
        return app.admin_start_validation(s, req.json())

    @route("GET", r"admin/orders")
    def admin_orders(req, s):
        return {"items": app.history(s, req.q("range", "today"), all_users=True)}

    @route("GET", r"admin/users")
    def admin_users(req, s):
        return {"items": app.admin_users(s)}

    @route("POST", r"admin/users")
    def admin_create_user(req, s):
        return app.admin_create_user(s, req.json())

    @route("PATCH", r"admin/users/(\d+)")
    def admin_patch_user(req, s, uid):
        return app.admin_update_user(s, _int(uid), req.json())

    @route("PATCH", r"admin/settings")
    def admin_settings(req, s):
        return app.update_settings(s, req.json())

    return R


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pos"
    sys_version = ""

    def log_message(self, fmt, *args):  # đưa vào logging, không in ra stderr
        log.debug("%s %s", self.address_string(), fmt % args)

    # -- tiện ích
    def bearer(self) -> str | None:
        auth = self.headers.get("Authorization") or ""
        return auth[7:].strip() if auth.lower().startswith("bearer ") else None

    def _cors(self) -> dict[str, str]:
        origin = self.headers.get("Origin")
        if origin and origin in self.server.app.s.allowed_origins:
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key",
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS"}
        return {}

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**_SEC_HEADERS, **self._cors(), **(extra or {})}.items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _error(self, exc: ApiError) -> None:
        self._json(exc.status, {"error": {"code": exc.code, "message": exc.message, **exc.extra}})

    # -- điều phối
    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._api(path[5:], query)
            else:
                self._static(path)
        except ApiError as exc:
            self._error(exc)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            log.exception("Lỗi không lường trước: %s %s", self.command, self.path)
            self.close_connection = True
            self._error(ApiError(500, "INTERNAL_ERROR", "Lỗi hệ thống"))

    def _api(self, path: str, query: dict) -> None:
        app: App = self.server.app
        if self.command == "OPTIONS":
            return self._send(204, b"", "text/plain", self._cors())
        m = re.match(r"^media/(.+)$", path)
        if m and self.command == "GET":
            return self._media(m.group(1), query)
        m = re.match(r"^gallery/([0-9]{1,9})/([0-9]{1,4})$", path)
        if m and self.command == "GET":
            return self._gallery(m.group(1), int(m.group(2)), query)
        allowed = False
        for method, rx, public, fn in self.server.routes:
            hit = rx.match(path)
            if not hit:
                continue
            allowed = True
            if method != self.command:
                continue
            sess: Session | None = None if public else app.authenticate(self.bearer())
            if sess is None and not public:
                raise ApiError(401, "AUTH_INVALID", "Cần đăng nhập")
            req = Request(self, query)
            result = fn(req, sess, *hit.groups())
            if req._body is None and self.command in ("POST", "PATCH", "PUT"):
                req.raw(MAX_JSON_BYTES)  # đọc bỏ body còn lại để giữ kết nối hợp lệ
            return self._json(200 if self.command != "POST" or "job_id" not in result else 202, result)
        raise ApiError(405 if allowed else 404, "NOT_FOUND", "Không tìm thấy")

    def _media(self, rel: str, query: dict) -> None:
        app: App = self.server.app
        try:
            exp, sig = int(query.get("exp", ["0"])[0]), query.get("sig", [""])[0]
        except ValueError:
            raise ApiError(403, "FORBIDDEN", "Liên kết ảnh không hợp lệ") from None
        if not _MEDIA_RE.match(rel) or not verify_media(app.s.jwt_secret, rel, exp, sig):
            raise ApiError(403, "FORBIDDEN", "Liên kết ảnh không hợp lệ hoặc đã hết hạn")
        root = app.s.media_root.resolve()
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():  # chặn path traversal
            raise ApiError(404, "NOT_FOUND", "Không thấy ảnh")
        self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                   {"Cache-Control": "private, max-age=300"})

    def _gallery(self, pid: str, idx: int, query: dict) -> None:
        """Ảnh gallery (đã thu nhỏ) qua URL ký ngắn hạn — dùng cho admin chấm màu tham chiếu."""
        app: App = self.server.app
        try:
            exp, sig = int(query.get("exp", ["0"])[0]), query.get("sig", [""])[0]
        except ValueError:
            raise ApiError(403, "FORBIDDEN", "Liên kết ảnh không hợp lệ") from None
        if not verify_media(app.s.jwt_secret, f"g/{pid}/{idx}", exp, sig):
            raise ApiError(403, "FORBIDDEN", "Liên kết ảnh không hợp lệ hoặc đã hết hạn")
        self._send(200, app.gallery_image(pid, idx), "image/jpeg", {"Cache-Control": "private, max-age=300"})

    def _static(self, path: str) -> None:
        if self.command not in ("GET", "HEAD"):
            raise ApiError(405, "NOT_FOUND", "Không hỗ trợ")
        root = self.server.app.s.frontend_dir.resolve()
        rel = path.lstrip("/") or "index.html"
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            raise ApiError(404, "NOT_FOUND", "Không tìm thấy")
        if not target.is_file():
            if "." in Path(rel).name:
                raise ApiError(404, "NOT_FOUND", "Không tìm thấy")
            target = root / "index.html"  # định tuyến phía client
        if not target.is_file():
            raise ApiError(404, "NOT_FOUND", "Chưa có giao diện (thiếu frontend/index.html)")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    do_GET = do_POST = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = lambda self: self._dispatch()
    do_PUT = lambda self: self._error(ApiError(405, "NOT_FOUND", "Không hỗ trợ"))


class PosServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, app: App) -> None:
        super().__init__(addr, Handler)
        self.app = app
        self.routes = build_routes(app)

    def handle_error(self, request, client_address) -> None:
        """Ghi lỗi qua logging (mặc định socketserver in thẳng stderr, dễ mất traceback)."""
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            log.debug("Client ngắt kết nối: %s (%s)", client_address, type(exc).__name__)
        else:
            log.exception("Lỗi khi xử lý kết nối từ %s", client_address)
