# Thiết kế: Stocktaking AI full-stack

## 1. Topology runtime (docker compose)

```
 browser ──► web (Vite dev :5173 | nginx prod :8080)
                │  /api/* proxy (same-origin, không CORS)
                ▼
             api  (uvicorn entrypoints.api:app :8000, --workers 1)
                ├─ FastAPI routers (async)
                └─ RecognitionWorker: asyncio.Queue(queue_max) + 1 task tiêu thụ,
                   gọi RecognizerPort qua ThreadPoolExecutor(max_workers=1) → GPU tuần tự
                ▼
             postgres  (web tables + catalog tables, Alembic sở hữu schema)
 volumes: ./data (gallery, cache/faiss, transactions) · ./weights (ro) · pgdata
```
- **Inference trong process API** (đã chốt): bắt buộc `--workers 1` vì model nằm trong RAM/GPU của process.
- Trạng thái job **lưu ở bảng `captures`** (queued/processing/done/error), không chỉ trong RAM → `GET /jobs/{id}` đọc DB; lúc khởi động, capture `queued/processing` còn sót bị đánh dấu `error: SERVER_RESTARTED`.
- Hàng đợi, idempotency key, login limiter: **in-memory** (1 process, KISS) — không cần Redis.
- Reload pipeline / validation / evidence-test submit vào **cùng** executor 1 luồng như legacy; `_reload_lock` → `system_reloading`; validation chạy → captures trả 503 `SYSTEM_BUSY`.
- `RECOGNIZER=fake` (Mac/dev/test) không import torch; `RECOGNIZER=local` lazy-import engine trong `_build`.

## 2. Backend layout

```
backend/
├── pyproject.toml · uv.lock · alembic.ini · Dockerfile · Dockerfile.dockerignore
├── migrations/            env.py (import mọi app.modules.*.models + engine catalog models), versions/0001_initial.py
├── entrypoints/
│   ├── api.py                     app = create_app()
│   ├── catalog_migrate.py         (thay `python -m src.catalog.migrate`)
│   ├── catalog_sync_gallery.py
│   ├── build_index.py             (thay `run.py --mode validate/infer` phần build FAISS)
│   ├── reset_password.py
│   └── import_legacy_sqlite.py    migrate data_demo/data app.db → Postgres (1 lần)
├── app/
│   ├── main.py            create_app(), _build() → Backends (recognizer chọn theo RECOGNIZER=local|fake)
│   ├── core/
│   │   ├── config.py      CoreSettings: DATABASE_URL, DATA_DIR, PIPELINE_CONFIG, APP_ENV, LOG_*
│   │   ├── db.py          create_engine
│   │   ├── base.py        DeclarativeBase + metadata
│   │   ├── backends.py    @dataclass Backends(auth?, catalog?, orders?, recognizer?, job_queue?, …, closers)
│   │   ├── deps.py        backends(request), require(value)
│   │   ├── errors.py      AppError(detail, status, code) → {"detail","code",...}
│   │   ├── logging.py     configure_logging + RequestLog middleware
│   │   ├── signed_url.py  HMAC media URL (dùng ≥2 module: captures, catalog gallery)
│   │   └── clock.py       timezone_offset_hours, "hôm nay" theo giờ VN (orders, reports)
│   └── modules/
│       ├── auth/          ports, config, models(users, shifts), repository, service, router, schemas, deps, exceptions, limiter.py
│       ├── users/         admin CRUD nhân viên (router/service/schemas) — dùng repo của auth qua port
│       ├── catalog/       search sản phẩm, giá, barcode, evidence, màu tham chiếu, gallery image; models(product_prices)
│       ├── orders/        order, items, checkout, void, history; models(orders, order_items)
│       ├── captures/      upload ảnh, idempotency, job status, media URL; models(captures)
│       ├── recognition/   ports.RecognizerPort; local_pipeline.py (engine), fake.py; mapper.py; worker.py (asyncio.Queue + executor 1 luồng)
│       ├── pos_settings/  settings công khai + advanced password; models(settings)
│       ├── engine_config/ registry (config_registry cũ), overrides, apply → recognizer.reload (rollback); models(config_overrides)
│       ├── audit/         change_log + revert; models(change_log)
│       ├── reports/       KPI, doanh thu theo ngày, top 5
│       └── validation/    chạy benchmark, trạng thái, so baseline
├── engine/                ← `src/` cũ (git mv), import `src.` → `engine.`; KHÔNG import fastapi/app; CLI `python -m engine` (run.py cũ)
├── configs/               config.yaml, config.demo.yaml, assets_manifest.json (pipeline config)
├── data/ · data_demo/ · weights/   runtime assets (gitignored trừ metadata/.gitkeep), mount vào container
├── debug/ · notebooks/    công cụ nghiên cứu engine (giữ nguyên vị trí tương đối → không phải sửa path)
├── scripts/               set_device, generate/verify_manifest, compare_golden/validate, make_demo_dataset, export_catalog_snapshot, check_gpu_capacity
└── tests/
    ├── conftest.py        env trước import app; migrated_database; api_app; client (httpx ASGITransport); auth fixture
    ├── fake_recognizer.py
    ├── api/…              test_auth.py, test_orders.py, test_captures.py, test_admin_products.py … (port từ test_backend_api.py)
    ├── test_*.py          test engine (giữ phẳng như cũ; có thể gom vào tests/engine/ sau)
    └── test_import_boundary.py   RECOGNIZER=fake: create_app() không kéo torch/faiss/transformers/sam2/easyocr
```

### Mapping legacy → module mới
| Legacy | Mới |
|---|---|
| `server.py` route table, `Handler` | `app/main.py` + `modules/*/router.py` dưới `APIRouter(prefix="/api")` |
| `security.py` (scrypt, token HMAC, LoginLimiter) | `auth/service.py` (giữ scrypt để hash cũ còn dùng được), `pyjwt` HS256, `auth/limiter.py` (in-memory) |
| `service.py` (orders, captures, jobs, queue thread) | `orders/`, `captures/`, `recognition/worker.py` (job state → DB) |
| `admin.py` (products, evidence, colors, config, validation, users, reports, change-log) | `catalog/`, `engine_config/`, `validation/`, `users/`, `reports/`, `audit/` |
| `inference.py` (LocalExecutor/FakeExecutor) | `recognition/local_pipeline.py` / `recognition/fake.py` (port `RecognizerPort`) |
| `mapper.py` | `recognition/mapper.py` |
| `catalog.py` (DbCatalog) | `catalog/repository.py` dùng `engine.catalog` qua adapter |
| `db.py` raw sqlite + ALTER | `*/models.py` + Alembic |
| `config.py` + `configs/backend.yaml` + `.env` tự sinh | `CoreSettings` + per-module `XSettings` (pydantic-settings, `.env`), `make .env` sinh secret |
| `config_registry.py` | `engine_config/registry.py` |
| static serve `frontend/` | bỏ — nginx/Vite phục vụ |

### Port nhận diện (điểm cắm ML)
```python
class RecognizerPort(Protocol):
    def recognize(self, image_path: Path, thresholds: Thresholds) -> RecognitionResult: ...
    def evidence_test(self, image_path: Path) -> EvidenceReport: ...
    def reload(self, overrides: dict[str, Any]) -> None: ...      # rollback nếu fail
    def reload_catalog(self) -> None: ...

def build_recognizer() -> tuple[RecognizerPort, Closer]:          # RECOGNIZER=local|fake
    if settings.recognizer == "fake": return FakeRecognizer(), noop
    from app.modules.recognition.local_pipeline import LocalRecognizer   # lazy: torch chỉ ở worker
    ...
```
`RecognitionWorker` sở hữu queue + executor; router `captures` chỉ gọi `worker.submit(capture_id)`.

## 3. Frontend layout

```
frontend/
├── package.json (pnpm) · vite.config.ts (/api proxy → API_PROXY_TARGET) · tsr.config.json · components.json
├── Dockerfile (deps/dev/build/prod-nginx) · nginx.conf · .env.example · eslint.config.js · tsconfig*.json
└── src/
    ├── main.tsx · index.css · routeTree.gen.ts
    ├── app/            query-client.ts, app-error-boundary.tsx
    ├── routes/
    │   ├── __root.tsx            beforeLoad guard (token) + role redirect
    │   ├── login.tsx · onboarding.tsx
    │   ├── pos.tsx (layout) · pos.capture.tsx · pos.orders.$orderId.tsx (invoice) · pos.orders.$orderId.pay.tsx · pos.orders.$orderId.done.tsx
    │   ├── history.tsx
    │   ├── admin.tsx (layout + tabs) · admin.reports.tsx · admin.products.tsx · admin.orders.tsx · admin.users.tsx · admin.settings.tsx · admin.advanced.tsx
    │   └── -components/  app-shell.tsx, route-error.tsx, route-pending.tsx, route-not-found.tsx
    ├── features/
    │   ├── auth/              (login form, onboarding)
    │   ├── capture/           camera viewfinder, use-tilt.ts, use-barcode-scanner.ts, downscale-image.ts (EXIF), job polling
    │   ├── invoice/           line items, capture-overlay (bbox 🟩🟨🟥), fix-item sheet, add-item search, zoom
    │   ├── payment/           cash/QR, receipt + print
    │   ├── history/
    │   ├── admin-reports/ · admin-products/ (evidence editor, color picker, change-log, evidence-test) · admin-orders/ · admin-users/ · admin-settings/ · admin-advanced/ (config registry, validation)
    └── shared/
        ├── api/        client.ts (apiFetch, Bearer, ApiError), errors.ts (map code → message i18n), keys.ts (qk), types.ts
        ├── auth/       auth-storage.ts (zustand vanilla, sessionStorage), use-auth.ts
        ├── components/ ui/ (shadcn), empty-state, error-state, money.tsx, confirm-dialog …
        ├── i18n/       config.ts, locales/vi.json (+ en.json nếu chọn)
        ├── hooks/ · lib/ (utils cn, format-vnd, normalize-vietnamese) · stores/ · theme/
    └── test/  setup.ts, test-utils.tsx (renderWithProviders, stubApiRoutes)
```
- Job polling: `useQuery({ refetchInterval: s => done ? false : 800 })` thay vòng lặp tay.
- Trạng thái đơn đang dở: server là nguồn sự thật (`GET /api/orders/open`) → không cần store riêng.

## 4. Makefile (root) — target chính
`setup` (.env + kiểm tra docker) · `docker-up` / `docker-up-gpu` / `docker-up-prod` / `docker-down` / `logs S=` · `migrate` / `migration MSG=` · `seed-demo` (catalog migrate + build index + prices cho data_demo) · `import-legacy DATA_DIR=` · `test` (backend) · `test-web` · `lint` (ruff + eslint) · `type-check` (mypy + tsc) · `db-save NAME=` / `db-restore NAME=` (pg_dump thay db_snapshot.py) · `reset-password USER=` · `check-env` · `clean`.
Backend dev cũng chạy được không cần docker: `make docker-up-data` (Postgres) rồi `cd backend && uv run uvicorn entrypoints.api:app --reload`.

## 5. Quyết định đã chốt (2026-10-03)
1. Engine ML: `git mv src backend/engine`, import `engine.`; `src_legacy/` chỉ chứa lớp web cũ + launcher + tkinter ui.
2. Database: Postgres + SQLAlchemy async Core + Alembic; catalog engine theo `DATABASE_URL`.
3. Inference: chạy trong process API (thread executor 1 luồng như legacy), job state lưu DB, không Redis.
4. i18n: chỉ `vi` (khung i18next sẵn để thêm ngôn ngữ sau).

Đã làm ở phase 0: `data/`, `data_demo/`, `weights/`, `debug/`, `notebooks/`, `tests/` nằm trong `backend/` cùng cấp `configs/` như layout cũ → mọi `parents[1]` / `config.parent.parent` giữ nguyên; `src/ui` (tkinter) → `src_legacy/engine/ui`.
