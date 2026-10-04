# STOCKTAKING AI

## DEVELOPMENT RULES

**Version:** 0.1.0 (extended)

---

# Purpose

Mandatory code standards and module interaction principles for every file in the recognition engine, `backend/engine/` (Rules 1–18; this is the former `src/`). The web application in `backend/app/`, `backend/entrypoints/` and `frontend/` follows Rule 19.

Whenever code and this document disagree, **this document wins**.

---

# 1. General Principles

- **Single Responsibility Principle:** strictly per module (see `02_MODULE_SPECIFICATION.md`).
- **Separation of Concerns:** UI, business logic, pipeline control, and model inference are never mixed in one file.
- **Pipeline-Centered Design:** `InventoryPipeline` is the only orchestrator.
- **Configuration Driven:** zero hard-coded thresholds, paths, weights, including evidence-fusion weights and VAL metric selection.
- **DTO-Driven Communication:** only typed dataclasses cross module boundaries, never raw `dict`.

---

# 2. Python Environment

- Python `>= 3.11, < 3.13` (`backend/pyproject.toml`; the tooling targets 3.12)
- Dependencies are managed with `uv` (`uv sync`, `uv sync --extra ml`); there is no `requirements.txt`
- Use modern generic syntax:
  - `list[str]`
  - `X | None`

---

# 3. Formatting & Naming

Follow **PEP 8** and **Black** formatting.

- Maximum line length: **88 characters**
- Python files: `snake_case.py`
- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_CASE`
- Private helpers: `_leading_underscore`

---

# 4. Type Hints

Every public function and method must be fully typed.

**Forbidden:**

- Untyped public interfaces
- Missing return type annotations
- Missing parameter type annotations

---

# 5. Docstrings

Use **Google-style docstrings** on:

- Every public module
- Every public class
- Every public method

---

# 6. Structured Logging

Use Python `logging` through `core.logger` only.

**Never use `print()` in production code.**

Log levels:

| Level | Usage |
|---|---|
| `INFO` | Lifecycle milestones |
| `WARNING` | Recoverable anomalies, e.g. `"OCR orientation ambiguous"` |
| `ERROR` | Failures that can be handled or reported |
| `DEBUG` | Internal diagnostics, e.g. per-candidate evidence scores in `Reranker` |

---

# 7. Configuration Management

All configuration must be loaded through:

- `engine.core.config`
- `backend/configs/config.yaml`

This explicitly includes:

- Evidence-fusion weights: `rerank.*`
- Retrieval-consensus protection curve parameters
- VAL per-stage metric selection
- Thresholds
- Model weights
- Paths

None of these values may be hard-coded as source-level constants, even during active tuning.

**Product data is not configuration.** SKUs and recognition evidence (OCR keywords, colour codes, confusable pairs, forced plugins, colour references) live in the catalog database and are read through `CatalogRepository` — never in `config.yaml` and never as source constants (see Rule 19.5 and `04_DATA_AND_CATALOG.md`). The config only selects the catalog source (`catalog.source`).

See **Rule 18**.

---

# 8. Exception & Error Handling

Never silently swallow exceptions.

When an unexpected exception occurs:

1. Log the stack trace with `logger.exception()`.
2. Re-raise the exception, **or**
3. Handle a specific exception type with a documented recovery path.

### Example

`Refiner` may fall back to the original detector bounding box when a segmentation backend raises an exception.

The recovery behavior must be explicit and documented.

---

# 9. Import Statements

Use **absolute imports from the `engine` package** (`from engine.core.config import ...`); the process runs from `backend/`.

### Import order

1. Standard library
2. Third-party packages
3. Local `engine` modules

### Forbidden

- Wildcard imports: `from module import *`
- Relative imports when an absolute import is available

---

# 10. Module Autonomy & Public API Boundary

Only designated **public APIs** may be called across module boundaries.

Private (`_`-prefixed) helpers must never be accessed from another module.

This includes cases such as `Reranker` reaching into `DecisionEngine`.

For example, `evaluate_thresholds()` is intentionally a **public, pure method** rather than a private helper because it is part of the permitted public API.

---

# 11. Pipeline Orchestration Rules

## `pipeline.py`

`InventoryPipeline` is the only component responsible for orchestrating the complete pipeline.

### Allowed

- Initialize every component once
- Execute stages in sequence
- Pass DTOs between stages
- Branch on:
  - `needs_plugin`
  - `needs_refinement`

### Forbidden

- AI inference logic
- File I/O
- UI rendering
- Module-specific business logic

---

# 12. Per-Module Rules

See `02_MODULE_SPECIFICATION.md` for the authoritative and current per-module responsibility and forbidden-behavior list.

This document intentionally does not duplicate those rules to prevent the two documents from drifting apart during active development.

---

# 13. Standardized DTOs

Only dataclasses defined in:

```text
backend/engine/models/models.py
```

may cross module boundaries.

Modules must not exchange raw dictionaries as their primary interface.

---

# 14. Public API Standard Summary

```text
Detector.detect(
    image_data: ImageData
) -> DetectionResult

OverlapResolver.resolve(
    detection_result: DetectionResult
) -> OverlapResult

find_suspicious_pairs(
    detections,
    iou_threshold,
    overlap_ratio_threshold
) -> list[OverlapPair]

Refiner.refine(
    image_array,
    detection_result: DetectionResult,
    overlap_result: OverlapResult
) -> RefinementResult

Cropper.crop(
    image_data: ImageData,
    detection_result: DetectionResult,
    refinement_result: RefinementResult
) -> list[CropImage]

Retriever.retrieve(
    crop: CropImage
) -> RetrievalResult

DecisionEngine.decide(
    retrieval_result: RetrievalResult
) -> DecisionResult

DecisionEngine.evaluate_thresholds(
    similarity: float,
    detection_confidence: float
) -> tuple[str, float]

PluginManager.run_plugins(
    crop: CropImage,
    decision: DecisionResult
) -> PluginResult

Reranker.rerank(
    retrieval_result: RetrievalResult,
    plugin_result: PluginResult
) -> DecisionResult

InventoryPipeline.run(
    image_data: ImageData,
    similarity_threshold: float | None = None,
    min_confidence_accept: float | None = None,
) -> InventoryResult

InventoryPipeline.run_with_trace(
    image_data: ImageData
) -> tuple[InventoryResult, PipelineTrace]
```

---

# 15. Performance & Memory Management

Heavy model weights must be loaded **once in `__init__`**.

They must never be loaded inside a hot-path method.

### Exception

`DecisionEngine` and `Reranker` may be cheaply re-instantiated per call when a runtime threshold override is required.

This exception is permitted because neither component holds model weights.

---

# 16. Testability & Independence

Every module must be unit-testable in isolation using synthetic inputs.

See:

```text
backend/tests/conftest.py
```

This property is essential for debugging and must be preserved when extending the system.

---

# 17. Extensibility Standard

## New Detection / Retrieval / Refinement Backend

Adding a new backend should require:

1. One new file in the relevant `backends/` directory.
2. One new dispatch branch.
3. Zero unrelated file changes.

## New VAL Metric

Adding a new validation metric should require:

1. One new function in `metrics.py`.
2. Registration in `METRIC_REGISTRY`.
3. Reference by name in `config.yaml`.
4. **Zero changes to `evaluator.py`.**

---

# 18. Absolute Forbidden Checklist

The following rules are absolute:

- ❌ Never bypass `InventoryPipeline`.
- ❌ Never hard-code configuration values, paths, thresholds, or evidence-fusion weights.
- ❌ Never put AI model logic inside UI files.
- ❌ Never allow direct module-to-module dependencies outside the Pipeline flow.
- ❌ Never use `print()` statements.
- ❌ Never use wildcard imports.
- ❌ Never catch exceptions without logging stack traces.
- ❌ Never reload model weights for every incoming query image.
- ❌ Never let `ColorPlugin`, `OcrPlugin`, or `BarcodePlugin` read the product catalog or compare against reference values. That is `Reranker`'s responsibility only.
- ❌ Never let `OverlapResolver` remove or mutate a `Detection`. It is **not NMS**.
- ❌ Never let `Refiner` overwrite `DetectionResult`.

---

# 19. Web Application Rules

The web POS (`backend/app/`, `backend/entrypoints/`, `frontend/`) is a layer **around** the engine and must never change how it works. Architecture: `docs/system-architecture.md`.

## 19.1 Boundary with the engine

1. **The engine never imports the web.** Nothing in `backend/engine/` imports `app`, `entrypoints` or FastAPI. The engine must keep running standalone (`python -m engine --mode infer|validate`, notebooks, Colab).
2. **The web reaches the engine through one port.** Recognition, catalog reload, pipeline reload and validation go through `RecognizerPort` (`app/modules/recognition/ports.py`) and are executed by `RecognitionWorker` on its single thread. No router or service calls `InventoryPipeline`, a detector, a retriever or a plugin.
3. **Engine imports in `app/` are lazy.** Outside `recognition/local_pipeline.py` (itself imported only when `RECOGNIZER=local`), `import engine...` appears only inside functions, and only in `catalog/`, `engine_config/` and `validation/`. With `RECOGNIZER=fake` the API must start without importing `engine`, torch, faiss, transformers, sam2, easyocr, rfdetr or cv2; `tests/test_import_boundary.py` enforces it.
4. **Catalog writes go through the engine's functions** (`engine.catalog.edits`, validated by `engine.catalog.validation`), never through hand-written SQL on the catalog tables. Reads for web screens may query those tables in `catalog/repository.py`.
5. **Pass thresholds per call, do not mutate config.** Per-capture behaviour (`similarity_threshold`, `min_confidence_accept`) is an argument of `recognize`. Engine settings change only through `engine_config` (stored in `config_overrides`, applied by rebuilding the pipeline, rolled back on failure).

## 19.2 Layers inside a module

Each module in `app/modules/<name>/` has `models.py` (tables), `repository.py`, `service.py`, `schemas.py`, `ports.py`, `deps.py`, `router.py`.

1. **SQL lives in `repository.py` only** (SQLAlchemy Core, async). Routers and services never build queries.
2. **Routers are thin**: parse the request, call one service method, return its schema. Business rules live in `service.py`.
3. **Modules depend on each other's ports**, not on each other's repositories. Wiring happens in one place: `app/main.py` builds every service into `Backends`.
4. **Several statements that must succeed together share one transaction**, following `orders/repository.py` (`read()` / `write()` units on one connection; rows locked with `lock=True`).
5. **The schema belongs to Alembic.** Change `models.py`, then `make migration MSG="..."`. Nothing creates or alters tables at start-up.
6. **One API process.** The model, the recognition queue, the login limiter and the idempotency keys live in the process's memory: `--workers 1` is mandatory. Anything that must survive a restart goes to the database (a capture's job state is in `captures`).

## 19.3 Errors and language

1. Business errors raise `AppError` (or a subclass in `app/core/errors.py`) and reach the client as `{"detail": "<message>", "code": "<CODE>"}`. Error codes and HTTP statuses are part of the contract with the frontend: do not rename them.
2. `detail` and every text the user sees are Vietnamese. Docstrings, comments and commit messages of the web code are English. The engine keeps its Vietnamese messages.
3. No fallback that hides a failure: missing data is reported, not replaced by a default.

## 19.4 Safe by default

The application is exposed through a public tunnel in demos.

1. Every endpoint except `/healthz`, `/api/health` and `/api/auth/login` needs a token; admin endpoints need the admin role. Media and gallery images are the exception by design: they are fetched by `<img>` tags, so they are protected by a signed, expiring URL instead.
2. There are no default accounts and no default secrets. Secrets come from `.env` (`make setup` generates them); passwords are created with `make reset-password` and printed once. Never commit `.env`, never log or print a secret or a password.
3. Generated API docs (`/docs`, `/openapi.json`) are served only when `APP_ENV=dev`.
4. Uploaded images are verified by decoding them (never trust `Content-Type`) and have a size limit.
5. One open shift per account. Wrong passwords are rate-limited; the advanced password has its own limiter.
6. The API has no CORS middleware: the SPA reaches it same-origin through the Vite proxy or nginx.

## 19.5 Data rules

1. **Business data changes are logged.** Every admin edit of prices, barcodes, names, evidence, colour references, POS settings or engine settings writes `change_log` rows (old value, new value, user) in the same transaction, through `audit.repository.record_changes`. A module that wants its changes revertible registers a reverter with `AuditService.register` in `app/main.py`.
2. **Identifiers are stable.** `product_id` equals the benchmark `category_id` and is never renumbered, reused or edited through the UI.
3. **Evidence is explicit.** The Reranker and every plugin use only evidence declared in the catalog (OCR keywords, colour code, barcode, confusable pairs, forced plugins), read through `CatalogRepository`. They must not infer evidence from a product name or folder name; undeclared evidence scores 0. See `docs/04_DATA_AND_CATALOG.md`.

## 19.6 Frontend

1. TypeScript strict. Routes are declared in code in `src/router.tsx`; one folder per feature under `src/features/`.
2. Server data goes through TanStack Query with keys from `src/lib/query-keys.ts`; every request goes through `apiFetch` (`src/lib/api-client.ts`). The server is the source of truth: no client-side copy of an order.
3. Error messages shown to the user are derived from the API error in `src/lib/errors.ts`, in one place.
4. No new dependency without asking (decided: no global store, no i18n library, no file-based routing, no ESLint).

## 19.7 Tests

1. API tests (`backend/tests/api/`) run against a real Postgres database (`stocktaking_test`), with the fixtures of `tests/api/conftest.py` (`db_app`, `http`, `token_for`, `catalog_url`).
2. Frontend tests (vitest) render through `renderApp` and stub the network with `stubFetchRoutes` (`src/test/test-utils.tsx`).
3. `make smoke` drives a running API over real HTTP.

## 19.8 Gates

Run from the repo root before every push:

| Change | Gate |
|---|---|
| any backend web code | `make format`, `make lint`, `make type-check`, `make test` |
| any frontend code | `make type-check`, `make test-web`, `cd frontend && pnpm build` |
| `backend/engine/`, pure refactor | engine tests, then the demo validation compared with `--exact`: zero differences |
| `backend/engine/`, behaviour change | the real validation, not lower than the baseline, with every difference explained |

Engine gates run from `backend/` with an environment that has the ML stack:

```text
python -m pytest -q --ignore=tests/api --ignore=tests/test_import_boundary.py
python -m engine --mode validate --config configs/config.demo.yaml --benchmark-dir data_demo/benchmark
python scripts/compare_validate.py data_demo/outputs data/baseline/demo/report.json --exact
python -m engine --mode validate
python scripts/compare_validate.py data/outputs data/baseline/report.json --exact --ignore crop_id
```

`make lint` and `mypy` cover the web code only (`app`, `entrypoints`, `migrations`, `tests/api`); the engine predates both gates.

## 19.9 Git

One branch per task, cut from the branch it builds on (`refactor/...`, `feature/...`, `opt/...`); small commits; push the **branch**; open a pull request; merge on GitHub after review. Never push `main`.

---

# Appendix: Debugging History

> This section is kept for context. Do not repeat these mistakes.

## 1. `product_id` Identity Bug

A code path populated the catalog `product_id` field with the gallery folder name instead of the stable numeric ID.

Every module that consumes `product_id` must treat it as an **opaque numeric-string identifier**.

Never assume that `product_id`:

- is human-readable;
- can be derived from a display name;
- is identical to a gallery folder name.

---

## 2. SigLIP2 Unpooled-Embedding Bug

`get_image_features()` returned per-patch features:

```text
(1, 196, 768)
```

These were silently truncated to a single patch by naive reshaping downstream.

Any new embedding backend must explicitly verify that its output shape is:

```text
(1, hidden_dim)
```

The embedding must be **pooled**, not:

```text
(1, num_patches, hidden_dim)
```

Do not assume that a Hugging Face model method name implies the expected output shape.

---

## 3. Shared-Resolution Crop Bug

OCR previously received a crop resized for Retrieval, which destroyed small text.

This is why `CropImage` carries **two resolutions**.

See:

```text
01_PROJECT_CONTEXT.md
Section 3.5
```

Do not reintroduce a single shared crop resolution for a new plugin without explicitly considering the requirements of that plugin.

---

## 4. Additive Evidence-Fusion Bug

A naive confidence-boost addition in `Reranker` corrected approximately as many cases as it broke.

Therefore, any new evidence source must integrate through the **retrieval-consensus protection mechanism**.

### Forbidden

Bypassing the protection mechanism with a flat additive boost.

---

## 5. Barcode Plugin Single-Attempt Bug

The original barcode plugin called:

```python
pyzbar.decode()
```

exactly once on a raw crop and gave up.

This failed under common real-world conditions such as:

- Rotation
- Low contrast
- Different orientations
- Image quality variations

Any adaptive-decode-style plugin, including barcode and potential future OCR refinements, should follow the established pattern:

1. Perform a cheap presence/region pre-check.
2. Commit to expensive preprocessing only when appropriate.
3. Execute cumulative fallback stages.
4. Retry the **actual decode operation** at each stage.
5. Exit early on the first successful decode.

### Forbidden

A single best-effort decode attempt is not sufficient for adaptive decoding.
