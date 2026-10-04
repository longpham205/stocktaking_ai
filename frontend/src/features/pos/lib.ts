/** Pure helpers of the POS screens (no React): tested on their own. */
import type { HistoryItem } from '@/features/pos/types';

/** Above this tilt (degrees from flat) a photo comes out skewed. */
export const TILT_LIMIT = 15;
/** Below the API's ceiling (MAX_PRICE x 10). */
export const MAX_CASH = 999_999_999;
/** A photo larger than this is scaled down before upload (slow over 4G, and the API caps the size). */
export const MAX_SIDE = 2048;
export const MAX_BYTES = 3 * 1024 * 1024;

export function tiltAngle(beta: number | null, gamma: number | null): number {
  return Math.hypot(beta ?? 0, gamma ?? 0);
}

/** "12.500đ" -> 12500; nothing typed -> null. */
export function parseMoney(text: string): number | null {
  const digits = text.replace(/\D/g, '');
  return digits === '' ? null : Number.parseInt(digits, 10);
}

/** One key of the cash keypad applied to the amount typed so far. */
export function pressKey(given: number, key: string): number {
  let typed = given ? String(given) : '';
  typed = key === '⌫' ? typed.slice(0, -1) : (typed + key).replace(/^0+/, '');
  return Math.min(Number.parseInt(typed || '0', 10), MAX_CASH);
}

export function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

const pad = (n: number) => String(n).padStart(2, '0');
const dayKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

export function timeOfDay(iso: string): string {
  const date = new Date(iso);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export interface HistoryDay {
  key: string;
  label: string;
  /** of the paid orders only */
  total: number;
  count: number;
  items: HistoryItem[];
}

/** The history by local day, newest first as the API sends it: "Hôm nay", "Hôm qua", then dates. */
export function groupByDay(items: HistoryItem[], now = new Date()): HistoryDay[] {
  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const days = new Map<string, HistoryDay>();
  for (const item of items) {
    const key = dayKey(new Date(item.created_at));
    let day = days.get(key);
    if (!day) {
      const [y, m, d] = key.split('-');
      const label = key === today ? 'Hôm nay' : key === yesterday ? 'Hôm qua' : `${d}/${m}/${y}`;
      day = { key, label, total: 0, count: 0, items: [] };
      days.set(key, day);
    }
    day.items.push(item);
    if (item.status === 'paid') {
      day.total += item.total;
      day.count += 1;
    }
  }
  return [...days.values()];
}

/**
 * A photo from the phone's library (12 MP, several MB) is slow over 4G and may exceed the upload
 * limit: scaled down to `MAX_SIDE`, turned as its EXIF says. Returned as is when already small, or
 * when the browser cannot decode it here (the API then judges it).
 */
export async function preparePhoto(photo: Blob): Promise<Blob> {
  if (typeof createImageBitmap !== 'function') return photo;
  try {
    const bitmap = await createImageBitmap(photo, { imageOrientation: 'from-image' });
    const longest = Math.max(bitmap.width, bitmap.height);
    if (longest <= MAX_SIDE && photo.size <= MAX_BYTES) {
      bitmap.close();
      return photo;
    }
    const scale = Math.min(1, MAX_SIDE / longest);
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const scaled = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.88));
    return scaled && scaled.size < photo.size ? scaled : photo;
  } catch {
    return photo;
  }
}
