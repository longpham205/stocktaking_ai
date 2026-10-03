# Phase 01 — Backend skeleton, Docker, Makefile, compose

**Priority:** P0 · **Status:** pending · Context: design.md §1, §2, §4

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
