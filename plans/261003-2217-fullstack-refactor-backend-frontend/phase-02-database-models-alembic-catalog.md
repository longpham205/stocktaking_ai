# Phase 02 — DB models, Alembic, catalog theo DATABASE_URL

**Priority:** P0 · **Status:** in progress (2a done 2026-10-04; 2b pending) · Context: design.md §5.2 (giả định chọn Postgres)

## Các bước
1. `*Row` models (SQLAlchemy Core-style declarative) theo bảng legacy `src_legacy/backend/db.py`: users, shifts (partial unique index open shift/user), orders, order_items, captures (+ `detections` JSONB, `job_status`, `job_error`), product_prices, settings (JSONB), config_overrides, change_log. Tiền: `BIGINT` (VND), thời gian: `timestamptz`.
2. Engine catalog (`engine/catalog/db.py` SQLModel): nhận `db_url` thay `db_path` (config key `catalog.db_url`, fallback `sqlite:///…` từ `db_path` để notebook/Colab còn chạy). Bảng `product`, `product_evidence`, `color_reference`, `catalog_meta` cũng do Alembic quản (import metadata SQLModel trong `migrations/env.py`).
3. `migrations/versions/0001_initial_schema.py` (autogenerate rồi review tay).
4. Repository pattern: chỉ `repository.py` chạy SQL; trả dataclass trong `ports.py`.
5. Catalog writes trong admin (legacy dùng raw SQL bypass repo) → đi qua `engine.catalog.repository` (thêm method write) để engine và web chung một đường ghi.

## Done khi
`make migrate` tạo schema sạch; `make migration MSG=x` không sinh diff thừa; test repository chạy trên Postgres test DB (skip nếu không có).

## Rủi ro
SQLModel sync engine trong worker vs async engine trong API — chấp nhận 2 driver (`psycopg` sync cho engine, `asyncpg` cho app); cùng 1 DB.

## Tách thành hai PR
- **2a (xong, 2026-10-04):** bước 1 và 3 cho 9 bảng web. Không đụng engine.
- **2b (chưa làm):** bước 2 và 5 (catalog của engine theo `db_url`, bảng catalog do Alembic quản, đường ghi catalog). Đụng `engine/catalog/` nên phải qua cổng so khớp kết quả (pytest đủ ML, G-demo `--exact`, golden).

## Kết quả 2a (Windows 11, Python 3.12.4, Postgres 16 trong Docker)
- `make migrate` trên DB trống: tạo 9 bảng + `alembic_version`; `alembic check`: không có khác biệt.
- `make lint`, `make type-check` (36 file): đạt. `make test`: 218 passed, 25 skipped (7 test schema mới chạy trên DB `stocktaking_test`, migration được chạy up -> down -> up).

## Khác với kế hoạch (2a)
- `captures`: chỉ có `job_status` (`queued|processing|done|error`) và `job_error` (JSONB `{code, message}`), không giữ thêm cặp `status`/`error` của bản cũ — hai cặp cột cùng mô tả một việc thì dễ lệch nhau. `timeout` của bản cũ thành `job_status='error'` + `job_error.code='PIPELINE_TIMEOUT'`.
- Cờ 0/1 của SQLite thành `boolean`; `evidence_json`/`detections_json`/`value_json` thành cột JSONB `evidence`/`detections`/`value`.
- Thêm ràng buộc mà bản cũ chỉ kiểm trong code: `role`, `orders.status`, `quantity >= 1`, `price >= 0`. `change_log.changed_by` là khoá ngoại `ON DELETE SET NULL`.
- Bước 4 (repository + ports) chưa làm ở 2a: viết cùng từng module ở phase 3, khi đã có service dùng chúng. Vì vậy test của 2a là test schema (ràng buộc, mặc định, kiểu dữ liệu), chưa phải test repository.
- Test cần Postgres báo lỗi rõ ("start it with `make docker-up-data`") thay vì tự bỏ qua; `make test` tự bật Postgres.

## Cần quyết trước 2b / phase 3
- Thư viện mới `psycopg` (driver đồng bộ cho engine).
- Một nguồn URL: tiến trình API truyền `DATABASE_URL` cho engine dưới dạng ghi đè `catalog.db_url`, không ghi URL vào `config.yaml`; có cả `db_url` lẫn `db_path` thì báo lỗi.
- Tính nguyên tử khi admin sửa catalog: bản cũ ghi catalog và `change_log` trong CÙNG giao dịch. Nếu catalog ghi qua engine (kết nối đồng bộ) còn `change_log` ghi qua app (async) thì là hai giao dịch.
