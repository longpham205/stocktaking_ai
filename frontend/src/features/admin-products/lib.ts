/** Pure helpers of the admin product screens (no React): tested on their own. */
import { formatVnd } from '@/lib/format';

/** "ABA, 100g  OR210" -> ["ABA", "100g", "OR210"] (commas or spaces). */
export function parseList(text: string): string[] {
  return text
    .split(/[,\s]+/)
    .map((token) => token.trim())
    .filter(Boolean);
}

export const FIELD_LABEL: Record<string, string> = {
  price: 'Giá',
  barcode: 'Barcode',
  name: 'Tên',
  ocr_keywords: 'Từ khoá OCR',
  color_code: 'Mã màu',
  force_evidence: 'Plugin bắt buộc',
  confusable_with: 'Dễ nhầm với SKU',
};

/** A change-log value as a person reads it (the log stores text, JSON for evidence). */
export function showValue(field: string, value: string | null): string {
  if (field === 'price') return value === null ? 'chưa có' : formatVnd(Number(value));
  if (value === null || value === '') return '—';
  if (field in FIELD_LABEL && field !== 'barcode' && field !== 'name') {
    try {
      const parsed: unknown = JSON.parse(value);
      return Array.isArray(parsed) ? parsed.join(', ') || '—' : String(parsed);
    } catch {
      // not JSON: shown as stored
    }
  }
  return value;
}

/** The average colour of RGBA pixels, as `#RRGGBB`. */
export function averageHex(pixels: Uint8ClampedArray | number[]): string {
  let r = 0;
  let g = 0;
  let b = 0;
  const count = pixels.length / 4;
  for (let k = 0; k < pixels.length; k += 4) {
    r += pixels[k];
    g += pixels[k + 1];
    b += pixels[k + 2];
  }
  const hex = (sum: number) => Math.round(sum / count).toString(16).padStart(2, '0');
  return `#${hex(r)}${hex(g)}${hex(b)}`.toUpperCase();
}

/** Where a tap on the displayed image falls in the image's own pixels. */
export function imagePoint(
  rect: { left: number; top: number; width: number; height: number },
  natural: { width: number; height: number },
  clientX: number,
  clientY: number,
): { x: number; y: number } {
  return {
    x: Math.round(((clientX - rect.left) * natural.width) / rect.width),
    y: Math.round(((clientY - rect.top) * natural.height) / rect.height),
  };
}

/**
 * The colour under a tap: the average of the 9x9 pixels around it (one pixel would be noise).
 * The gallery photos are same-origin (served through /api), so the canvas may be read.
 */
export function sampleColor(image: HTMLImageElement, clientX: number, clientY: number): string {
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('canvas unavailable');
  context.drawImage(image, 0, 0);
  const { x, y } = imagePoint(image.getBoundingClientRect(), { width: image.naturalWidth, height: image.naturalHeight }, clientX, clientY);
  return averageHex(context.getImageData(Math.max(0, x - 4), Math.max(0, y - 4), 9, 9).data);
}

/** A validation metric as a percentage, and its distance to the baseline in points. */
export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(2)}%`;
}

export function versusBaseline(current: number | null | undefined, baseline: number | null | undefined): string {
  if (current === null || current === undefined || baseline === null || baseline === undefined) return '';
  const points = (current - baseline) * 100;
  return `${points >= 0 ? '+' : ''}${points.toFixed(2)} điểm so với baseline`;
}
