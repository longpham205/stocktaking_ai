import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AdminProducts, EvidenceView } from '@/features/admin-products/api';
import { averageHex, imagePoint, parseList, percent, showValue, versusBaseline } from '@/features/admin-products/lib';
import type { Product } from '@/features/pos/types';
import { clearToken, setToken } from '@/lib/auth-token';
import { jsonResponse, meBody, renderApp, stubFetchRoutes } from '@/test/test-utils';

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** Records every non-GET call (url, method, JSON body) and answers by method. */
function recording(calls: Call[], url: string, handlers: Partial<Record<string, (body: unknown) => Response>>) {
  return (init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body;
    if (method !== 'GET') calls.push({ url, method, body });
    const handler = handlers[method];
    if (!handler) throw new Error(`unexpected ${method} ${url}`);
    return handler(body);
  };
}

const product = (id: string, overrides: Partial<Product> = {}): Product => ({
  id,
  name: `Sản phẩm ${id}`,
  barcode: '',
  price: null,
  stock: null,
  needs_naming: false,
  missing_color_reference: false,
  ...overrides,
});

const listing = (items: Product[], overrides: Partial<AdminProducts> = {}): AdminProducts => ({
  total: items.length,
  page: 1,
  size: 50,
  items,
  missing_price: 8,
  missing_barcode: 38,
  needs_naming: 1,
  out_of_stock: 0,
  ...overrides,
});

const adminMe = () => jsonResponse(meBody('admin'));

beforeEach(() => setToken('t'));
afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

describe('admin product helpers', () => {
  it('splits a typed list on commas and spaces', () => {
    expect(parseList(' ABA, 100g  OR210,')).toEqual(['ABA', '100g', 'OR210']);
    expect(parseList('  ')).toEqual([]);
  });

  it('shows change-log values as a person reads them', () => {
    expect(showValue('price', null)).toBe('chưa có');
    expect(showValue('price', '15000')).toBe('15.000đ');
    expect(showValue('stock', null)).toBe('không theo dõi');
    expect(showValue('stock', '42')).toBe('42');
    expect(showValue('barcode', '')).toBe('—');
    expect(showValue('ocr_keywords', '["ABA", "KT42"]')).toBe('ABA, KT42');
    expect(showValue('color_code', '"BE203"')).toBe('BE203');
    expect(showValue('confusable_with', null)).toBe('—');
    expect(showValue('name', 'Kem ABA')).toBe('Kem ABA');
  });

  it('averages the pixels around a tap and maps the tap to image pixels', () => {
    expect(averageHex([255, 0, 0, 255, 0, 0, 255, 255])).toBe('#800080');
    expect(imagePoint({ left: 10, top: 20, width: 100, height: 50 }, { width: 1000, height: 500 }, 60, 45)).toEqual({ x: 500, y: 250 });
  });

  it('compares a validation metric with the baseline in points', () => {
    expect(percent(0.5912)).toBe('59.12%');
    expect(percent(null)).toBe('—');
    expect(versusBaseline(0.6, 0.5912)).toBe('+0.88 điểm so với baseline');
    expect(versusBaseline(0.58, 0.5912)).toBe('-1.12 điểm so với baseline');
    expect(versusBaseline(null, 0.5)).toBe('');
  });
});

describe('admin products', () => {
  it('searches, filters, and saves a row with the name only when it changed', async () => {
    const calls: Call[] = [];
    const urls: string[] = [];
    const first = product('2', { name: 'Bút chì', barcode: '8931', price: 15000, missing_color_reference: true });
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/products?': () => {
        return jsonResponse(listing([first, product('10', { needs_naming: true })]));
      },
      '/api/admin/products/2': recording(calls, 'products/2', { PATCH: () => jsonResponse(first) }),
    });
    const fetchSpy = vi.mocked(fetch);
    await renderApp('/admin/products');
    const row = await screen.findByTestId('product-2');
    expect(within(row).getByText('thiếu màu tham chiếu')).toBeInTheDocument();
    expect(within(screen.getByTestId('product-10')).getByText('chưa đặt tên')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thiếu giá (8)' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Thiếu barcode (38)' }));
    await userEvent.type(screen.getByLabelText('Tìm sản phẩm'), 'chan may');
    await waitFor(() => {
      urls.splice(0, urls.length, ...fetchSpy.mock.calls.map(([url]) => String(url)));
      expect(urls).toContain('/api/admin/products?page=1&filter=missing_barcode&search=chan%20may');
    });
    // typing asks the server once it pauses, not at every key
    expect(urls.filter((url) => url.includes('search=chan')).length).toBe(1);

    const price = within(screen.getByTestId('product-2')).getByLabelText('Giá 2');
    await userEvent.clear(price);
    await userEvent.type(price, '17.500');
    await userEvent.click(within(screen.getByTestId('product-2')).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls).toEqual([{ url: 'products/2', method: 'PATCH', body: { price: 17500, barcode: '8931' } }]));

    const name = within(screen.getByTestId('product-2')).getByLabelText('Tên 2');
    await userEvent.clear(name);
    await userEvent.type(name, 'Bút chì chân mày');
    await userEvent.click(within(screen.getByTestId('product-2')).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls[1]?.body).toEqual({ price: 17500, barcode: '8931', name: 'Bút chì chân mày' }));
  });

  it('shows the stock on hand and sends it only when a new count was typed', async () => {
    const calls: Call[] = [];
    const oversold = product('2', { barcode: '8931', price: 15000, stock: -2 });
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/products?': () => jsonResponse(listing([oversold, product('3', { stock: 0 }), product('10')], { out_of_stock: 2 })),
      '/api/admin/products/2': recording(calls, 'products/2', { PATCH: () => jsonResponse(oversold) }),
      '/api/admin/products/10': recording(calls, 'products/10', { PATCH: () => jsonResponse(product('10')) }),
    });
    await renderApp('/admin/products');
    const row = await screen.findByTestId('product-2');
    expect(within(row).getByText('bán vượt tồn')).toBeInTheDocument();
    expect(within(screen.getByTestId('product-3')).getByText('hết hàng')).toBeInTheDocument();
    expect(within(screen.getByTestId('product-10')).getByLabelText('Tồn kho 10')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Hết hàng (2)' })).toBeInTheDocument();

    // saved without touching the stock: a sale may have moved it since the page loaded
    await userEvent.click(within(row).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls[0]?.body).toEqual({ price: 15000, barcode: '8931' }));

    const stock = within(row).getByLabelText('Tồn kho 2');
    await userEvent.clear(stock);
    await userEvent.type(stock, '40');
    await userEvent.click(within(row).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls[1]?.body).toEqual({ price: 15000, barcode: '8931', stock: 40 }));
  });

  it('saves evidence only once confirmed: the colour reference first, then the evidence', async () => {
    const calls: Call[] = [];
    const view: EvidenceView = {
      product: product('7', { name: 'Kem ABA' }),
      evidence: { ocr_keywords: ['ABA'], color_code: 'BE203', force_evidence: ['ocr'], confusable_with: ['8'] },
      colors: [{ code: 'BE203', hex: null, used_by: ['7'], missing: true }],
      confirm_text: 'Tôi hiểu thay đổi này ảnh hưởng độ chính xác AI',
      ocr_min_length: 3,
      gallery: [],
      warnings: [],
    };
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/products?': () => jsonResponse(listing([product('7', { name: 'Kem ABA' })])),
      '/api/admin/colors/BE203': recording(calls, 'colors/BE203', { PATCH: () => jsonResponse(view.colors[0]) }),
      '/api/admin/products/7/evidence': recording(calls, 'evidence', {
        GET: () => jsonResponse(view),
        PATCH: () => jsonResponse({ ...view, warnings: ['SKU 7: cảnh báo'] }),
      }),
    });
    await renderApp('/admin/products');
    await userEvent.click(await screen.findByRole('button', { name: 'Bằng chứng 7' }));
    const dialog = await screen.findByRole('dialog', { name: /Bằng chứng nhận diện · SKU 7 — Kem ABA/ });
    expect(await within(dialog).findByText(/chưa có màu tham chiếu/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Từ khoá OCR/)).toHaveValue('ABA');
    const save = within(dialog).getByRole('button', { name: 'Lưu bằng chứng' });
    expect(save).toBeDisabled(); // not confirmed yet

    await userEvent.type(within(dialog).getByLabelText(/Từ khoá OCR/), ', kt42');
    await userEvent.type(within(dialog).getByLabelText(/Màu tham chiếu/), '#6d3f3e');
    await userEvent.click(within(dialog).getByLabelText('Màu'));
    await userEvent.clear(within(dialog).getByLabelText(/Dễ nhầm với SKU/));
    await userEvent.click(within(dialog).getByLabelText(view.confirm_text));
    await userEvent.click(save);
    await waitFor(() =>
      expect(calls).toEqual([
        { url: 'colors/BE203', method: 'PATCH', body: { hex: '#6d3f3e', confirm: true } },
        {
          url: 'evidence',
          method: 'PATCH',
          body: { ocr_keywords: ['ABA', 'kt42'], color_code: 'BE203', force_evidence: ['ocr', 'color'], confusable_with: [], confirm: true },
        },
      ]),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('shows the gallery photos of a product, one enlarged at a time', async () => {
    const photos = ['/api/gallery/7/0?exp=1&sig=a', '/api/gallery/7/1?exp=1&sig=b'];
    const view = (gallery: string[]): EvidenceView => ({
      product: product('7', { name: 'Kem ABA' }),
      evidence: { ocr_keywords: [], color_code: null, force_evidence: [], confusable_with: [] },
      colors: [],
      confirm_text: 'x',
      ocr_min_length: 3,
      gallery,
      warnings: [],
    });
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/products?': () => jsonResponse(listing([product('7', { name: 'Kem ABA' }), product('8')])),
      '/api/admin/products/7/evidence': () => jsonResponse(view(photos)),
      '/api/admin/products/8/evidence': () => jsonResponse(view([])),
    });
    await renderApp('/admin/products');
    await userEvent.click(await screen.findByRole('button', { name: 'Ảnh 7' }));
    const dialog = await screen.findByRole('dialog', { name: 'Ảnh gallery · SKU 7 — Kem ABA' });
    expect(await within(dialog).findByAltText('Ảnh 1 của SKU 7')).toHaveAttribute('src', photos[0]);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Xem ảnh 2' }));
    expect(within(dialog).getByAltText('Ảnh 2 của SKU 7')).toHaveAttribute('src', photos[1]);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Đóng' }));

    await userEvent.click(screen.getByRole('button', { name: 'Ảnh 8' }));
    expect(await screen.findByText(/chưa có ảnh gallery/)).toBeInTheDocument();
  });

  it('shows the history of a product, evidence included, and reverts an entry after asking', async () => {
    const calls: Call[] = [];
    const at = '2026-10-04T03:00:00+00:00';
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/products?': () => jsonResponse(listing([product('2')])),
      '/api/admin/change-log?table=product&record=2': () =>
        jsonResponse({ items: [{ id: 5, table: 'product', record_id: '2', field: 'price', old: null, new: '15000', by: 'admin', at, name: 'x' }] }),
      '/api/admin/change-log?table=product_evidence&record=2': () =>
        jsonResponse({
          items: [{ id: 9, table: 'product_evidence', record_id: '2', field: 'ocr_keywords', old: null, new: '["ABA"]', by: 'admin', at, name: 'x' }],
        }),
      '/api/admin/change-log/5/revert': recording(calls, 'revert/5', { POST: () => jsonResponse({ ok: true }) }),
    });
    await renderApp('/admin/products');
    await userEvent.click(await screen.findByRole('button', { name: 'Lịch sử 2' }));
    const dialog = await screen.findByRole('dialog', { name: 'Lịch sử thay đổi · SKU 2' });
    const entries = await within(dialog).findAllByTestId('change-entry');
    expect(entries.map((entry) => entry.textContent)).toEqual([
      expect.stringContaining('Từ khoá OCR: — → ABA'), // newest first, whichever table it is in
      expect.stringContaining('Giá: chưa có → 15.000đ'),
    ]);
    await userEvent.click(within(entries[1]).getByRole('button', { name: 'Hoàn tác' }));
    const ask = await screen.findByRole('dialog', { name: 'Xác nhận' });
    await userEvent.click(within(ask).getByRole('button', { name: 'Hoàn tác' }));
    await waitFor(() => expect(calls).toEqual([{ url: 'revert/5', method: 'POST', body: {} }]));
  });

  it('runs an evidence test and reports what each plugin read', async () => {
    const calls: Call[] = [];
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/products?': () => jsonResponse(listing([product('7')])),
      '/api/admin/evidence-test': recording(calls, 'evidence-test', {
        POST: () =>
          jsonResponse({
            detected_count: 2,
            processing_time_ms: 50.4,
            items: [
              {
                product_id: '7',
                name: 'Kem ABA',
                status: 'uncertain',
                bbox: [10, 10, 100, 120],
                ocr_text: 'hop ngoai ABA 200g',
                ocr_keyword_hits: ['7'],
                color_hex: '#C6A096',
                color_nearest: { code: 'BE203', rgb_distance: 2.0 },
                barcodes: ['8931000001372'],
                barcode_skus: [],
                plugins: ['barcode', 'color', 'ocr'],
              },
            ],
          }),
      }),
    });
    await renderApp('/admin/products');
    await userEvent.click(await screen.findByRole('button', { name: /Thử bằng chứng/ }));
    const dialog = await screen.findByRole('dialog', { name: '🧪 Thử bằng chứng' });
    const photo = new File([new Uint8Array([0xff, 0xd8])], 'thu.jpg', { type: 'image/jpeg' });
    await userEvent.upload(within(dialog).getByLabelText('Ảnh để thử'), photo);
    const tested = await within(dialog).findByTestId('tested-object');
    expect(calls[0].body).toBeInstanceOf(Blob);
    expect(within(dialog).getByText(/1 vật nhận ra \/ 2 vật phát hiện · 50 ms/)).toBeInTheDocument();
    expect(tested).toHaveTextContent('Kem ABA (SKU 7) · 🟨 cần xác nhận');
    expect(tested).toHaveTextContent('OCR đọc: hop ngoai ABA 200g → khớp từ khoá của SKU 7');
    expect(tested).toHaveTextContent('#C6A096 · gần nhất BE203 (khoảng cách RGB 2)');
    expect(tested).toHaveTextContent('8931000001372 → không SKU nào có mã này');
  });
});
