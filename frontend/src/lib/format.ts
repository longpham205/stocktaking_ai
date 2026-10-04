const VND = new Intl.NumberFormat('vi-VN');

/** 1234500 -> "1.234.500đ" (VND has no minor unit). */
export function formatVnd(amount: number): string {
  return `${VND.format(amount)}đ`;
}

/** An API timestamp (ISO, UTC) in the shop's local time, as the browser knows it. */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
}
