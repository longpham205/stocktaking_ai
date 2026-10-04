# Phase 02 — DB models, Alembic, catalog theo DATABASE_URL

**Priority:** P0 · **Status:** done (2a, 2b: 2026-10-04) — trừ bước 4 và 5, chuyển sang phase 3 · Context: design.md §5.2 (giả định chọn Postgres)

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
- **2b (xong, 2026-10-04):** bước 2 (bước 5 chuyển sang phase 3, xem dưới). Gốc kế hoạch: bước 2 và 5 (catalog của engine theo `db_url`, bảng catalog do Alembic quản, đường ghi catalog). Đụng `engine/catalog/` nên phải qua cổng so khớp kết quả (pytest đủ ML, G-demo `--exact`, golden).

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

## Kết quả 2b
- Engine: `catalog.source` thêm giá trị `database` + khoá `catalog.db_url`; `make_engine_from_url`, `DatabaseCatalogRepository`; `BuildPipeline` đồng bộ gallery được trên cả hai nguồn; CLI `engine.catalog.migrate` và `engine.catalog.sync_gallery` nhận `--db-url` (loại trừ với `--db`). `build_config(..., catalog_db_url=...)` thay cả khối `catalog` bằng nguồn `database`.
- App: `LocalRecognizer` truyền `DATABASE_URL` (đổi driver sang psycopg) làm `catalog_db_url`, nên URL và mật khẩu không nằm trong YAML. Thư viện mới: `psycopg[binary]`.
- Alembic: migration `0002` tạo `product`, `product_evidence`, `color_reference`, `catalog_meta` từ chính định nghĩa SQLModel của engine (`migrations/env.py` nhận hai metadata).
- `make lint`, `make type-check` (37 file): đạt. `make test` (bản nhẹ): 225 passed, 25 skipped. Test engine trên venv đủ ML: 229 passed.
- Cổng engine (đường SQLite không đổi hành vi): G-demo `compare_validate.py --exact` 0 khác biệt; golden đạt.
- Cổng bổ sung cho đường mới: nạp catalog demo vào một DB Postgres tạm bằng `migrate --db-url`, chạy `python -m engine --mode validate` với `catalog.source: database`, so `--exact` với baseline demo: **0 khác biệt** (50 SKU đọc từ Postgres).

## Khác với kế hoạch (2b)
- Không có "fallback `sqlite:///` từ `db_path`": nguồn được chọn tường minh bằng `catalog.source` (`sqlite` | `database` | `snapshot`); thiếu khoá của nguồn đã chọn, hoặc khai báo cả `db_url` lẫn `db_path`, đều là lỗi cấu hình.
- Bước 5 (đường ghi catalog qua engine) chưa làm: cần câu trả lời về tính nguyên tử catalog + `change_log` (mục "Cần quyết" ở trên) và sẽ viết cùng module quản trị ở phase 3.
- Kiểu cột của bảng catalog giữ nguyên như engine đang dùng (thời gian là chuỗi ISO, JSON là text), để một định nghĩa chạy được trên cả SQLite lẫn Postgres.

## Chưa kiểm (2b)
- `RECOGNIZER=local` qua API với catalog Postgres: mới kiểm đường đọc bằng CLI; worker và API dùng pipeline thật là phase 4.
- Validate thật trên GPU sau 2b: chạy riêng, kết quả ghi trong PR.
