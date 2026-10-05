# 04 — DỮ LIỆU & CATALOG

> Trạng thái: **đã triển khai**. Tài liệu này mô tả code trong `backend/engine/catalog/`. Mọi đường dẫn và lệnh dưới đây tính từ thư mục `backend/`.
>
> Catalog có hai nơi ở tuỳ cách chạy: **web POS** giữ catalog trong Postgres, chung database với đơn hàng (`catalog.source: database`, API tự truyền URL); **pipeline chạy độc lập** (`python -m engine`) đọc file SQLite `data/db/app.db` hoặc snapshot theo file config. Lược đồ và luật giống nhau ở cả hai.

## 1. Sở hữu dữ liệu

| Dữ liệu | Nơi lưu | Ai ghi | Ai đọc |
|---|---|---|---|
| Catalog SKU + bằng chứng nhận diện + màu tham chiếu | Bảng catalog trong Postgres (web) hoặc `<data-dir>/db/app.db` (SQLite, pipeline độc lập) | `migrate`, `sync_gallery`, trang quản trị web | Pipeline qua `CatalogRepository`; màn hình web qua `app/modules/catalog` |
| Giá, ca, đơn, lượt chụp, nhật ký thay đổi của web | Bảng web trong **cùng database** Postgres | `backend/app/` | `backend/app/` |
| Ảnh gallery | `data/gallery/<thư mục>/` | người dùng / `sync_gallery --inbox-dir` | `GalleryIndexBuilder` |
| Index FAISS + metadata vector + fingerprint | `data/cache/gallery_index.faiss`, `gallery_metadata.json`, `gallery_index.faiss.fingerprint.json` | `BuildPipeline` | `Retriever`, `checks` |
| Snapshot catalog (Colab/Kaggle) | `catalog_snapshot.json` (vị trí do `catalog.snapshot_path`) | `scripts/export_catalog_snapshot.py` | `JsonSnapshotCatalogRepository` |

`products.json` / `product_ids.json` / `product_colors.json` **không còn là nguồn của pipeline**; chúng chỉ là đầu vào một lần của `migrate`. Web cũng đọc catalog từ DB, không đọc các file này.

## 2. Schema (`engine/catalog/db.py`, SQLModel — định nghĩa một lần)

| Bảng | Cột chính | Ràng buộc |
|---|---|---|
| `product` | `product_id` (PK, chuỗi số), `product_name`, `brand`, `category`, `barcode`, `description`, `image_count`, `gallery_folder`, `is_active`, `needs_naming`, `created_at`, `updated_at` | `barcode` và `gallery_folder` duy nhất khi không rỗng (partial unique index) |
| `product_evidence` | `product_id` (FK), `evidence_type`, `value_json`, `updated_by`, `updated_at` | `UNIQUE(product_id, evidence_type)`; `evidence_type` là danh sách mở |
| `color_reference` | `color_code` (PK), `r`, `g`, `b`, `hex`, `source` (`seed`/`manual`/`gallery`) | `r,g,b ∈ [0,255]` — lưu RGB, **không** lưu Lab |
| `catalog_meta` | `key`, `value` | `schema_version`, `next_product_id`, `seed_completed_at` |

- `product_id` = COCO `category_id` của benchmark; **bất biến, không tái dùng, không lấp khoảng trống** (khoảng trống thật: 10, 11, 14, 16, 19, 20). ID mới cấp từ `next_product_id`.
- Xoá SKU = `is_active=false`. SKU mới chưa có tên thật: `needs_naming=true`.
- SQLite: `create_all()` idempotent, chỉ tạo bảng catalog; phiên bản schema lệch → lỗi rõ. Postgres: bảng do Alembic tạo (migration `0002`), không tạo lúc chạy.

### Loại bằng chứng (`evidence_type`)

| Loại | `value_json` | Dùng bởi |
|---|---|---|
| `force_evidence` | `["ocr","color","barcode"]` (tập con) | `DecisionEngine` (hợp trên toàn Top-K) |
| `confusable_with` | `["8"]` — **hai chiều** | `Reranker` (guard cặp dễ nhầm) |
| `ocr_keywords` | `["BE203","ABA"]` — chữ hoa, bỏ trùng, độ dài ≥ `plugins.ocr.min_text_length` | `Reranker` |
| `color_code` | `"BE203"` → tra `color_reference` | `Reranker` |
| `disabled_plugins` | backlog | — |

Barcode khớp chính xác `product.barcode`.

## 3. Quy tắc bằng chứng (nguyên tắc 7 — đã áp dụng từ C8)

- Reranker và plugin **chỉ** dùng dữ liệu khai báo trong catalog; **không suy diễn từ tên** sản phẩm hay tên thư mục.
- Thiếu khai báo = điểm 0 tường minh cho SKU đó (không fallback).
- Luật chặn khi lưu (`engine/catalog/validation.py`): `confusable_with` trỏ tới SKU tồn tại, không tự trỏ, phải hai chiều; `force_evidence` ⊆ {ocr, color, barcode}; `ocr_keywords` đã chuẩn hoá; **cặp dễ nhầm có bên bắt buộc OCR thì cả hai phải có từ khoá và không trùng token** (Reranker cho điểm 1.0 khi khớp bất kỳ token nào, token chung làm mất khả năng phân biệt).
- Cảnh báo (không chặn): `color_code` chưa có `color_reference` (hiện BR641, OR210).

## 4. `CatalogRepository` (`engine/catalog/repository.py`)

Giao diện chỉ đọc: `products()`, `get_product(id)`, `folder_to_product_id()` (chỉ SKU đang bán có thư mục), `evidence(id, type)`, `force_evidence(id)`, `confusable_pairs()`, `ocr_keywords(id)`, `color_code(id)`, `color_references()`, `version()`, `reload()`.

- Nguồn chọn bằng `catalog.source` (`engine/catalog/factory.py`): `sqlite` → `catalog.db_path`; `snapshot` → `catalog.snapshot_path`; `database` → `catalog.db_url` (URL SQLAlchemy, dùng cho Postgres). **Không tự chuyển nguồn khi lỗi.**
- Nạp một lần thành `CatalogData` bất biến; `reload()` đổi tham chiếu nguyên tử; `version()` là hash nội dung (không gồm thời gian).
- `InventoryPipeline` dựng **một** repository và truyền cho `Retriever`, `DecisionEngine`, `Reranker`; các module truy vấn lúc dùng, cache gắn `version()`.
- `JsonSnapshotCatalogRepository` không import SQLModel. Snapshot sửa tay (nội dung khác `version` ghi trong file) bị từ chối.
- `InMemoryCatalogRepository`: dùng cho test/công cụ.

## 5. Config

```yaml
catalog:
  source: "sqlite"          # sqlite | snapshot | database
  db_path: "data/db/app.db" # demo: data_demo/db/app.db
  # snapshot_path: "data/metadata/catalog_snapshot.json"
  # db_url: "postgresql+psycopg://..."   # với source: database
```

Web POS không cần sửa khối này: `LocalRecognizer` gọi `build_config(..., catalog_db_url=<DATABASE_URL>)`, thay cả khối `catalog` bằng nguồn `database`.

Khoá cũ `catalog.id_mapping`, `catalog.build_metadata`, `catalog.products_filename`, `catalog.product_ids_filename`, `plugins.force_rules`, `rerank.confusable_pairs`, `rerank.color.references_path` đã gỡ: config còn chứa chúng sẽ **báo lỗi rõ**, không bị bỏ qua. Bản config trước khi gỡ lưu ở `configs/legacy/` (đầu vào của `migrate`).

## 6. Quy trình

### 6.1 Migrate một lần (idempotent, chỉ chèn, không ghi đè dòng đã có)
```
python -m engine.catalog.migrate --seed-dir data/metadata --legacy-config configs/legacy/config.legacy.yaml \
    --db data/db/app.db --gallery-dir data/gallery --benchmark-labels data/benchmark/_annotations.coco.json \
    --manual-evidence data/metadata/evidence_manual.json --expected-max-id 28 [--dry-run]
```
- Thẩm quyền ID khi nguồn lệch: `id_mapping` > `products.json` > `product_ids.json`; tên thư mục so khớp sau chuẩn hoá NFKC (dấu cách toàn góc U+3000); `gallery_folder` lưu đúng tên thật trên đĩa.
- Dừng khi: trùng ID/thư mục, hai nguồn JSON xung đột ngoài `id_mapping`, barcode trùng, vi phạm luật bằng chứng, `category_id` benchmark thiếu trong catalog.
- Bằng chứng: dữ liệu thật → tái hiện logic suy diễn cũ của Reranker (loại token chung, ví dụ `KEM`) rồi ghi đè bằng `--manual-evidence` (chữ in trên bao bì cho SKU không có token dùng được); demo → `data_demo/seed/expected_evidence.json` (`force_evidence`/`confusable_with` cũng lấy từ đây khi config không còn khoá cũ).
- Barcode admin đã sửa ở bảng web `product_overrides` được gộp vào `product.barcode`.
- Đích là Postgres: thay `--db <file>` bằng `--db-url postgresql+psycopg://...` (hai cờ loại trừ nhau).
- Demo vào Postgres của web: `make seed-demo` (gọi chính lệnh migrate này với `data_demo/seed` và `configs/legacy/config.demo.legacy.yaml`, rồi nạp giá demo). Demo vào SQLite: `python -m engine.catalog.migrate --seed-dir data_demo/seed --legacy-config configs/legacy/config.demo.legacy.yaml --db data_demo/db/app.db`.
- Chuyển cả database web v1 (catalog + đơn + tài khoản) sang Postgres: `make import-legacy DATA_DIR=...` (xem `docs/WEB.md` mục 8).

### 6.2 Thêm SKU mới
1. Bỏ ảnh vào `data/gallery_inbox/<tên bất kỳ>/`. Ảnh còn lẫn lộn trong một thư mục thì phân loại bằng tay với `python scripts/sort_gallery_images.py --source <thư mục ảnh>`: cửa sổ hiện từng ảnh, chọn hoặc tạo thư mục, ảnh được sao chép vào `data/gallery_inbox/<thư mục>/0001.jpg, 0002.jpg, ...` (ảnh gốc giữ nguyên; `--dest data/gallery` để thêm ảnh cho SKU đã có; làm dở chạy lại sẽ tiếp tục).
2. `python -m engine.catalog.sync_gallery --db data/db/app.db --gallery-dir data/gallery --inbox-dir data/gallery_inbox` (Postgres: `--db-url ...` thay cho `--db`) → cấp ID từ `next_product_id`, chuyển thành `data/gallery/<ID 4 chữ số>/`, tạo SKU `needs_naming=true` (DB ghi trước, rồi mới chuyển thư mục).
3. Chạy build (`python -m engine --mode validate` hoặc `infer`): `BuildPipeline` đồng bộ gallery vào catalog rồi build lại FAISS **chỉ khi fingerprint đổi**.

Thư mục xuất hiện thẳng trong `data/gallery/` mà chưa có SKU cũng được tạo SKU (giữ tên thư mục). Thư mục biến mất → cảnh báo, không xoá SKU. ID đã cấp không bao giờ cấp lại.

### 6.3 Fingerprint gallery (`engine/retrieval/fingerprint.py`)
Hash nội dung từng ảnh + ánh xạ thư mục→ID + backend/model/weights + `embedding_dim`. Lưu cạnh index; `retrieval.build_gallery_index: true` chỉ build khi fingerprint khác hoặc thiếu index.

### 6.4 Snapshot cho Colab
`python scripts/export_catalog_snapshot.py --db data/db/app.db --out data/metadata/catalog_snapshot.json` (đọc lại và so `version` trước khi báo thành công; script này đọc file SQLite), rồi đặt `catalog.source: snapshot` + `catalog.snapshot_path`.

### 6.5 Kiểm nhất quán (`engine/catalog/checks.py`)
Lỗi: `product_id` trong index không có trong catalog; số vector ≠ số dòng metadata; dim index ≠ `embedding_dim`; `category_id` benchmark thiếu; bằng chứng vi phạm luật. Cảnh báo: thiếu màu tham chiếu; SKU có thư mục nhưng chưa có vector.

## 7. Quy ước đặt tên thư mục gallery
SKU mới: thư mục = ID đệm 4 chữ số (`0029`) — ASCII, ổn định khi đổi tên hiển thị, khớp `category_id`. 22 thư mục cũ giữ tên hiện tại (đổi tên cần script có log + build lại FAISS; chưa làm).

## 8. Web
- Đọc cho màn hình web: `app/modules/catalog/repository.py` truy vấn các bảng `product`, `product_evidence`, `color_reference` trong Postgres (API không nạp engine để đọc).
- Ghi: `CatalogEdits` (cùng file) mở một phiên đồng bộ trong luồng phụ, gọi hàm của `engine.catalog.edits`, kiểm bằng `engine.catalog.validation`, ghi `change_log` trên **cùng kết nối** rồi commit một lần.
- Sau mỗi lần ghi catalog, `RecognitionWorker.reload_catalog()` nạp lại catalog cho pipeline giữa hai lượt chụp: thay đổi có hiệu lực ở lượt chụp sau, không cần khởi động lại.
- Barcode: một nguồn duy nhất `product.barcode`. Giá thuộc web (bảng `product_prices`).
- API quản trị: `PATCH /api/admin/products/{id}` (`price` thuộc web; `barcode`, `name` thuộc catalog — đặt tên tắt `needs_naming`), `GET|PATCH /api/admin/products/{id}/evidence` (`ocr_keywords`, `color_code`, `force_evidence`, `confusable_with`; bắt buộc `confirm: true`; lỗi -> 422 `EVIDENCE_INVALID` kèm danh sách), `GET /api/admin/colors`, `PATCH /api/admin/colors/{code}` (`hex` hoặc `null` để xoá; `confirm: true`), `GET /api/gallery/{product_id}/{index}` (ảnh gallery, URL có ký).
- Mọi thay đổi ghi `change_log` (`product`/`product_evidence`/`color_reference`) và hoàn tác được (`POST /api/admin/change-log/{id}/revert`, từ chối `CHANGE_STALE` nếu đã bị đổi sau đó; hoàn tác cặp dễ nhầm khôi phục cả hai chiều).
- Kiểm thử: `tests/api/test_catalog.py`, `test_catalog_writes.py`, `test_catalog_postgres.py`, `test_data_ops.py`.
