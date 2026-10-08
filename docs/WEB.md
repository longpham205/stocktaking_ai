# Web POS — hướng dẫn chạy

Ba dịch vụ chạy bằng Docker: `postgres`, `api` (FastAPI + bộ nhận diện) và `web` (giao diện React). Mọi lệnh là target của `Makefile` ở gốc repo. Kiến trúc: [`system-architecture.md`](system-architecture.md). Ngày demo: [`DEMO.md`](DEMO.md).

## 1. Cần có

- Docker Desktop (có `docker compose`), [`uv`](https://docs.astral.sh/uv/), `make`, `openssl`. Trên Windows chạy `make` trong **Git Bash** (công thức là shell POSIX).
- `pnpm` chỉ cần khi chạy test hoặc kiểm kiểu frontend trên máy (`make test-web`, `make type-check`); chạy web qua Docker thì không cần.
- Dữ liệu demo ở `backend/data_demo/` (không commit). Chưa có thì sinh một lần, xem mục 3.

## 2. Chạy lần đầu

```bash
make setup          # tạo .env từ .env.example, sinh JWT_SECRET và MEDIA_URL_SECRET, kiểm docker + uv
make docker-up      # build, bật postgres, chạy migration, bật api và web
make seed-demo      # catalog demo 50 SKU + giá demo vào database
make reset-password USER_NAME=admin ROLE=admin    # tạo tài khoản admin, mật khẩu in ra MỘT lần
make reset-password USER_NAME=staff ROLE=staff    # tạo tài khoản thu ngân
make reset-advanced-password                      # mật khẩu nâng cao (thiết lập pipeline, kiểm định)
make check-env      # bảng OK / WARN / FAIL của bản cài này
```

Mở `http://localhost:5173`. API: `http://localhost:8000/api/health` phải trả `{"status": "ready", ...}`; tài liệu API tự sinh ở `http://localhost:8000/docs` (chỉ khi `APP_ENV=dev`).

**Không có tài khoản hay mật khẩu mặc định.** Mật khẩu do `make reset-password` sinh ngẫu nhiên, in ra một lần và không lưu ở đâu khác; quên thì chạy lại lệnh đó (mọi ca đang mở của tài khoản bị đóng).

Dừng: `make docker-down` (dữ liệu trong volume `pg-data` được giữ). Xem log: `make logs S=api`.

## 3. Chế độ nhận diện

Hai biến trong `.env` (hoặc đặt trước lệnh) quyết định API nhận diện bằng gì:

| `RECOGNIZER` | `PIPELINE_CONFIG` | Ý nghĩa |
|---|---|---|
| `fake` (mặc định của compose) | bất kỳ | Không nạp model: trả kết quả giả từ catalog. Image nhẹ, chạy ở mọi máy. Dùng để thử giao diện. |
| `local` | `configs/config.demo.yaml` | Pipeline thật với backend mock trên CPU, dữ liệu `data_demo/`. |
| `local` | `configs/config.yaml` | Pipeline thật (RF-DETR, SAM2, SigLIP2), dữ liệu `data/`, cần `weights/` và GPU. |

- Catalog demo đi với `PIPELINE_CONFIG=configs/config.demo.yaml`: nếu để config mặc định thì ảnh gallery ở màn Sản phẩm không khớp. Ví dụ: `PIPELINE_CONFIG=configs/config.demo.yaml docker compose up -d --wait api`.
- **Nhận diện thật trên GPU của máy:** nhấp đúp `scripts\run_real.bat` (hoặc `./scripts/run_real.sh`): API chạy trên máy bằng môi trường Python có model, Postgres vẫn trong Docker. Chi tiết và số đo: [`instruct_dev.md`](instruct_dev.md) mục 6.
  Lúc khởi động, API kiểm chỉ mục gallery có khớp gallery, catalog và cấu hình không (lệch thì dừng và in cách lập lại), rồi chạy nóng pipeline trên một ảnh gallery để lần chụp đầu tiên không bị chậm.
- Pipeline thật trong Docker: `make docker-up-gpu`. **Đang hỏng** (lần chạy thử làm Docker Desktop ngừng hẳn, xem `instruct_dev.md` mục 6): đừng chạy cho tới khi `backend/Dockerfile` được sửa.
- Bản không có hot reload, log JSON, không có `/docs`, giao diện đã build do nginx phục vụ: `make docker-up-prod`. **Mới kiểm tới bước build image web; chưa chạy đủ luồng.**
- Chạy API ngay trên máy với bộ nhận diện giả (để sửa code backend): `make docker-up-data` (chỉ Postgres, cổng 5437) rồi `make dev-api`.

**Sinh dữ liệu demo** (khi `backend/data_demo/` chưa có): `cd backend && uv run python scripts/make_demo_dataset.py`, chạy pipeline một lần để lập index FAISS (`uv run python -m engine --mode validate --config configs/config.demo.yaml --benchmark-dir data_demo/benchmark`), rồi `make seed-demo`. Script ghi đè `configs/config.demo.yaml`: sau khi chạy hãy `git checkout backend/configs/config.demo.yaml` để giữ bản đã commit. Ảnh sinh ra không giống hệt giữa các máy, nên đừng chạy lại khi đã có dữ liệu.

## 4. Dùng trên điện thoại

Xem trước camera trong trang và cảm biến nghiêng cần `https://`. Mở bằng `http://<IP LAN>:5173` vẫn dùng được: màn chụp báo không xem trước được, bấm chụp bằng ứng dụng camera của máy hoặc chọn ảnh từ thư viện.

**Điện thoại ở mạng khác** (giám khảo dùng 4G của họ): nhấp đúp `scripts\run_real_tunnel.bat`. Cửa sổ in dòng `PHONE (any network): open https://….trycloudflare.com`; mở địa chỉ đó trên bất kỳ điện thoại nào. Đi qua HTTPS nên có khung camera trong trang. Cần `tools\cloudflared.exe` (Cloudflare, miễn phí, không cần tài khoản: tải `cloudflared-windows-amd64.exe` từ github.com/cloudflare/cloudflared/releases, đổi tên). Mỗi lần bật là một địa chỉ mới; đóng cửa sổ là tắt cả tunnel. Đã thử trên điện thoại thật qua 4G (2026-10-08).

Mỗi tài khoản chỉ có **một ca mở**: đăng nhập cùng tài khoản ở máy thứ hai sẽ đá máy thứ nhất ra. Khi demo: tài khoản thu ngân chỉ trên điện thoại, admin trên laptop.

## 5. Trước khi thu ngân dùng

Đăng nhập admin → **Sản phẩm**: nhập **giá** và **barcode** cho các SKU sẽ bán. Bộ demo đã có giá cho 42/50 SKU, cố ý để 8 SKU trống để thử luồng nhập giá tay. Chỉ SKU có barcode mới quét được.

## 6. Luồng thu ngân

Chụp → hoá đơn (dòng viền vàng = chưa chắc, chạm để xác nhận hoặc đổi) → nhập giá tay nếu thiếu → thanh toán tiền mặt / QR → hoàn tất.

- **Chụp thêm** cộng dồn vào đơn; **Thêm món** tìm theo tên (không cần dấu) hoặc nhập / quét mã vạch. Máy quét USB/Bluetooth dùng được ngay trên màn chụp và hoá đơn.
- Tải lại trang hoặc đăng nhập lại **không mất đơn**: đơn đang dở được khôi phục; đơn rỗng được dùng lại.
- Ảnh được thu nhỏ (cạnh dài tối đa 4032 px) và sửa hướng EXIF trước khi tải lên.
- Trên ảnh kết quả: khung xanh = chắc chắn, vàng = cần xác nhận, đỏ = chưa nhận diện (chạm để thêm món thủ công), xám nét đứt = dòng đã xoá.

## 7. Quản trị

- **Báo cáo:** KPI hôm nay, doanh thu theo ngày (hôm nay / 7 / 30 ngày), top 5 sản phẩm. Đơn đã huỷ không tính. "Hôm nay" theo `TIMEZONE_OFFSET_HOURS`.
- **Đơn hàng:** danh sách đơn của mọi thu ngân.
- **Nhân viên:** thêm, khoá, đặt lại mật khẩu.
- **Sản phẩm:** sửa tên / giá / barcode; xem ảnh gallery; sửa **bằng chứng nhận diện** (từ khoá OCR, mã màu + màu tham chiếu, plugin bắt buộc, SKU dễ nhầm — ghi hai chiều); **lịch sử thay đổi** và **hoàn tác** từng thay đổi (bị từ chối nếu giá trị đã bị đổi sau đó); **thử bằng chứng**: chọn một ảnh, xem OCR đọc gì, màu đo được, mã vạch đọc được (không lưu).
- **Cài đặt:** ngưỡng nhận diện theo lượt chụp, cho phép thanh toán khi thiếu giá, chặn chụp khi máy nghiêng, tự in hoá đơn. Hiệu lực ở lượt chụp kế tiếp.
- **Nâng cao:** thông số của pipeline, hai mức — *chỉ xem* (model, backend, thiết bị: đổi trong file YAML rồi chạy cổng kiểm định) và *cần áp dụng* (ngưỡng detector, trọng số, bật/tắt plugin…). Áp dụng cần **mật khẩu nâng cao**, dựng lại pipeline (ngừng nhận diện trong lúc đó), dựng lỗi thì tự quay về thiết lập cũ. Thiết lập lưu ở bảng `config_overrides` và còn nguyên sau khi khởi động lại.
- **Kiểm định** (trong tab Nâng cao): chạy benchmark bằng pipeline đang dùng, so với baseline ở `backend/data/baseline/`. Trong lúc chạy thu ngân **không chụp nhận diện được** (vẫn thêm món thủ công được): chỉ chạy ngoài giờ bán. Báo cáo ở `<DATA_DIR>/validation_web/`.

## 8. Lệnh vận hành

| Lệnh | Việc |
|---|---|
| `make reset-password USER_NAME=<tên> [ROLE=admin\|staff]` | mật khẩu ngẫu nhiên mới; có `ROLE` thì tạo tài khoản nếu chưa có |
| `make reset-advanced-password` | mật khẩu nâng cao mới |
| `make db-save NAME=<tên>` | sao lưu database vào `backups/<tên>.dump` |
| `make db-restore NAME=<tên>` | **thay toàn bộ** database bằng bản sao lưu (dừng `api` trước). *Chưa chạy thử.* |
| `make purge-media DAYS=30 [DRY_RUN=1]` | xoá ảnh chụp cũ hơn số ngày; đơn vẫn còn, chỉ mất ảnh |
| `make import-legacy DATA_DIR=<thư mục> [REPLACE=1]` | chép `app.db` của web v1 (`<thư mục>/db/app.db`) vào Postgres; file SQLite chỉ được đọc. Ảnh đơn hàng không được chép: chép thư mục `transactions/` cũ vào `MEDIA_DIR`. |
| `make check-env` | công cụ, bí mật (không in giá trị), database + migration, số SKU / giá / tài khoản, pipeline, thư mục ảnh, GPU |
| `make migrate` · `make migration MSG="..."` | áp dụng / sinh migration Alembic |

Gõ `make help` để xem đủ danh sách.

## 9. Kiểm thử

```bash
make lint          # ruff trên code web của backend
make type-check    # mypy (backend) + tsc (frontend)
make test          # pytest backend: test engine + test API trên database riêng stocktaking_test
make test-web      # vitest (frontend)
make smoke         # đi hết luồng qua HTTP thật trên API đang chạy
```

`make smoke` cần hai tài khoản và catalog trong database, đọc tên và mật khẩu từ biến môi trường `SMOKE_ADMIN`, `SMOKE_ADMIN_PW`, `SMOKE_STAFF`, `SMOKE_STAFF_PW`. Nó tạo và thanh toán một đơn thật (đơn đó ở lại trong báo cáo ngày), nên chạy trên database thử.

Thay đổi trong `backend/engine/` còn phải qua cổng kiểm định của pipeline: xem [`03_DEVELOPMENT_RULES.md`](03_DEVELOPMENT_RULES.md) mục 19.8.

## 10. Bẫy đã gặp

- Sửa code backend khi chạy qua Docker Desktop: `uvicorn --reload` không thấy thay đổi qua bind mount → `docker compose restart api`.
- Trên một số máy Windows, kết nối Postgres qua `localhost` rất chậm: dùng `127.0.0.1` (mặc định trong `.env.example`).
- `docker build` báo "Release file is not valid yet": đồng hồ máy lệch, đồng bộ lại giờ.
- Đổi `JWT_SECRET` làm mọi người phải đăng nhập lại; đổi `MEDIA_URL_SECRET` làm các URL ảnh đang mở hết hiệu lực.
