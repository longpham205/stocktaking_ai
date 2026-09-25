# Stocktaking AI — Nhận diện & đếm sản phẩm tại quầy thanh toán

> Hệ thống AI nhận diện và đếm từng sản phẩm đặt trên **bàn thu ngân** từ một ảnh chụp duy nhất — kết hợp truy xuất hình ảnh với bằng chứng từ OCR, màu sắc và mã vạch để phân biệt các biến thể sản phẩm có bao bì gần như giống hệt nhau.

> **Ghi chú phiên bản:** Tài liệu này mô tả hướng điều chỉnh dự án từ kịch bản *ảnh kệ hàng* sang *ảnh bàn thu ngân*. Các mục đánh dấu **[Kế hoạch]** là phần chưa triển khai; các mục đánh dấu **[Baseline kệ hàng]** là số liệu đo trên bộ dữ liệu kệ hàng cũ, chưa phản ánh hiệu năng trên bàn thu ngân.

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Khác biệt giữa kệ hàng và bàn thu ngân](#2-khác-biệt-giữa-kệ-hàng-và-bàn-thu-ngân)
3. [Kiến trúc hệ thống & Pipeline](#3-kiến-trúc-hệ-thống--pipeline)
4. [Các module & tính năng chính](#4-các-module--tính-năng-chính)
5. [Cấu trúc dự án](#5-cấu-trúc-dự-án)
6. [Yêu cầu hệ thống](#6-yêu-cầu-hệ-thống)
7. [Cài đặt & thiết lập môi trường](#7-cài-đặt--thiết-lập-môi-trường)
8. [Đặc tả Dataset & Metadata](#8-đặc-tả-dataset--metadata)
9. [Schema cấu hình](#9-schema-cấu-hình)
10. [Hướng dẫn thực thi](#10-hướng-dẫn-thực-thi)
11. [Đặc tả Input & Output](#11-đặc-tả-input--output)
12. [Đánh giá hiệu năng](#12-đánh-giá-hiệu-năng)
13. [Hạn chế kỹ thuật](#13-hạn-chế-kỹ-thuật)
14. [Lộ trình phát triển](#14-lộ-trình-phát-triển)
15. [Trích dẫn](#15-trích-dẫn)
16. [Giấy phép](#16-giấy-phép)

## 1. Tổng quan

**Stocktaking AI** là hệ thống Computer Vision tự động nhận diện và đếm sản phẩm khách hàng đặt trên bàn thu ngân từ một bức ảnh. Với mỗi ảnh, hệ thống thực hiện pipeline nhiều giai đoạn:

1. **Định vị không phụ thuộc lớp (Object Detection):** xác định bounding box của mọi vật thể trên bàn, không phụ thuộc class.
2. **Tinh chỉnh biên & phân tích chồng lấp:** phát hiện các vật bị che khuất hoặc xếp chồng và tinh chỉnh ranh giới từng instance bằng SAM2.
3. **Biểu diễn hình ảnh & truy xuất:** ánh xạ crop vào không gian vector bằng SigLIP2 và truy xuất Top-K ứng viên bằng chỉ mục FAISS.
4. **Hợp nhất bằng chứng đa phương thức & reranking:** kết hợp token OCR, màu trong không gian Lab (CIEDE2000) và mã vạch để phân biệt các biến thể có hình thức gần giống nhau (khác màu, khối lượng tịnh, hoặc một phần nội dung chữ).
5. **Audit trail & tổng hợp kết quả:** xuất danh sách sản phẩm, số lượng theo SKU và toàn bộ nhật ký quyết định phục vụ kiểm tra lại.

Hệ thống giữ kiến trúc tách biệt giữa **Localization** (phát hiện/phân đoạn) và **Identification** (truy xuất/hợp nhất bằng chứng), cho phép tối ưu độc lập từng thành phần, thay thế backend linh hoạt và cô lập lỗi ở từng giai đoạn.

## 2. Khác biệt giữa kệ hàng và bàn thu ngân

| Tiêu chí | Kệ hàng (bản gốc) | Bàn thu ngân (bản này) |
| --- | --- | --- |
| Số lượng vật thể / ảnh | Nhiều, mật độ cao | Ít hơn, đặt rời rạc |
| Cách sắp xếp | Xếp thành hàng, mặt trước hướng ra ngoài | Đặt tự do, hướng bất kỳ (nằm, úp, nghiêng) |
| Mặt sản phẩm nhìn thấy | Chủ yếu mặt trước | Có thể là mặt sau hoặc mặt bên |
| Che khuất | Sản phẩm sát nhau | Sản phẩm xếp chồng, tay người, túi |
| Nhiễu nền | Kệ, nhãn giá | Mặt bàn, tay, túi, hóa đơn |
| Mã vạch | Thường khó thấy | Thường lộ rõ hơn |
| Điều kiện chụp | Thay đổi theo cửa hàng | Có thể cố định camera và ánh sáng |
| Yêu cầu độ trễ | Kiểm kê, có thể chạy offline | Cần phản hồi nhanh khi thanh toán |

## 3. Kiến trúc hệ thống & Pipeline

```
Input Image (bàn thu ngân)
    │
    ▼
① DETECTION
    Detector (RF-DETR / Mock Contour)
    — Bounding box không phụ thuộc class
    │
    ▼
② PHÂN TÍCH CHỒNG LẤP
    OverlapResolver
    — Xác định nhóm vật bị che khuất / xếp chồng
    │
    ▼
③ SEGMENTATION
    Refinement Engine (SAM2 / Mock / None)
    — Tinh chỉnh mask biên
    │
    ▼
④ CROPPING
    Cropper
    — Resized Crop (Retrieval) & Raw High-Res Crop (OCR / Barcode / Color)
    │
    ▼
⑤ VISUAL RETRIEVAL
    Retriever (SigLIP2 + FAISS)
    — Top-K sản phẩm gần nhất trong gallery
    │
    ▼
⑥ DECISION
    Decision Engine
    — Đánh giá similarity, phát cờ uncertain / ambiguous / force
    │
    ▼
⑦ SECONDARY EVIDENCE
    Plugin Manager (OCR / Color / Barcode)
    │
    ▼
⑧ RERANKING & FUSION
    Reranker Engine
    — Hợp nhất bằng chứng, Consensus Protection & Confusable-Pair Guards
    │
    ▼
OUTPUT
    Storage Manager
    — JSON / CSV / ảnh annotation
```

Hai phương thức thực thi:

- `InventoryPipeline.run()`: luồng suy luận chuẩn, tối ưu độ trễ và bộ nhớ.
- `InventoryPipeline.run_with_trace()`: luồng chẩn đoán, ghi lại trạng thái qua mọi giai đoạn để benchmark và debug.

## 4. Các module & tính năng chính

- **Backend dạng plugin:** thay thế backend qua cấu hình.
  - Detection: `rf_detr` / `mock_contour`
  - Retrieval: `siglip2` / `mock_visual_embedding`
  - Segmentation: `sam2` / `none`
- **Dual-Resolution Cropping:** crop resize chuẩn hóa cho trích xuất vector, đồng thời crop độ phân giải cao không nén cho OCR và mã vạch.
- **Chính sách kích hoạt plugin:**
  - `uncertain`: similarity nằm trong vùng ngưỡng biên.
  - `ambiguous`: chênh lệch cosine giữa các ứng viên Top-N rất nhỏ.
  - `force`: quy tắc miền cho nhóm sản phẩm cần xác minh đa phương thức.
- **Reranking đa bằng chứng:**
  - *Barcode:* giải mã thích ứng 9 giai đoạn cho crop độ phân giải thấp, biến dạng hoặc xoay.
  - *OCR:* quét đa hướng (0°, 90°, 180°, 270°), CLAHE, đối chiếu token.
  - *Màu sắc:* trích xuất CIELAB, so khớp bằng CIEDE2000.
  - *Consensus & Guard:* bảo vệ kết quả retrieval tin cậy khỏi nhiễu plugin, kiểm tra chặt các cặp sản phẩm dễ nhầm.
- **Bộ validation 9 giai đoạn:** đánh giá từng giai đoạn, từ detection đến phân loại SKU end-to-end.
- **Giao diện desktop (Tkinter):** hiển thị số lượng SKU, chỉnh ngưỡng động, kiểm tra ảnh và trace.

**[Kế hoạch] Bổ sung cho bàn thu ngân:**

- Retrieval bất biến hướng: gallery nhiều mặt (trước/sau/bên) và augmentation xoay khi lập chỉ mục, hoặc truy vấn với nhiều bản xoay của crop.
- Vùng quan tâm (ROI) và trừ nền cho camera cố định để loại nhiễu mặt bàn.
- Lọc đối tượng không phải sản phẩm (tay, túi, hóa đơn).
- Màn hình xác nhận cho thu ngân với các ca bị gắn cờ `ambiguous`.
- Trường giá trong catalog và tính tổng tiền (nếu mở rộng sang thanh toán).

## 5. Cấu trúc dự án

```
stocktaking_ai/
├── .env
├── .gitignore
├── README.md
├── requirements.txt
├── setup.bat / setup.sh / setup.command
├── run.py                       # CLI entry point (Build -> Infer / Validate / UI)
├── assets_manifest.json
├── configs/
│   └── config.yaml              # Cấu hình runtime chính
├── data/
│   ├── gallery/                 # Ảnh sản phẩm tham chiếu theo từng SKU
│   ├── metadata/                # Catalog SKU, bản đồ màu, ánh xạ ID
│   ├── benchmark/               # Dataset đánh giá định dạng COCO
│   ├── query/                   # Ảnh bàn thu ngân đầu vào
│   ├── outputs/                 # JSON, CSV, ảnh annotation
│   └── cache/                   # FAISS index & metadata cache
├── docs/
├── notebooks/
├── scripts/
├── weights/                     # detector / refinement / retriever
├── src/
│   ├── catalog/  core/  decision/  detection/  inference/
│   ├── models/   pipeline/  plugins/  retrieval/
│   ├── segmentation/  storage/  ui/  validation/
└── tests/
```

## 6. Yêu cầu hệ thống

- **Hệ điều hành:** Linux (khuyến nghị Ubuntu 20.04/22.04) / macOS / Windows 11
- **Python:** `>= 3.11`
- **Thư viện chính:** `numpy`, `opencv-python-headless`, `PyYAML`, `pydantic`, `Pillow`, `matplotlib`, `faiss-cpu`
- **Deep learning:** `torch`, `torchvision`, `transformers`, `rfdetr`, `supervision`, `sam2`
- **Chuyên biệt:** `easyocr`, `pyzbar` (cần `libzbar0`), `tkinter`
- **Phần cứng:** GPU NVIDIA với >= 8GB VRAM (khuyến nghị CUDA). Chạy CPU được hỗ trợ nhưng chậm hơn.

## 7. Cài đặt & thiết lập môi trường

### Thiết lập nhanh

Chạy script tương ứng từ **thư mục gốc** của project: `setup.bat` (Windows), `./setup.sh` (Linux), `./setup.command` (macOS).

### Thiết lập thủ công

```bash
git clone https://github.com/longpham205/stocktaking_ai
cd stocktaking_ai

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Linux: thư viện hệ thống cho giải mã mã vạch
sudo apt-get install -y libzbar0
```

### Model checkpoint

| Model | Đường dẫn |
| --- | --- |
| RF-DETR Detector | `weights/detector/checkpoint_best_ema.pth` |
| SAM2 Refinement | `weights/refinement/sam2/sam2.1_hiera_small.pt` |
| SigLIP2 Encoder | Tự tải từ Hugging Face (`google/siglip2-base-patch16-224`) |

> **[Kế hoạch]** Checkpoint RF-DETR hiện tại được fine-tune trên ảnh kệ hàng. Cần fine-tune lại trên ảnh bàn thu ngân trước khi dùng thực tế.

## 8. Đặc tả Dataset & Metadata

- **Gallery (`data/gallery/`):** ảnh tham chiếu theo từng SKU, tên thư mục ánh xạ đến descriptor trong `configs/config.yaml`. **[Kế hoạch]** mỗi SKU có ảnh nhiều mặt (trước, sau, hai bên).
- **Chuẩn màu (`data/metadata/product_colors.json`):** màu canonical dạng RGB/CIELAB cho từng biến thể.

```json
{
  "PK300": { "name": "PK300", "rgb": [109, 63, 62], "hex": "#6D3F3E" }
}
```

- **Catalog (`products.json`):** thông tin SKU và mã GTIN/barcode. **[Kế hoạch]** thêm trường giá.
- **Benchmark (`data/benchmark/`):** annotation dạng COCO, `category_id` ánh xạ đến `product_id` nội bộ. **[Kế hoạch]** thu thập và gán nhãn bộ ảnh bàn thu ngân riêng.

## 9. Schema cấu hình

Toàn bộ tham số runtime nằm trong `configs/config.yaml`:

| Khối | Phạm vi |
| --- | --- |
| `catalog` | Ánh xạ SKU ID, biên dịch catalog |
| `detection` | Backend, confidence, IoU, tham số detector |
| `refinement` | Điều kiện gọi SAM2, giới hạn hình học |
| `cropping` | Padding box, độ phân giải tensor |
| `retrieval` | Kích thước vector, tham số FAISS, Top-K |
| `decision` | Ngưỡng accept / uncertain / reject |
| `plugins` | Cấu hình OCR, Color, Barcode, rule override |
| `rerank` | Trọng số hợp nhất, ngưỡng ΔE, rule bảo vệ |
| `storage` | Định dạng output, kiểu annotation |
| `validation` | IoU đánh giá, bật/tắt stage, xuất báo cáo |

## 10. Hướng dẫn thực thi

```bash
# 1. Inference trên một ảnh bàn thu ngân
python run.py --mode infer --image data/query/test_counter.jpg

# 2. Inference batch
python run.py --mode infer --image-dir data/query/

# 3. Validation trên benchmark COCO
python run.py --mode validate --benchmark-dir data/benchmark/

# 4. Giao diện desktop
python run.py --mode ui
```

Dùng như thư viện Python:

```python
from src.core.config import load_config
from src.pipeline.pipeline import InventoryPipeline

config = load_config()
pipeline = InventoryPipeline(config)

result = pipeline.run(image_data)                     # suy luận chuẩn
result, trace = pipeline.run_with_trace(image_data)   # chẩn đoán đầy đủ
```

## 11. Đặc tả Input & Output

| Chế độ | Input | Artifacts (`data/outputs/`) |
| --- | --- | --- |
| Inference | Ảnh / thư mục ảnh | `result.json` (audit log), `result.csv` (số lượng theo SKU), `result.jpg` (ảnh annotation) |
| Validation | Thư mục benchmark COCO | `report.json/csv`, `records.csv`, ảnh & biểu đồ chẩn đoán |
| GUI | Tương tác người dùng | Bảng đếm thời gian thực, overlay, thanh chỉnh ngưỡng |

## 12. Đánh giá hiệu năng

### [Baseline kệ hàng]

Đo trên bộ test kệ hàng: 8 SKU cốt lõi (tối đa 16 SKU mở rộng), 31 cảnh, 293 instance.

| Giai đoạn | Metric | Giá trị |
| --- | --- | --- |
| Detection (RF-DETR FT) | Precision / Recall / mAP@50 | 0.970 / 0.940 / 0.990 |
| Detection | F1 (class-agnostic, IoU ≥ 0.3) | 0.950 |
| Visual Retrieval (SigLIP2) | Top-1 / Top-5 | 0.735 / 1.000 |
| Decision Engine | Pre-fusion accuracy | 0.712 |
| Evidence Fusion | Accuracy delta | +0.224 |
| Post-fusion | Accuracy | 0.936 |
| Product Counting | Count accuracy | 0.871 |
| End-to-End | Precision / Recall / F1 | 0.913 / 0.966 / 0.939 |

> Các số liệu trên **không** áp dụng trực tiếp cho bàn thu ngân.

### [Kế hoạch] Đánh giá trên bàn thu ngân

Sau khi thu thập bộ ảnh mới, đo lại toàn bộ metric trên, kèm hai chỉ số mới: độ trễ end-to-end trên mỗi ảnh và tỷ lệ ca bị gắn cờ `ambiguous` cần thu ngân xác nhận.

## 13. Hạn chế kỹ thuật

- Lỗi nhận diện fine-grained tập trung ở các biến thể gần như giống hệt nhau (ví dụ khác khối lượng tịnh); các ca này kích hoạt cờ `ambiguous`.
- Retrieval hiện chưa xử lý tốt sản phẩm nằm ở hướng bất kỳ hoặc chỉ thấy mặt sau/mặt bên **[cần cải tiến]**.
- Detector chưa được huấn luyện để bỏ qua tay, túi và hóa đơn **[cần cải tiến]**.
- Chuẩn màu tham chiếu đang cấu hình thủ công.
- OCR mới tối ưu cho chữ Latin và chữ số (`[en]`).
- Chạy CPU độ trễ cao hơn GPU.

## 14. Lộ trình phát triển

- Thu thập và gán nhãn bộ ảnh bàn thu ngân, fine-tune lại RF-DETR.
- Gallery nhiều mặt và truy xuất bất biến hướng.
- ROI và trừ nền cho camera cố định.
- Giao diện xác nhận cho thu ngân, tính tổng tiền theo catalog.
- Tự động trích xuất màu tham chiếu từ gallery bằng K-Means trong CIELAB.
- Tinh chỉnh heuristic kích hoạt SAM2 cho sản phẩm xếp chồng.
- Mở rộng OCR đa ngôn ngữ.

## 15. Trích dẫn

Codebase nghiên cứu và phát triển nội bộ, chưa gắn với công bố học thuật bên ngoài.

## 16. Giấy phép

Phần mềm nội bộ độc quyền. Cần tham khảo điều khoản cấp phép của tổ chức trước khi phân phối ra bên ngoài.
