# Phase 04 — Recognition worker in-process + job state trong DB

**Priority:** P0 · **Status:** pending · Context: design.md §1 (đã chốt: inference trong process API)

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
