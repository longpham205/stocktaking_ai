import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Job, Order, OrderItem } from '@/features/pos/types';
import { clearToken, getToken, setToken } from '@/lib/auth-token';
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

/** A route stub that answers by HTTP method and records the JSON bodies it received. */
function byMethod(handlers: Partial<Record<string, (body: unknown) => Response>>, calls: { method: string; body: unknown }[] = []) {
  return (init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body;
    calls.push({ method, body });
    const handler = handlers[method];
    if (!handler) throw new Error(`unexpected ${method}`);
    return handler(body);
  };
}

const staffMe = () => jsonResponse(meBody('staff'));

beforeEach(() => setToken('t'));
afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

describe('capture -> recognition -> invoice', () => {
  it('sends a picked photo with an idempotency key, waits for the job, then shows lines, boxes and warnings', async () => {
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
            { item_id: 11, product_id: '11', status: 'accepted', bbox: [110, 20, 200, 120] },
            { item_id: 12, product_id: '12', status: 'uncertain', bbox: [210, 20, 300, 120] },
            { item_id: null, product_id: null, status: 'rejected', bbox: [10, 150, 60, 230] },
          ],
        },
      ],
    });
    const jobs: Job[] = [
      { status: 'queued', position: 1, system_reloading: false },
      {
        status: 'done',
        position: 0,
        system_reloading: false,
        added: 3,
        warnings: [{ type: 'overlap_detected' }, { type: 'unrecognized_objects', count: 1 }],
        order: recognised,
      },
    ];
    const fetchMock = stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/open': () => jsonResponse({ order: null }),
      '/api/orders': byMethod({ POST: () => jsonResponse(order()) }),
      '/api/orders/5/captures': () => jsonResponse({ job_id: 9, duplicate: false }, { status: 202 }),
      '/api/jobs/9': () => jsonResponse(jobs.length > 1 ? jobs.shift() : jobs[0]),
      '/api/orders/5': () => jsonResponse(recognised),
    });
    const { router } = await renderApp('/pos');
    const photo = new File([new Uint8Array([0xff, 0xd8, 0xff])], 'ro.jpg', { type: 'image/jpeg' });
    await userEvent.upload(screen.getByLabelText('Ảnh từ thư viện'), photo);

    expect(await screen.findByText(/đứng thứ 2 trong hàng chờ/)).toBeInTheDocument();
    expect(await screen.findByText('Đơn #5', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/pos/orders/5');
    const upload = fetchMock.mock.calls.find(([url]) => String(url) === '/api/orders/5/captures')![1]!;
    expect((upload.headers as Record<string, string>)['Idempotency-Key']).toMatch(/.+/);
    expect(upload.body).toBeInstanceOf(Blob);

    expect(screen.getByText(/chồng lên nhau/)).toBeInTheDocument();
    expect(screen.getByText(/Phát hiện thêm 1 vật chưa nhận diện/)).toBeInTheDocument();
    expect(screen.getByText(/Có 1 dòng cần xác nhận/)).toBeInTheDocument();
    expect(screen.getAllByTestId('capture-box').map((b) => b.dataset.state)).toEqual(['ok', 'ok', 'unc', 'rej']);
    expect(screen.getByTestId('order-total')).toHaveTextContent('30.000đ');

    // a box and its line are highlighted together
    const focused = () => screen.getAllByTestId('capture-box').map((b) => b.dataset.focused === 'true');
    fireEvent.click(screen.getAllByTestId('capture-box')[0]);
    expect(screen.getByTestId('line-11').className).toMatch(/ring-2/);
    expect(focused()).toEqual([true, true, false, false]); // both objects of the line
    fireEvent.click(screen.getAllByTestId('capture-box')[0]);
    expect(focused()).toEqual([false, false, false, false]); // the same box again clears the mark

    // and the other way: tapping a line marks its boxes on the photo, tapping it again clears them
    fireEvent.click(screen.getByTestId('line-11'));
    expect(focused()).toEqual([true, true, false, false]);
    expect(screen.getByTestId('line-11').className).toMatch(/ring-2/);
    expect(screen.getAllByTestId('capture-box')[2].className).toMatch(/opacity-35/); // the others fade
    fireEvent.click(screen.getByTestId('line-11'));
    expect(focused()).toEqual([false, false, false, false]);
    expect(screen.getByTestId('line-11').className).not.toMatch(/ring-2/);
  });

  it('a marked line switches the photo to the capture that shows it', async () => {
    const box = (itemId: number) => ({ item_id: itemId, product_id: String(itemId), status: 'accepted' as const, bbox: [10, 20, 100, 120] as [number, number, number, number] });
    const photo = (id: number, itemId: number) => ({
      id,
      created_at: '2026-10-04T02:01:00+00:00',
      image_url: `/api/media/5/capture_${id}.jpg?exp=1&sig=x`,
      width: 320,
      height: 240,
      boxes: [box(itemId)],
    });
    const twoPhotos = order({ items: [item(11), item(12)], captures: [photo(1, 11), photo(2, 12)] });
    stubFetchRoutes({ '/api/me': staffMe, '/api/orders/open': () => jsonResponse({ order: twoPhotos }), '/api/orders/5': () => jsonResponse(twoPhotos) });
    await renderApp('/pos/orders/5');

    const shown = () => screen.getAllByTestId('capture-box').map((b) => `${b.getAttribute('aria-label')}:${b.dataset.focused ?? 'false'}`);
    await screen.findByTestId('line-11');
    expect(shown()).toEqual([`${item(12).product_name}:false`]); // the latest photo by default
    fireEvent.click(screen.getByTestId('line-11')); // a line of the first photo
    expect(shown()).toEqual([`${item(11).product_name}:true`]);
  });
});

describe('invoice', () => {
  function stubInvoice(current: Order, calls: { method: string; body: unknown }[]) {
    return stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/5': () => jsonResponse(current),
      '/api/orders/5/items/12': byMethod(
        {
          PATCH: () => jsonResponse(order({ items: [item(12)] })),
          DELETE: () => jsonResponse(order()),
        },
        calls,
      ),
      '/api/orders/5/items/13': byMethod({ PATCH: () => jsonResponse(order({ items: [item(13, { manual_price: true })] })) }, calls),
    });
  }

  it('confirms a flagged line as the recognizer guessed it', async () => {
    const calls: { method: string; body: unknown }[] = [];
    stubInvoice(order({ items: [item(12, { flagged: true })] }), calls);
    await renderApp('/pos/orders/5');
    fireEvent.click(await screen.findByTestId('line-12'));
    const picker = await screen.findByRole('dialog', { name: 'Xác nhận sản phẩm' });
    await userEvent.click(within(picker).getByRole('button', { name: /Đúng, giữ nguyên/ }));
    await waitFor(() => expect(calls).toContainEqual({ method: 'PATCH', body: { confirm: true } }));
  });

  it('takes a typed price for a line without one, and blocks payment until then', async () => {
    const calls: { method: string; body: unknown }[] = [];
    stubInvoice(order({ items: [item(13, { unit_price: null, price_missing: true, line_total: null })] }), calls);
    const { router } = await renderApp('/pos/orders/5');
    const pay = await screen.findByRole('button', { name: 'Nhập giá cho 1 món' });
    await userEvent.click(pay);
    expect(router.state.location.pathname).toBe('/pos/orders/5'); // not to payment
    const price = screen.getByLabelText('Giá Sản phẩm 13');
    expect(price).toHaveFocus();
    await userEvent.type(price, '12.000{Enter}');
    await waitFor(() => expect(calls).toContainEqual({ method: 'PATCH', body: { manual_price: 12000 } }));
  });

  it('asks before removing a line whose quantity goes to zero', async () => {
    const calls: { method: string; body: unknown }[] = [];
    stubInvoice(order({ items: [item(12)] }), calls);
    await renderApp('/pos/orders/5');
    await userEvent.click(await screen.findByRole('button', { name: 'Giảm' }));
    const ask = await screen.findByRole('dialog', { name: 'Xác nhận' });
    expect(ask).toHaveTextContent('Xoá "Sản phẩm 12" khỏi đơn?');
    await userEvent.click(within(ask).getByRole('button', { name: 'Xoá' }));
    await waitFor(() => expect(calls.map((c) => c.method)).toContain('DELETE'));
  });

  it('adds what a barcode scanner types', async () => {
    const calls: { method: string; body: unknown }[] = [];
    stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/5': () => jsonResponse(order({ items: [item(12)] })),
      '/api/catalog/products?barcode=8931000009999': () =>
        jsonResponse({ items: [{ id: '20', name: 'Son dưỡng', barcode: '8931000009999', price: 5000 }] }),
      '/api/orders/5/items': byMethod({ POST: () => jsonResponse(order({ items: [item(12), item(20)] })) }, calls),
    });
    await renderApp('/pos/orders/5');
    await screen.findByTestId('line-12');
    for (const key of '8931000009999') fireEvent.keyDown(document.body, { key });
    fireEvent.keyDown(document.body, { key: 'Enter' });
    await waitFor(() => expect(calls).toContainEqual({ method: 'POST', body: { product_id: '20', quantity: 1 } }));
    expect(await screen.findByTestId('line-20')).toBeInTheDocument();
  });
});

describe('payment', () => {
  it('computes the change and pays in cash', async () => {
    const calls: { method: string; body: unknown }[] = [];
    const due = order({ items: [item(12, { quantity: 4, line_total: 47000 })], total: 47000 });
    const paid = { ...due, status: 'paid' as const, payment_method: 'cash' as const, cash_given: 50000, change_given: 3000 };
    let current: Order = due;
    stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/5': () => jsonResponse(current),
      '/api/orders/5/checkout': byMethod(
        {
          POST: () => {
            current = paid;
            return jsonResponse(paid);
          },
        },
        calls,
      ),
    });
    const { router } = await renderApp('/pos/orders/5/pay');
    const confirm = await screen.findByRole('button', { name: 'Xác nhận thanh toán' });
    expect(confirm).toBeDisabled(); // nothing handed over yet
    expect(screen.getByTestId('cash-change')).toHaveTextContent('Còn thiếu 47.000đ');
    await userEvent.click(screen.getByRole('button', { name: '50k' }));
    expect(screen.getByTestId('cash-change')).toHaveTextContent('3.000đ');
    await userEvent.click(confirm);
    await waitFor(() => expect(router.state.location.pathname).toBe('/pos/orders/5/done'));
    expect(calls).toEqual([{ method: 'POST', body: { method: 'cash', cash_given: 50000 } }]);
    expect(await screen.findByTestId('done-change')).toHaveTextContent('3.000đ');
  });
});

describe('entering the POS', () => {
  it('resumes an unfinished order', async () => {
    stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/open': () => jsonResponse({ order: order({ id: 8, items: [item(12)] }) }),
      '/api/orders/8': () => jsonResponse(order({ id: 8, items: [item(12)] })),
    });
    const { router } = await renderApp('/pos');
    expect(router.state.location.pathname).toBe('/pos/orders/8');
    expect(await screen.findByText('Đơn #8')).toBeInTheDocument();
  });

  it('shows the onboarding once to a new cashier', async () => {
    const calls: { method: string; body: unknown }[] = [];
    const me = meBody('staff');
    me.user.has_seen_onboarding = false;
    stubFetchRoutes({
      '/api/me/onboarding-seen': byMethod({ POST: () => jsonResponse({ ok: true }) }, calls),
      '/api/me': () => jsonResponse(me),
      '/api/orders/open': () => jsonResponse({ order: null }),
    });
    const { router } = await renderApp('/pos');
    expect(router.state.location.pathname).toBe('/onboarding');
    expect(await screen.findByText('Đặt hàng vào khung')).toBeInTheDocument();
    await waitFor(() => expect(calls.map((c) => c.method)).toEqual(['POST']));
    await userEvent.click(screen.getByRole('button', { name: 'Bỏ qua' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/pos')); // seen: not shown again
  });

  it('asks before logging out with an unpaid order, and voids it', async () => {
    const calls: { method: string; body: unknown }[] = [];
    stubFetchRoutes({
      '/api/me': staffMe,
      '/api/orders/open': () => jsonResponse({ order: order({ items: [item(12)] }) }),
      '/api/orders/5/void': byMethod({ POST: () => jsonResponse(order({ status: 'void' })) }, calls),
      '/api/orders/5': () => jsonResponse(order({ items: [item(12)] })),
      '/api/auth/logout': byMethod({ POST: () => jsonResponse({ ok: true }) }, calls),
    });
    await renderApp('/pos/orders/5');
    await userEvent.click(await screen.findByRole('button', { name: 'Đăng xuất' }));
    const ask = await screen.findByRole('dialog', { name: 'Xác nhận' });
    await userEvent.click(within(ask).getByRole('button', { name: 'Huỷ đơn và đóng ca' }));
    await waitFor(() => expect(calls.map((c) => c.method)).toEqual(['POST', 'POST']));
    expect(getToken()).toBeNull();
  });
});

describe('history', () => {
  it('groups the sales by day and lets an admin void one', async () => {
    const calls: { method: string; body: unknown }[] = [];
    const today = new Date().toISOString();
    stubFetchRoutes({
      '/api/me': () => jsonResponse(meBody('admin')),
      '/api/history?range=today': () =>
        jsonResponse({
          items: [
            { id: 7, status: 'paid', created_at: today, paid_at: today, total: 47000, item_count: 4, payment_method: 'cash', cashier: 'A' },
            { id: 6, status: 'void', created_at: today, paid_at: null, total: 0, item_count: 1, payment_method: null, cashier: 'A' },
          ],
        }),
      '/api/orders/7/void': byMethod({ POST: () => jsonResponse(order({ id: 7, status: 'void' })) }, calls),
      '/api/orders/7': () => jsonResponse(order({ id: 7, status: 'paid', items: [item(12)] })),
    });
    await renderApp('/history');
    const row = await screen.findByRole('button', { name: /#7/ });
    expect(screen.getByText('Hôm nay', { selector: 'span' })).toBeInTheDocument();
    // the day's count and total leave the voided order out
    expect(screen.getByText((_, el) => el?.tagName === 'SPAN' && el.textContent === '1 đơn · 47.000đ')).toBeInTheDocument();
    await userEvent.click(row);
    const detail = await screen.findByRole('dialog', { name: 'Đơn #7' });
    await userEvent.click(await within(detail).findByRole('button', { name: 'Huỷ đơn này' }));
    const ask = await screen.findByRole('dialog', { name: 'Xác nhận' });
    await userEvent.click(within(ask).getByRole('button', { name: 'Huỷ đơn' }));
    await waitFor(() => expect(calls).toEqual([{ method: 'POST', body: undefined }]));
  });
});
