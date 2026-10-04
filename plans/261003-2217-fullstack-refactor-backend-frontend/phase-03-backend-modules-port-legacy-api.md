# Phase 03 — Backend modules (port API legacy)

**Priority:** P0 · **Status:** in progress (3a auth, 3b settings + catalog đọc: done 2026-10-04) · Context: design.md §2 mapping; nguồn: `src_legacy/backend/{service,admin,security}.py`

## Thứ tự module (mỗi module: ports → config → repository → service → schemas → deps → router → tests)
1. `auth`: login (mở shift, đóng shift cũ = đá máy cũ), logout, `GET /me`, onboarding-seen; scrypt giữ format hash cũ; JWT `{uid,sid,role,exp}` qua pyjwt; `CurrentUser` kiểm shift còn mở; `RequireAdmin`; limiter 5 lần/5 phút theo (username, IP) in-memory → 429 `RATE_LIMITED` + `retry_after`.
2. `pos_settings`: `GET /settings`, `PATCH /admin/settings`, advanced password (hash trong settings).
3. `catalog`: `GET /catalog/products?search=&barcode=` (bỏ dấu tiếng Việt), `admin/products*`, evidence GET/PATCH (`confirm`), colors, `GET /gallery/{pid}/{idx}` (signed URL, resize ≤1024).
4. `audit`: ghi change_log (helper dùng chung bởi catalog/engine_config), `GET /admin/change-log`, revert (chặn khi giá trị đã đổi sau đó).
5. `orders`: create/reuse empty open, `orders/open`, `orders/{id}` view, items CRUD, checkout (409 `PRICE_MISSING_BLOCKED`), void (paid → admin), `history`, `admin/orders`.
6. `captures`: `POST orders/{id}/captures` (raw body ≤ max_upload_mb, `Idempotency-Key` in-memory TTL) → lưu file, insert capture `queued`, `worker.submit` → 202 `{job_id}`; `GET jobs/{id}` (status, position, system_reloading, added, warnings, order); `GET /media/{order}/{file}` signed.
7. `users`: list/create/patch nhân viên.
8. `reports`: KPI hôm nay, doanh thu theo ngày (kể cả ngày 0), top 5; theo `timezone_offset_hours`.
9. `engine_config`: `GET admin/config`, `POST admin/config/apply` (validate bounds từ registry → gửi control `reload` → chờ kết quả/rollback).
10. `validation`: `GET/POST admin/validation`, `POST admin/evidence-test` (qua worker).

## Quy ước
- Error code legacy giữ nguyên trong field `code`; HTTP status giữ nguyên.
- Startup side-effects legacy (backup DB, seed users/prices, purge media cũ) → chuyển thành entrypoint/Make target rõ ràng (`make seed-demo`, cron `purge-media`), không chạy ngầm lúc boot.

## Done khi
Bộ test API port từ `test_backend_api.py` (httpx ASGITransport + FakeRecognizer) xanh, phủ đủ ~37 route.

## Chia PR (mỗi module một PR, theo thứ tự phụ thuộc)
| PR | Module | Trạng thái |
|---|---|---|
| 3a | `auth` (+ `entrypoints/reset_password.py`) | **done** (2026-10-04) |
| 3b | `pos_settings`, `catalog` (đọc + giá) | **done** (2026-10-04) |
| 3c | `orders` | pending |
| 3d | `captures` (bộ nhận diện giả) | pending |
| 3e | `audit`, phần ghi của `catalog`, `users`, `reports`, `engine_config`, `validation` | pending |

## Quyết định cho phần ghi catalog (PR 3e)
Engine cung cấp hàm ghi nhận kết nối từ bên gọi và KHÔNG commit (cùng kiểu `set_meta` đang có); module quản trị
mở một giao dịch đồng bộ (psycopg, chạy trong luồng phụ), gọi hàm đó rồi ghi `change_log` trên cùng kết nối.
Vẫn một đường ghi qua engine, và catalog + nhật ký nằm trong cùng một giao dịch như bản cũ.

## Kết quả 3a
- Route: `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me`, `POST /api/me/onboarding-seen`; dependency `current_user`, `require_admin` cho các module sau.
- Giữ hành vi cũ: một ca mở mỗi tài khoản (đăng nhập nơi khác đá phiên cũ -> `SESSION_INVALID`), logout idempotent, thông báo giống nhau cho sai mật khẩu / tài khoản không tồn tại / tài khoản bị khoá, khoá tạm theo (tài khoản, IP) -> 429 `RATE_LIMITED` + `retry_after`, hash scrypt đúng format cũ.
- `make lint`, `make type-check` (48 file): đạt. `make test`: 241 passed, 25 skipped (16 test auth mới: 12 qua HTTP trên Postgres, 1 khởi động, 3 đơn vị).

## Khác với bản cũ / kế hoạch (3a)
- Token là JWT HS256 (pyjwt) thay token HMAC tự chế: token cũ không còn hợp lệ, người dùng đăng nhập lại.
- Lỗi theo `design.md`: `{"detail", "code", ...}` thay `{"error": {"code", "message"}}`; lỗi kiểm dữ liệu của FastAPI cũng về dạng này (422 `VALIDATION_ERROR` + `errors`).
- `GET /api/me` chưa trả `settings`: thêm ở PR 3b cùng module `pos_settings`.
- Không seed tài khoản lúc khởi động (theo "Quy ước" ở trên): tạo/đặt lại bằng `make reset-password USER_NAME=admin ROLE=admin` (mật khẩu ngẫu nhiên, in một lần).
- Thiếu `JWT_SECRET` thì API dừng ngay lúc khởi động với thông báo rõ; `make setup` điền các bí mật còn thiếu vào `.env` đã có (không ghi đè giá trị đã có); `make docker-up*` chạy `setup` trước.
- Mật khẩu được kiểm trong luồng phụ (`asyncio.to_thread`) vì scrypt cố ý chậm.

## Kết quả 3b
- Route: `GET /api/settings`, `GET /api/catalog/products?search=&barcode=` (cần đăng nhập); `GET /api/me` trả thêm `settings`.
- Giữ hành vi cũ: 5 khoá công khai (`allow_checkout_without_price`, `similarity_threshold`, `min_confidence_accept`, `tilt_block_capture`, `auto_print_receipt`), giá trị trong bảng `settings` đè mặc định; tìm sản phẩm không dấu theo một phần tên hoặc đúng id, barcode khớp chính xác và ưu tiên hơn `search`, chỉ SKU đang bán, tối đa 50, thứ tự catalog của engine; mỗi sản phẩm có `price` (null khi chưa có giá), `needs_naming`, `missing_color_reference`.
- `make lint`, `make type-check` (60 file): đạt. `make test`: 249 passed, 25 skipped (8 test mới: 3 settings, 5 catalog).

## Khác với bản cũ / kế hoạch (3b)
- `design.md` ghi `catalog/repository.py` dùng `engine.catalog` qua adapter. Ở đây API đọc thẳng các bảng `product`, `product_evidence`, `color_reference` bằng SQLAlchemy Core: `tests/test_import_boundary.py` cấm tiến trình `RECOGNIZER=fake` import `engine`, và đọc bằng truy vấn thì luôn thấy dữ liệu admin vừa lưu (không còn bản sao trong bộ nhớ phải `reload()`). Phần GHI catalog (3e) vẫn đi qua hàm của engine như đã chốt.
- API không còn dừng khởi động khi catalog rỗng (bản cũ báo lỗi ở `DbCatalog`): catalog rỗng trả danh sách rỗng; nạp catalog là việc của `make seed-demo` / Phase 7.
- `barcode` chỉ gồm khoảng trắng được coi là không lọc (bản cũ khớp với mọi SKU không có barcode).
- Mặc định `allow_checkout_without_price` lấy từ biến môi trường `ALLOW_CHECKOUT_WITHOUT_PRICE` (`PosSettings`), thay `pos.allow_checkout_without_price` trong `configs/backend.yaml`.
- Chưa làm trong 3b (cần `change_log`, thuộc 3e): `PATCH /api/admin/settings`, mật khẩu nâng cao, ghi giá. Test tự chèn dòng vào `settings` / `product_prices`.
