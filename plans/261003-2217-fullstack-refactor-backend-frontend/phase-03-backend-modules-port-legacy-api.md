# Phase 03 — Backend modules (port API legacy)

**Priority:** P0 · **Status:** in progress (3a–3d, 3e-1 audit + settings + giá: done 2026-10-04) · Context: design.md §2 mapping; nguồn: `src_legacy/backend/{service,admin,security}.py`

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
| 3c | `orders` | **done** (2026-10-04) |
| 3d | `captures` (bộ nhận diện giả) | **done** (2026-10-04) |
| 3e-1 | `audit` (nhật ký + hoàn tác), `PATCH /admin/settings`, mật khẩu nâng cao, ghi giá, `GET /admin/products` | **done** (2026-10-04) |
| 3e-2 | ghi catalog (barcode, tên, bằng chứng, màu) theo hướng C, `GET /gallery/...` | pending |
| 3e-3 | `users`, `reports` | pending |
| 3e-4 | `engine_config`, `validation` (cùng phần reload/kiểm định của Phase 4) | pending |

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

## Kết quả 3c
- Route: `POST /api/orders`, `GET /api/orders/open`, `GET /api/orders/{id}`, `POST /api/orders/{id}/items`, `PATCH|DELETE /api/orders/{id}/items/{item_id}`, `POST /api/orders/{id}/checkout`, `POST /api/orders/{id}/void`, `GET /api/history?range=`, `GET /api/admin/orders?range=` (admin).
- Giữ hành vi cũ: dùng lại đơn mở rỗng (gắn sang ca mới), khôi phục đơn mở sau khi đăng nhập lại, gộp dòng cùng SKU đã xác nhận (trần 999), đổi SKU của dòng thì bỏ cờ + bỏ giá tay + gộp, giá tay, 409 `PRICE_MISSING_BLOCKED` (+ `missing`) trừ khi `allow_checkout_without_price`, đóng băng đơn giá khi thanh toán, cộng `total_collected` của ca, 409 `ORDER_NOT_OPEN`, đơn của người khác trả 404, huỷ đơn đã thanh toán chỉ admin (trừ lại tiền của ca đã thu), lịch sử theo ngày của cửa hàng (`TIMEZONE_OFFSET_HOURS`).
- `make lint`, `make type-check` (66 file): đạt. `make test`: 260 passed, 25 skipped (11 test đơn hàng mới).

## Khác với bản cũ / kế hoạch (3c)
- Mỗi thao tác đổi đơn khoá dòng `orders` (`SELECT ... FOR UPDATE`) trong giao dịch: hai yêu cầu cùng lúc trên một đơn (bấm Thanh toán hai lần) chạy lần lượt, lần sau nhận `ORDER_NOT_OPEN`. Bản cũ dựa vào khoá ghi của SQLite.
- Repository cung cấp đơn vị công việc (`read()` / `write()` trả `OrdersUnit` trên một kết nối); luật nghiệp vụ ở service ghép các câu lệnh trong một giao dịch. Giá và tên sản phẩm đọc qua `CatalogService.lookup` trên kết nối khác (không cùng giao dịch với đơn).
- Kiểm dữ liệu vào bằng pydantic (`StrictInt`): số lượng/giá phải là số nguyên JSON (bản cũ nhận cả `2.0`); id đơn không phải số trả 422 thay 404.
- `admin/orders` làm luôn ở đây (cùng truy vấn với `history`), không đợi 3e.
- `captures` trong đơn luôn là `[]` và chưa có route `/api/media/...`: phần của 3d. `thumbnail_url` đã ký sẵn khi dòng có `thumb_path`. Dòng bị gắn cờ trong test được chèn thẳng vào DB.

## Kết quả 3d
- Route: `POST /api/orders/{id}/captures` (body ảnh thô, `Idempotency-Key`, 202 `{job_id, duplicate}`), `GET /api/jobs/{id}` (`status`, `position`, `system_reloading`; xong thì `added`, `warnings`, `order`; lỗi thì `error`), `GET /api/media/{order}/{file}?exp=&sig=` (công khai, URL ký). Đơn hàng trả `captures` (ảnh gốc URL ký, `width`, `height`, `boxes` nối với `item_id`).
- Giữ hành vi cũ: luật gộp (accepted cùng SKU một dòng, mỗi uncertain một dòng riêng có cờ), cộng dồn vào dòng đã xác nhận, ảnh thu nhỏ 240 px, khung đỏ cho vật không nhận ra (`item_id: null`), cảnh báo `overlap_detected` / `unrecognized_objects`, mã lỗi `IMAGE_DECODE_ERROR` (400), `IMAGE_TOO_LARGE` (413), `QUEUE_FULL` (503), `PIPELINE_TIMEOUT`, `GPU_OOM`, `PIPELINE_ERROR`, `ORDER_NOT_OPEN` (đơn đóng trong lúc nhận diện: không thêm dòng), job/đơn của người khác trả 404, bằng chứng numpy được chuyển về JSON.
- Migration `0003`: `captures.image_width`, `image_height`, `warnings`.
- `make lint`, `make type-check` (76 file): đạt. `make test`: 273 passed, 25 skipped (13 test mới). `LocalRecognizer.recognize` chạy thử trên `config.demo.yaml` (mock backends, CPU) bằng venv ML: 8 vật, có bằng chứng plugin, không ghi file kết quả của engine.

## Khác với bản cũ / kế hoạch (3d)
- Làm trước một phần Phase 4 (thay vì viết bản tạm rồi bỏ): `recognition/worker.py` (`asyncio.Queue` + một luồng, timeout, `position`), `recognition/mapper.py`, `FakeRecognizer` (port `FakeExecutor`), `RecognizerPort.recognize` + `LocalRecognizer.recognize`. Phase 4 còn: reload pipeline/catalog, kiểm định, evidence-test, `system_reloading`, `SYSTEM_BUSY`, `--workers 1`.
- `job_id` là id của dòng `captures` (số nguyên) thay chuỗi hex 32 ký tự; trạng thái job đọc từ DB.
- Không dọn job treo lúc khởi động (khởi động không được đụng DB: `api_app` và test ranh giới import chạy không có DB). Thay vào đó: job còn `queued`/`processing` trong DB mà worker của tiến trình không biết thì `GET /jobs/{id}` trả và ghi `error: SERVER_RESTARTED`.
- Ảnh xử lý bằng Pillow (không OpenCV: đường fake không được nạp `cv2`), xoay theo EXIF như OpenCV. Đường dẫn ảnh lưu tương đối so với `MEDIA_DIR` (mặc định `data/transactions`).
- `FakeRecognizer` chọn SKU từ catalog đang bán trong DB ở mỗi ảnh (bản cũ nhận danh sách lúc khởi động).
- Chưa làm: xoá ảnh cũ theo hạn lưu trữ (bản cũ dọn lúc khởi động; theo "Quy ước" sẽ là lệnh/cron riêng), `GET /gallery/...` (3e).

## Kết quả 3e-1
- Route: `GET /api/admin/change-log?table=&record=&limit=`, `POST /api/admin/change-log/{id}/revert`, `PATCH /api/admin/settings`, `GET /api/admin/products?search=&filter=&page=&size=`, `PATCH /api/admin/products/{id}` (chỉ `price`). Tất cả chỉ admin (403 `FORBIDDEN`).
- `audit`: `record_changes(conn, ...)` ghi nhật ký trên kết nối của module đang sửa (dữ liệu + nhật ký cùng một giao dịch); hoàn tác qua module sở hữu dữ liệu, mỗi module đăng ký bộ hoàn tác cho bảng của mình (`AuditService.register`, nối ở `app.main`); hoàn tác chỉ khi trường còn giữ giá trị của lần sửa đó, không thì 409 `CHANGE_STALE`; lần hoàn tác cũng được ghi nhật ký.
- Giữ hành vi cũ: kiểm `PATCH /admin/settings` (cờ phải true/false, ngưỡng trong khoảng hoặc null, khoá lạ bị bỏ qua, không có gì hợp lệ thì 422), nhật ký settings dạng JSON với `record_id = field = khoá`, nhật ký giá dạng chữ (`None` khi không có giá), giá không đổi thì không ghi, danh sách admin (tìm theo tên/id/barcode, lọc `missing_price|missing_barcode|needs_naming`, phân trang, ba bộ đếm), mật khẩu nâng cao (409 `ADVANCED_PASSWORD_NOT_SET`, 403 `ADVANCED_PASSWORD_INVALID`, khoá tạm 429 `RATE_LIMITED`).
- `make reset-advanced-password` (`entrypoints.reset_password --advanced`): mật khẩu ngẫu nhiên in một lần, hash lưu ở `settings` và không vào nhật ký.
- `make lint`, `make type-check` (82 file): đạt. `make test`: 280 passed, 25 skipped (7 test mới).

## Khác với bản cũ / kế hoạch (3e-1)
- `PATCH /admin/products/{id}` tạm chỉ nhận `price`; `barcode`/`name` trả 422 cho tới 3e-2 (ghi vào bảng của engine theo hướng C). Hoàn tác barcode/tên/bằng chứng/màu/thiết lập engine cũng trả 422 cho tới khi module tương ứng đăng ký bộ hoàn tác.
- Mật khẩu nâng cao chưa có route dùng (3e-4: `admin/config/apply`, `admin/validation`); đã có `SettingsService.verify_advanced_password` và test ở mức service. Bộ đếm sai mật khẩu nâng cao dùng chung giới hạn với đăng nhập (`LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`) nhưng đếm riêng.
- Bản cũ seed mật khẩu nâng cao lúc khởi động từ cấu hình; giờ chỉ đặt bằng lệnh.
- `MAX_PRICE` chuyển sang `catalog/schemas.py` (orders dùng lại).
