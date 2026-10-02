# Gói 04 — bản vá `src/` (P1-1, P1-2, P1-4) + web nâng cấp

## Nội dung
**Bản vá pipeline (chạm `src/`, cần qua cổng kiểm chứng):** chỉ 3 file — `src/models/models.py`, `src/pipeline/pipeline.py`, `src/inference/infer.py` (giữ nguyên CRLF; xem `src_p1.patch` để review).
- `InventoryResult.has_overlap` (True khi số cặp chồng lấp ≥ `overlap.min_overlapping_pairs`, không bật với chồng lấp nhẹ).
- `min_confidence_accept` override theo từng lần gọi (`run`, `run_single`, `run_batch`) + **cache LRU 8 mục** cho cặp (DecisionEngine, Reranker) theo hai ngưỡng.
- `run_single(..., persist=False)` bỏ qua `save_all` (không ghi đè file kết quả dùng chung).
- Tham số mới đều **ở cuối và có mặc định** → mọi chỗ gọi cũ giữ nguyên hành vi.

**Web:** timeout cứng cho job, banner "chụp thêm" khi có chồng lấp, admin chỉnh `min_confidence_accept`, tự sao lưu DB lúc khởi động (giữ 7 bản), `scripts/db_snapshot.py`.

## Áp dụng và kiểm chứng (làm đúng thứ tự)
1. **Trước khi chép file**, chụp golden baseline trên code hiện tại (chưa vá):
   ```powershell
   python scripts\compare_golden.py --config configs\config.demo.yaml --images data_demo\golden\images --expected data_demo\golden\expected.json --save data\baseline\demo_golden\golden.json
   ```
2. Đảm bảo có baseline validate: lưu bản chạy gốc ổn định của bạn, ví dụ `Copy-Item data_demo\outputs_run1 data\baseline\demo -Recurse`.
3. Giải nén gói vào thư mục dự án (ghi đè). `git diff --stat` chỉ được thấy **3 file trong `src/`** (ngoài file mới).
4. `python -m pytest -q` → kỳ vọng **133 passed + test mới** (`test_pipeline_overrides.py` 6, `test_infer.py` 4, `test_backend_api.py`, `test_db_snapshot.py`). Nếu có test cũ fail, **dán tên test + lỗi**: có thể do field mới `has_overlap` xuất hiện trong JSON kết quả.
5. **Cổng G-demo `--exact`** (refactor thuần, báo cáo phải giống hệt):
   ```powershell
   python run.py --mode validate --config configs\config.demo.yaml --benchmark-dir data_demo\benchmark
   python scripts\compare_validate.py data_demo\outputs data\baseline\demo\report.json --exact
   ```
   Kỳ vọng `ĐẠT`, 0 khác biệt. (Nếu thư mục output của bạn khác, dùng đúng đường dẫn chứa `report.json`.)
6. **Golden so baseline:**
   ```powershell
   python scripts\compare_golden.py --config configs\config.demo.yaml --images data_demo\golden\images --expected data_demo\golden\expected.json --baseline data\baseline\demo_golden\golden.json
   ```
   Kỳ vọng mọi ảnh "giống hệt".

Bước 4–6 đều đạt thì bản vá coi như xong (P1-1, P1-2, P1-4).

## Dùng web sau khi vá
`python -m backend --config configs\config.demo.yaml --data-dir data_demo` — web tự nhận biết pipeline đã vá (`persist=False`, `min_confidence_accept`, `has_overlap`); chưa vá thì vẫn chạy, chỉ bỏ qua các tính năng đó (có cảnh báo log).

## Sao lưu DB (demo)
```powershell
python scripts\db_snapshot.py purge-orders --data-dir data_demo    # xoá đơn thử, giữ tài khoản/giá/barcode (server phải dừng)
python scripts\db_snapshot.py save demo_clean --data-dir data_demo
python scripts\db_snapshot.py restore demo_clean --data-dir data_demo   # trước giờ demo; mọi phiên cũ bị vô hiệu
python scripts\db_snapshot.py list --data-dir data_demo
```
