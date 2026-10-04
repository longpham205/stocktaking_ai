# debug/ — Công cụ debug theo từng giai đoạn pipeline

Mỗi file ứng với đúng 1 giai đoạn trong 8 giai đoạn của `InventoryPipeline`
(xem `docs/02_MODULE_SPECIFICATION.md`), đánh số theo thứ tự chạy thật của
pipeline để dễ tra — không phải thứ tự quan trọng.

Toàn bộ file tương tác (mở cửa sổ matplotlib) dùng chung 1 bộ phím tắt từ
`_shared/paged_viewer.py`:

| Phím | Chức năng |
|---|---|
| `←` / `A` | Ảnh/crop trước |
| `→` / `D` | Ảnh/crop sau |
| `Home` | Nhảy tới mục đầu tiên |
| `End` | Nhảy tới mục cuối cùng |
| `S` | Lưu khung hình hiện tại ra PNG |
| `Esc` / `Q` | Thoát |

## Mục lục theo giai đoạn pipeline

| # | File | Giai đoạn | Cần dữ liệu gì trước |
|---|---|---|---|
| 01 | `01_detection_viewer.py` | ① Detection | Chỉ cần ảnh (benchmark hoặc query) |
| 02 | `02_overlap_segmentation_viewer.py` | ② Overlap + ③ Segmentation (SAM2) | Chỉ cần ảnh |
| 03 | `03_cropping_compare.py` | ④ Cropping | **Cần chạy `python -m engine --mode validate` trước** (đọc `records.csv`) |
| 04 | `04_retrieval_selfcheck.py` | ⑤ Retrieval | Cần gallery đã build FAISS index (`python -m engine` sẽ tự build) |
| 05 | `05_retrieval_embedding_pairs.py` | ⑤ Retrieval (backend thô, không qua FAISS) | Cần gallery; sửa cặp test ở `_config/embedding_pairs.json` |
| 06 | `06_decision_trigger_report.py` | ⑥ Decision | Chỉ cần ảnh benchmark |
| 07 | `07_plugin_ocr_viewer.py` | ⑦ Plugin: OCR | Chỉ cần ảnh; chạy pipeline thật |
| 08 | `08_plugin_color_viewer.py` | ⑦ Plugin: Color | Chỉ cần ảnh; chạy pipeline thật |
| 09 | `09_plugin_barcode_viewer.py` | ⑦ Plugin: Barcode (raw pyzbar, chưa qua 9-stage) | Chỉ cần ảnh |
| 10 | `10_reranker_viewer.py` | ⑧ Reranker | Chỉ cần ảnh; chạy pipeline thật |
| 11 | `11_reranker_delta_e_calibration.py` | ⑧ Reranker (hiệu chuẩn ΔE màu) | Cần benchmark COCO đầy đủ |
| 12 | `12_benchmark_gt_viewer.py` | Ground truth (chưa chạy pipeline) | Chỉ cần benchmark COCO |
| 13 | `13_validation_dashboard.py` | End-to-end / Validation | **Cần chạy `python -m engine --mode validate` trước** (đọc `records.csv`) |

`_shared/` (bootstrap, io_utils, formatting, paged_viewer) và `_config/`
(dữ liệu cấu hình tách khỏi code, ví dụ cặp ảnh test embedding) không phải
tool debug — là hạ tầng dùng chung, không chạy trực tiếp.

## Không còn trong debug/ (đã dọn / chuyển đi)

| File cũ | Lý do |
|---|---|
| `debug.py` | Bản nháp, chức năng trùng `04_retrieval_selfcheck.py` |
| `debug_benchmark.py` | Bản nháp thô của `12_benchmark_gt_viewer.py`, không tương tác |
| `debug/__pycache__/*.pyc` | Rác build |
| `check_requirements.py` | Không debug pipeline — đã xoá (2026-10-03); kiểm môi trường bằng `make check-env` |
| `generate_demo.py` | Sinh dữ liệu GIẢ LẬP cho demo — đã xoá (2026-10-03), thay bằng `scripts/make_demo_dataset.py` |

## Lỗi đã sửa khi dọn lại (đáng chú ý cho lần sau)

- **`rerank_debug` không tồn tại**: `debug_color.py`, `debug_reranker.py`,
  `debug_delta_e.py` bản gốc đều đọc `final_decision.rerank_debug` — field
  này **không có** trên `DecisionResult` (xem `engine/models/models.py`).
  Hệ quả: bảng so sánh Top-K/ΔE trong 3 file đó **luôn luôn rỗng** dù chạy
  đúng. Đã sửa ở `08`, `10`, `11` bằng cách gọi thẳng dữ liệu/hàm thật đang
  có (`retrieval_result.candidates`, `Reranker._calculate_color_distances`).
- **Sai file annotation**: `debug_benchmark_labels.py` và
  `debug_val_product_viewer.py` bản gốc trỏ `COCO_FILE` vào
  `data/benchmark/products.json` (catalog SKU, không phải COCO) thay vì
  `_annotations.coco.json`. Đã sửa ở `12` và `13`, lấy path từ
  `config.yaml` để không lệch nữa nếu cấu hình đổi.
- **`ct.crop.bbox` không tồn tại**: `debug_ocr.py` bản gốc luôn vẽ bbox
  rỗng lên ảnh gốc vì field đúng là `source_bbox`. Đã sửa ở `07`.
- **`pipeline.plugin_manager` không tồn tại**: `debug_color.py` bản gốc
  dò sai tên thuộc tính (`_plugin_manager`, và là list chứ không phải
  dict) nên không bao giờ gọi được hàm resize thật của `ColorPlugin`. Đã
  sửa ở `08`.
- **Path Unicode**: nhiều file bản gốc dùng `cv2.imread`/`load_image_bgr`
  thường, lỗi âm thầm với tên thư mục gallery tiếng Nhật. Toàn bộ debug/
  nay dùng `_shared/io_utils.load_bgr` (an toàn Unicode) thống nhất.
- **Hardcode path máy cá nhân** (`G:/VsCode/...`): đã bỏ hết, thay bằng
  `config.yaml` hoặc `--source`/tham số dòng lệnh.

## Giới hạn còn lại (chưa có, có thể làm tiếp)

- Chưa có debug tool nào validate riêng `engine/storage/results.py` (ít cần
  thiết vì module này không có logic AI).
- `09_plugin_barcode_viewer.py` cố ý gọi thẳng `pyzbar`, KHÔNG qua
  `BarcodePlugin` 9-stage thật (quyết định có chủ đích — xem docstring
  đầu file). Muốn debug đúng 9-stage decode thật thì cần viết thêm 1 tool
  gọi `BarcodePlugin.run(crop)` trực tiếp.
