# Phase 07 — Data migration, scripts, tests, docs, cleanup

**Priority:** P1 · **Status:** in progress (7a dữ liệu + lệnh vận hành: done 2026-10-04; 7b tài liệu, 7c rà soát: pending)

## Các bước
1. `entrypoints/import_legacy_sqlite.py --sqlite data_demo/db/app.db`: copy users (giữ hash scrypt), orders/items/captures, prices, settings, overrides, change_log, catalog → Postgres; `make import-legacy DATA_DIR=data_demo`.
2. Scripts: `db_snapshot.py` → `make db-save/db-restore NAME=` (pg_dump/pg_restore, giữ kịch bản "demo_clean" của DEMO.md); `reset_password.py` → entrypoint dùng auth repository; `check_env.py` kiểm Postgres/weights/index/GPU thay vì sqlite + cổng.
3. E2E smoke thay `frontend_smoke.js`: Playwright (tuỳ chọn) hoặc script pytest gọi API + vitest UI. Đề xuất: Playwright 1 kịch bản happy-path chạy với `RECOGNIZER=fake`.
4. Docs: viết lại `README.md` (Quick start bằng make), `docs/WEB.md`, `docs/DEMO.md` (ngày demo với docker), `docs/03_DEVELOPMENT_RULES.md` (luật layer/import mới), thêm `docs/system-architecture.md`.
5. Code review (`code-reviewer`), xoá thứ thừa; giữ `src_legacy/` đến khi parity được xác nhận rồi mới quyết định xoá (git history vẫn còn).

## Done khi
Từ máy sạch: `make setup && make docker-up && make seed-demo` → demo chạy đủ luồng; mọi test (backend, engine, frontend) xanh.

## Chia PR (người dùng duyệt 2026-10-04)
| PR | Nội dung | Trạng thái |
|---|---|---|
| 7a | `seed-demo`, `import-legacy`, `db-save`/`db-restore`, `purge-media`, `check-env` | **done** (2026-10-04) |
| 7b | README, WEB.md, DEMO.md, luật phát triển, system-architecture, sơ đồ mới | pending |
| 7c | tự review, dọn thừa, chạy mọi cổng, thử từ DB mới tinh | pending |
Người dùng chốt: KHÔNG thêm Playwright (bước 3 của kế hoạch gốc): đã có `make smoke` (API qua HTTP thật) và test vitest cho UI.

## Kết quả 7a
- `make seed-demo` (`entrypoints/seed_demo.py`): catalog demo qua lệnh migrate của engine + giá từ `data_demo/seed/product_prices.json`; chạy lại không chèn trùng, không ghi đè giá đã có.
- `make import-legacy DATA_DIR=… [REPLACE=1]` (`entrypoints/import_legacy_sqlite.py`): mở SQLite chỉ đọc; một giao dịch; từ chối khi đích đã có dữ liệu trừ khi `--replace` (xoá bảng web + catalog trước); `--database-url` để nhập vào database khác. Chuyển đổi: thời gian text -> timestamptz, JSON text -> JSONB, `captures.status` -> `job_status` + `job_error`, `detections_json {w,h,boxes}` -> `detections` + `image_width/height`, đường dẫn ảnh -> tương đối `<đơn>/<file>`, ca còn mở -> đóng, `change_log.changed_by` trỏ tài khoản đã xoá -> null; nối lại sequence id. KHÔNG chép file ảnh (chép thư mục `transactions/` cũ vào `MEDIA_DIR`).
- `make db-save NAME=` / `make db-restore NAME=` (pg_dump/pg_restore trong container, file ở `backups/`, đã gitignore), `make purge-media DAYS= [DRY_RUN=1]` (`entrypoints/purge_media.py`), `make check-env` (`entrypoints/check_env.py`: bí mật — không in giá trị, DB + migration + số SKU/giá/tài khoản, pipeline, MEDIA_DIR, GPU).
- Test: `tests/api/test_data_ops.py` (3: import đầy đủ rồi đăng nhập bằng mật khẩu cũ và mở lại đơn cũ qua API; giá seed; dọn ảnh). `make lint`, `make type-check` (112 file) đạt; `make test` 303 passed, 25 skipped.
- Chạy thật trên Docker: `check-env` toàn OK; `seed-demo` lần hai chèn 0; `db-save NAME=dev_before_phase7` tạo file dump; `purge-media` chạy khô.
- Thử với `app.db` THẬT (người dùng cho phép; bản sao `backend/data/db/app.db`, mở chỉ đọc, mã băm trước/sau giống nhau) vào database riêng `stocktaking_import`: 22 SKU, 30 bằng chứng, 12 màu, 2 tài khoản, 6 ca, 4 đơn, 29 dòng hàng, 4 lượt chụp, 14 giá, 16 dòng nhật ký. Đối chiếu với nguồn: số dòng 12 bảng, tổng tiền đơn (920.000), tổng giá, tiền các ca, và phiên bản catalog do engine tính từ nội dung (`fda7b1206cc8f7fe`) đều KHỚP.

## Chưa làm (7a)
- `make db-restore` chưa chạy thử (thay toàn bộ DB dev; cần dừng `api` trước).
- Chưa mở web trên dữ liệu thật đã nhập (database `stocktaking_import` còn đó: trỏ `DATABASE_URL` sang nó để xem).
