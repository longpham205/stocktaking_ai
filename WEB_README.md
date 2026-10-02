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
launch.bat demo            # dữ liệu demo (config.demo.yaml + data_demo)
launch.bat                 # dữ liệu thật (config.yaml + data)
launch.bat demo --fake     # thêm tham số cho backend, ví dụ --fake / --port 8080
```
Script tự kích hoạt `venv`, chạy `scripts\check_env.py` (kiểm tra thư viện, catalog, gallery, index, weights, cổng, DB, giá/barcode; **dừng nếu có [FAIL]**) rồi mới khởi động server. Linux/macOS: `./launch.sh [demo|real]`. Chỉ kiểm tra không chạy: `python scripts\check_env.py --config configs\config.demo.yaml --data-dir data_demo`. Runbook ngày demo: `docs/runbooks/DEMO.md`.

## 2. Dùng trên điện thoại (camera cần HTTPS)
VS Code → tab **Ports** → *Forward a Port* `8000` → đổi Visibility thành **Public** → mở URL `https://…` trên điện thoại. Chỉ cần **một** URL. Xong buổi thì tắt cổng.
Mỗi tài khoản chỉ có **một ca mở**: đăng nhập cùng tài khoản ở máy thứ hai sẽ đá máy thứ nhất ra. Khi demo: `staff` chỉ trên điện thoại, `admin` trên laptop.

## 3. Trước khi thu ngân dùng
Đăng nhập `admin` → **Sản phẩm**: nhập **giá** và **barcode** cho các SKU sẽ bán. (Bộ demo đã seed giá cho 42/50 SKU, cố ý để 8 SKU trống để thử luồng nhập giá tay.) Barcode chỉ quét được với SKU đã có barcode.

## 4. Luồng thu ngân
**Tải lại trang hoặc đăng nhập lại không mất đơn:** đơn đang dở được khôi phục tự động (và đơn rỗng được dùng lại, không sinh đơn mồ côi). Ảnh chọn từ máy được thu nhỏ (tối đa 2048px) và sửa hướng EXIF trước khi tải lên.
Chụp → hoá đơn (dòng **viền vàng ⚠** = chưa chắc, chạm để xác nhận/đổi) → nhập giá tay nếu thiếu → thanh toán tiền mặt/QR → hoàn tất. Có thể **Chụp thêm** (cộng dồn), **Thêm món** (tìm theo tên không cần dấu, hoặc nhập/quét mã vạch), máy quét mã vạch USB/Bluetooth dùng được ngay trên màn chụp và hoá đơn.

## 4b. Quản trị
- **Báo cáo:** KPI hôm nay + doanh thu theo ngày (hôm nay / 7 / 30 ngày, kể cả ngày không có đơn) + top 5 sản phẩm bán chạy. Đơn đã huỷ không tính.
- **Sản phẩm:** sửa giá/barcode; nút 🕘 xem **lịch sử thay đổi** và **Hoàn tác** từng thay đổi. Hoàn tác bị từ chối nếu giá trị đã bị đổi sau đó (tránh ghi đè nhầm) và bản thân việc hoàn tác cũng được ghi log.
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
