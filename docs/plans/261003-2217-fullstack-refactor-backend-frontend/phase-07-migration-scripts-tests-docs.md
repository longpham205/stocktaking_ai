# Phase 07 — Data migration, scripts, tests, docs, cleanup

**Priority:** P1 · **Status:** done (7a dữ liệu + lệnh vận hành, 7b tài liệu, 7c rà soát cuối: 2026-10-04); còn các mục "chưa kiểm" ghi ở cuối file

## Các bước
1. `entrypoints/import_legacy_sqlite.py --sqlite data_demo/db/app.db`: copy users (giữ hash scrypt), orders/items/captures, prices, settings, overrides, change_log, catalog → Postgres; `make import-legacy DATA_DIR=data_demo`.
2. Scripts: `db_snapshot.py` → `make db-save/db-restore NAME=` (pg_dump/pg_restore, giữ kịch bản "demo_clean" của DEMO.md); `reset_password.py` → entrypoint dùng auth repository; `check_env.py` kiểm Postgres/weights/index/GPU thay vì sqlite + cổng.
3. E2E smoke thay `frontend_smoke.js`: Playwright (tuỳ chọn) hoặc script pytest gọi API + vitest UI. Đề xuất: Playwright 1 kịch bản happy-path chạy với `RECOGNIZER=fake`.
4. Docs: viết lại `README.md` (Quick start bằng make), `docs/WEB.md`, `docs/DEMO.md` (ngày demo với docker), `docs/03_DEVELOPMENT_RULES.md` (luật layer/import mới), thêm `docs/system-architecture.md`.
5. Code review (`code-reviewer`), xoá thứ thừa; giữ `src_legacy/` đến khi parity được xác nhận rồi mới quyết định xoá (git history vẫn còn).

## Done khi
Từ máy sạch: `make setup && make docker-up && make seed-demo` → demo chạy đủ luồng; mọi test (backend, engine, frontend) xanh.

## Chia PR (người dùng duyệt 2026-10-04)
| PR | Nội dung | Trạng thái |
|---|---|---|
| 7a | `seed-demo`, `import-legacy`, `db-save`/`db-restore`, `purge-media`, `check-env` | **done** (2026-10-04) |
| 7b | README, WEB.md, DEMO.md, luật phát triển, system-architecture, sơ đồ mới | **done** (2026-10-04) |
| 7c | tự review, dọn thừa, chạy mọi cổng, thử từ DB mới tinh | **done** (2026-10-04) |
Người dùng chốt: KHÔNG thêm Playwright (bước 3 của kế hoạch gốc): đã có `make smoke` (API qua HTTP thật) và test vitest cho UI.

## Kết quả 7a
- `make seed-demo` (`entrypoints/seed_demo.py`): catalog demo qua lệnh migrate của engine + giá từ `data_demo/seed/product_prices.json`; chạy lại không chèn trùng, không ghi đè giá đã có.
- `make import-legacy DATA_DIR=… [REPLACE=1]` (`entrypoints/import_legacy_sqlite.py`): mở SQLite chỉ đọc; một giao dịch; từ chối khi đích đã có dữ liệu trừ khi `--replace` (xoá bảng web + catalog trước); `--database-url` để nhập vào database khác. Chuyển đổi: thời gian text -> timestamptz, JSON text -> JSONB, `captures.status` -> `job_status` + `job_error`, `detections_json {w,h,boxes}` -> `detections` + `image_width/height`, đường dẫn ảnh -> tương đối `<đơn>/<file>`, ca còn mở -> đóng, `change_log.changed_by` trỏ tài khoản đã xoá -> null; nối lại sequence id. KHÔNG chép file ảnh (chép thư mục `transactions/` cũ vào `MEDIA_DIR`).
- `make db-save NAME=` / `make db-restore NAME=` (pg_dump/pg_restore trong container, file ở `backups/`, đã gitignore), `make purge-media DAYS= [DRY_RUN=1]` (`entrypoints/purge_media.py`), `make check-env` (`entrypoints/check_env.py`: bí mật — không in giá trị, DB + migration + số SKU/giá/tài khoản, pipeline, MEDIA_DIR, GPU).
- Test: `tests/api/test_data_ops.py` (3: import đầy đủ rồi đăng nhập bằng mật khẩu cũ và mở lại đơn cũ qua API; giá seed; dọn ảnh). `make lint`, `make type-check` (112 file) đạt; `make test` 303 passed, 25 skipped.
- Chạy thật trên Docker: `check-env` toàn OK; `seed-demo` lần hai chèn 0; `db-save NAME=dev_before_phase7` tạo file dump; `purge-media` chạy khô.
- Thử với `app.db` THẬT (người dùng cho phép; bản sao `backend/data/db/app.db`, mở chỉ đọc, mã băm trước/sau giống nhau) vào database riêng `stocktaking_import`: 22 SKU, 30 bằng chứng, 12 màu, 2 tài khoản, 6 ca, 4 đơn, 29 dòng hàng, 4 lượt chụp, 14 giá, 16 dòng nhật ký. Đối chiếu với nguồn: số dòng 12 bảng, tổng tiền đơn (920.000), tổng giá, tiền các ca, và phiên bản catalog do engine tính từ nội dung (`fda7b1206cc8f7fe`) đều KHỚP.

## Chưa làm (7a)
- `make db-restore` chưa chạy thử (thay toàn bộ DB dev; cần dừng `api` trước).
- Chưa mở web trên dữ liệu thật đã nhập (database `stocktaking_import` còn đó: trỏ `DATABASE_URL` sang nó để xem).

## Kết quả 7b (chỉ tài liệu, không đổi code)
- `README.md`: bỏ băng "đang refactor"; viết lại mục 5 (cấu trúc), 6 (yêu cầu), 7 (cài đặt bằng `make`), 10 (chạy pipeline bằng `python -m engine`); sửa các lệnh và đường dẫn ở mục 4, 8, 9, 11, 12. Mục 1–3 và 12–16 (thuật toán, số đo) giữ nguyên.
- `docs/WEB.md`, `docs/DEMO.md`: viết lại cho Docker + `make` (cổng 5173, không có mật khẩu mặc định, `db-save`/`db-restore` thay `db_snapshot.py`).
- `docs/system-architecture.md` (mới): sơ đồ HLD và sequence chụp → hoá đơn → thanh toán bằng Mermaid (GitHub tự vẽ), module và bảng, ranh giới với engine, dữ liệu, frontend, điểm khác web v1. Không đụng sơ đồ Excalidraw ở nhánh `docs/diagrams`.
- `docs/03_DEVELOPMENT_RULES.md`: luật 1–18 trỏ sang `backend/engine/`; luật 19 viết lại (ranh giới engine, lớp trong module, lỗi và ngôn ngữ, an toàn, dữ liệu, frontend, test, cổng kiểm, git).
- `docs/01`, `docs/02`, `docs/04`: sửa chỗ lỗi thời (cây thư mục, mục 14–15, catalog trong Postgres và `--db-url`, lệnh `python -m engine.catalog.*`).

## Khác kế hoạch (7b)
- Sơ đồ mới dùng Mermaid trong file Markdown thay vì file Excalidraw riêng: không cần công cụ ngoài, sửa cùng chỗ với chữ.
- Ngoài danh sách của kế hoạch, sửa thêm `docs/01`, `02`, `04` vì chúng còn dẫn tới `src/`, `run.py` và SQLite là nơi duy nhất của catalog.

## Điều tài liệu ghi rõ là chưa kiểm (7b)
- Sơ đồ Mermaid chưa xem bản vẽ trên GitHub.
- `make docker-up-gpu`, `make docker-up-prod` đủ luồng, `make db-restore`, tunnel HTTPS trên điện thoại thật (và việc Vite bản dev có nhận tên miền tunnel không).
- Quy trình sinh `data_demo/` từ máy sạch (`make_demo_dataset.py` → validate → `make seed-demo`) viết theo code, chưa chạy lại (không được chạy lại script sinh dữ liệu demo khi chưa hỏi).

## Kết quả 7c
- Rà soát: quét tên định nghĩa mà không nơi nào dùng (backend `app/`, `entrypoints/`; frontend `src/`), đối chiếu `.env.example` với các lớp settings, đọc lại các file nhạy cảm (`core/signed_url.py`, `core/uploads.py`, `auth/` tokens + limiter + service + deps, `captures/` service + router, `recognition/worker.py`, `orders/service.py`, `validation/service.py`, `engine_config/service.py`).
- Lỗi đã sửa: `signed_url.verify` so chuỗi bằng `hmac.compare_digest` trên `str`; chữ ký có ký tự ngoài ASCII trong query làm `GET /api/media/...` và `GET /api/gallery/...` (hai route công khai) trả **500** thay vì 403. Giờ so trên bytes; thêm một dòng kiểm trong `test_signed_url_round_trip_expiry_and_tampering`. Kiểm trên API đang chạy: 500 -> 403.
- Dọn thừa: xoá `frontend/src/components/ui/skeleton.tsx` (không ai import), bốn thành phần con không dùng của `card.tsx`, `getHealth` + kiểu `Health`; bỏ chữ "phase ..." còn sót trong comment (`audit/schemas.py`, `pyproject.toml`); thêm `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` vào `.env.example` (settings có, mẫu thiếu).
- Cổng sau khi sửa: `make lint`, `make type-check` (mypy 112 file + tsc) đạt; `make test` 303 passed / 25 skipped; `make test-web` 48 passed; `pnpm build` đạt. Engine không bị đụng: test engine 233 passed và G-demo `--exact` 0 khác biệt (chạy trên cùng code engine, trước khi sửa web).
- Từ database trống (người dùng chọn: database riêng `stocktaking_fresh` trên Postgres dev, không xoá volume): `make seed-demo` (tự chạy `make migrate`: tới 0003; 50 SKU, 66 bằng chứng, 4 màu, 42 giá), chạy lần hai chèn 0; tạo `fresh_admin`/`fresh_staff` + mật khẩu nâng cao bằng `entrypoints.reset_password`; `make check-env` không FAIL; API tạm ở cổng 8001 (`RECOGNIZER=fake`, `config.demo.yaml`); `make smoke`: **SMOKE PASSED** (18 bước), log API không có lỗi. Database và thư mục ảnh tạm đã xoá sau khi thử.

## Ghi nhận khi rà soát, chưa sửa (hành vi giống web v1, cần quyết định)
- Lượt chụp đang chờ lúc admin áp dụng thiết lập nâng cao hoặc bắt đầu kiểm định sẽ đợi sau việc đó trên cùng luồng; thời gian đợi tính vào `RECOGNITION_TIMEOUT_SECONDS` (60 s) nên có thể bị báo `PIPELINE_TIMEOUT`.
- Giới hạn đăng nhập sai tính theo (tài khoản, IP). Sau proxy (`web`, tunnel) mọi request có cùng IP, nên thực tế là khoá theo tài khoản: người ngoài gõ sai đủ số lần có thể khoá tạm một tài khoản.
- `MEDIA_URL_SECRET` trống chỉ lộ ra khi ký URL ảnh đầu tiên (500), không dừng lúc khởi động như `JWT_SECRET`. `make setup` và `make check-env` đã che trường hợp này.
- `POST /api/admin/evidence-test`: lỗi pipeline ngoài timeout trả 500 mặc định, không theo dạng `{"detail", "code"}`.
- Ảnh đã lưu của một lượt chụp bị từ chối vì hàng đợi vừa đầy không được xoá (chờ `make purge-media`).

## Chưa kiểm (cả Phase 7) — vấn đề đã biết
- Job `migrate` của compose trên volume Postgres mới tinh (`docker compose down -v` rồi `make docker-up`): không làm vì sẽ xoá DB dev.
- Vấn đề đã biết: `make docker-up-prod` chưa chạy đủ luồng. Đã giải quyết 2026-10-08: `make db-restore` (thử với catalog phát hành trong một container Postgres dùng tạm: chạy được, cả lên database trống lẫn database đã có); `make docker-up-gpu` (sửa Dockerfile; build, smoke và validate trong container đạt, F1 0,9382). G thật trên GPU (engine không đổi trong Phase 7). POS trên điện thoại thật qua HTTPS: đã thử 2026-10-08 (tunnel Cloudflare, 4G).

## Sau 7c: gỡ `src_legacy/` (người dùng quyết 2026-10-04)
- Xoá cả thư mục (37 file: web v1, launcher, giao diện Tkinter). Bản gốc còn ở commit `f30710d` và ở thư mục làm việc cũ `stocktaking_ai_mini`. Các chỗ dẫn tới `src_legacy/` trong README, docs, comment test và engine đổi sang dẫn commit đó. Các file kế hoạch pha 0–6 giữ nguyên chữ (ghi lại lịch sử).
