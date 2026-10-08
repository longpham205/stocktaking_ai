# Stocktaking AI — Nhận diện & đếm sản phẩm tại quầy thanh toán

> Hệ thống AI nhận diện và đếm từng sản phẩm đặt trên **bàn thu ngân** từ một ảnh chụp duy nhất — kết hợp truy xuất hình ảnh với bằng chứng từ OCR, màu sắc và mã vạch để phân biệt các biến thể sản phẩm có bao bì gần như giống hệt nhau.

> **Kiến trúc:** `backend/` (FastAPI + Postgres + engine nhận diện ở `backend/engine/`) và `frontend/` (Vite + React), chạy bằng `make` và Docker Compose. Web POS thế hệ đầu (stdlib `http.server` + SQLite) không còn trong cây thư mục; xem lại ở commit `f30710d` (`git worktree add ../stocktaking-legacy f30710d`).

> **Hướng dẫn nhanh:** dev chạy thử đầu-cuối, tài khoản và mật khẩu — [`docs/instruct_dev.md`](docs/instruct_dev.md) · chạy web POS — [`docs/WEB.md`](docs/WEB.md) · kiến trúc và sơ đồ — [`docs/system-architecture.md`](docs/system-architecture.md) · ngày demo — [`docs/DEMO.md`](docs/DEMO.md) · dữ liệu và catalog — [`docs/04_DATA_AND_CATALOG.md`](docs/04_DATA_AND_CATALOG.md) · luật phát triển — [`docs/03_DEVELOPMENT_RULES.md`](docs/03_DEVELOPMENT_RULES.md).

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
13. [Hạn chế & hướng khắc phục](#13-hạn-chế--hướng-khắc-phục)
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

### Điểm nổi bật

- **F1 end-to-end ≈ 0,92** trên 58 ảnh rổ hàng thật với 483 sản phẩm: **0,938** trên bộ 8 SKU và **0,885** trên bộ 13 SKU có bao bì gần như giống hệt nhau (chi tiết ở [mục 12](#12-đánh-giá-hiệu-năng)).
- **Định vị chính xác:** detection F1 **0,94–0,97**; SKU đúng nằm trong Top-5 ứng viên ở **97–99,6%** trường hợp.
- **Nhanh trên phần cứng phổ thông:** **~7–9 giây cho cả rổ** 5–13 món trên GPU laptop 4 GB (RTX 3050 Ti).
- **Thêm sản phẩm không cần huấn luyện lại:** 33 SKU hiện tại; thêm SKU chỉ cần chụp ảnh gallery và lập lại chỉ mục.
- **Phân biệt biến thể gần giống nhau** bằng cách kết hợp hình ảnh, chữ trên bao bì (OCR) và chữ ký màu học từ ảnh gallery.
- **Biết lúc mình không chắc:** với các cặp sản phẩm dễ nhầm, hệ thống tự gắn cờ **"cần xác nhận"** để thu ngân kiểm lại thay vì tính tiền sai (trên bộ test: 7 lần hỏi thì 5 lần đúng là ca cần sửa).
- **Sẵn sàng vận hành:** web POS chạy trên điện thoại, tự kiểm tính nhất quán của chỉ mục khi khởi động, nạp sẵn model để lần chụp đầu tiên không bị chậm.

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
    — Lọc khung trùng và khung ôm cả cụm hàng
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
    — Top-K sản phẩm gần nhất trong gallery (gallery tăng cường bằng ảnh xoay)
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
    — Gộp vật lồng nhau cùng SKU, gắn cờ "cần xác nhận" cho cặp dễ nhầm
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
  - *OCR:* quét đa hướng (0°, 90°, 180°, 270°), CLAHE, đối chiếu từ khoá khai báo trong catalog; độ tin cậy lấy theo đoạn chữ chứa từ khoá.
  - *Màu sắc:* **chữ ký màu học tự động từ ảnh gallery** (chế độ `signature`), so với phân bố màu của crop; chế độ cũ CIELAB/CIEDE2000 vẫn giữ.
  - *Consensus & Guard:* bảo vệ kết quả retrieval tin cậy khỏi nhiễu plugin, kiểm tra chặt các cặp sản phẩm dễ nhầm.
- **Gallery tăng cường:** SKU ít ảnh được thêm bản xoay 90°/180°/270° khi lập chỉ mục, bù cho việc hàng nằm đủ hướng trên bàn; có trần số vector để không SKU nào lấn át.
- **Hậu xử lý khung:** bỏ khung trùng, khung ôm cả cụm/túi hàng, và vật bị tách đôi (ví dụ tuýp kem gắn trên vỉ giấy) để không tính tiền hai lần.
- **"Cần xác nhận" cho cặp dễ nhầm:** SKU được đánh dấu `confirm_if_unsure` trong catalog, khi không có bằng chứng phân định, được gắn cờ để thu ngân chọn lại.
- **An toàn khi vận hành:** server tự kiểm chỉ mục FAISS có khớp gallery, catalog và cấu hình không (lệch thì dừng kèm hướng dẫn), và chạy nóng pipeline lúc khởi động.
- **Công cụ dữ liệu:** gán nhãn benchmark có máy đề xuất (`label_benchmark.py`), tách SKU gộp nhầm (`split_sku.py`), đo thời gian từng bước (`bench_stages.py`), chia ảnh demo theo kết quả (`sort_demo_images.py`).
- **Bộ validation 9 giai đoạn:** đánh giá từng giai đoạn, từ detection đến phân loại SKU end-to-end.
- **Web POS (`backend/app/` + `frontend/`):** thu ngân chụp rổ hàng bằng điện thoại, hệ thống lập hoá đơn, đánh dấu dòng cần xác nhận, thanh toán; trang quản trị có báo cáo, đơn hàng, nhân viên, sửa giá / barcode / bằng chứng nhận diện, thiết lập nâng cao của pipeline và chạy kiểm định. API FastAPI + Postgres, giao diện React. Xem [`docs/WEB.md`](docs/WEB.md).

## 5. Cấu trúc dự án

```
stocktaking_ai/
├── README.md
├── Makefile                     # Mọi lệnh hằng ngày (make help)
├── scripts/                     # Chạy bằng một lệnh: run_real.bat · run_real_phone.bat (nhận diện thật
│                                #   trên GPU), run_real_tunnel.bat (địa chỉ https công khai cho điện thoại
│                                #   ở mạng khác), run_e2e.bat (dịch vụ + dữ liệu demo + smoke), kèm bản .sh
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
└── docs/                        # Đặc tả 01–04, system-architecture, WEB, DEMO; plans/ = kế hoạch refactor
```

## 6. Yêu cầu hệ thống

- **Chạy web POS:** Docker Desktop (có `docker compose`), [`uv`](https://docs.astral.sh/uv/), `make`, `openssl`. Windows: chạy `make` trong Git Bash. `pnpm` chỉ cần cho test / kiểm kiểu frontend trên máy.
- **Python:** `>= 3.11, < 3.13` (uv tự cài; giới hạn trên do torch, sam2, faiss-cpu).
- **Thư viện:** khai báo trong `backend/pyproject.toml`. Bản nhẹ (`uv sync`) đủ cho API, test và pipeline với backend mock; bản đầy đủ (`uv sync --extra ml`) thêm `torch`, `rfdetr`, `sam2`, `transformers`, `easyocr`.
- **Hệ thống:** `pyzbar` cần thư viện zbar (`apt install libzbar0`, `brew install zbar`); image Docker đã có sẵn.
- **Phần cứng cho pipeline thật:** GPU NVIDIA; đã chạy ổn định trên GPU laptop **4 GB** (RTX 3050 Ti, ~7–9 giây/ảnh). Chạy CPU được nhưng chậm.

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

Làm tất cả các bước trên và chạy kiểm đầu-cuối bằng một lệnh: `./scripts/run_e2e.sh` (Windows: nhấp đúp `scripts\run_e2e.bat`).

Mặc định API chạy với bộ nhận diện giả (`RECOGNIZER=fake`, không cần model). Các chế độ khác, tài khoản, điện thoại và lệnh vận hành: [`docs/WEB.md`](docs/WEB.md).

### Chỉ pipeline (không web)

```bash
cd backend
uv sync                 # bản nhẹ: backend mock trên CPU
uv sync --extra ml      # pipeline thật
uv run python scripts/set_device.py show|cpu|cuda   # đổi các khoá device: trong configs/config.yaml
```

### Weights và dữ liệu

Weights đã fine-tune và toàn bộ dữ liệu được phát hành trên **GitHub Releases** (tag `assets-v1`): gallery 33 SKU, hai bộ benchmark đã gán nhãn, catalog, chỉ mục FAISS + chữ ký màu dựng sẵn, mốc kiểm định và bộ ảnh demo. Một lệnh tải, kiểm SHA-256 từng file và giải nén vào `backend/`:

```bash
make assets                                   # hoặc: cd backend && python scripts/fetch_assets.py
python scripts/fetch_assets.py --from-dir D:/tai_ve   # đã tải tay các file zip
```

Web POS dùng catalog trong Postgres: `make db-restore NAME=stocktaking_catalog` (catalog + giá, không chứa tài khoản hay đơn hàng), rồi `make migrate` và `make reset-password USER_NAME=admin ROLE=admin`.

| File phát hành | Nội dung | Dung lượng |
| --- | --- | --- |
| `stocktaking_weights_detector_sam2.zip` | RF-DETR fine-tune + RF-DETR base, SAM2.1 hiera-small | 653 MB |
| `stocktaking_weights_siglip2.zip` | SigLIP2 base patch16-224 | 1,4 GB |
| `stocktaking_data.zip` | gallery, benchmark (31 + 27 ảnh), catalog SQLite, cache FAISS, baseline, demo_sets | 610 MB |
| `stocktaking_catalog.zip` | catalog + giá cho Postgres của web (giải nén thành `backups/stocktaking_catalog.dump`) | 8 KB |

| Model | Đường dẫn | Nguồn · giấy phép |
| --- | --- | --- |
| RF-DETR Detector (fine-tune cho bàn thu ngân) | `weights/detector/checkpoint_best_ema.pth` | Dự án này (khởi tạo từ RF-DETR base của Roboflow, Apache-2.0) |
| SAM2 Refinement | `weights/refinement/sam2/sam2.1_hiera_small.pt` | Meta AI · Apache-2.0 |
| SigLIP2 Encoder | `weights/retriever/siglip2/` | Google (`google/siglip2-base-patch16-224`) · Apache-2.0 |

Phát hành bản mới (người bảo trì): làm sạch catalog SQLite (bỏ tài khoản, đơn hàng), rồi `python scripts/pack_assets.py --db <app.db sạch> --out <thư mục ngoài repo> --catalog-dump <dump catalog>`; script ghi các file zip, `SHA256SUMS.txt` và `configs/assets_manifest.json` (commit file này).


## 8. Đặc tả Dataset & Metadata

- **Gallery (`backend/data/gallery/`):** ảnh tham chiếu theo từng SKU; thư mục ánh xạ tới `product_id` qua catalog DB. SKU mới: bỏ ảnh vào `data/gallery_inbox/<tên>/` rồi chạy `python -m engine.catalog.sync_gallery ...` (xem `docs/04_DATA_AND_CATALOG.md`). Khi lập chỉ mục, SKU có ít ảnh được tự thêm bản xoay (`retrieval.augment`). Sau khi thêm hoặc đổi ảnh: lập lại chỉ mục và chữ ký màu (`scripts/build_color_signatures.py`); server tự phát hiện chỉ mục cũ và từ chối chạy.
- **Màu:** chữ ký màu của từng SKU học tự động từ ảnh gallery (`data/cache/color_signatures.npz`), dùng cho SKU có `color_code`. Bảng `color_reference` trong catalog DB giữ màu chuẩn RGB + hex cho chế độ cũ. File `product_colors.json` chỉ còn là đầu vào một lần của migrate.

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
| `detection` | Backend, confidence, IoU, tham số detector; `suppression` (khung trùng, khung ôm cụm, vật lồng nhau cùng SKU) |
| `refinement` | Điều kiện gọi SAM2, giới hạn hình học |
| `cropping` | Padding box, độ phân giải tensor |
| `retrieval` | Kích thước vector, tham số FAISS, Top-K; `augment` (xoay ảnh gallery cho SKU ít ảnh, trần vector mỗi SKU) |
| `decision` | Ngưỡng accept / uncertain / reject |
| `plugins` | Cấu hình OCR, Color, Barcode (plugin bắt buộc theo SKU nằm trong catalog: `force_evidence`) |
| `rerank` | Trọng số hợp nhất, ngưỡng ΔE, rule bảo vệ, cặp dễ nhầm và "cần xác nhận" |
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

Đo bằng `python -m engine --mode validate` trên hai bộ ảnh rổ hàng chụp thật, cấu hình hiện tại (`backend/configs/config.yaml`), GPU RTX 3050 Ti Laptop 4 GB.

| Bộ test | Ảnh | Sản phẩm | SKU |
| --- | --- | --- | --- |
| Bộ chuẩn | 31 | 294 | 8 |
| Bộ mở rộng (bao bì gần giống nhau: Cléo, Simple, Hatomugi, Nivea, Skin Aqua…) | 27 | 189 | 13 |

| Giai đoạn | Metric | Bộ chuẩn | Bộ mở rộng |
| --- | --- | --- | --- |
| Detection (RF-DETR FT, class-agnostic) | Precision / Recall / F1 | 0,966 / 0,966 / 0,966 | 0,902 / 0,979 / 0,939 |
| Detection | Mean IoU | 0,910 | 0,966 |
| Visual Retrieval (SigLIP2) | Top-1 / Top-5 | 0,722 / 0,997 | 0,784 / 0,968 |
| Evidence Fusion (OCR + màu) | Độ chính xác trước → sau | 0,699 → **0,962** | 0,753 → **0,895** |
| **End-to-End** | **Precision / Recall / F1** | **0,948 / 0,929 / 0,938** | **0,876 / 0,894 / 0,885** |
| Độ trễ | Giây / ảnh (cả rổ) | 8,5 | 7,1 |

Gộp hai bộ: **F1 ≈ 0,92** trên 483 sản phẩm. Evidence fusion sửa đúng 95 ca mà retrieval xếp sai, chỉ làm sai 3 ca.

**Tiến trình tối ưu** (cùng bộ ảnh, cùng phần cứng):

| | Ban đầu | Hiện tại |
| --- | --- | --- |
| F1 bộ chuẩn | 0,893 | **0,938** |
| F1 bộ mở rộng | 0,609 | **0,885** |
| Thời gian mỗi ảnh | 23,2 s | **~7–9 s** |
| Bộ nhớ GPU giữ chỗ | 7 GB | **1,5 GB** |

Các bước chính: chia sẻ bước phát hiện chữ giữa các hướng OCR, lọc khung trùng, chữ ký màu học từ gallery, tăng cường gallery bằng ảnh xoay, độ tin cậy OCR theo đoạn chứa từ khoá, gộp vật lồng nhau cùng SKU, bỏ đọc mã vạch khi catalog chưa có mã.

## 13. Hạn chế & hướng khắc phục

- Biến thể bao bì gần như giống hệt nhau (khác màu nhạt, khác dòng chữ nhỏ) vẫn là ca khó nhất; hệ thống gắn cờ **"cần xác nhận"** cho các cặp này để thu ngân kiểm lại.
- Hàng trong túi nylon trong suốt, hoặc bao bì dài bị vật khác che một phần, đôi khi bị tách hoặc gộp khung; sẽ cải thiện khi huấn luyện thêm detector với ảnh bàn thu ngân thực tế.
- OCR tối ưu cho chữ Latin và chữ số (`[en]`).
- Chạy CPU có độ trễ cao hơn GPU.

## 14. Lộ trình phát triển

- Thu thập và gán nhãn thêm ảnh bàn thu ngân đa dạng hướng đặt và ánh sáng (đã có công cụ gán nhãn bán tự động), fine-tune lại RF-DETR.
- Gallery nhiều mặt (trước/sau/bên) chụp thật cho mọi SKU.
- Vùng quan tâm (ROI) và trừ nền cho camera cố định để loại nhiễu mặt bàn.
- Lọc đối tượng không phải sản phẩm (tay, túi, hóa đơn).
- Mở rộng OCR đa ngôn ngữ.

## 15. Trích dẫn

Codebase nghiên cứu và phát triển nội bộ, chưa gắn với công bố học thuật bên ngoài.

## 16. Giấy phép

Phần mềm nội bộ độc quyền. Cần tham khảo điều khoản cấp phép của tổ chức trước khi phân phối ra bên ngoài.
