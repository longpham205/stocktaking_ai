# Hướng dẫn ngày demo — Web POS

> Chỉ có các bước làm; cách cài đặt và giải thích ở [`WEB.md`](WEB.md). Mọi lệnh `make` chạy ở gốc repo (Windows: Git Bash).
> Giao diện và API đi chung **một** cổng 5173 (`web` chuyển tiếp `/api`), nên tunnel chỉ cần một cổng và không phải cấu hình lại khi URL tunnel đổi.

## A. Trước ngày demo

**Kiểm chứng phần mềm**
- [ ] `make lint`, `make type-check`, `make test`, `make test-web` đều đạt.
- [ ] Nếu đã đổi `backend/engine/` hoặc dữ liệu: chạy cổng kiểm định của pipeline (`03_DEVELOPMENT_RULES.md` mục 19.8) và phải `ĐẠT`.
- [ ] `make docker-up` rồi `make check-env`: **không còn dòng FAIL**; đọc kỹ các dòng WARN.
- [ ] Chọn chế độ nhận diện cho buổi demo (`WEB.md` mục 3) và chạy thử đúng chế độ đó. Nhận diện thật: nhấp đúp `scripts\run_real.bat` (GPU của laptop, ~7–9 giây/ảnh).
- [ ] Hiệu năng máy: **cắm sạc**, Armoury Crate / chế độ điện **Turbo** hoặc **Performance** (chạy pin ở chế độ Silent chậm khoảng gấp đôi). Kiểm `nvidia-smi -q -d PERFORMANCE`: dòng `SW Thermal Slowdown` phải là `Not Active`.

**Dữ liệu**
- [ ] Đăng nhập admin → **Sản phẩm** → lọc thiếu giá: nhập giá cho mọi SKU sẽ bán. Nhập **barcode** cho các SKU cần quét.
- [ ] Thêm SKU mới (dữ liệu thật): quy trình ở [`04_DATA_AND_CATALOG.md`](04_DATA_AND_CATALOG.md) mục 6.2, rồi lập lại index và so cổng kiểm định. Đặt tên và khai báo bằng chứng ở Admin → Sản phẩm.
- [ ] Dựng database demo sạch: huỷ hoặc thanh toán hết đơn thử, rồi `make db-save NAME=demo_clean`. Kiểm có file `backups/demo_clean.dump`.
- [ ] Thử `make db-restore NAME=demo_clean` **một lần trước ngày demo**: `docker compose stop api` → `make db-restore NAME=demo_clean` → `docker compose start api` → đăng nhập lại, kiểm dữ liệu.
- [ ] Tài khoản: thu ngân chỉ đăng nhập trên điện thoại, admin trên laptop (đăng nhập cùng tài khoản ở máy thứ hai sẽ đá máy thứ nhất ra). Mật khẩu không nằm trong repo hay tài liệu; quên thì `make reset-password USER_NAME=<tên>`.

**Thiết bị và mạng**
- [ ] Thử **Android và iOS**: cấp quyền camera; iOS cần chạm nút bật cảm biến nghiêng.
- [ ] Thử `scripts\run_real_tunnel.bat` trên **4G điện thoại thật** (giám khảo dùng mạng của họ): mở địa chỉ `https://….trycloudflare.com` cửa sổ in ra, đăng nhập, chụp bằng camera trong trang.
- [ ] Thử thao tác thật trên điện thoại: chụp bằng camera, chọn ảnh lớn từ thư viện, tải lại trang khi đang có giỏ (đơn phải được khôi phục), quét mã vạch, in hoá đơn.
- [ ] Ảnh trình diễn: `backend/data/demo_sets/` đã chia sẵn bằng `scripts/sort_demo_images.py` — `1_dung_het` (nhận đúng toàn bộ, xếp theo thời gian chạy) cho phần trình diễn chính; `2_nhan_dien_sai` để minh hoạ cơ chế **"cần xác nhận"** (ví dụ ảnh `0001.jpg`: các món Cléo/Simple hiện viền vàng).
- [ ] Quay **video** chạy trơn tru làm phương án lùi cuối.

## B. Ngay trước giờ demo (thứ tự cố định)

1. Đăng xuất mọi thiết bị.
2. `docker compose stop api` → `make db-restore NAME=demo_clean` → `make docker-up` (đúng chế độ nhận diện đã chọn).
3. `make check-env`: không có FAIL.
4. Mở `http://localhost:8000/api/health` trên laptop: phải thấy `"status": "ready"` và đúng tên bộ nhận diện (`fake` hoặc `local`).
5. Bật `scripts\run_real_tunnel.bat` (hoặc `scripts\run_real_phone.bat` nếu mọi điện thoại bắt chung Wi-Fi/hotspot với laptop), copy địa chỉ cửa sổ in ra.
6. Mở URL đó trên điện thoại **qua đúng mạng 4G dùng lúc demo**, đăng nhập tài khoản thu ngân.
7. Chạy thử **một giao dịch đầy đủ**: chụp → hoá đơn → (nhập giá tay nếu thiếu) → thanh toán → hoàn tất. Không demo thật nếu bước này chưa trơn.
8. Xem trạng thái từ laptop bằng tài khoản admin (không dùng tài khoản thu ngân).
9. Xoá đơn thử: lặp lại bước 1–2. Tunnel còn sống thì giữ nguyên URL.

## C. Trong lúc demo — nếu có sự cố

| Tình huống | Làm gì |
|---|---|
| Báo còn vật chưa nhận diện, hoặc thiếu sản phẩm | **Thêm món**: tìm theo tên (không cần dấu) hoặc nhập/quét mã vạch; hoặc **Chụp thêm** gần hơn, tách các món ra |
| Nhận sai sản phẩm | Chạm dòng viền vàng ("cần xác nhận") → xác nhận hoặc chọn đúng sản phẩm; hoặc chạm dòng thường để sửa số lượng / xoá |
| Thiếu giá | Nhập giá tay ngay tại dòng; thanh toán bị chặn đến khi nhập đủ |
| Cảnh báo chồng lấp | Dàn lại hàng, **Chụp thêm** (cộng dồn); hoặc bỏ qua |
| Camera không mở (trang mở bằng `http://` hoặc chưa cấp quyền) | Chụp bằng ứng dụng camera của máy, hoặc chọn ảnh từ thư viện |
| "Hệ thống đang bận" (hàng đợi đầy) | Đợi vài giây rồi chụp lại |
| Không chụp được vì đang kiểm định / đang áp dụng thiết lập | Đợi xong; không chạy kiểm định trong giờ demo |
| "Máy chủ đã khởi động lại, hãy chụp lại" | Chụp lại; đơn và các dòng đã có vẫn còn |
| Bị đưa về màn đăng nhập | Kiểm có ai đăng nhập cùng tài khoản ở máy khác không; đăng nhập lại — **đơn đang dở được khôi phục** |
| API không trả lời | `make logs S=api` xem lỗi; `docker compose restart api`; kiểm `/api/health` |
| Server/mạng lỗi không sửa kịp | Chuyển sang **video** đã quay |

> Phương án lùi cuối cùng khi máy chủ hoặc mạng gặp sự cố: chiếu **video** đã quay.

## D. Sau demo
- [ ] Đóng cửa sổ `run_real_tunnel.bat` (tắt cả tunnel). Đặt lại mật khẩu các tài khoản đã dùng qua địa chỉ công khai.
- [ ] Nếu mật khẩu có thể đã lộ: Admin → Nhân viên → đặt lại mật khẩu; với admin: `make reset-password USER_NAME=admin`.
- [ ] Ảnh chụp tích lại trong `MEDIA_DIR`: `make purge-media DAYS=30 DRY_RUN=1` để xem, bỏ `DRY_RUN` để xoá.
