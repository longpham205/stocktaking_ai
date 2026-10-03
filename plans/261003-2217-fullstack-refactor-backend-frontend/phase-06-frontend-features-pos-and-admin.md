# Phase 06 — Frontend features (POS + Admin)

**Priority:** P1 · **Status:** pending · Nguồn hành vi: `src_legacy/frontend/app.js`, `docs/WEB.md`

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
