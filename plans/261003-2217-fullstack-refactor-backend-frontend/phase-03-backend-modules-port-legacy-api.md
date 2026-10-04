# Phase 03 — Backend modules (port API legacy)

**Priority:** P0 · **Status:** pending · Context: design.md §2 mapping; nguồn: `src_legacy/backend/{service,admin,security}.py`

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
