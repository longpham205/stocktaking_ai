# Runbook ngày demo — Web POS

> Kiến trúc thực tế: **một tiến trình, một cổng (8000), một URL** — backend tự phục vụ giao diện, nên tunnel chỉ cần **một** cổng và **không phải khởi động lại** khi URL tunnel đổi (cùng origin, không có CORS).
> Chỉ có các bước làm, lý do ở `WEB_README.md` và `process.md`.

## A. Trước ngày demo

**Kiểm chứng phần mềm**
- [ ] `python -m pytest -q` đạt toàn bộ.
- [ ] Nếu đã đổi code hoặc dữ liệu: `python run.py --mode validate` rồi `python scripts\compare_validate.py <report mới> data\baseline\report.json` phải `ĐẠT` (cổng G, dữ liệu thật).
- [ ] `launch.bat` (dữ liệu thật) hoặc `launch.bat demo`: bảng `check_env` **không còn dòng [FAIL]**.

**Dữ liệu**
- [ ] Đăng nhập `admin` → **Sản phẩm** → lọc **Thiếu giá**: nhập giá cho mọi SKU sẽ bán. Nhập **barcode** cho các SKU cần quét (chỉ SKU có barcode mới quét được).
- [ ] Thêm SKU mới: đặt ảnh vào `data\gallery\`, chạy `python run.py --mode validate` (tự build lại index) rồi so cổng G; SKU mới cần có trong `products.json`.
- [ ] Dựng DB demo sạch (server **đã dừng**): `python scripts\db_snapshot.py purge-orders` → `python scripts\db_snapshot.py save demo_clean` → `python scripts\db_snapshot.py list`. (`--data-dir data_demo` nếu dùng dữ liệu demo.)
- [ ] Mật khẩu `staff`/`admin` nằm trong `.env` (không commit, không ghi vào tài liệu). **`staff` chỉ đăng nhập trên điện thoại, `admin` trên laptop** — đăng nhập cùng tài khoản ở máy thứ hai sẽ đá máy thứ nhất ra.

**Thiết bị và mạng**
- [ ] Thử **Android và iOS**: cấp quyền camera; iOS cần chạm nút "Bật cảm biến nghiêng"; iOS Safari không rung.
- [ ] Thử tunnel qua **4G điện thoại thật** ít nhất một lần *(CHƯA LÀM)*: VS Code → tab **Ports** → forward `8000` → Visibility **Public**.
- [ ] Thử thao tác thật trên điện thoại: chụp bằng camera, chọn ảnh lớn từ thư viện, tải lại trang khi đang có giỏ (đơn phải được khôi phục).
- [ ] Quay **video** chạy trơn tru làm phương án lùi cuối.

## B. Ngay trước giờ demo (thứ tự cố định)

1. Dừng server nếu đang chạy. Đăng xuất mọi thiết bị.
2. `python scripts\db_snapshot.py restore demo_clean` (mọi phiên cũ bị vô hiệu, cần đăng nhập lại).
3. `launch.bat` (hoặc `launch.bat demo`). Đợi dòng "Web POS đang chạy". Nếu có [FAIL] thì sửa rồi chạy lại.
4. Mở `http://127.0.0.1:8000/api/health` trên laptop: phải thấy `{"status": "ready"}`.
5. Mở tunnel (Ports → forward 8000 → Public), copy URL `https://…`.
6. Mở URL đó trên điện thoại **qua đúng mạng 4G dùng lúc demo**, đăng nhập `staff`.
7. Chạy thử **một giao dịch đầy đủ**: chụp → hoá đơn → (nhập giá tay nếu thiếu) → thanh toán → hoàn tất. Không demo thật nếu bước này chưa trơn.
8. Xem trạng thái từ laptop bằng `admin` (không dùng `staff`).
9. Xoá đơn thử: đăng xuất → dừng server → `db_snapshot.py restore demo_clean` → `launch.bat` lại. Tunnel còn sống thì giữ nguyên URL (không cần cấu hình lại).

## C. Trong lúc demo — nếu có sự cố

| Tình huống | Làm gì |
|---|---|
| Banner "Phát hiện thêm N vật chưa nhận diện" hoặc thiếu sản phẩm | **Thêm món**: tìm theo tên (không cần dấu) hoặc nhập/quét mã vạch; hoặc **Chụp thêm** gần hơn, tách các món ra |
| Nhận sai sản phẩm | Chạm dòng viền vàng → chọn đúng sản phẩm; hoặc chạm dòng thường để sửa số lượng/xoá |
| Thiếu giá | Nhập giá tay ngay tại dòng; thanh toán bị chặn đến khi nhập đủ |
| Banner "chụp thêm" (chồng lấp) | Dàn lại hàng, **Chụp thêm** (cộng dồn); hoặc **Bỏ qua** |
| Camera không mở | Dùng **Chọn ảnh** (chụp bằng app camera của máy rồi chọn) |
| "Hệ thống đang bận" (hàng đợi đầy) | Đợi vài giây rồi chụp lại |
| "Xử lý quá lâu" / "Hệ thống quá tải" | Chụp lại; nếu lặp lại thì khởi động lại server |
| Bị đưa về màn đăng nhập | Kiểm tra có ai đăng nhập cùng tài khoản ở máy khác không; đăng nhập lại — **đơn đang dở được khôi phục** |
| Server/mạng lỗi không sửa kịp | Chuyển sang **video** đã quay |

> **Không có** phương án lùi "GPU từ xa" (Colab/worker từ xa chưa được xây). Nếu máy chủ chết hoặc mất mạng thì dùng video.

## D. Sau demo
- [ ] Tắt tunnel (Ports → dừng chia sẻ cổng 8000). `Ctrl+C` dừng server.
- [ ] Nếu mật khẩu có thể đã lộ: Admin → Nhân viên → **Đặt lại MK**.
