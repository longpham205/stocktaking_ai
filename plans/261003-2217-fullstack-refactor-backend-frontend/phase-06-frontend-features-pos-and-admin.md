# Phase 06 — Frontend features (POS + Admin)

**Priority:** P1 · **Status:** in progress (6a POS, 6b-1 admin cơ bản: done 2026-10-04; 6b-2 sản phẩm + nâng cao: pending) · Nguồn hành vi: `src_legacy/frontend/app.js`, `docs/WEB.md`

## POS (staff, mobile-first)
- [ ] onboarding (một lần, `POST me/onboarding-seen`)
- [ ] capture: `getUserMedia` khung ngắm, `use-tilt` (deviceorientation, chặn khi > 15° nếu setting bật), fallback chụp bằng camera máy / chọn ảnh, downscale ≤2048px + sửa EXIF, `Idempotency-Key`, máy quét barcode USB (keystroke buffer) trên màn chụp + hoá đơn
- [ ] loading: poll job (refetchInterval), timeout 120s, hiển thị `position`/`system_reloading`
- [ ] invoice: dòng viền vàng ⚠ = uncertain (chạm để xác nhận/đổi SKU), giá tay khi thiếu, sửa SL, xoá; ảnh capture + overlay bbox 🟩🟨🟥/xám nét đứt, đánh dấu hai chiều dòng↔khung, zoom 1–4×; Chụp thêm (cộng dồn); Thêm món (tìm không dấu / barcode)
- [ ] pay: tiền mặt (tiền khách đưa, tiền thừa) / QR; chặn khi thiếu giá trừ khi setting cho phép
- [ ] done: hoá đơn + in (`#print-area`, auto print theo setting)
- [ ] history (today/7d/30d/all, chi tiết, void)
- [ ] khôi phục đơn dở sau reload/đăng nhập lại (`orders/open`)

## Admin (desktop)
- [ ] reports: KPI + doanh thu theo ngày + top 5 (bảng/biểu đồ nhẹ, CSS bar — tránh thêm lib chart nếu không cần)
- [ ] products: phân trang, search, filter (chưa đặt tên / thiếu màu), sửa tên/giá/barcode, 🧠 evidence editor (OCR keywords chuẩn hoá, color code + chấm màu 9×9 trên ảnh gallery, force_evidence, confusable_with, ô xác nhận), 🕘 change-log + hoàn tác, 🧪 thử bằng chứng
- [ ] orders (mọi nhân viên), users (tạo, đặt lại MK, khoá), settings
- [ ] advanced: registry 🔒/🟡, apply với advanced password + trạng thái reload, validation (chạy, theo dõi, so baseline)

## Done khi
Checklist parity đủ; vitest cho mỗi feature (stub API); smoke thủ công trên điện thoại qua HTTPS forward.

## Kết quả 6a — POS (2026-10-04)
- Route: `/pos` (chụp; khôi phục đơn mở còn hàng -> hoá đơn kèm "Đã khôi phục đơn #…"; staff chưa xem hướng dẫn -> `/onboarding`), `/onboarding` (4 bước, đánh dấu đã xem ngay khi hiện), `/pos/orders/$id` (hoá đơn; `?job=` theo dõi job), `/pos/orders/$id/capture` (chụp thêm), `/pos/orders/$id/pay`, `/pos/orders/$id/done` (`?fresh` -> tự in nếu cài đặt bật), `/history`.
- `frontend/src/features/pos/`: `api.ts`, `types.ts`, `lib.ts` (nghiêng, tiền, bàn phím tiền mặt, nhóm lịch sử theo ngày, thu nhỏ ảnh ≤2048 px + EXIF bằng `createImageBitmap`), `use-order.ts` (mỗi thay đổi thay đơn trong cache bằng đơn server trả về), `use-camera.ts` (khung ngắm camera sau, lý do khi không dùng được, cảm biến nghiêng + xin quyền iOS), `use-barcode-scanner.ts`, `capture-page.tsx`, `invoice-page.tsx` (theo dõi job 700 ms, hàng chờ, `system_reloading`, hết hạn 120 s không tính lúc nạp lại; cảnh báo chồng lấp/vật chưa nhận diện/dòng cần xác nhận; chặn thanh toán khi thiếu giá), `capture-overlay.tsx` (khung 🟩🟨🟥/xám, đánh dấu hai chiều, chọn lượt, phóng to 1–4×), `line-item.tsx`, `product-picker.tsx` (tìm không dấu, mã vạch), `pay-page.tsx`, `done-page.tsx` (+ hoá đơn in, CSS `@media print`), `history-page.tsx` (chi tiết, admin huỷ đơn), `onboarding-page.tsx`.
- Dùng chung: `components/ui/dialog.tsx` (portal + Tailwind, không thêm thư viện), `components/confirm-dialog.tsx` (`useConfirm`); đăng xuất khi đơn dở thì hỏi rồi huỷ đơn.
- Test: 27 vitest (14 mới: hàm thuần 4, luồng POS 10 — chụp -> job -> hoá đơn có khung + cảnh báo + đánh dấu, xác nhận dòng, giá tay + chặn thanh toán, xoá dòng có hỏi, máy quét, tiền mặt + tiền thừa, khôi phục đơn, hướng dẫn, đăng xuất có đơn dở, lịch sử + admin huỷ); chạy lại 3 lần ổn định. `pnpm typecheck`, `pnpm build` đạt.
- Chạy thật trên Docker trong trình duyệt nội bộ (bộ nhận diện giả, ảnh tạo bằng canvas qua ô "Chọn ảnh"): hướng dẫn -> chụp -> hoá đơn 7 món, 7 khung, ảnh ký + 6 ảnh thu nhỏ tải được -> xác nhận dòng vàng -> nhập giá tay -> tiền mặt 530.000đ, thối 4.500đ -> lịch sử -> khôi phục đơn dở sau khi tải lại -> huỷ đơn.
- Docker: `vite.config.ts` bật polling khi có `VITE_USE_POLLING` (compose dev đặt sẵn: Docker Desktop không chuyển sự kiện file qua bind-mount); image `web` dev/prod có tag riêng (`stocktaking-ai-web:dev|prod`) — trước đó build prod đã ghi đè image dev.

## Chưa thử được (cần điện thoại thật qua HTTPS)
- Khung ngắm camera trực tiếp và cảm biến nghiêng (trình duyệt nội bộ chặn camera; `http://` qua IP LAN cũng bị chặn — app tự chuyển sang "Chụp bằng camera máy"/"Chọn ảnh").
- Máy quét mã vạch thật (đã test bằng phím giả lập), in hoá đơn ra máy in.

## Kết quả 6b-1 — admin: báo cáo, đơn hàng, nhân viên, cài đặt (2026-10-04)
- `features/admin-reports/reports-page.tsx`: 9 thẻ KPI hôm nay (tỉ lệ lỗi > 10% và SKU thiếu giá tô đỏ), chọn khoảng today/7d/30d, biểu đồ cột CSS theo ngày (tooltip ngày · tiền · số đơn; 30 cột thì 5 ngày một nhãn), top sản phẩm, tự làm mới 15 s.
- `features/admin-orders/orders-page.tsx`: đơn của mọi nhân viên (today/7d/30d/all), "Chi tiết" dùng lại hộp thoại `OrderDetail` của lịch sử (admin huỷ đơn).
- `features/admin-users/users-page.tsx`: thêm nhân viên, trạng thái (đang trong ca / hoạt động / đã khoá), khoá-mở khoá, đặt lại mật khẩu (nhập hai lần, ≥ 8 ký tự); server từ chối thì giữ nguyên form.
- `features/admin-settings/settings-page.tsx`: ba công tắc + hai thanh trượt ngưỡng với ô "Dùng mặc định của hệ thống" (null); lưu xong làm mới `/me` cho màn POS.
- `components/ui/switch.tsx` (tự viết, `role="switch"`).
- Test: 33 vitest (6 mới). `pnpm typecheck`, `pnpm build` đạt.

## Khác bản cũ (6b-1)
- Cài đặt chỉ gửi các khoá thật sự đổi (bản cũ gửi cả 5 khoá mỗi lần lưu, nên mỗi lần lưu sinh 5 dòng nhật ký); không đổi gì thì báo "Không có gì thay đổi".

## Chạy thật 6b-1 (2026-10-04, sau khi Docker bật lại)
- `make docker-up`, đăng nhập `e2e_admin` ở http://localhost:5173: vào thẳng `/admin/reports` (KPI thật, biểu đồ 7 cột, thẻ SKU thiếu giá đỏ, top 5), `/admin/orders` (7 đơn hôm nay), `/admin/users` (2 tài khoản, đang trong ca). Chưa bấm thử khoá/đặt lại mật khẩu và lưu cài đặt trong trình duyệt (người dùng đang tự thao tác trên cùng khung; các thao tác đó có test vitest + test API).
- Lỗi thấy khi chạy thật, đã sửa ở backend: cột "Thu ngân" trống với tài khoản chưa có họ tên -> `history`/`admin/orders` trả tên tài khoản thay thế (`orders/repository.py`, có test).
