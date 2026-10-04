# Phase 04 — Recognition worker in-process + job state trong DB

**Priority:** P0 · **Status:** done (2026-10-04; phần lớn làm trong Phase 3: 3d, 3e-4) · Context: design.md §1 (đã chốt: inference trong process API)

## Các bước
1. `recognition/worker.py` `RecognitionWorker`: `asyncio.Queue(maxsize=queue_max)` → đầy thì 503 `QUEUE_FULL`; 1 task tiêu thụ; mỗi job `await loop.run_in_executor(executor_1_thread, recognizer.recognize, ...)`; `asyncio.wait_for(timeout_seconds)` → đánh dấu `error: TIMEOUT` (thread GPU vẫn chạy nốt — ghi rõ giới hạn như legacy).
2. Job state ghi vào `captures` (queued → processing → done/error, `detections`, `processing_time_ms`); `position` tính từ queue; khởi động: capture treo → `error: SERVER_RESTARTED`.
3. `reload(overrides)` / `reload_catalog` / `evidence_test` / `validation_run` submit vào cùng executor; `reload_lock`; cờ `system_reloading`, `validation_running` (→ 503 `SYSTEM_BUSY`).
4. `recognition/local_pipeline.py` bọc `engine.inference.infer.InferenceRunner` (free pipeline cũ + `torch.cuda.empty_cache` trước reload, rollback khi fail — ràng buộc GPU 4GB).
5. `recognition/mapper.py` (gộp accepted cùng SKU, uncertain tách dòng, thumbnail) và `recognition/fake.py` (port FakeExecutor).
6. Lifespan: start worker sau khi build recognizer; closer: dừng nhận job, chờ job đang chạy, shutdown executor.
7. Uvicorn `--workers 1` (ghi chú trong compose + Makefile).

## Done khi
`RECOGNIZER=fake make docker-up` → chụp ảnh → job done; `RECOGNIZER=local` + config demo (mock backends, CPU) chạy end-to-end; test worker (queue đầy, timeout, reload rollback, restart dọn job treo) xanh.

## Đã làm trước trong Phase 3 (2026-10-04)
- 3d: `recognition/worker.py` (queue + một luồng, timeout, `position`), job state trong `captures`, job treo báo `SERVER_RESTARTED` khi được hỏi (thay vì dọn lúc khởi động), `mapper.py`, `fake.py`, `local_pipeline.recognize`.
- 3e-2: `reload_catalog` trên luồng worker sau khi ghi catalog.
- 3e-4: `reload_pipeline` (giải phóng GPU, rollback), `validate`, evidence-test, cờ `reloading`/`validating`, `SYSTEM_BUSY`, `RELOAD_IN_PROGRESS`.

## Còn lại
- Bước 7: `--workers 1` ghi rõ trong compose + Makefile (kiểm lại cấu hình hiện có).
- "Done khi": `RECOGNIZER=fake make docker-up` -> chụp ảnh -> job done; `RECOGNIZER=local` + config demo chạy end-to-end qua API (chưa thử qua HTTP thật).

## Kết quả khép Phase 4 (2026-10-04)
- `--workers 1`: đã có trong `docker-compose.yml`, `docker-compose.prod.yml`, `Dockerfile`; Makefile ghi rõ lý do (model, hàng đợi, limiter, idempotency nằm trong bộ nhớ một process).
- `backend/scripts/smoke_api.py` + `make smoke`: kiểm đầu-cuối API đang chạy qua HTTP thật (đăng nhập, đơn, ảnh, job, khung + ảnh ký, sửa dòng, thanh toán, lịch sử, báo cáo, thử bằng chứng, thiết lập nâng cao, staff bị chặn khỏi admin).
- Chạy thật trên Docker (`make docker-up`, DB dev nạp catalog demo 50 SKU bằng `python -m engine.catalog.migrate --seed-dir data_demo/seed --legacy-config configs/legacy/config.demo.legacy.yaml --db-url postgresql+psycopg://...@postgres:5432/stocktaking --gallery-dir data_demo/gallery --benchmark-labels data_demo/benchmark/_annotations.coco.json` trong container `api`):
  - `RECOGNIZER=fake`: toàn luồng đạt (job xong 0,7 s).
  - `RECOGNIZER=local` + `PIPELINE_CONFIG=configs/config.demo.yaml` (engine thật, backend mock, CPU, image nhẹ): toàn luồng đạt (job 0,5 s); áp dụng `retrieval.top_k` qua `POST /admin/config/apply` (pipeline nạp lại 0,5 s), chụp ảnh sau khi nạp lại, hoàn tác về mặc định: đạt.
- Chưa thử: `make docker-up-gpu` (image CUDA nhiều GB, pipeline thật trên GPU).
