# Stocktaking AI — Nhận diện & đếm sản phẩm tại quầy thanh toán

> Hệ thống AI nhận diện và đếm từng sản phẩm đặt trên **bàn thu ngân** từ một ảnh chụp duy nhất — kết hợp truy xuất hình ảnh với bằng chứng từ OCR, màu sắc và mã vạch để phân biệt các biến thể sản phẩm có bao bì gần như giống hệt nhau.

> **Kiến trúc:** `backend/` (FastAPI + Postgres + engine nhận diện ở `backend/engine/`) và `frontend/` (Vite + React), chạy bằng `make` và Docker Compose. Web POS thế hệ đầu (stdlib `http.server` + SQLite) đóng băng trong [`src_legacy/`](src_legacy/README.md), chỉ để đối chiếu.

> **Hướng dẫn nhanh:** chạy web POS — [`docs/WEB.md`](docs/WEB.md) · kiến trúc và sơ đồ — [`docs/system-architecture.md`](docs/system-architecture.md) · ngày demo — [`docs/DEMO.md`](docs/DEMO.md) · dữ liệu và catalog — [`docs/04_DATA_AND_CATALOG.md`](docs/04_DATA_AND_CATALOG.md) · luật phát triển — [`docs/03_DEVELOPMENT_RULES.md`](docs/03_DEVELOPMENT_RULES.md).

> Các mục đánh dấu **[Kế hoạch]** là hạng mục chưa triển khai.

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Đặc thù của bối cảnh bàn thu ngân](#2-đặc-thù-của-bối-cảnh-bàn-thu-ngân)
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

**Stocktaking AI** là hệ thống Computer Vision được thiết kế ngay từ đầu cho bài toán tự động nhận diện và đếm sản phẩm khách hàng đặt trên bàn thu ngân từ một bức ảnh chụp duy nhất. Với mỗi ảnh, hệ thống thực hiện pipeline nhiều giai đoạn:

1. **Định vị không phụ thuộc lớp (Object Detection):** xác định bounding box của mọi vật thể trên bàn, không phụ thuộc class.
2. **Tinh chỉnh biên & phân tích chồng lấp:** phát hiện các vật bị che khuất hoặc xếp chồng và tinh chỉnh ranh giới từng instance bằng SAM2.
3. **Biểu diễn hình ảnh & truy xuất:** ánh xạ crop vào không gian vector bằng SigLIP2 và truy xuất Top-K ứng viên bằng chỉ mục FAISS.
4. **Hợp nhất bằng chứng đa phương thức & reranking:** kết hợp token OCR, màu trong không gian Lab (CIEDE2000) và mã vạch để phân biệt các biến thể có hình thức gần giống nhau (khác màu, khối lượng tịnh, hoặc một phần nội dung chữ).
5. **Audit trail & tổng hợp kết quả:** xuất danh sách sản phẩm, số lượng theo SKU và toàn bộ nhật ký quyết định phục vụ kiểm tra lại.

Hệ thống giữ kiến trúc tách biệt giữa **Localization** (phát hiện/phân đoạn) và **Identification** (truy xuất/hợp nhất bằng chứng), cho phép tối ưu độc lập từng thành phần, thay thế backend linh hoạt và cô lập lỗi ở từng giai đoạn.

## 2. Đặc thù của bối cảnh bàn thu ngân

Ảnh đầu vào là ảnh chụp bàn thu ngân tại thời điểm thanh toán, có một số đặc thù định hình trực tiếp các quyết định kiến trúc của hệ thống:

- **Số lượng vật thể / ảnh:** ít, đặt rời rạc (không dày đặc như một kệ trưng bày).
- **Cách sắp xếp:** đặt tự do, hướng bất kỳ (nằm, úp, nghiêng) — không có mặt trước cố định hướng ra ngoài.
- **Mặt sản phẩm nhìn thấy:** có thể là mặt trước, mặt sau hoặc mặt bên, tùy cách khách đặt sản phẩm lên bàn.
- **Che khuất:** sản phẩm có thể xếp chồng lên nhau, hoặc bị tay người/túi che một phần.
- **Nhiễu nền:** mặt bàn, tay, túi, hóa đơn.
- **Mã vạch:** thường lộ rõ vì sản phẩm được đặt gần camera.
- **Điều kiện chụp:** có thể cố định camera và ánh sáng nhờ trạm thu ngân cố định vị trí.
- **Yêu cầu độ trễ:** cần phản hồi nhanh ngay tại thời điểm thanh toán — không thể chạy offline như một đợt kiểm kê.

Chính những đặc thù này là lý do hệ thống dùng detection class-agnostic (không giả định hướng/mặt cố định), dual-resolution cropping (giữ độ phân giải cao cho OCR/mã vạch dù ảnh có nhiễu nền), và evidence fusion đa phương thức (để phân biệt các sản phẩm khi chỉ nhìn thấy mặt sau/mặt bên, nơi retrieval thuần hình ảnh dễ nhầm lẫn).

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
- **Web POS (`backend/app/` + `frontend/`):** thu ngân chụp rổ hàng bằng điện thoại, hệ thống lập hoá đơn, đánh dấu dòng cần xác nhận, thanh toán; trang quản trị có báo cáo, đơn hàng, nhân viên, sửa giá / barcode / bằng chứng nhận diện, thiết lập nâng cao của pipeline và chạy kiểm định. API FastAPI + Postgres, giao diện React. Xem [`docs/WEB.md`](docs/WEB.md).

## 5. Cấu trúc dự án

```
stocktaking_ai/
├── README.md
├── Makefile                     # Mọi lệnh hằng ngày (make help)
├── docker-compose.yml           # postgres + migrate + api + web; .gpu.yml / .prod.yml là lớp phủ
├── .env.example                 # Mẫu cấu hình; `make setup` tạo .env và sinh bí mật
├── backend/
│   ├── pyproject.toml · uv.lock # uv, Python 3.12
│   ├── entrypoints/             # api.py + lệnh vận hành (reset_password, seed_demo, import_legacy_sqlite, …)
│   ├── app/                     # API web: core/ + modules/ (auth, orders, captures, catalog, …)
│   ├── engine/                  # Pipeline AI (không import app/)
│   │   ├── catalog/  core/  decision/  detection/  inference/
│   │   ├── models/   pipeline/  plugins/  retrieval/
│   │   └── segmentation/  storage/  validation/
│   ├── migrations/              # Alembic
│   ├── configs/                 # config.yaml (thật), config.demo.yaml (mock CPU, sinh tự động)
│   ├── scripts/                 # Cổng kiểm định, manifest, snapshot, đổi thiết bị, smoke API
│   ├── tests/                   # test engine + tests/api/
│   ├── debug/ · notebooks/      # Công cụ nghiên cứu pipeline
│   ├── data/                    # Dữ liệu thật (không commit): gallery, benchmark, cache FAISS, baseline
│   ├── data_demo/               # Dữ liệu tổng hợp cho demo CPU (không commit)
│   └── weights/                 # detector / refinement / retriever (không commit)
├── frontend/                    # Vite + React + TypeScript (pnpm)
├── docs/                        # Đặc tả 01–04, system-architecture, WEB, DEMO
├── plans/                       # Kế hoạch refactor theo pha
└── src_legacy/                  # Web POS v1, đóng băng
```

## 6. Yêu cầu hệ thống

- **Chạy web POS:** Docker Desktop (có `docker compose`), [`uv`](https://docs.astral.sh/uv/), `make`, `openssl`. Windows: chạy `make` trong Git Bash. `pnpm` chỉ cần cho test / kiểm kiểu frontend trên máy.
- **Python:** `>= 3.11, < 3.13` (uv tự cài; giới hạn trên do torch, sam2, faiss-cpu).
- **Thư viện:** khai báo trong `backend/pyproject.toml`. Bản nhẹ (`uv sync`) đủ cho API, test và pipeline với backend mock; bản đầy đủ (`uv sync --extra ml`) thêm `torch`, `rfdetr`, `sam2`, `transformers`, `easyocr`.
- **Hệ thống:** `pyzbar` cần thư viện zbar (`apt install libzbar0`, `brew install zbar`); image Docker đã có sẵn.
- **Phần cứng cho pipeline thật:** GPU NVIDIA với >= 8GB VRAM (khuyến nghị). Chạy CPU được nhưng chậm.

## 7. Cài đặt & thiết lập môi trường

### Web POS (khuyến nghị)

```bash
git clone https://github.com/longpham205/stocktaking_ai
cd stocktaking_ai

make setup        # tạo .env, sinh bí mật, kiểm docker + uv
make docker-up    # postgres, migration, api, web -> http://localhost:5173
make seed-demo    # catalog demo + giá demo (cần backend/data_demo/, xem docs/WEB.md mục 3)
make reset-password USER_NAME=admin ROLE=admin   # tạo tài khoản đầu tiên, mật khẩu in ra một lần
```

Mặc định API chạy với bộ nhận diện giả (`RECOGNIZER=fake`, không cần model). Các chế độ khác, tài khoản, điện thoại và lệnh vận hành: [`docs/WEB.md`](docs/WEB.md).

### Chỉ pipeline (không web)

```bash
cd backend
uv sync                 # bản nhẹ: backend mock trên CPU
uv sync --extra ml      # pipeline thật
uv run python scripts/set_device.py show|cpu|cuda   # đổi các khoá device: trong configs/config.yaml
```

### Model checkpoint

| Model | Đường dẫn |
| --- | --- |
| RF-DETR Detector | `weights/detector/checkpoint_best_ema.pth` |
| SAM2 Refinement | `weights/refinement/sam2/sam2.1_hiera_small.pt` |
| SigLIP2 Encoder | Tự tải từ Hugging Face (`google/siglip2-base-patch16-224`) |

> **[Kế hoạch]** Tiếp tục fine-tune RF-DETR khi có thêm dữ liệu ảnh bàn thu ngân thực tế để cải thiện độ chính xác trên các hướng đặt sản phẩm đa dạng.

## 8. Đặc tả Dataset & Metadata

- **Gallery (`backend/data/gallery/`):** ảnh tham chiếu theo từng SKU; thư mục ánh xạ tới `product_id` qua catalog DB. SKU mới: bỏ ảnh vào `data/gallery_inbox/<tên>/` rồi chạy `python -m engine.catalog.sync_gallery ...` (xem `docs/04_DATA_AND_CATALOG.md`). **[Kế hoạch]** mỗi SKU có ảnh nhiều mặt (trước, sau, hai bên) để khớp tốt hơn với việc sản phẩm có thể đặt ở hướng bất kỳ trên bàn thu ngân.
- **Chuẩn màu (bảng `color_reference` trong catalog DB):** màu canonical dạng RGB + hex cho từng biến thể (Reranker tự đổi sang Lab). File `product_colors.json` chỉ còn là đầu vào một lần của migrate.

```json
{
  "PK300": { "name": "PK300", "rgb": [109, 63, 62], "hex": "#6D3F3E" }
}
```

- **Catalog:** SKU, barcode, bằng chứng nhận diện (từ khoá OCR, mã màu, cặp dễ nhầm, plugin bắt buộc). Web POS giữ catalog trong Postgres (cùng database với đơn hàng) và pipeline đọc từ đó; chạy pipeline độc lập thì nguồn theo `catalog.source` của file config (`sqlite` → `data/db/app.db`, `snapshot`, hoặc `database`). Nạp lần đầu bằng `python -m engine.catalog.migrate ...` (demo: `make seed-demo`); giá do web quản lý (bảng `product_prices`). Chi tiết: `docs/04_DATA_AND_CATALOG.md`.
- **Benchmark (`backend/data/benchmark/`):** annotation dạng COCO, `category_id` ánh xạ đến `product_id` nội bộ.

## 9. Schema cấu hình

Tham số của pipeline nằm trong `backend/configs/config.yaml` (bảng dưới). Cấu hình của web (database, bí mật, chế độ nhận diện, giới hạn tải ảnh…) nằm trong `.env`, mô tả từng biến ở `.env.example`.

| Khối | Phạm vi |
| --- | --- |
| `catalog` | Nguồn catalog: `source` (`sqlite`/`snapshot`/`database`), `db_path`/`snapshot_path`/`db_url` — dữ liệu SKU nằm trong DB, không trong config |
| `detection` | Backend, confidence, IoU, tham số detector |
| `refinement` | Điều kiện gọi SAM2, giới hạn hình học |
| `cropping` | Padding box, độ phân giải tensor |
| `retrieval` | Kích thước vector, tham số FAISS, Top-K |
| `decision` | Ngưỡng accept / uncertain / reject |
| `plugins` | Cấu hình OCR, Color, Barcode (plugin bắt buộc theo SKU nằm trong catalog: `force_evidence`) |
| `rerank` | Trọng số hợp nhất, ngưỡng ΔE, rule bảo vệ |
| `storage` | Định dạng output, kiểu annotation |
| `validation` | IoU đánh giá, bật/tắt stage, xuất báo cáo |

## 10. Hướng dẫn thực thi

Web POS: `make docker-up` rồi mở `http://localhost:5173` — chi tiết ở [`docs/WEB.md`](docs/WEB.md), các bước cho buổi demo ở [`docs/DEMO.md`](docs/DEMO.md).

Pipeline chạy độc lập, từ thư mục `backend/`:

```bash
# 1. Inference trên một ảnh bàn thu ngân
uv run python -m engine --mode infer --image data/query/test_counter.jpg

# 2. Inference batch
uv run python -m engine --mode infer --image-dir data/query/

# 3. Validation trên benchmark COCO
uv run python -m engine --mode validate --benchmark-dir data/benchmark/

# 4. Bộ demo tổng hợp (backend mock, CPU)
uv run python -m engine --mode validate --config configs/config.demo.yaml --benchmark-dir data_demo/benchmark
```

Dùng như thư viện Python (từ `backend/`):

```python
from engine.core.config import load_config
from engine.pipeline.pipeline import InventoryPipeline

config = load_config()
pipeline = InventoryPipeline(config)

result = pipeline.run(image_data)                     # suy luận chuẩn
result, trace = pipeline.run_with_trace(image_data)   # chẩn đoán đầy đủ
```

## 11. Đặc tả Input & Output

| Chế độ | Input | Artifacts (`backend/data/outputs/`) |
| --- | --- | --- |
| Inference | Ảnh / thư mục ảnh | `result.json` (audit log), `result.csv` (số lượng theo SKU), `result.jpg` (ảnh annotation) |
| Validation | Thư mục benchmark COCO | `report.json/csv`, `records.csv`, ảnh & biểu đồ chẩn đoán |
| Web POS | Ảnh chụp từ điện thoại / tải lên | Hoá đơn (dòng sản phẩm, số lượng, giá), ảnh kèm khung nhận diện; đơn lưu trong Postgres, ảnh trong `MEDIA_DIR` |

## 12. Đánh giá hiệu năng

Đo trên bộ test hiện có: 8 SKU cốt lõi, 31 cảnh, 293 instance (`python -m engine --mode validate`). Số đo ngày 2026-10-03, config hiện tại (ngưỡng detector 0.50, catalog trong DB, Reranker chỉ dùng bằng chứng khai báo). Baseline cổng kiểm chứng: `backend/data/baseline/report.json`.

| Giai đoạn | Metric | Giá trị |
| --- | --- | --- |
| Detection (RF-DETR FT) | Precision / Recall / F1 (class-agnostic, IoU ≥ 0.3) | 0.952 / 0.956 / 0.954 |
| Visual Retrieval (SigLIP2) | Top-1 / Top-5 | 0.721 / 0.996 |
| Decision Engine | Pre-fusion accuracy | 0.700 |
| Evidence Fusion | Accuracy delta | +0.231 |
| Post-fusion | Accuracy | 0.931 |
| Product Counting | Count accuracy | 0.548 |
| End-to-End | Precision / Recall / F1 | 0.891 / 0.894 / 0.893 |

Số cũ trong các phiên bản trước của tài liệu (F1 0.939, post-fusion 0.936, count accuracy 0.871, mAP@50 0.990) đo ở một trạng thái weights/dữ liệu/code cũ hơn và **không tái hiện được** trên dữ liệu hiện tại; mAP@50 không được `validate` tính lại.

**[Kế hoạch]** Sau khi mở rộng bộ dữ liệu ảnh bàn thu ngân (đa dạng hướng đặt, điều kiện ánh sáng), đo lại toàn bộ metric trên, kèm hai chỉ số mới: độ trễ end-to-end trên mỗi ảnh và tỷ lệ ca bị gắn cờ `ambiguous` cần thu ngân xác nhận.

## 13. Hạn chế kỹ thuật

- Lỗi nhận diện fine-grained tập trung ở các biến thể gần như giống hệt nhau (ví dụ khác khối lượng tịnh); các ca này kích hoạt cờ `ambiguous`.
- Retrieval hiện chưa xử lý tốt sản phẩm nằm ở hướng bất kỳ hoặc chỉ thấy mặt sau/mặt bên **[cần cải tiến]**.
- Detector chưa được huấn luyện để bỏ qua tay, túi và hóa đơn **[cần cải tiến]**.
- Chuẩn màu tham chiếu đang cấu hình thủ công.
- OCR mới tối ưu cho chữ Latin và chữ số (`[en]`).
- Chạy CPU độ trễ cao hơn GPU.

## 14. Lộ trình phát triển

- Thu thập và gán nhãn thêm bộ ảnh bàn thu ngân đa dạng hướng đặt/điều kiện ánh sáng, fine-tune lại RF-DETR.
- Gallery nhiều mặt (trước/sau/bên) và truy xuất bất biến hướng (augmentation xoay khi lập chỉ mục, hoặc truy vấn với nhiều bản xoay của crop).
- Vùng quan tâm (ROI) và trừ nền cho camera cố định để loại nhiễu mặt bàn.
- Lọc đối tượng không phải sản phẩm (tay, túi, hóa đơn).
- Tự động trích xuất màu tham chiếu từ gallery bằng K-Means trong CIELAB.
- Tinh chỉnh heuristic kích hoạt SAM2 cho sản phẩm xếp chồng.
- Mở rộng OCR đa ngôn ngữ.

## 15. Trích dẫn

Codebase nghiên cứu và phát triển nội bộ, chưa gắn với công bố học thuật bên ngoài.

## 16. Giấy phép

Phần mềm nội bộ độc quyền. Cần tham khảo điều khoản cấp phép của tổ chức trước khi phân phối ra bên ngoài.
