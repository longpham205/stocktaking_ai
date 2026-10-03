# Web POS — hướng dẫn chạy nhanh

Backend (Python thuần, không cần cài thêm gói nào) tự phục vụ luôn giao diện, nên **chỉ cần một cổng, một URL**.

## 1. Chạy

```powershell
# (a) Thử giao diện NGAY, không nạp model (kết quả nhận diện là ngẫu nhiên):
python -m backend --fake --config configs\config.demo.yaml --data-dir data_demo

# (b) Chạy với pipeline thật trên bộ demo (mock, CPU) — cần đã chạy validate 1 lần để có index FAISS:
python -m backend --config configs\config.demo.yaml --data-dir data_demo

# (c) Dữ liệu thật (config.yaml, thư mục data/):
python -m backend
```
Mở `http://127.0.0.1:8000`. Dùng `--port 8080` nếu cổng bận. `Ctrl+C` để dừng.

**Tài khoản:** lần chạy đầu tiên, mật khẩu `staff` và `admin` được sinh ngẫu nhiên, in ra **một lần** và lưu trong `.env` (không commit). Quên thì mở `.env`. Không có mật khẩu mặc định; xoá `.env` sẽ sinh mật khẩu mới cho tài khoản chưa tồn tại (tài khoản đã có trong DB giữ mật khẩu cũ — đổi bằng Admin → Nhân viên → Đặt lại MK).

### Chạy bằng script (khuyến nghị)
```powershell
.\launch.bat demo          # dữ liệu demo (config.demo.yaml + data_demo)
.\launch.bat               # dữ liệu thật (config.yaml + data)
.\launch.bat demo --fake   # thêm tham số cho backend, ví dụ --fake / --port 8080
```
Yêu cầu trước: catalog đã nằm trong DB (`python -m src.catalog.migrate ...`, xem `docs/04_DATA_AND_CATALOG.md`); web và pipeline phải dùng **cùng một** `app.db` (`catalog.db_path` = `<data-dir>/db/app.db`), sai sẽ báo lỗi khi khởi động. Script tự kích hoạt `venv`, chạy `scripts\check_env.py` (kiểm tra thư viện, catalog, gallery, index, weights, cổng, DB, giá/barcode; **dừng nếu có [FAIL]**) rồi mới khởi động server. Linux/macOS: `./bin/launch.sh [demo|real]`. Chỉ kiểm tra không chạy: `python scripts\check_env.py --config configs\config.demo.yaml --data-dir data_demo`. Các bước cho ngày demo: [`DEMO.md`](DEMO.md).

## 2. Dùng trên điện thoại (xem trước camera trong trang cần HTTPS)
Mở bằng `http://<IP LAN>:8000` vẫn dùng được: màn chụp báo không xem trước được camera, bấm **📷 Chụp bằng camera máy** (mở ứng dụng camera của điện thoại) hoặc **🖼️ Chọn ảnh** (thư viện). Muốn có khung ngắm + cảnh báo nghiêng ngay trong trang thì phải mở qua `https://`:
VS Code → tab **Ports** → *Forward a Port* `8000` → đổi Visibility thành **Public** → mở URL `https://…` trên điện thoại. Chỉ cần **một** URL. Xong buổi thì tắt cổng.
Mỗi tài khoản chỉ có **một ca mở**: đăng nhập cùng tài khoản ở máy thứ hai sẽ đá máy thứ nhất ra. Khi demo: `staff` chỉ trên điện thoại, `admin` trên laptop.

## 3. Trước khi thu ngân dùng
Đăng nhập `admin` → **Sản phẩm**: nhập **giá** và **barcode** cho các SKU sẽ bán. (Bộ demo đã seed giá cho 42/50 SKU, cố ý để 8 SKU trống để thử luồng nhập giá tay.) Barcode chỉ quét được với SKU đã có barcode.

## 4. Luồng thu ngân
**Tải lại trang hoặc đăng nhập lại không mất đơn:** đơn đang dở được khôi phục tự động (và đơn rỗng được dùng lại, không sinh đơn mồ côi). Ảnh chọn từ máy được thu nhỏ (tối đa 2048px) và sửa hướng EXIF trước khi tải lên.
Chụp → hoá đơn (dòng **viền vàng ⚠** = chưa chắc, chạm để xác nhận/đổi) → nhập giá tay nếu thiếu → thanh toán tiền mặt/QR → hoàn tất. Có thể **Chụp thêm** (cộng dồn), **Thêm món** (tìm theo tên không cần dấu, hoặc nhập/quét mã vạch), máy quét mã vạch USB/Bluetooth dùng được ngay trên màn chụp và hoá đơn.

## 4b. Quản trị
- **Báo cáo:** KPI hôm nay + doanh thu theo ngày (hôm nay / 7 / 30 ngày, kể cả ngày không có đơn) + top 5 sản phẩm bán chạy. Đơn đã huỷ không tính.
- **Sản phẩm:** sửa tên/giá/barcode (tên + barcode ghi vào catalog DB dùng chung với pipeline, có hiệu lực ngay); lọc **Chưa đặt tên**; badge **thiếu màu tham chiếu**; nút 🧠 sửa **bằng chứng nhận diện** (từ khoá OCR, mã màu + màu tham chiếu, plugin bắt buộc, SKU dễ nhầm — ghi hai chiều) với ô xác nhận bắt buộc; nút 🕘 xem **lịch sử thay đổi** và **Hoàn tác** từng thay đổi. Hoàn tác bị từ chối nếu giá trị đã bị đổi sau đó (tránh ghi đè nhầm) và bản thân việc hoàn tác cũng được ghi log.
- **Cài đặt:** ngưỡng nhận diện (`similarity`, `min_confidence_accept`), cho phép thanh toán khi thiếu giá, **chặn chụp khi máy nghiêng > 15°**, **tự in hoá đơn**. Có hiệu lực ở lần chụp kế tiếp của thu ngân, không cần khởi động lại.

## 5. Kiểm thử
```powershell
python -m pytest tests\test_backend_api.py tests\test_db_snapshot.py -q   # test API qua HTTP thật (bộ suy luận giả) + sao lưu DB
# smoke test giao diện (cần Node; chạy khi server đang chạy ở chế độ --fake):
node tests\frontend_smoke.js http://127.0.0.1:8000 <mk staff> <mk admin> data_demo\query\query_01.jpg
```
Smoke test đăng nhập, chụp, thanh toán, đổi giá… nên chạy trên **DB sạch** (`--data-dir` mới) vì nó tạo đơn và nhân viên thật.

## 6. Cấu trúc
`backend/` (config, db, security, catalog, inference, mapper, service, server) · `frontend/` (index.html, app.css, app.js) · `configs/backend.yaml` · dữ liệu web ở `<data-dir>/db/app.db`, ảnh đơn hàng ở `<data-dir>/transactions/`.
Backend gọi pipeline **chỉ** qua `InferenceRunner`; không sửa gì trong `src/`.

## Quên mật khẩu
- Mật khẩu ban đầu của `staff`/`admin` nằm trong `.env` (`SEED_STAFF_PASSWORD`, `SEED_ADMIN_PASSWORD`) — chỉ đúng khi chưa đổi trên web.
- Quên mật khẩu `staff`: đăng nhập `admin` → **Nhân viên** → đặt lại.
- Quên mật khẩu bất kỳ tài khoản nào (kể cả `admin`): chạy **trên máy chủ** `python scripts\reset_password.py admin [--data-dir data_demo] [--prompt] [--unlock]` — sinh mật khẩu ngẫu nhiên, in ra **một lần**, đóng các ca đang mở. `--list` để xem danh sách tài khoản.

## Thiết lập nâng cao (Admin → 🧪 Nâng cao)
- Danh sách thông số do `backend/config_registry.py` quy định (thêm thông số = thêm một dòng). Hai mức:
  🔒 **chỉ xem** (model, backend, thiết bị, số chiều vector — đổi trong file config rồi chạy lại cổng kiểm định) và
  🟡 **cần áp dụng** (ngưỡng detector, trọng số quyết định/hợp nhất, bật/tắt plugin...). Ngưỡng theo lượt chụp và cài đặt quầy vẫn ở tab ⚙️ Cài đặt (hiệu lực ngay).
- Áp dụng cần **mật khẩu nâng cao** (khác mật khẩu đăng nhập; `SEED_ADVANCED_PASSWORD` trong `.env`, tự sinh lần chạy đầu; quên thì `python scripts\reset_password.py --advanced [--data-dir data_demo]`). Sai 5 lần bị khoá tạm.
- Khi áp dụng: kiểm khoảng an toàn → validate toàn bộ config → **nạp lại pipeline (ngừng nhận diện ~30–60 giây)**; nạp lỗi thì tự quay về thiết lập cũ. Ghi đè lưu ở bảng `config_overrides` (file YAML giữ giá trị gốc), có nhật ký + hoàn tác (cũng cần mật khẩu nâng cao). Khởi động lại server vẫn giữ thiết lập; nếu thiết lập đã lưu làm pipeline không nạp được: `python -m backend ... --clear-config-overrides`.

## Công cụ bằng chứng & kiểm định (Admin)
- **Từ khoá OCR** được chuẩn hoá giống hệt phía nhận diện (chỉ giữ chữ + số: `BE-203` → `BE203`); OCR đọc chữ Latin — từ khoá ngoài Latin sẽ được cảnh báo.
- **Chấm màu**: trong 🧠 Bằng chứng, chạm một ảnh gallery của SKU rồi chạm vùng màu đặc trưng (lấy trung bình 9×9 điểm ảnh) → điền sẵn hex màu tham chiếu.
- **🧪 Thử bằng chứng** (tab Sản phẩm): chọn ảnh → nhận diện thử (không lưu) → xem OCR đọc gì, khớp từ khoá SKU nào, màu đo được + mã màu gần nhất, mã vạch đọc được.
- **Kiểm định** (tab Nâng cao): chạy benchmark bằng pipeline đang dùng, so F1 / độ chính xác sau hợp nhất với baseline (`data/baseline/report.json`, demo: `data/baseline/demo/report.json`). Cần mật khẩu nâng cao; trong lúc chạy (20–50 phút với model thật) thu ngân **không chụp nhận diện được** (báo lỗi rõ, vẫn thêm món thủ công được) — chỉ chạy ngoài giờ bán. Báo cáo lưu ở `<data-dir>/validation_web/`.

## Ảnh kết quả trên hoá đơn
Khung 🟩 chắc chắn · 🟨 cần xác nhận (chạm để sửa) · 🟥 chưa nhận diện (chạm để thêm món thủ công) · xám nét đứt = dòng đã xoá. Chạm dòng/khung để đánh dấu hai chiều; nút 🔍 phóng to 1–4×.
