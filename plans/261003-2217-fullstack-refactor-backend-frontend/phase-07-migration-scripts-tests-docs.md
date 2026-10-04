# Phase 07 — Data migration, scripts, tests, docs, cleanup

**Priority:** P1 · **Status:** pending

## Các bước
1. `entrypoints/import_legacy_sqlite.py --sqlite data_demo/db/app.db`: copy users (giữ hash scrypt), orders/items/captures, prices, settings, overrides, change_log, catalog → Postgres; `make import-legacy DATA_DIR=data_demo`.
2. Scripts: `db_snapshot.py` → `make db-save/db-restore NAME=` (pg_dump/pg_restore, giữ kịch bản "demo_clean" của DEMO.md); `reset_password.py` → entrypoint dùng auth repository; `check_env.py` kiểm Postgres/weights/index/GPU thay vì sqlite + cổng.
3. E2E smoke thay `frontend_smoke.js`: Playwright (tuỳ chọn) hoặc script pytest gọi API + vitest UI. Đề xuất: Playwright 1 kịch bản happy-path chạy với `RECOGNIZER=fake`.
4. Docs: viết lại `README.md` (Quick start bằng make), `docs/WEB.md`, `docs/DEMO.md` (ngày demo với docker), `docs/03_DEVELOPMENT_RULES.md` (luật layer/import mới), thêm `docs/system-architecture.md`.
5. Code review (`code-reviewer`), xoá thứ thừa; giữ `src_legacy/` đến khi parity được xác nhận rồi mới quyết định xoá (git history vẫn còn).

## Done khi
Từ máy sạch: `make setup && make docker-up && make seed-demo` → demo chạy đủ luồng; mọi test (backend, engine, frontend) xanh.
