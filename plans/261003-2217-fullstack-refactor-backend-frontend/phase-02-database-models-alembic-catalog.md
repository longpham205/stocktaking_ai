# Phase 02 — DB models, Alembic, catalog theo DATABASE_URL

**Priority:** P0 · **Status:** pending · Context: design.md §5.2 (giả định chọn Postgres)

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
