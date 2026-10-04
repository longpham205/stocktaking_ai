# Plan: Refactor Stocktaking AI → `backend/` (FastAPI, pattern `demo/app`) + `frontend/` (Vite React, pattern `demo/web`)

Status: **APPROVED design, chưa implement** · Quyết định: [design.md §5](design.md#5-quyết-định-đã-chốt-2026-10-03) · Branch đề xuất: `refactor/fullstack-layout`
Tham chiếu: `blog-code.henryonai.com/packages/system_design_how_to_design_chatgpt/demo/{app,web}`
Thiết kế chi tiết: [design.md](design.md)

## Hiện trạng (tóm tắt)
- `backend/` (~2.6k LOC): stdlib `ThreadingHTTPServer`, regex route table, ~35 endpoint `/api/*`, raw `sqlite3`, token HMAC tự chế, 1 worker thread GPU, job/idempotency trong RAM.
- `frontend/`: vanilla JS 979 dòng (`innerHTML` + `data-act`), 9 màn + 6 tab admin, chuỗi tiếng Việt hard-code.
- `src/`: ML pipeline (detection/SAM2/SigLIP2/FAISS/OCR/color/barcode/rerank) + catalog SQLModel trên **cùng** `app.db`.
- Nhiều script/debug/notebook/test phụ thuộc `parents[1]` + `from src...`.

## Layout đích (root chỉ còn những thứ này)
```
stocktaking_ai/
├── Makefile · docker-compose.yml · docker-compose.gpu.yml · .env.example · README.md
├── backend/      FastAPI app + engine ML + configs/ data/ weights/ + scripts/ debug/ notebooks/ tests/ (uv, py3.12)
├── frontend/     Vite + React 19 + TS + TanStack Router/Query + shadcn + zustand + i18n
├── src_legacy/   code web cũ đóng băng (backend/, frontend/, bin/, launch.bat, tkinter ui, …)
└── docs/ · plans/
```

## Phases
| # | Phase | File | Status |
|---|-------|------|--------|
| 0 | Chuẩn bị: branch, `src_legacy/`, chuyển engine | [phase-00](phase-00-prepare-legacy-move-and-engine-relocation.md) | **done** (2026-10-03) |
| 1 | Backend skeleton: core, create_app, entrypoints, Docker, Makefile, compose | [phase-01](phase-01-backend-skeleton-core-docker-makefile.md) | **done** (2026-10-04) |
| 2 | DB: models + Alembic + catalog repo theo `DATABASE_URL` | [phase-02](phase-02-database-models-alembic-catalog.md) | **done** (2026-10-04; repository + đường ghi catalog chuyển sang phase 3) |
| 3 | Backend modules (auth, catalog, orders, captures, recognition, admin…) | [phase-03](phase-03-backend-modules-port-legacy-api.md) | done (2026-10-04) |
| 4 | Recognition worker in-process + job state trong DB | [phase-04](phase-04-inference-worker-and-job-queue.md) | done (2026-10-04, mostly within phase 3) |
| 5 | Frontend scaffold + shared (api, auth, i18n, ui) | [phase-05](phase-05-frontend-scaffold-and-shared-layer.md) | done (2026-10-04) |
| 6 | Frontend features: POS flow + Admin | [phase-06](phase-06-frontend-features-pos-and-admin.md) | done (2026-10-04); phone smoke over HTTPS left to do by hand |
| 7 | Data migration, scripts, tests, docs, cleanup | [phase-07](phase-07-migration-scripts-tests-docs.md) | pending |

Thứ tự: 0 → 1 → 2 → 3 ∥ 5 → 4 → 6 → 7 (frontend scaffold làm song song với backend modules vì hợp đồng API giữ nguyên path).

## Nguyên tắc xuyên suốt
- **Giữ nguyên hợp đồng nghiệp vụ** (path `/api/...`, ý nghĩa field, error code như `PRICE_MISSING_BLOCKED`, `QUEUE_FULL`, `SYSTEM_BUSY`) → frontend mới và legacy so sánh được 1-1.
- Engine ML **không** biết gì về web; backend gọi engine qua port `RecognizerPort` (adapter `local` / `fake`).
- Inference chạy trong process API (đã chốt) nhưng engine **lazy-import**: `RECOGNIZER=fake` không kéo torch (test import-boundary).
- Mỗi phase kết thúc phải: `make lint`, `make test` xanh, `make docker-up` chạy được.

## Rủi ro chính
- GPU trong Docker (NVIDIA Container Toolkit trên Windows/Linux; Mac chỉ CPU + mock) → `docker-compose.gpu.yml` override.
- Python: engine cần **3.11–3.12** (torch/sam2/faiss) ≠ reference 3.14 → dùng 3.12.
- Camera trên điện thoại cần HTTPS → giữ cách port-forward cũ, hoặc thêm Caddy (tuỳ chọn).
- Rewrite UI 1k dòng có nhiều chi tiết vi mô (tilt, scanner USB, EXIF, bbox overlay, in hoá đơn) → checklist parity ở phase 6.

## Câu hỏi còn mở
- HTTPS cho camera điện thoại: giữ port-forward hay thêm Caddy?
- E2E thay `frontend_smoke.js`: Playwright hay chỉ vitest + test API?
