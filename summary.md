# SUMMARY.md — Stocktaking AI

> Tài liệu tự sinh để tra cứu nhanh toàn bộ project mà **không cần đọc lại source code**.
> Nguồn: `stocktaking_ai.zip` (nhánh `main`, commit mới nhất `cba0df8 "Update README.md"`).
> Ngôn ngữ tài liệu gốc: README/docs viết song ngữ (README = tiếng Việt, docs/ + code = tiếng Anh).

---

## 0. TL;DR — Project là gì

**Stocktaking AI**: hệ thống Computer Vision đếm & nhận diện sản phẩm từ **một ảnh chụp bàn thu ngân**, được thiết kế cho bài toán này ngay từ đầu (xem README mục 2 — đặc thù của bối cảnh bàn thu ngân). Kiến trúc tách biệt:

- **Localization** (Detector, class-agnostic) — "vật ở đâu"
- **Identification** (Retriever + Plugins + Reranker) — "vật đó là SKU nào"

Pipeline 8 giai đoạn, chỉ 1 orchestrator (`InventoryPipeline`), mọi backend đều thay được qua `configs/config.yaml` (không sửa code). Có bộ Validation 9-stage riêng, UI desktop Tkinter, và bộ notebook phân tích từng giai đoạn.

**Trạng thái:** v0.1.0 (extended). Là **codebase nghiên cứu/nội bộ**, chưa production-ready (nhiều mục README đánh dấu `[Kế hoạch]` — chủ yếu liên quan tới độ đa dạng dữ liệu, gallery nhiều mặt, và UI xác nhận cho thu ngân — xem mục 12–13).

---

## 1. Cấu trúc thư mục đầy đủ (như trong file zip đã tải lên)

```
stocktaking_ai/
├── .git/                         # có sẵn lịch sử git, remote "origin" trỏ tới
│                                    github.com/longpham205/stocktaking_ai
├── .gitignore
├── README.md                     # 309 dòng (đã cập nhật) — tài liệu chính, song ngữ VN, rất chi tiết
├── requirements.txt               # 53 dòng, xem mục 6
├── run.py                        # CLI entry point — xem mục 5
├── assets_manifest.json          # checksum (sha256+size) cho weights/ & data/, dùng để
│                                    verify tải về (scripts/generate_manifest.py sinh ra,
│                                    scripts/verify_manifest.py kiểm tra)
├── setup.bat / setup.sh / setup.command / setup_colab.sh
├── configs/
│   └── config.yaml               # 367 dòng — NGUỒN CẤU HÌNH DUY NHẤT, xem mục 7
├── data/
│   ├── metadata/
│   │   ├── .gitkeep
│   │   └── product_colors.json   # chỉ có 2 entry mẫu: PK300, BE203 (xem mục 8)
│   ├── gallery/     ← KHÔNG có trong zip (ảnh tham chiếu SKU, phải tự thêm)
│   ├── benchmark/   ← KHÔNG có trong zip (dataset COCO để validate)
│   ├── query/       ← KHÔNG có trong zip (ảnh input để infer)
│   ├── outputs/     ← KHÔNG có trong zip (sinh ra khi chạy)
│   └── cache/       ← KHÔNG có trong zip (FAISS index sinh ra khi build)
├── weights/         ← KHÔNG có trong zip (checkpoint RF-DETR/SAM2/SigLIP2, xem
│                        assets_manifest.json để biết size/sha256 kỳ vọng)
├── docs/
│   ├── 01_PROJECT_CONTEXT.md     # 249 dòng — triết lý thiết kế, phạm vi hiện tại
│   ├── 02_MODULE_SPECIFICATION.md# 260 dòng — hợp đồng API từng module (rất quan trọng)
│   └── 03_DEVELOPMENT_RULES.md   # 434 dòng — coding rules + "debugging history" (5 bug
│                                    kinh điển đã sửa, xem mục 11)
├── notebooks/                     # 9 notebook, xem mục 10
├── debug/                          # 13 tool debug theo từng giai đoạn pipeline, xem mục 10.1
│   ├── README.md                  # mục lục + bảng lỗi đã sửa khi dọn lại (2026 refactor)
│   ├── 01_detection_viewer.py … 13_validation_dashboard.py
│   ├── _shared/                   # bootstrap/io_utils/formatting/paged_viewer dùng chung
│   └── _config/embedding_pairs.json
├── scripts/
│   ├── setup.py                  # helper cài đặt Windows E2E (venv, tải weights, verify)
│   ├── generate_manifest.py      # sinh assets_manifest.json (path/size/sha256)
│   ├── verify_manifest.py        # kiểm tra file local khớp manifest (exit 0/1)
│   ├── check_requirements.py     # (mới chuyển từ debug/) rà soát import vs requirements.txt
│   └── generate_demo.py          # (mới chuyển từ debug/) sinh dữ liệu GIẢ LẬP cho demo/thuyết trình
├── src/                           # xem mục 4 (chi tiết từng module)
│   ├── catalog/  core/  decision/  detection/  inference/
│   ├── models/   pipeline/  plugins/  retrieval/
│   ├── segmentation/  storage/  ui/  validation/
└── tests/                         # 16 file pytest, 1-1 theo module (xem mục 9)
```

> ⚠️ **Lưu ý quan trọng:** zip này **không chứa** `data/gallery/`, `data/benchmark/`, `data/query/`, `weights/`. Đây là dữ liệu/model nặng, thường tải riêng qua `setup.sh`/`setup.bat` (theo checksum trong `assets_manifest.json`) hoặc Google Drive. Nếu cần chạy inference/build thật, phải bổ sung các thư mục này trước.

> 🧹 **File rác đáng chú ý:** `src/decision/tempCodeRunnerFile.py` là bản sao y hệt `decision.py` (còn sót lại từ extension "Code Runner" của VSCode) — không phải file nghiệp vụ, có thể xoá an toàn.

---

## 2. Kiến trúc pipeline (8 giai đoạn runtime)

```
Input Image
  → ① Detection (class-agnostic bbox)          — src/detection/detector.py
  → ② OverlapResolver (phát hiện chồng lấp)     — src/pipeline/overlap.py
  → ③ Refiner/Segmentation (SAM2, có điều kiện) — src/segmentation/refiner.py
  → ④ Cropper (dual-resolution crop)            — src/detection/cropper.py
  [với mỗi crop, lặp lại bên dưới]
  → ⑤ Retriever (SigLIP2 + FAISS Top-K)         — src/retrieval/retriever.py
  → ⑥ DecisionEngine (accept/uncertain/reject)  — src/decision/decision.py
  → ⑦ PluginManager (OCR/Color/Barcode, có điều kiện) — src/plugins/manager.py
  → ⑧ Reranker (hợp nhất bằng chứng)            — src/decision/reranker.py
  → InventoryResult → StorageManager (JSON/CSV/ảnh annotate) — src/storage/results.py
```

Chỉ có `InventoryPipeline` (src/pipeline/pipeline.py) được điều phối toàn bộ luồng — **không module nào được gọi chéo module khác** (xem bảng "Forbidden imports" ở mục 4.9).

Hai entry point suy luận, **cùng một chuỗi giai đoạn**:
- `InventoryPipeline.run(image_data, similarity_threshold=None) -> InventoryResult` — luồng production, không ghi trace.
- `InventoryPipeline.run_with_trace(image_data) -> tuple[InventoryResult, PipelineTrace]` — dùng riêng cho Validation/debug, ghi lại từng crop qua từng stage (`CropTrace`).

---

## 3. Data Transfer Objects chính (`src/models/models.py`, 639 dòng)

Tất cả module chỉ được trao đổi qua các dataclass này (không dùng `dict` thô làm interface chính):

| DTO | Sinh ra bởi | Trường quan trọng |
|---|---|---|
| `BoundingBox` | dùng chung | `x1,y1,x2,y2`; có `.iou()`, `.area`, `.intersection_ratio()`, `.contains()` |
| `ImageData` | loader | ảnh gốc + metadata |
| `Detection` / `DetectionResult` | Detector | bbox + confidence, class-agnostic |
| `OverlapPair` / `OverlapGroup` / `OverlapResult` | OverlapResolver | nhóm detection nghi ngờ chồng lấp |
| `RefinedBox` / `RefinementResult` | Refiner | bbox đã tinh chỉnh bằng SAM2, **độc lập** với `DetectionResult` (không được ghi đè) |
| `CropImage` | Cropper | **hai mảng pixel**: `image_array` (resize theo `cropping.target_size`, chỉ Retriever dùng) và `raw_image_array` (độ phân giải gốc, chỉ Plugin OCR/Color/Barcode dùng) |
| `RetrievalCandidate` / `RetrievalResult` | Retriever | Top-K `product_id/product_name/similarity_score/rank`; `product_id` LUÔN là ID số dạng string nội bộ, KHÔNG PHẢI tên thư mục gallery |
| `DecisionResult` | DecisionEngine / Reranker | `status` ∈ {accepted, uncertain, rejected}; `trigger_reasons` ⊆ {uncertain, ambiguous, force}; `forced_plugins` |
| `PluginResult` | PluginManager | `executed_plugins`, `evidence: dict[plugin_name, dict]` |
| `InventoryItem` / `InventoryResult` | Pipeline | kết quả cuối cùng: danh sách item + tổng số lượng + thời gian xử lý |
| `CropTrace` / `PipelineTrace` | `run_with_trace()` | chỉ dùng cho chẩn đoán/validation, KHÔNG sinh ra bởi `run()` |

---

## 4. Chi tiết từng module trong `src/`

### 4.1 `src/core/` — nền tảng, KHÔNG chứa logic AI/UI
| File | Public API | Ghi chú |
|---|---|---|
| `config.py` (631 dòng) | `load_config(config_path=None) -> AppConfig`, `reload_config(...)`, `AppConfig.resolve_path(relative_path)` | Định nghĩa toàn bộ Pydantic `BaseModel` cho mọi khối trong `config.yaml` (`DetectionSection`, `RefinementSection`, `RetrievalSection`, `DecisionSection`, `PluginsSection` gồm Ocr/Color/Barcode, `RerankSection`, `ValidationSection`, v.v). Có validator (`validate_coverage_thresholds`, `validate_barcode_section`, `validate_thresholds`) để bắt lỗi cấu hình sớm. |
| `logger.py` | `setup_logger(name, level, log_dir, log_to_file, log_to_console, max_bytes, backup_count) -> Logger`, `get_logger(name) -> Logger` | Logging có xoay file (RotatingFileHandler), không dùng `print()`. |
| `utils.py` | `ensure_dir`, `generate_id(prefix="")`, `list_image_files(dir)`, `load_image_bgr(path) -> np.ndarray`, `save_image_bgr`, `timer()` (context manager đo ms), `clip_coordinate` | Helper file/ảnh dùng chung. |

### 4.2 `src/detection/` — định vị vật thể (class-agnostic)
| File | Public API | Input → Output |
|---|---|---|
| `detector.py` (Detector) | `Detector.detect(image_data: ImageData) -> DetectionResult` | Dispatcher mỏng, chọn backend theo `detection.backend` trong config. Cấm: classification, retrieval, cropping, I/O file. |
| `backends/base.py` | abstract `DetectionBackend.detect(image_array) -> list[Detection]` | |
| `backends/mock_contour.py` | `MockContourBackend` | Dùng Canny + contour, KHÔNG cần weight — dùng để test nhanh không cần GPU/model. |
| `backends/rf_detr.py` | `RfDetrBackend` | Detector thần kinh thật (RF-DETR), nạp checkpoint từ `weights/detector/checkpoint_best_ema.pth` (hoặc `.pt` theo config), variant nano→large. |
| `cropper.py` (Cropper) | `Cropper.crop(image_data, detection_result, refinement_result) -> list[CropImage]` | Ưu tiên `RefinedBox.refined_bbox` nếu có & `cropping.use_refined_bbox=true`; fallback về `Detection.bbox` gốc. Sinh cả `image_array` (resize) lẫn `raw_image_array` (giữ nguyên độ phân giải, chỉ clip biên + pad). Cấm: mutate `DetectionResult`. |

### 4.3 `src/pipeline/overlap.py` — OverlapResolver (thuần hình học)
- `OverlapResolver.resolve(detection_result) -> OverlapResult`
- `find_suspicious_pairs(detections, iou_threshold, overlap_ratio_threshold) -> list[OverlapPair]` — **hàm module-level thuần**, là nguồn chân lý duy nhất cho "thế nào là chồng lấp nghi ngờ", được dùng lại y hệt bởi cả runtime lẫn Evaluator của VAL (để 2 nơi không bao giờ lệch nhau).
- **Tuyệt đối không phải NMS**: không bao giờ xoá/sửa `Detection` nào.

### 4.4 `src/segmentation/` — Refiner (tinh chỉnh biên, tùy chọn)
| File | Ghi chú |
|---|---|
| `refiner.py` (Refiner) | `Refiner.refine(image_array, detection_result, overlap_result) -> RefinementResult`. Chỉ chạy cho detection mà `OverlapResolver` gắn cờ `needs_refinement=True`. Có `_apply_output_policy`/`_fallback_box` để quyết định giữ bbox refine hay rơi về bbox gốc (dựa `min_mask_coverage_ratio`, `max_bbox_expansion_ratio`, `min_refinement_iou` trong config). |
| `backends/base.py` | abstract `SegmentationBackend.refine(...)` |
| `backends/mock_refiner.py` | passthrough (không đổi gì) |
| `backends/sam2.py` (Sam2Backend, 435 dòng) | SAM2 thật, checkpoint `weights/refinement/sam2/sam2.1_hiera_small.pt`. Có `_ensure_checkpoint`, `_select_best_mask`, `_mask_to_bbox`. |

### 4.5 `src/retrieval/` — truy xuất hình ảnh
| File | Public API | Ghi chú |
|---|---|---|
| `retriever.py` (Retriever) | `Retriever.retrieve(crop: CropImage) -> RetrievalResult`, `get_product(product_id) -> dict|None` | Chỉ **load** FAISS index có sẵn (`data/cache/gallery_index.faiss`) + metadata (`data/cache/gallery_metadata.json`) + catalog (`products.json`) — không bao giờ build lại lúc runtime. |
| `backends/base.py` | abstract `EmbeddingBackend.embed(image_array) -> np.ndarray` | |
| `backends/mock_visual_embedding.py` | HSV histogram, không cần weight | |
| `backends/siglip2.py` (Siglip2Backend) | Model thật `google/siglip2-base-patch16-224`. **Bắt buộc** mean-pool qua patch tokens trước khi trả về `(1, hidden_dim)` — xem bug lịch sử ở mục 11.2. | |
| `gallery_builder.py` (GalleryIndexBuilder) | `GalleryIndexBuilder.build() -> tuple[faiss.Index, list[dict]]`, hàm free `build_gallery_index(config, backend)` | **Chỉ chạy offline** (qua `BuildPipeline`), quét `data/gallery/`, ánh xạ tên thư mục → `product_id` nội bộ, build FAISS index + lưu metadata. |

### 4.6 `src/decision/` — ngưỡng quyết định & hợp nhất bằng chứng
| File | Public API | Ghi chú |
|---|---|---|
| `decision.py` (DecisionEngine, 137+ dòng) | `decide(retrieval_result) -> DecisionResult`; `evaluate_thresholds(similarity, detection_confidence) -> tuple[str, float]` (pure, dùng lại bởi Reranker) | Set `needs_plugin` + `trigger_reasons` (uncertain/ambiguous/force) + `forced_plugins` (đối chiếu **toàn bộ Top-K**, không chỉ ứng viên thắng, với `plugins.force_rules`). Cấm: load model, gọi Detector/Retriever/PluginManager/Reranker. |
| `reranker.py` (Reranker, **1340 dòng — file lớn nhất project**) | `rerank(retrieval_result, plugin_result) -> DecisionResult` | Chỉ chạy khi `needs_plugin=True`, sau PluginManager. Re-score **toàn bộ Top-K** bằng: barcode exact-match, OCR đa hướng (đối chiếu token catalog), màu CIEDE2000 (Lab, có ngưỡng tuyệt đối + margin-so-với-hạng-nhì), **Retrieval-consensus protection** (bằng chứng yếu không thể lật ngược một Top-K đã đồng thuận mạnh — điều chỉnh bằng `rerank.retrieval_protection.*`), và **Confusable-pair guard** (`rerank.confusable_pairs`, hạ cấp quyết định về `uncertain` nếu không đủ số plugin đồng thuận `confusable_min_agreeing_plugins`). Chỉ được gọi `DecisionEngine.evaluate_thresholds()`, KHÔNG BAO GIỜ gọi lại `decide()`. |
| `tempCodeRunnerFile.py` | — | File rác (bản sao `decision.py`), có thể xoá. |

### 4.7 `src/plugins/` — bằng chứng phụ (chỉ đọc `crop.raw_image_array`)
| File | Public API | Cấm |
|---|---|---|
| `manager.py` (PluginManager) | `run_plugins(crop, decision) -> PluginResult` | Chính sách chọn plugin: nếu `trigger_reasons` có `uncertain`/`ambiguous` → chạy mọi plugin bật; nếu đúng bằng `{"force"}` → chỉ chạy plugin trong `decision.forced_plugins`. Cờ `enabled` riêng của từng plugin luôn override. |
| `ocr.py` (OcrPlugin, 694 dòng) | `run(crop) -> dict` | EasyOCR, upscale thích ứng + CLAHE, quét mọi góc trong `plugins.ocr.rotation_angles` (mặc định 90/180/270°), chấm điểm theo "information-content" (ưu tiên đoạn dài/rõ/alphanumeric), trả về hướng tốt nhất + ứng viên thứ 2 nếu top-2 gần nhau. |
| `color.py` (ColorPlugin, 481 dòng) | `run(crop) -> dict` | Tự dò ROI hình chữ nhật ("powder pan") bằng Canny+contour theo 5 tiêu chí (rectangularity, centering, lower-position bias, inner margin, area), fallback 3 tầng (contour→center-crop→lower-center). Chuyển Lab, loại pixel bị lóe sáng, K-Means (giảm trọng số kênh L, tăng trọng số theo vùng tâm), chọn màu chủ đạo có độ bão hòa (chroma) cao. **KHÔNG** đọc catalog / so khớp màu tham chiếu — đó là việc của Reranker. |
| `barcode.py` (BarcodePlugin, 324 dòng) | `run(crop) -> dict`, `is_enabled()` | pyzbar, pipeline giải mã thích ứng **9 giai đoạn** (presence pre-check → raw decode → phát hiện vùng → deskew → upscale có mục tiêu → CLAHE → binarization → denoise/sharpen → xoay đa góc fallback). Trả về confidence từ chất lượng decode + độ tin cậy symbology + phạt nếu nhiều mã xung đột. **KHÔNG** đọc catalog / resolve product identity. Hiện **`enabled: true`** trong `config.yaml` (đã xác nhận; xem `note.md` §1). |

### 4.8 `src/pipeline/` — orchestrator
| File | Ghi chú |
|---|---|
| `pipeline.py` (InventoryPipeline) | `run(image_data, similarity_threshold=None) -> InventoryResult`; `run_with_trace(image_data) -> tuple[InventoryResult, PipelineTrace]`. Chuỗi giai đoạn cố định: Detect → Overlap → Refine (nếu cờ) → Crop → [mỗi crop: Retrieve → Decide → Plugins (nếu cần) → Rerank (nếu plugin đã chạy)] → InventoryResult. `run()` bỏ qua ghi trace để tối ưu hot-path; VAL luôn dùng `run_with_trace()`. Cấm: cài đặt model cụ thể, I/O file, UI. |
| `overlap.py` | xem mục 4.3 |
| `build.py` (BuildPipeline, offline) | `BuildPipeline.run()`, hàm free `run_build(config=None)`. Điều phối `MetadataBuilder` (→ `products.json`, `product_ids.json`) và `GalleryIndexBuilder` (→ FAISS index + metadata). **Độc lập hoàn toàn** với `InventoryPipeline`; `run.py` gọi `run_build()` mặc định trước mỗi mode (trừ khi có `--skip-build`). |

### 4.9 Quy tắc phụ thuộc (Dependency Rules — từ `docs/02_MODULE_SPECIFICATION.md`)

Luồng cho phép: `UI/Runner → InventoryPipeline → [Detector, OverlapResolver, Refiner, Cropper, Retriever, DecisionEngine, PluginManager, Reranker] → StorageManager`

**Cấm tuyệt đối:**
- Detector ↔ Retriever (2 chiều)
- Plugin → Detector / Retriever
- UI → Detector / Retriever / bất kỳ backend cụ thể nào
- StorageManager → InventoryPipeline
- Reranker → `DecisionEngine.decide()` (chỉ được gọi `evaluate_thresholds`)
- DecisionEngine → PluginManager / Reranker
- Bất kỳ module pipeline nào → gọi thẳng class backend cụ thể (phải qua dispatcher: `Detector`, `Retriever`, hoặc `Refiner`)

### 4.10 `src/catalog/metadata.py` — MetadataBuilder
- `MetadataBuilder(config).build() -> list[dict]` — quét `data/gallery/`, đối chiếu với `products.json`/`product_ids.json` hiện có, gán/giữ ID số nội bộ ổn định (không bao giờ dùng lại tên thư mục làm ID — xem bug lịch sử mục 11.1). Có `_reconcile_products`, `_validate_config_mapping`, `_validate_gallery`.
- Hàm free: `build_product_metadata(config) -> list[dict]`.

### 4.11 `src/storage/results.py` — StorageManager
- `save_json(result) -> str`, `save_csv(result) -> str`, `save_annotated_image(image, result) -> str`, `save_all(image, result) -> dict[str,str]`.
- Không chứa logic AI. Không bao giờ được `InventoryPipeline` gọi trực tiếp — chỉ `InferenceRunner`/`ValidationRunner` gọi.
- Output mặc định (theo `config.yaml storage:`): `result.json`, `result.csv`, `result.jpg` (box màu xanh `[0,200,0]`, độ dày 6px).

### 4.12 `src/inference/infer.py` — InferenceRunner
- `run_single(image_path, similarity_threshold=None) -> InventoryResult`
- `run_batch(image_dir, similarity_threshold=None) -> list[InventoryResult]`
- Override `similarity_threshold` rẻ vì `DecisionEngine`/`Reranker` không giữ trọng số model → có thể khởi tạo lại tạm thời mỗi lần gọi mà không vi phạm quy tắc "không load lại weight mỗi query".

### 4.13 `src/validation/` — bộ đánh giá 9 giai đoạn
| File | Ghi chú |
|---|---|
| `validate.py` (ValidationRunner) | `run(benchmark_dir) -> dict`. Đọc COCO ground-truth (`category_id == product_id` số), gọi `InventoryPipeline.run_with_trace()` cho từng ảnh benchmark, chuyển hết cho `Evaluator`. Ghi: `report.json/csv`, `records.csv` (bảng phẳng theo từng crop, gồm cả GT bị bỏ sót hoàn toàn), `summary.txt` (kèm breakdown latency từng giai đoạn), ảnh annotate màu (xanh=đúng, đỏ=sai, cam nét đứt=bị bỏ sót), và biểu đồ cột metric từng giai đoạn (`validation_summary_chart.png`). |
| `evaluator.py` (Evaluator, 1043 dòng) | `evaluate(records: list[ImageEvalInput]) -> dict`. Tính **một** phép match detection↔ground-truth dùng chung cho cả 9 stage (Detection, Cropping, Overlap, Segmentation, Retrieval, Decision, Plugins, Fusion, End-to-End) — để không có 2 stage nào bất đồng về crop nào ứng với GT nào. Có `greedy_iou_match(...)` (cố ý dùng greedy thay vì Hungarian optimal — quyết định thiết kế cho v0.1.0). Mỗi stage bị tắt qua `validation.stages` sẽ báo cáo literal `"skipped_by_config"`. |
| `metrics.py` (243 dòng) | Registry hàm metric thuần: `precision`, `recall`, `f1`, `accuracy`, `confusion_matrix`, `mean_iou`, `top1_accuracy`, `topk_accuracy`, `recall_at_k`, `mrr`, `mean_rank`, `trigger_rate`, `success_rate`, `correction_rate`, `degradation_rate`, `valid_rate`, `improved_rate`, `iou_improvement`, `accuracy_before/after`, `improvement`, `coverage_rate`, `influence_rate`, và `compute_metrics(stats, metric_names)`. Thêm metric mới = viết 1 hàm + đăng ký trong `METRIC_REGISTRY`, KHÔNG sửa `evaluator.py`. |

### 4.14 `src/ui/app.py` — UI desktop Tkinter
- `launch_app(config_path=None) -> None`.
- `ImageViewer` (zoom/pan ảnh), `StocktakingApp` (tab Inference + tab Validation, cây kết quả, highlight bbox theo SKU, xem ảnh annotate/gallery, chạy validation và hiện biểu đồ).
- **Cấm:** không bao giờ import Detector/Retriever/bất kỳ model class nào trực tiếp — chỉ giao tiếp qua `InferenceRunner`/`ValidationRunner`.

---

## 5. CLI (`run.py`)

```bash
# Mặc định MỌI mode đều chạy BuildPipeline trước (đồng bộ gallery + metadata),
# trừ khi thêm --skip-build

python run.py --mode infer  --image data/query/test_counter.jpg
python run.py --mode infer  --image-dir data/query/
python run.py --mode validate --benchmark-dir data/benchmark/
python run.py --mode ui
python run.py --mode <bất kỳ> --config path/to/other_config.yaml --skip-build
```

Dùng như thư viện:
```python
from src.core.config import load_config
from src.pipeline.pipeline import InventoryPipeline

config = load_config()
pipeline = InventoryPipeline(config)
result = pipeline.run(image_data)                    # production
result, trace = pipeline.run_with_trace(image_data)   # debug/VAL
```

---

## 6. `requirements.txt` (Python ≥3.11,<3.13)

- **Core:** numpy, opencv-python-headless, PyYAML, pydantic(-settings), Pillow, matplotlib, pandas
- **Retrieval:** faiss-cpu==1.15.0
- **Detection:** rfdetr==1.9.4, supervision==0.30.1
- **Retrieval (SigLIP2):** transformers==5.16.1
- **Refinement:** sam2==1.1.0
- **Plugins:** easyocr==1.7.2, pyzbar>=0.1.9 (cần `libzbar0` ở Linux, `apt-get install -y libzbar0`)
- **Test:** pytest>=8.0, pytest-cov>=5.0
- **PyTorch** KHÔNG nằm trong requirements.txt — cài riêng bởi `setup.bat`/`setup.sh` tuỳ có GPU CUDA hay không.

---

## 7. Schema `configs/config.yaml` (367 dòng, nguồn cấu hình DUY NHẤT)

| Khối | Tham số đáng chú ý |
|---|---|
| `app` | `device: cuda` (đổi `cpu` nếu không có GPU) |
| `logging` | mức `INFO`, ghi cả file (`data/cache/logs`, xoay vòng 5MB×3) và console |
| `paths` | toàn bộ path tương đối tới project root: gallery/benchmark/query/output/cache/metadata/weights |
| `catalog` | `build_metadata: false`; `id_mapping` — bảng ánh xạ ID số → tên SKU tiếng Nhật/mã GTIN mẫu (ví dụ `"5": "マジョリカマジョルカ シャドーカスタマイズ（BE203）"`) |
| `detection` | backend `rf_detr` (fallback `mock_contour`), `confidence_threshold=0.70`, `min/max_box_area_ratio`, `min/max_aspect_ratio`, RF-DETR variant `base`, `device: cuda` |
| `refinement` | `enabled: true`, backend `sam2`; trigger theo `iou_threshold=0.05`, `overlap_ratio_threshold=0.20`; output policy `min_mask_coverage_ratio=0.55`, `max_bbox_expansion_ratio=1.50`, `min_refinement_iou=0.50` |
| `cropping` | `padding_pixels=4`, `target_size=[1024,1024]`, `use_refined_bbox=true` |
| `retrieval` | backend `siglip2`, `embedding_dim=768`, `top_k=5`, index/metadata path trong `data/cache/`, model `google/siglip2-base-patch16-224` |
| `decision` | `similarity_threshold=0.55`, `min_confidence_accept=0.60`, `uncertain_band=0.15`, `detection_weight=0.35`/`similarity_weight=0.65`, `ambiguous_top_n=3`, `ambiguous_margin=0.05` |
| `plugins.ocr` | ngôn ngữ `en` (chỉ Latin/số hiện tại), upscale tới ×3, CLAHE, xoay 90/180/270° |
| `plugins.color` | rất nhiều tham số dò ROI (rectangularity/aspect/area ratio), Lab + K-Means (`n_clusters=3`), loại highlight |
| `plugins.barcode` | **`enabled: true`** (giá trị đúng, đã xác nhận; README/docs cũ ghi "đang tắt" là lỗi thời); 9-stage decode, `type_trust` theo symbology (EAN13/UPCA=1.0, CODE39/CODABAR=0.6, QRCODE=0.9) |
| `plugins.force_rules` | ví dụ: SKU `"5"`,`"6"` → bắt buộc chạy `barcode+color`; SKU `"7"`,`"8"` → bắt buộc `ocr` |
| `rerank.retrieval_protection` | `consensus_mode: hybrid` (weights: count 0.5/weighted 0.3/margin 0.2), `min_switch_margin=0.05` |
| `rerank.confusable_pairs` | `[["7","8"]]`, `confusable_min_agreeing_plugins=1` |
| `rerank.{barcode,ocr,color}` | trọng số hợp nhất: barcode=1.00, ocr=0.60, color=0.20; color dùng `delta_e_strong=6.0`/`delta_e_weak=20.0` |
| `storage` | xuất JSON+CSV+ảnh annotate (box xanh `[0,200,0]`, dày 6px) |
| `validation` | `iou_match_threshold=0.30`, bật/tắt 9 stage, danh sách metric cho từng stage (map 1-1 với `metrics.py`) |

---

## 8. Dataset & Metadata

- **Gallery** (`data/gallery/`, **không có sẵn trong zip**): ảnh tham chiếu theo từng SKU, tên thư mục ánh xạ đến ID số qua `configs/config.yaml: catalog.id_mapping` + `data/metadata/product_ids.json`. `[Kế hoạch]` mỗi SKU cần nhiều mặt (trước/sau/2 bên) — hiện chưa có.
- **Product colors** (`data/metadata/product_colors.json`, CÓ trong zip nhưng chỉ 2 entry mẫu):
  ```json
  { "PK300": {"name": "PK300", "rgb": [109,63,62], "hex": "#6D3F3E"},
    "BE203": {"name": "BE203", "rgb": [199,161,148], "hex": "#C7A194"} }
  ```
  Hiện được viết tay, chưa sinh tự động từ gallery (roadmap: K-Means trên Lab).
- **Catalog** (`products.json`, `product_ids.json` — sinh bởi `BuildPipeline`, **không có sẵn trong zip**, sẽ được tạo khi chạy `run.py` lần đầu hoặc `scripts/setup.py`): chứa SKU + GTIN/barcode. Hiện `products.json.barcode` trống cho hầu hết entry (barcode plugin chưa dùng được đầy đủ).
- **Benchmark** (`data/benchmark/`, **không có trong zip**): annotation dạng COCO, `category_id` = `product_id` nội bộ.

---

## 9. Tests (`tests/`, pytest, 16 file — mỗi module ứng với 1 file test)

```
conftest.py         # fixture chung (synthetic input — mọi module test độc lập được)
test_catalog.py      test_cropper.py      test_decision.py
test_detector.py     test_evaluator.py    test_metrics.py
test_models.py       test_overlap.py      test_pipeline.py
test_plugins.py      test_reranker.py     test_retriever.py
test_segmentation.py test_storage.py      test_validate.py
```
Chạy: `pytest` (từ project root, sau khi `pip install -r requirements.txt`).

---

## 10. Notebooks (`notebooks/`, Jupyter — dùng để phân tích/debug từng giai đoạn)

| File | Nội dung |
|---|---|
| `01_data_exploration.ipynb` | Khám phá config, gallery, metadata, benchmark COCO, ảnh query trước khi chạy pipeline |
| `02_detection_analysis.ipynb` | Chạy riêng `Detector` (mock_contour hoặc rf_detr), soi output |
| `03_overlap_segmentation.ipynb` | Soi `OverlapResolver` (thuần hình học, không phải NMS) + `Refiner`/SAM2 |
| `04_retrieval_analysis.ipynb` | Soi `Retriever`: SigLIP2/mock embedding + FAISS gallery index (chỉ load, không build) |
| `05_decision_reranking.ipynb` | Soi `DecisionEngine` + `Reranker`; evidence plugin được **dựng thủ công** (chưa chạy plugin thật) |
| `06_plugins_deep_dive.ipynb` | Chạy plugin THẬT (EasyOCR đa hướng+CLAHE, Color Lab+K-Means+CIEDE2000, Barcode 9-stage) trên crop thật — khép vòng lặp với notebook 05 |
| `07_end_to_end_pipeline.ipynb` | Chạy `InventoryPipeline.run_with_trace()` full trên ảnh thật, visualize từng giai đoạn, xuất qua `StorageManager` thật |
| `08_validation_benchmark.ipynb` | Đọc report VAL thật (`report.json`, `records.csv`, `report.csv`) sinh bởi `ValidationRunner.run()`, tái tạo bảng 9-stage metric của README, thêm triage theo ảnh |
| `pipeline_visual.ipynb` | (tiếng Việt) Demo trực quan hoá pipeline: chạy `run_with_trace()` trên một ảnh bất kỳ trong `data/query/`, vẽ bbox từng giai đoạn (Detection → Overlap → ...). ⚠️ Tiêu đề gốc bên trong notebook này vẫn dùng chữ "ảnh kệ hàng" (chưa được đồng bộ theo cách gọi "bàn thu ngân" đã sửa ở README/docs — notebook là `.ipynb`, nằm ngoài phạm vi lần sửa `.md` này). |

---

## 10.1 `debug/` — 13 tool debug theo từng giai đoạn pipeline (đánh số theo thứ tự chạy pipeline)

Khác notebook (dùng để *phân tích/khám phá*), `debug/` là các script CLI
chạy nhanh, có tương tác bằng phím (←/→/A/D chuyển ảnh, Home/End, `S` lưu
PNG, Esc thoát — cùng 1 class `_shared/paged_viewer.KeyboardPagedViewer`
cho mọi tool). Đọc `debug/README.md` để có bảng đầy đủ; tóm tắt:

| # | File | Giai đoạn |
|---|---|---|
| 01 | `01_detection_viewer.py` | ① Detection |
| 02 | `02_overlap_segmentation_viewer.py` | ② Overlap + ③ Segmentation (SAM2) |
| 03 | `03_cropping_compare.py` | ④ Cropping (so crop dual-resolution với ảnh gallery GT) |
| 04 | `04_retrieval_selfcheck.py` | ⑤ Retrieval (self-retrieval accuracy toàn gallery) |
| 05 | `05_retrieval_embedding_pairs.py` | ⑤ Retrieval (cosine similarity theo cặp, cấu hình ở `_config/embedding_pairs.json`) |
| 06 | `06_decision_trigger_report.py` | ⑥ Decision (phân phối trigger_reasons, raw→final transitions) |
| 07 | `07_plugin_ocr_viewer.py` | ⑦ Plugin OCR (lưới 2×3, 4 góc xoay) |
| 08 | `08_plugin_color_viewer.py` | ⑦ Plugin Color (lưới 2×5, tái dựng 8 bước ROI/K-Means) |
| 09 | `09_plugin_barcode_viewer.py` | ⑦ Plugin Barcode (cố ý gọi thẳng pyzbar thô, KHÔNG qua 9-stage thật) |
| 10 | `10_reranker_viewer.py` | ⑧ Reranker (Top-K trước/sau, so ảnh gallery) |
| 11 | `11_reranker_delta_e_calibration.py` | ⑧ Reranker — hiệu chuẩn `delta_e_strong/weak` từ benchmark thật |
| 12 | `12_benchmark_gt_viewer.py` | Ground truth COCO (chưa chạy pipeline) |
| 13 | `13_validation_dashboard.py` | End-to-end — dashboard 6 ô đọc `records.csv` sau `--mode validate` |

**Bối cảnh:** bộ debug gốc do người dùng viết khá lộn xộn (16 file, nhiều
bản trùng chức năng, path hardcode máy cá nhân). Đã dọn lại theo yêu cầu
"giữ nguyên logic cốt lõi, tối ưu/gộp/thêm chi tiết", đồng thời **phát
hiện và sửa 5 lỗi có thật** khiến 1 số tool gốc luôn chạy ra kết quả rỗng
(chi tiết đầy đủ ở `debug/README.md` mục "Lỗi đã sửa"), quan trọng nhất:
`final_decision.rerank_debug` được nhiều file gốc đọc nhưng **field này
không tồn tại** trên `DecisionResult` — khiến `debug_color.py`,
`debug_reranker.py`, `debug_delta_e.py` bản gốc luôn coi như "không có dữ
liệu rerank" dù chạy đúng cú pháp. 2 file không liên quan debug pipeline
(`check_requirements.py`, `generate_demo.py` — file sau tự nhận sinh dữ
liệu GIẢ LẬP cho demo) đã chuyển sang `scripts/`.



Ghi lại để **không lặp lại**:

1. **Product ID Identity Bug** — từng lấy tên thư mục gallery làm `product_id`. Quy tắc: `product_id` LUÔN là chuỗi số nội bộ ổn định, không suy ra được từ tên hiển thị hay tên thư mục.
2. **SigLIP2 Unpooled-Embedding Bug** — `get_image_features()` trả về `(1, 196, 768)` (per-patch), từng bị cắt nhầm thành 1 patch. Mọi backend embedding mới phải xác nhận rõ output là `(1, hidden_dim)` đã pooled.
3. **Shared-Resolution Crop Bug** — OCR từng dùng chung crop đã resize cho Retrieval → phá huỷ chữ nhỏ trên bao bì. Đây là lý do `CropImage` có 2 độ phân giải (`image_array` / `raw_image_array`).
4. **Additive Evidence-Fusion Bug** — cộng thẳng confidence-boost của plugin vào ứng viên đang thắng → sửa đúng gần bằng số ca làm sai thêm. Nay mọi bằng chứng mới phải đi qua cơ chế "retrieval-consensus protection".
5. **Barcode Plugin Single-Attempt Bug** — chỉ gọi `pyzbar.decode()` một lần rồi bỏ cuộc, fail với ảnh xoay/mờ/độ tương phản thấp. Quy tắc chuẩn cho decode thích ứng: pre-check rẻ → cam kết preprocessing đắt khi cần → chuỗi fallback tích luỹ → thử decode ở mỗi bước → thoát sớm khi thành công lần đầu.

---

## 12. Trạng thái hiện tại / hạn chế đã biết (từ README §13 + docs/01 §6)

**Đã hoàn thành:**
- Pipeline runtime 8 giai đoạn đầy đủ + `run_with_trace()`.
- Backend có thể hoán đổi cho Detection/Retrieval/Refinement.
- Barcode plugin 9-stage đã tích hợp đầy đủ về mặt code.
- Dual-resolution cropping; chính sách trigger plugin 3 lý do; Reranker với consensus-protection + confusable-pair guard.

**Chưa hoàn thiện / đang tinh chỉnh:**
- Barcode plugin: `plugins.barcode.enabled: true` trong `config.yaml` là giá trị đúng (`note.md` §1); README lỗi thời sẽ sửa ở TASK P1-5. Còn lại: metadata GTIN/barcode catalog chưa đầy đủ (chỉ 5/22 SKU có barcode).
- `products.json.barcode` trống cho gần như mọi entry catalog.
- `data/metadata/product_colors.json` viết tay, chưa tự sinh.
- SAM2 refinement chỉ giúp ích cận biên (cải thiện ~bằng số box làm hỏng).
- Lỗi nhận diện fine-grained (~6%) tập trung ở biến thể bao bì gần giống hệt (khác khối lượng tịnh).
- OCR chỉ tối ưu cho tiếng Anh/chữ số (`[en]`).
- Retrieval/Detector chưa xử lý tốt trường hợp sản phẩm ở hướng bất kỳ hoặc chỉ thấy mặt sau/bên (thường gặp trên bàn thu ngân vì khách đặt sản phẩm tự do).
- Detector chưa được huấn luyện để bỏ qua tay người/túi/hóa đơn.

**Loại trừ có chủ đích (chưa làm, không phải bug):**
- Framework dependency-injection phức tạp hơn dispatcher hiện có.
- Suy luận camera thời gian thực/phân tán.
- Sinh màu tham chiếu tự động từ gallery (đề xuất, chưa cài).
- Hungarian matching trong VAL (dùng greedy IoU có chủ đích cho v0.1.0).

**Baseline hiệu năng đã đo (README mục 12):**
8 SKU cốt lõi, 31 cảnh, 293 instance → Detection P/R/mAP50 = 0.970/0.940/0.990; Retrieval Top-1/Top-5 = 0.735/1.000; Decision pre-fusion acc = 0.712; sau fusion = 0.936; Count accuracy = 0.871; End-to-End P/R/F1 = 0.913/0.966/0.939. `[Kế hoạch]` đo lại trên bộ dữ liệu bàn thu ngân mở rộng (đa dạng hướng đặt/ánh sáng hơn), bổ sung 2 chỉ số mới: độ trễ end-to-end/ảnh và tỷ lệ ca bị gắn cờ `ambiguous` cần thu ngân xác nhận.

---

## 13. Lộ trình phát triển (README §14)

1. Thu thập + gán nhãn ảnh bàn thu ngân, fine-tune lại RF-DETR.
2. Gallery nhiều mặt + truy xuất bất biến hướng xoay.
3. ROI + trừ nền cho camera cố định.
4. UI xác nhận cho thu ngân (case `ambiguous`), tính tổng tiền theo catalog.
5. Tự động trích màu tham chiếu bằng K-Means trong CIELAB.
6. Tinh chỉnh heuristic kích hoạt SAM2 cho sản phẩm xếp chồng.
7. Mở rộng OCR đa ngôn ngữ.

---

## 14. Cách dùng file này

Lần sau, chỉ cần đọc `summary.md` này để nắm:
- File nào ở đâu, chức năng gì, input/output ra sao (mục 4).
- Toàn bộ tham số cấu hình quan trọng (mục 7) — không cần mở lại `config.yaml` 367 dòng trừ khi cần giá trị số chính xác cho tinh chỉnh.
- Những gì ĐÃ xong / CHƯA xong / bị loại trừ có chủ đích (mục 12) — tránh đề xuất lại thứ đã bị từ chối có lý do.
- 5 bug lịch sử (mục 11) — tránh lặp lại khi sửa code.

Khi codebase thay đổi (thêm module, đổi API, đổi config), **nên yêu cầu tái tạo lại summary.md** thay vì tin tưởng bản cũ nếu đã lâu không cập nhật.

---

## 15. Giới hạn của tài liệu này

- Không đọc toàn văn `src/decision/reranker.py` (1340 dòng) và `src/plugins/ocr.py` (694 dòng) từng dòng — đã tóm tắt qua danh sách hàm + docstring + `02_MODULE_SPECIFICATION.md`. Nếu cần sửa logic bên trong các hàm private (`_rerank_with_candidates`, `_run_ocr`, v.v.), vẫn cần mở file gốc.
- Không có quyền truy cập `data/gallery/`, `weights/`, `data/benchmark/` (không nằm trong zip) nên không thể xác nhận số lượng SKU/ảnh thật hay kiểm thử chạy pipeline thực tế.
- Nội dung 9 notebook chỉ được lấy từ ô markdown mở đầu (mục đích), không phân tích code/kết quả cell bên trong.
