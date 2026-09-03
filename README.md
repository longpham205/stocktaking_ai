# Stocktaking AI

> Hệ thống kiểm kê sản phẩm bán lẻ ứng dụng AI — phát hiện, phân đoạn, truy xuất và nhận diện từng sản phẩm từ ảnh kệ hàng, kết hợp truy xuất hình ảnh với bằng chứng từ OCR, màu sắc và mã vạch để phân biệt các biến thể sản phẩm có hình thức gần như giống hệt nhau.

## Mục lục

* [1. Tổng quan](#1-tổng-quan)
* [2. Kiến trúc hệ thống & Pipeline](#2-kiến-trúc-hệ-thống--pipeline)
* [3. Các module & tính năng chính](#3-các-module--tính-năng-chính)
* [4. Cấu trúc dự án](#4-cấu-trúc-dự-án)
* [5. Yêu cầu hệ thống](#5-yêu-cầu-hệ-thống)
* [6. Cài đặt & thiết lập môi trường](#6-cài-đặt--thiết-lập-môi-trường)
* [7. Đặc tả Dataset & Metadata](#7-đặc-tả-dataset--metadata)
* [8. Schema cấu hình](#8-schema-cấu-hình)
* [9. Hướng dẫn thực thi hệ thống](#9-hướng-dẫn-thực-thi-hệ-thống)
* [10. Đặc tả Input & Output](#10-đặc-tả-input--output)
* [11. Đánh giá hiệu năng định lượng](#11-đánh-giá-hiệu-năng-định-lượng)
* [12. Phân tích kỹ thuật & Nhật ký thực nghiệm](#12-phân-tích-kỹ-thuật--nhật-ký-thực-nghiệm)
* [13. Trạng thái Runtime hiện tại](#13-trạng-thái-runtime-hiện-tại)
* [14. Hạn chế kỹ thuật](#14-hạn-chế-kỹ-thuật)
* [15. Lộ trình phát triển](#15-lộ-trình-phát-triển)
* [16. Trích dẫn](#16-trích-dẫn)
* [17. Giấy phép](#17-giấy-phép)

## 1. Tổng quan

**Stocktaking AI** là hệ thống Computer Vision cấp doanh nghiệp được thiết kế để tự động hóa quá trình kiểm kê sản phẩm trên kệ bán lẻ từ một bức ảnh duy nhất. Với một ảnh kệ hàng có mật độ sản phẩm cao, hệ thống thực hiện một pipeline suy luận gồm nhiều giai đoạn:

1. **Định vị không phụ thuộc lớp (Object Detection):** Xác định tọa độ bounding box của tất cả các đối tượng sản phẩm mà không phụ thuộc vào class.

2. **Tinh chỉnh biên & Phân tích chồng lấp:** Phát hiện các vùng sản phẩm bị che khuất và tinh chỉnh ranh giới của từng instance bằng Segment Anything Model 2 (SAM2).

3. **Biểu diễn hình ảnh & Truy xuất:** Ánh xạ các crop của ảnh vào không gian vector nhiều chiều thông qua SigLIP2 và truy xuất Top-K ứng viên nhận diện bằng chỉ mục vector FAISS.

4. **Hợp nhất bằng chứng đa phương thức & Reranking:** Kết hợp các thuộc tính bổ sung — token OCR, đặc trưng màu trong không gian màu Lab (CIEDE2000) và dữ liệu mã vạch — để phân biệt các biến thể chi tiết của sản phẩm, ví dụ các sản phẩm có bao bì giống nhau nhưng khác màu, khối lượng tịnh hoặc một phần nội dung chữ.

5. **Audit Trail & Tổng hợp kiểm kê:** Tạo số lượng sản phẩm, thống kê theo từng SKU và toàn bộ nhật ký quyết định phục vụ kiểm toán và báo cáo.

Hệ thống duy trì kiến trúc tách biệt giữa **Localization** (phát hiện/phân đoạn) và **Identification** (truy xuất/hợp nhất bằng chứng). Thiết kế module hóa này cho phép tối ưu độc lập từng thành phần, thay thế backend linh hoạt và cô lập lỗi ở từng giai đoạn.

## 2. Kiến trúc hệ thống & Pipeline

```text
Input Image
    │
    ▼
① GIAI ĐOẠN DETECTION
    Detector (RF-DETR / Mock Contour)
    — Trích xuất bounding box không phụ thuộc class
    │
    ▼
② GIAI ĐOẠN PHÂN TÍCH CHỒNG LẤP
    OverlapResolver
    — Xác định các nhóm đối tượng bị che khuất với mật độ cao
    │
    ▼
③ GIAI ĐOẠN SEGMENTATION
    Refinement Engine (SAM2 / Mock / None)
    — Tinh chỉnh mask biên tại các vùng được đánh dấu
    │
    ▼
④ GIAI ĐOẠN CROPPING
    Cropper
    — Tạo hai đầu ra:
      Resized Crop (Retrieval)
      & Raw High-Res Crop (Secondary Plugins)
    │
    ▼
⑤ GIAI ĐOẠN VISUAL RETRIEVAL
    Retriever (SigLIP2 Vector Extraction + FAISS)
    — Truy xuất Top-K sản phẩm gần nhất trong gallery
    │
    ▼
⑥ GIAI ĐOẠN DECISION
    Decision Engine
    — Đánh giá similarity score dựa trên các threshold
    — Phát sinh cờ kích hoạt plugin (uncertain/ambiguous/force)
    │
    ▼
⑦ GIAI ĐOẠN SECONDARY EVIDENCE
    Plugin Manager (OCR / Color / Barcode)
    — Trích xuất bằng chứng chuyên biệt theo miền khi cần
    │
    ▼
⑧ GIAI ĐOẠN RERANKING & FUSION
    Reranker Engine
    — Hợp nhất nhiều loại bằng chứng trên Top-K ứng viên
    — Áp dụng Consensus Protection & Confusable-Pair Guards
    │
    ▼
OUTPUT SYNTHESIS
    Storage Manager
    — Xuất JSON / CSV / Annotated Visuals
```

Pipeline hỗ trợ hai phương thức thực thi:

* `InventoryPipeline.run()`: Luồng suy luận dành cho môi trường production, tối ưu độ trễ và mức sử dụng bộ nhớ.
* `InventoryPipeline.run_with_trace()`: Luồng thực thi mở rộng, ghi lại đầy đủ trạng thái qua cả 9 giai đoạn của pipeline để benchmark và chẩn đoán.

## 3. Các module & tính năng chính

* **Framework Backend dạng Plugin:** Các interface được tách biệt cho phép dễ dàng thay thế backend thông qua cấu hình:

  * **Detection Backends:** `rf_detr` (deep learning inference) / `mock_contour` (classical CV fallback).
  * **Retrieval Backends:** `siglip2` (vision-language embeddings) / `mock_visual_embedding` (HSV histograms).
  * **Segmentation Backends:** `sam2` (instance segmentation) / `none`.

* **Dual-Resolution Cropping Engine:** Tạo tensor resized được chuẩn hóa để trích xuất feature vector đồng thời tạo crop độ phân giải cao, không nén để thực hiện OCR và trích xuất mã vạch ở các plugin phụ trợ.

* **Dynamic Plugin Trigger Policies:** Hỗ trợ ba tiêu chí kích hoạt xác định:

  * `uncertain`: Similarity score nằm trong vùng threshold biên.
  * `ambiguous`: Các ứng viên Top-N có độ chênh cosine margin rất nhỏ.
  * `force`: Áp dụng các quy tắc miền cho những nhóm sản phẩm yêu cầu xác minh đa phương thức.

* **Multi-Modal Evidence Reranking:**

  * **Barcode Processing:** Pipeline giải mã thích ứng gồm 9 giai đoạn, được tối ưu cho crop có độ phân giải thấp, bị biến dạng hoặc xoay.
  * **OCR Processing:** Phân tích văn bản theo nhiều hướng (0°, 90°, 180°, 270°), kết hợp biến đổi tương phản CLAHE và đối chiếu token.
  * **Color Analysis:** Trích xuất màu trong không gian CIELAB và so sánh với chuẩn tham chiếu bằng metric CIEDE2000.
  * **Consensus & Guard Mechanisms:** Bảo vệ kết quả visual retrieval có độ tin cậy cao khỏi việc bị ghi đè bởi nhiễu từ plugin, đồng thời áp dụng các quy tắc kiểm tra nghiêm ngặt cho những cặp sản phẩm dễ nhầm lẫn.

* **9-Stage Validation Suite:** Framework đánh giá tích hợp, đo lường hiệu năng trên từng giai đoạn — từ object detection ban đầu đến phân loại SKU End-to-End — cùng hệ thống báo cáo chẩn đoán tự động.

* **Desktop Graphical Interface (Tkinter UI):** Dashboard giám sát thời gian thực, hỗ trợ trực quan hóa số lượng SKU, điều chỉnh threshold động, kiểm tra hình ảnh và phân tích validation trace.

## 4. Cấu trúc dự án

```text
stocktaking_ai/
├── .env
├── .gitignore
├── README.md
├── requirements.txt
├── setup.bat                    # Script thiết lập E2E một lần chạy trên Windows
├── setup.sh                     # Script thiết lập tự động trên Linux
├── setup.command                # Script thiết lập một lần chạy trên macOS
├── run.py                       # CLI entry point (Build -> Infer / Validate / UI)
├── test.py                      # Entry script kiểm thử thủ công nhanh
├── assets_manifest.json         # Manifest kiểm tra checksum và tính toàn vẹn cấu trúc
├── configs/
│   └── config.yaml              # Cấu hình runtime chính
├── data/
│   ├── gallery/                 # Ảnh sản phẩm tham chiếu dùng để lập chỉ mục vector
│   ├── metadata/                # Catalog SKU, bản đồ màu và ánh xạ ID
│   ├── benchmark/               # Dataset đánh giá theo định dạng COCO
│   ├── query/                   # Ảnh kệ hàng đầu vào cho inference
│   ├── outputs/                 # Kết quả xuất ra (JSON, CSV, ảnh đã annotation)
│   └── cache/                   # FAISS vector index & metadata cache được serialize
├── debug/                       # Các script chẩn đoán và xác minh độc lập
├── docs/                        # Đặc tả kiến trúc, context và hướng dẫn phát triển
├── notebooks/                   # Jupyter notebook phân tích và đánh giá pipeline
├── scripts/
│   ├── setup.py                 # Logic khởi tạo environment và assets
│   ├── generate_manifest.py     # Script tạo asset manifest
│   └── verify_manifest.py       # Script kiểm tra tính toàn vẹn manifest
├── weights/                     # Model checkpoints (RF-DETR, SAM2, SigLIP2)
│   ├── detector/                # Trọng số model detection
│   ├── refinement/              # Trọng số model segmentation
│   └── retriever/               # Trọng số vision encoder offline
├── src/
│   ├── catalog/                 # Biên dịch metadata và lập chỉ mục catalog
│   ├── core/                    # Cấu hình hệ thống, logging và tiện ích chung
│   ├── decision/                # Similarity thresholding & multi-evidence reranking
│   ├── detection/               # Backend object detection và tạo dual-crop
│   ├── inference/               # Engine inference production dạng batch/single-image
│   ├── models/                  # Domain Data Transfer Objects (Pydantic / Dataclasses)
│   ├── pipeline/                # Master orchestrator, xử lý overlap và offline build
│   ├── plugins/                 # Plugin bằng chứng phụ trợ (OCR, Color, Barcode)
│   ├── retrieval/               # Trích xuất SigLIP2 embedding và FAISS indexer
│   ├── segmentation/            # Tinh chỉnh boundary mask bằng SAM2
│   ├── storage/                 # Lưu output CSV/JSON và visualization overlay
│   ├── ui/                      # Desktop Graphical Interface (Tkinter dashboard)
│   └── validation/              # Bộ đánh giá 9 giai đoạn và tính toán metric
└── tests/                       # Bộ test unit và integration bằng pytest
```

## 5. Yêu cầu hệ thống

* **Hệ điều hành:** Linux (khuyến nghị Ubuntu 20.04/22.04 LTS) / macOS / Windows 11
* **Môi trường Runtime:** Python `>= 3.11`
* **Dependencies chính:** `numpy`, `opencv-python-headless`, `PyYAML`, `pydantic`, `Pillow`, `matplotlib`, `faiss-cpu`
* **Framework Deep Learning:** `torch`, `torchvision`, `transformers`, `rfdetr`, `supervision`, `sam2`
* **Công cụ chuyên biệt:** `easyocr`, `pyzbar` (yêu cầu system-level `libzbar0`), `tkinter`
* **Tăng tốc phần cứng:** NVIDIA GPU với **>= 8GB VRAM** (khuyến nghị CUDA cho RF-DETR, SAM2 và SigLIP2).

## 6. Cài đặt & thiết lập môi trường

### Thiết lập tự động nhanh (Khuyến nghị)

Project cung cấp các script thiết lập đa nền tảng (`setup.bat`, `setup.sh`, `setup.command`) tại thư mục gốc. Các script này kiểm tra Python, xác thực dependencies và thực thi `scripts/setup.py`.

> **Lưu ý:** Hãy đảm bảo chạy các script setup trực tiếp từ **thư mục gốc của project**.

#### Windows

Nhấp đúp vào `setup.bat` hoặc thực thi trong CMD / PowerShell:

```bat
setup.bat
```

#### Linux

Cấp quyền thực thi và chạy:

```bash
chmod +x setup.sh
./setup.sh
```

#### macOS

Nhấp đúp vào `setup.command` trong Finder hoặc chạy bằng Terminal:

```bash
chmod +x setup.command
./setup.command
```

### Dependencies hệ thống & Cài đặt thủ công

Nếu muốn thiết lập môi trường thủ công:

```bash
# Clone repository và cài đặt các dependencies chính
git clone <https://github.com/longpham205/stocktaking_ai>
cd stocktaking_ai
pip install -r requirements.txt --break-system-packages

# Cài đặt dependency cấp hệ thống cho giải mã mã vạch (Linux)
sudo apt-get install -y libzbar0
```

#### Đặt Model Checkpoint

Các trọng số neural network phải được đặt vào đúng thư mục trước khi chạy hệ thống.

Bạn có thể tải toàn bộ model weights tại [Google Drive Link](https://drive.google.com/file/d/1c-vusZjXSafgXFLbeaFzU6MRuBQGS4-k/view?usp=drive_link) (`weight.zip`).

| Model / Subsystem          | Đường dẫn Checkpoint yêu cầu                                    |
| :------------------------- | :-------------------------------------------------------------- |
| **RF-DETR Detector**       | `weights/detector/checkpoint_best_ema.pth`                      |
| **SAM2 Refinement Model**  | `weights/refinement/sam2/sam2.1_hiera_small.pt`                 |
| **SigLIP2 Vision Encoder** | Tự động tải từ Hugging Face (`google/siglip2-base-patch16-224`) |

## 7. Đặc tả Dataset & Metadata

* **Gallery Store (`data/gallery/`):** Cấu trúc thư mục chứa các ảnh sản phẩm tham chiếu theo từng SKU. Tên thư mục ánh xạ trực tiếp đến descriptor của sản phẩm được định nghĩa trong `configs/config.yaml`.

* **Color Standards (`data/metadata/product_colors.json`):** Định nghĩa các chuẩn màu canonical trong không gian RGB/CIELAB để phân biệt các biến thể:

```json
{
  "PK300": {
    "name": "PK300",
    "rgb": [109, 63, 62],
    "hex": "#6D3F3E"
  }
}
```

* **Benchmark Dataset (`data/benchmark/`):** Annotation object detection chuẩn hóa theo định dạng COCO. Thuộc tính `category_id` ánh xạ trực tiếp đến `product_id` dạng số nội bộ của hệ thống.

**Tải dữ liệu:** Có thể tải toàn bộ cấu trúc dataset (`data.zip`) từ [Google Drive Link](https://drive.google.com/file/d/1QHuoY2Wmo49jKrEcf-wvSfA4mU7YL1o5/view?usp=drive_link).

## 8. Schema cấu hình

Tất cả tham số runtime được tập trung trong `configs/config.yaml`.

| Parameter Block | Phạm vi chức năng                                                     |
| :-------------- | :-------------------------------------------------------------------- |
| `catalog`       | Quy tắc ánh xạ SKU ID và các tùy chọn biên dịch catalog               |
| `detection`     | Lựa chọn backend, confidence threshold, IoU limit và tham số detector |
| `refinement`    | Trigger gọi SAM2 và các giới hạn boundary hình học                    |
| `cropping`      | Padding mở rộng bounding box và độ phân giải tensor mục tiêu          |
| `retrieval`     | Kích thước feature vector, tham số FAISS và giới hạn Top-K            |
| `decision`      | Similarity threshold cho acceptance, uncertainty và rejection         |
| `plugins`       | Tham số cấu hình cho OCR, Color, Barcode và các rule override         |
| `rerank`        | Trọng số hợp nhất bằng chứng, ngưỡng ΔE màu và các rule bảo vệ        |
| `storage`       | Định dạng output và kiểu annotation trực quan                         |
| `validation`    | IoU threshold đánh giá, bật/tắt stage và xuất báo cáo                 |

## 9. Hướng dẫn thực thi hệ thống

Entry point chính `run.py` tự động xây dựng các index và metadata cần thiết trước khi thực thi command (`--skip-build` có thể bỏ qua bước này).

```bash
# 1. Thực hiện inference trên một ảnh
python run.py --mode infer --image data/query/test_shelf.jpg

# 2. Thực hiện inference batch trên một thư mục
python run.py --mode infer --image-dir data/query/

# 3. Chạy validation suite trên COCO benchmark dataset
python run.py --mode validate --benchmark-dir data/benchmark/

# 4. Khởi chạy Desktop Graphical Interface
python run.py --mode ui
```

### Sử dụng Python API

```python
from src.core.config import load_config
from src.pipeline.pipeline import InventoryPipeline

config = load_config()
pipeline = InventoryPipeline(config)

# Thực thi inference tiêu chuẩn
result = pipeline.run(image_data)

# Thực thi chẩn đoán với trace đầy đủ các stage
result, trace = pipeline.run_with_trace(image_data)
```

## 10. Đặc tả Input & Output

| Chế độ vận hành | Định dạng Input        | Artifacts được tạo (`data/outputs/`)                                                              |
| :-------------- | :--------------------- | :------------------------------------------------------------------------------------------------ |
| Inference       | Image file / Directory | `result.json` (audit log đầy đủ), `result.csv` (số lượng sản phẩm), `result.jpg` (ảnh annotation) |
| Validation      | COCO Benchmark Dir     | `report.json/csv` (metric 9 giai đoạn), `records.csv` (log cấp instance), ảnh & biểu đồ chẩn đoán |
| GUI Application | User Input tương tác   | Bảng đếm thời gian thực, annotated overlay, thanh điều chỉnh threshold động                       |

## 11. Đánh giá hiệu năng định lượng

Benchmark hiệu năng trên test dataset tiêu chuẩn (**8 SKU cốt lõi / tối đa 16 SKU mở rộng, 31 cảnh kệ hàng, 293 instance được annotation**):

| Pipeline Stage             | Metric chính                                       |           Giá trị đo được |
| :------------------------- | :------------------------------------------------- | ------------------------: |
| Detection (RF-DETR FT)     | Precision / Recall / mAP@50                        | **0.970 / 0.940 / 0.990** |
| Detection (RF-DETR FT)     | F1-Score (Class-Agnostic, IoU ≥ 0.3)               |                 **0.950** |
| Visual Retrieval (SigLIP2) | Top-1 Accuracy (287 crop hợp lệ)                   |                 **0.735** |
| Visual Retrieval (SigLIP2) | Top-5 Accuracy (K=5)                               |                 **1.000** |
| Decision Engine            | Pre-Fusion Accuracy (264 trường hợp được đánh giá) |                 **0.712** |
| Evidence Fusion            | Accuracy Delta (Reranking Gain)                    |                **+0.224** |
| Post-Fusion Decision       | Post-Fusion Accuracy (Reranking)                   |                 **0.936** |
| Product Counting           | Product Count Accuracy (31 cảnh)                   |                 **0.871** |
| **End-to-End Pipeline**    | **Precision / Recall / F1-Score**                  | **0.913 / 0.966 / 0.939** |

### Tóm tắt kết quả chính

Kiến trúc multi-evidence reranking đã xử lý đúng **60 trường hợp nhận diện sai từ visual retrieval**, với chỉ **1 trường hợp sửa sai (1 regression)** trên tổng số 264 trường hợp được đánh giá.

Điều này giúp độ chính xác nhận diện sau fusion đạt **93.6%**, đồng thời đưa **F1-Score End-to-End lên 0.939**, với **Product Count Accuracy đạt 87.1%**.

## 12. Phân tích kỹ thuật & Nhật ký thực nghiệm

1. **Tối ưu Pooling của SigLIP2:** Khắc phục vấn đề khi embedding chưa pooling có kích thước `(1, 196, 768)` thay vì global feature vector `(1, 768)`. Việc thực hiện mean-pooling trên chiều spatial patch đã tăng **Top-1 Retrieval Accuracy từ 3% lên 55%**.

2. **Kiến trúc Dual-Resolution Crop:** Giải quyết vấn đề suy giảm chất lượng chữ do downsampling đồng nhất trong quá trình trích xuất visual feature. Việc tạo crop riêng không nén (`raw_image_array`) cho plugin đã cải thiện **tỷ lệ nhận diện OCR từ 54% lên 98%**.

3. **OCR đa hướng:** Triển khai quét xoay nhiều góc (0°, 90°, 180°, 270°) kết hợp Contrast Limited Adaptive Histogram Equalization (CLAHE) để xử lý các sản phẩm có hướng đặt tùy ý trên kệ.

4. **Kiến trúc Reranking có cơ chế bảo vệ:** Thiết kế **Retrieval Consensus Protection** và **Confusable-Pair Guard** nhằm ngăn nhiễu từ các plugin phụ trợ ghi đè lên kết quả vector matching có độ tin cậy cao, đạt mức tăng accuracy ròng **+22.4%**.

5. **Pipeline giải mã mã vạch thích ứng:** Xây dựng pipeline giải mã thích ứng gồm 9 giai đoạn, sử dụng gradient anisotropy để định vị mã vạch, deskew dựa trên vùng và adaptive thresholding nhằm xử lý các mã bị mờ hoặc lệch góc.

## 13. Trạng thái Runtime hiện tại

* **Trạng thái Barcode Subsystem:** Engine giải mã mã vạch thích ứng gồm 9 giai đoạn đã được bật hoàn toàn (`plugins.barcode.enabled: true`) và tích hợp vào pipeline evidence fusion đang hoạt động.

* **Tích hợp Catalog Metadata:** Product catalog (`products.json`) đã được điền đầy đủ các giá trị GTIN/Barcode canonical, cho phép bằng chứng từ mã vạch tham gia trực tiếp vào quá trình re-ranking ứng viên và phân biệt biến thể.

## 14. Hạn chế kỹ thuật

* Các lỗi nhận diện fine-grained (~6%) tập trung ở các biến thể bao bì gần như giống hệt nhau và chỉ khác một số mô tả nhỏ trên chữ, ví dụ các biến thể khác khối lượng tịnh. Những trường hợp này sẽ kích hoạt cờ ambiguous để kiểm tra thủ công.

* Database màu tham chiếu (`product_colors.json`) hiện phụ thuộc vào cấu hình thủ công thay vì tự động trích xuất từ gallery dataset.

* Module nhận diện OCR được tối ưu cho các ký tự Latin và chữ số (ngôn ngữ: `[en]`) và chưa xử lý các hệ chữ không phải Latin trên bao bì.

* Chạy trên CPU được hỗ trợ đầy đủ nhưng có độ trễ xử lý cao hơn so với tăng tốc bằng CUDA GPU.

## 15. Lộ trình phát triển

* Tự động hóa việc trích xuất màu tham chiếu trực tiếp từ ảnh gallery bằng phương pháp K-Means clustering cục bộ trong không gian CIELAB.

* Re-validate và kích hoạt adaptive barcode plugin trong pipeline evidence fusion sau khi hoàn tất tích hợp metadata.

* Tinh chỉnh heuristic kích hoạt segmentation của SAM2 để tối ưu chất lượng boundary đối với các kệ hàng có sản phẩm được xếp sát nhau.

* Mở rộng phạm vi ngôn ngữ OCR để hỗ trợ bao bì bán lẻ đa ngôn ngữ.

## 16. Trích dẫn

Codebase nghiên cứu và phát triển nội bộ. Hiện tại chưa gắn với một công bố học thuật bên ngoài.

## 17. Giấy phép

Phần mềm nội bộ độc quyền. Cần tham khảo các điều khoản cấp phép của tổ chức trước khi phân phối ra bên ngoài.
