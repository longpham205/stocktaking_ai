# Phase 01 — Backend skeleton, Docker, Makefile, compose

**Priority:** P0 · **Status:** done (2026-10-04) · Context: design.md §1, §2, §4

## Các bước
1. `backend/app/core/*` theo reference: config (CoreSettings, `@lru_cache get_settings`), db, base, backends, deps, errors (`{"detail","code"}`), logging (+RequestLog), signed_url, clock.
2. `app/main.py`: `create_app()`, lifespan build/close `Backends`, `APIRouter(prefix="/api")`, `/healthz`; `_build()` lazy-import engine khi `RECOGNIZER=local`.
3. `entrypoints/api.py` (`app = create_app()`).
4. `backend/Dockerfile`: base `python:3.12-slim` + uv, `apt libzbar0 libgl1`, `ARG TORCH_VARIANT=cpu|cu128`; `Dockerfile.dockerignore` whitelist.
5. Root `docker-compose.yml`: `postgres:16`, `migrate` (one-shot), `api` (`--reload --workers 1`, mount `backend/app`, `backend/engine`, `entrypoints`), `web` (target dev). Volumes `./data`, `./data_demo`, `./weights:ro`.
   `docker-compose.gpu.yml`: `deploy.resources.reservations.devices: [nvidia]` + `TORCH_VARIANT=cu128` cho `api`. `docker-compose.prod.yml`: web target prod (nginx), không reload.
6. Root `Makefile` (target ở design.md §4), `.env.example` nhóm theo module, `make .env` sinh `JWT_SECRET`, `MEDIA_URL_SECRET`, `SEED_*_PASSWORD` bằng `openssl rand`.
7. Tooling: ruff (py312, line 100), mypy, pytest (`asyncio_mode=auto`).
8. `tests/test_import_boundary.py` + `tests/conftest.py` khung.

## Done khi
`make docker-up` → `GET :8000/healthz` ok; `make lint`, `make type-check`, `make test` xanh; import-boundary test xanh.

## Kết quả (2026-10-04, Windows 11, Python 3.12.4 qua uv, Docker Desktop)
- `make lint` (ruff check + format trên `app entrypoints migrations tests/api tests/test_import_boundary.py`): đạt.
- `make type-check` (mypy strict trên `app`, `entrypoints`, `migrations`): đạt.
- `make test` (bản cài nhẹ): 211 passed, 25 skipped (các test cần EasyOCR tự bỏ qua khi chưa cài `--extra ml`). Với bộ ML đầy đủ: 226 test engine đều chạy và đạt.
- `docker compose up --build --wait`: postgres healthy, `migrate` thoát 0, `api` healthy; `GET /healthz` -> `{"status":"ok"}`, `GET /api/health` -> `{"status":"ready","recognizer":"fake"}`. Image nhẹ 1,31 GB.

## Khác với kế hoạch
- Compose chưa có service `web`: `frontend/` chưa tồn tại, thêm ở phase 5.
- `make lint` chỉ quét code web mới. Engine còn 60 lỗi ruff có sẵn; sửa chúng đụng vào pipeline nên cần PR riêng có cổng so khớp kết quả.
- Ruff giữ `line-length = 120` như phase 0 đã đặt (bước 7 ghi 100).
- `.env` mới chỉ sinh `MEDIA_URL_SECRET`; `JWT_SECRET` và `SEED_*_PASSWORD` thuộc module auth (phase 3).
- `RecognizerPort` mới có `name` và `close()`; các phương thức nhận diện thêm ở phase 4 cùng worker.
- Có cả `/healthz` (tiến trình còn sống, không đụng DB, dùng cho healthcheck) và `/api/health` (DB trả lời, giữ hợp đồng `{"status": "ready"}` của bản cũ).
- URL DB mặc định dùng `127.0.0.1`: trên Windows + Docker Desktop, `localhost` thử IPv6 trước và mỗi kết nối treo ~20 s.

## Chưa kiểm
- `make docker-up-gpu` (image có bộ ML + torch cu128, vài GB) và `make docker-up-prod`: chưa build.
- `RECOGNIZER=local` qua API: chưa chạy (môi trường uv trên máy kiểm chưa cài `--extra ml`).
