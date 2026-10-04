/**
 * What the user reads for an API error `code`. The server's `detail` is already Vietnamese and
 * specific (it names the field that is wrong), so it wins for the codes where it carries that
 * information; the table covers the codes where the app knows better what to say, and the network.
 */
const MESSAGES: Record<string, string> = {
  AUTH_INVALID: 'Sai tài khoản hoặc mật khẩu',
  SESSION_INVALID: 'Phiên làm việc đã kết thúc (đăng nhập ở máy khác hoặc ca đã đóng). Hãy đăng nhập lại.',
  TOKEN_EXPIRED: 'Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.',
  RATE_LIMITED: 'Nhập sai quá nhiều lần, hãy thử lại sau ít phút',
  FORBIDDEN: 'Bạn không có quyền thực hiện thao tác này',
  NOT_FOUND: 'Không tìm thấy',
  ORDER_NOT_OPEN: 'Đơn hàng đã thanh toán hoặc đã huỷ',
  PRICE_MISSING_BLOCKED: 'Còn sản phẩm chưa có giá — hãy nhập giá tay trước khi thanh toán',
  QUEUE_FULL: 'Hệ thống đang bận, thử lại sau ít giây',
  SYSTEM_BUSY: 'Hệ thống đang kiểm định — tạm thời chưa nhận diện được, hãy thêm món thủ công',
  IMAGE_TOO_LARGE: 'Ảnh quá lớn',
  IMAGE_DECODE_ERROR: 'Không đọc được ảnh, hãy chụp lại',
  BACKEND_UNAVAILABLE: 'Máy chủ chưa sẵn sàng, thử lại sau',
  DB_UNAVAILABLE: 'Máy chủ chưa sẵn sàng, thử lại sau',
  NETWORK: 'Không kết nối được máy chủ — kiểm tra mạng',
};

/** Codes whose server `detail` says more than a fixed sentence could (which field, which limit). */
const DETAIL_FIRST = new Set(['VALIDATION_ERROR', 'CONFLICT', 'EVIDENCE_INVALID', 'CONFIG_INVALID', 'RELOAD_FAILED']);

export function errorMessage(code: string, detail?: string, status?: number): string {
  if (detail && (DETAIL_FIRST.has(code) || !(code in MESSAGES))) return detail;
  if (code in MESSAGES) return MESSAGES[code];
  if (status !== undefined && status >= 500) return 'Máy chủ gặp lỗi, thử lại sau';
  return detail ?? 'Có lỗi xảy ra';
}
