import { describe, expect, it } from 'vitest';
import { groupByDay, MAX_CASH, parseMoney, pressKey, tiltAngle } from '@/features/pos/lib';
import type { HistoryItem } from '@/features/pos/types';

const sale = (id: number, createdAt: Date, total: number, status: 'paid' | 'void' = 'paid'): HistoryItem => ({
  id,
  status,
  created_at: createdAt.toISOString(),
  paid_at: null,
  total,
  item_count: 1,
  payment_method: 'qr',
  cashier: 'Thu ngân',
});

describe('POS helpers', () => {
  it('measures the tilt from both axes', () => {
    expect(tiltAngle(3, 4)).toBe(5);
    expect(tiltAngle(null, null)).toBe(0);
  });

  it('reads money typed with dots, letters or nothing', () => {
    expect(parseMoney('12.500đ')).toBe(12500);
    expect(parseMoney('  ')).toBeNull();
  });

  it('applies the cash keypad', () => {
    expect(pressKey(0, '0')).toBe(0);
    expect(pressKey(5, '000')).toBe(5000);
    expect(pressKey(5000, '⌫')).toBe(500);
    expect(pressKey(5, '⌫')).toBe(0);
    expect(pressKey(999_999_99, '9999')).toBe(MAX_CASH); // capped below the API's ceiling
  });

  it('groups the history by local day with the paid totals only', () => {
    const now = new Date(2026, 9, 4, 15, 0);
    const days = groupByDay(
      [
        sale(3, new Date(2026, 9, 4, 9, 0), 7000),
        sale(2, new Date(2026, 9, 4, 8, 0), 9000, 'void'),
        sale(1, new Date(2026, 9, 3, 20, 0), 5000),
        sale(0, new Date(2026, 8, 30, 10, 0), 1000),
      ],
      now,
    );
    expect(days.map((d) => [d.label, d.count, d.total, d.items.length])).toEqual([
      ['Hôm nay', 1, 7000, 2],
      ['Hôm qua', 1, 5000, 1],
      ['30/09/2026', 1, 1000, 1],
    ]);
  });
});
