# Gói 07 — `detected_count` + banner "vật chưa nhận diện được"

**Chạm `src/` (2 file, cần cổng như lần trước):** `src/models/models.py`, `src/pipeline/pipeline.py` — thêm `InventoryResult.detected_count` (số vật detector tìm thấy trước bước quyết định). Hai file này **đã gồm** bản vá gói 04 (`has_overlap`, override, cache), có thể chép đè trực tiếp.

## Kiểm chứng (đúng thứ tự)
1. (Tuỳ chọn, nếu muốn so golden) golden baseline đã có từ lần trước: `data\baseline\demo_golden\golden.json`.
2. Chép gói vào dự án. `git diff --stat` so với lần commit gói 04 chỉ thấy 2 file `src/` này.
3. `python -m pytest -q` — test mới: `test_pipeline_overrides.py::test_detected_count_matches_detector_and_covers_items`.
4. Cổng G-demo `--exact` (refactor thuần, phải 0 khác biệt):
   ```powershell
   python run.py --mode validate --config configs\config.demo.yaml --benchmark-dir data_demo\benchmark
   python scripts\compare_validate.py data_demo\outputs data\baseline\demo\report.json --exact
   ```
5. `python scripts\compare_golden.py --config configs\config.demo.yaml --images data_demo\golden\images --expected data_demo\golden\expected.json --baseline data\baseline\demo_golden\golden.json` — kỳ vọng 15/15 giống hệt.

## Dùng web
Web tự nhận biết: chưa vá thì không có cảnh báo (không lỗi); vá rồi thì khi detector thấy nhiều vật hơn số vật nhận ra, hoá đơn hiện banner **"Phát hiện thêm N vật chưa nhận diện được"** kèm nút "Thêm món".
