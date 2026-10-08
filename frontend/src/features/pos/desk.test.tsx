import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Job, Order, OrderItem } from '@/features/pos/types';
import { clearToken, setToken } from '@/lib/auth-token';
import { jsonResponse, meBody, renderApp, stubFetchRoutes } from '@/test/test-utils';

function item(id: number, overrides: Partial<OrderItem> = {}): OrderItem {
  return {
    id,
    product_id: String(id),
    product_name: `Sản phẩm ${id}`,
    quantity: 1,
    unit_price: 10000,
    price_missing: false,
    manual_price: false,
    line_total: 10000,
    flagged: false,
    thumbnail_url: null,
    evidence: null,
    ...overrides,
  };
}

function order(overrides: Partial<Order> = {}): Order {
  const items = overrides.items ?? [];
  return {
    id: 5,
    status: 'open',
    created_at: '2026-10-04T02:00:00+00:00',
    paid_at: null,
    payment_method: null,
    cash_given: null,
    change_given: null,
    items,
    captures: [],
    item_count: items.reduce((n, i) => n + i.quantity, 0),
    total: items.reduce((n, i) => n + (i.line_total ?? 0), 0),
    missing_price_count: items.filter((i) => i.price_missing).length,
    flagged_count: items.filter((i) => i.flagged).length,
    ...overrides,
  };
}

/** Records the method and JSON body of each call, answers with `respond`. */
function recording(calls: { method: string; body: unknown }[], respond: () => Response) {
  return (init?: RequestInit) => {
    calls.push({ method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    return respond();
  };
}

/** The window is as wide as a desktop (`true`) or a phone. */
function windowWide(wide: boolean) {
  vi.stubGlobal('matchMedia', (media: string) => ({
    matches: wide,
    media,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

const staffMe = () => jsonResponse(meBody('staff'));

beforeEach(() => {
  setToken('t');
  windowWide(true);
});
afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

describe('counter screen (wide window)', () => {
  it('recognises a photo and fills the basket next to the camera, without leaving the screen', async () => {
    const recognised = order({
      items: [item(11, { quantity: 2, line_total: 20000 }), item(12, { flagged: true })],
      captures: [
        {
          id: 1,
          created_at: '2026-10-04T02:01:00+00:00',
          image_url: '/api/media/5/capture_a.jpg?exp=1&sig=x',
          width: 320,
          height: 240,
          boxes: [
            { item_id: 11, product_id: '11', status: 'accepted', bbox: [10, 20, 100, 120] },
            { item_id: 12, product_id: '12', status: 'uncertain', bbox: [210, 20, 300, 120] },
          ],
        },
      ],
    });
    const jobs: Job[] = [
      { status: 'processing', position: 0, system_reloading: false },
      { status: 'done', position: 0, system_reloading: false, added: 3, warnings: [], order: recognised },
    ];
    stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/open': () => jsonResponse({ order: null }),
      '/api/orders/5/captures': () => jsonResponse({ job_id: 9, duplicate: false }, { status: 202 }),
      '/api/orders/5': () => jsonResponse(recognised),
      '/api/orders': () => jsonResponse(order()),
      '/api/jobs/9': () => jsonResponse(jobs.length > 1 ? jobs.shift() : jobs[0]),
    });
    const { router } = await renderApp('/pos');

    // jsdom has no camera: the screen says so and still takes a photo file
    expect(await screen.findByText(/kéo thả ảnh vào đây, bấm "Chọn ảnh"/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Đơn mới' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Thanh toán/ })).toBeDisabled();
    const photo = new File([new Uint8Array([0xff, 0xd8, 0xff])], 'ro.jpg', { type: 'image/jpeg' });
    await userEvent.upload(screen.getByLabelText('Ảnh từ máy'), photo);

    expect(await screen.findByText(/Đang nhận diện sản phẩm/)).toBeInTheDocument();
    expect(await screen.findByTestId('line-11', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/pos/orders/5');
    expect(screen.getByRole('heading', { name: 'Đơn #5' })).toBeInTheDocument();
    expect(screen.getByTestId('order-total')).toHaveTextContent('30.000đ');
    expect(screen.getByText(/Có 1 dòng cần xác nhận/)).toBeInTheDocument();
    // the photo with its boxes replaces the camera, which stays in a corner
    expect((await screen.findAllByTestId('capture-box')).map((b) => b.dataset.state)).toEqual(['ok', 'unc']);
    expect(screen.getByRole('button', { name: /Ảnh đã chụp \(1\)/ })).toBeInTheDocument();
    // the small camera in the corner can be hidden so it does not cover the boxes, and shown again
    await userEvent.click(screen.getByRole('button', { name: 'Ẩn camera' }));
    expect(screen.queryByRole('button', { name: 'Ẩn camera' })).toBeNull();
    expect(screen.getAllByTestId('capture-box')).toHaveLength(2); // still on the photo
    await userEvent.click(screen.getByRole('button', { name: /Hiện camera/ }));
    expect(screen.getByRole('button', { name: 'Ẩn camera' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^Camera$/ }));
    expect(screen.queryAllByTestId('capture-box')).toEqual([]);
  });

  it('F4 adds a product by name before any photo, starting the order', async () => {
    const calls: { method: string; body: unknown }[] = [];
    const withLine = order({ items: [item(20)] });
    stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/open': () => jsonResponse({ order: null }),
      '/api/catalog/products?search=': () =>
        jsonResponse({ items: [{ id: '20', name: 'Son dưỡng', barcode: '', price: 10000, needs_naming: false, missing_color_reference: false }] }),
      '/api/orders/5/items': recording(calls, () => jsonResponse(withLine)),
      '/api/orders/5': () => jsonResponse(withLine),
      '/api/orders': recording(calls, () => jsonResponse(order())),
    });
    const { router } = await renderApp('/pos');
    await screen.findByRole('heading', { name: 'Đơn mới' });

    fireEvent.keyDown(document.body, { key: 'F4' });
    const picker = await screen.findByRole('dialog', { name: 'Thêm sản phẩm' });
    await userEvent.click(await within(picker).findByRole('button', { name: /Son dưỡng/ }));

    expect(await screen.findByTestId('line-20')).toBeInTheDocument();
    expect(calls).toEqual([
      { method: 'POST', body: undefined },
      { method: 'POST', body: { product_id: '20', quantity: 1 } },
    ]);
    expect(router.state.location.pathname).toBe('/pos/orders/5');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('F2 goes to payment, where the amount is typed on the keyboard and Enter pays', async () => {
    const calls: { method: string; body: unknown }[] = [];
    const due = order({ items: [item(12, { quantity: 4, line_total: 47000 })], total: 47000 });
    let current: Order = due;
    stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/5/checkout': recording(calls, () => {
        current = { ...due, status: 'paid', payment_method: 'cash', cash_given: 50000, change_given: 3000 };
        return jsonResponse(current);
      }),
      '/api/orders/5': () => jsonResponse(current),
    });
    const { router } = await renderApp('/pos/orders/5');
    await screen.findByTestId('line-12');

    fireEvent.keyDown(document.body, { key: 'F2' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/pos/orders/5/pay'));
    await screen.findByTestId('pay-total');
    for (const key of '50000') fireEvent.keyDown(document.body, { key });
    fireEvent.keyDown(document.body, { key: 'Enter' }); // right after the digits: a barcode scanner, not a person
    expect(calls).toEqual([]);
    expect(screen.getByTestId('cash-change')).toHaveTextContent('3.000đ');

    await new Promise((resolve) => setTimeout(resolve, 80));
    fireEvent.keyDown(document.body, { key: 'Enter' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/pos/orders/5/done'));
    expect(calls).toEqual([{ method: 'POST', body: { method: 'cash', cash_given: 50000 } }]);
  });

  it('a narrow window keeps the phone flow', async () => {
    windowWide(false);
    stubFetchRoutes({ '/api/me': staffMe, '/api/orders/open': () => jsonResponse({ order: null }) });
    await renderApp('/pos');
    expect(await screen.findByRole('heading', { name: 'Chụp rổ hàng' })).toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: 'Giỏ hàng' })).toBeNull();
  });
});
