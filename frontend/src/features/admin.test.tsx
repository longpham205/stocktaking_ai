import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Report } from '@/features/admin-reports/reports-page';
import { clearToken, setToken } from '@/lib/auth-token';
import { apiError, jsonResponse, meBody, renderApp, stubFetchRoutes } from '@/test/test-utils';

const adminMe = () => jsonResponse(meBody('admin'));

interface Call {
  method: string;
  body: unknown;
}

/** Answers by HTTP method and records the JSON bodies received. */
function byMethod(handlers: Partial<Record<string, (body: unknown) => Response>>, calls: Call[]) {
  return (init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    if (method !== 'GET') calls.push({ method, body });
    const handler = handlers[method];
    if (!handler) throw new Error(`unexpected ${method}`);
    return handler(body);
  };
}

function report(overrides: Partial<Report> = {}): Report {
  return {
    range: '7d',
    range_orders: 3,
    range_revenue: 47000,
    daily: Array.from({ length: 7 }, (_, k) => ({
      date: `2026-10-0${k + 1}`,
      orders: k === 6 ? 3 : 0,
      revenue: k === 6 ? 47000 : 0,
    })),
    top_products: [{ product_id: '8', name: 'Sữa rửa mặt', quantity: 5, revenue: 5000 }],
    orders_today: 3,
    revenue_today: 47000,
    captures_today: 4,
    error_rate: 0.25,
    avg_processing_ms: 200.4,
    active_shifts: 1,
    active_staff: 2,
    queue_size: 0,
    products_total: 50,
    products_missing_price: 8,
    products_missing_barcode: 38,
    ...overrides,
  };
}

beforeEach(() => setToken('t'));
afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

describe('admin reports', () => {
  it('shows today, the range, a bar per day and the best sellers', async () => {
    const fetchMock = stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/reports?range=7d': () => jsonResponse(report()),
      '/api/admin/reports?range=30d': () =>
        jsonResponse(report({ range: '30d', daily: Array.from({ length: 30 }, (_, k) => ({ date: `2026-09-${String(k + 1).padStart(2, '0')}`, orders: 0, revenue: 0 })), top_products: [] })),
    });
    await renderApp('/admin/reports');
    expect(await screen.findByText('Báo cáo hôm nay')).toBeInTheDocument();
    expect(screen.getByText('25.0%')).toHaveAttribute('data-warn', 'true'); // more than 10% of the photos failed
    expect(screen.getByText('8/50')).toHaveAttribute('data-warn', 'true');
    expect(screen.getByText('38/50')).not.toHaveAttribute('data-warn');
    expect(screen.getByText('200 ms')).toBeInTheDocument();
    const bars = screen.getAllByTestId('day-bar');
    expect(bars).toHaveLength(7);
    expect(bars[6]).toHaveAttribute('title', '2026-10-07: 47.000đ · 3 đơn');
    expect(screen.getByRole('cell', { name: 'Sữa rửa mặt' })).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('Khoảng báo cáo'), '30d');
    expect(await screen.findByText('Chưa có đơn nào trong khoảng này.')).toBeInTheDocument();
    expect(screen.getAllByTestId('day-bar')).toHaveLength(30);
    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/admin/reports?range=30d')).toBe(true);
  });
});

describe('admin orders', () => {
  it("lists every cashier's orders and voids one from its detail", async () => {
    const calls: Call[] = [];
    const now = new Date().toISOString();
    const paid = {
      id: 7,
      status: 'paid',
      created_at: now,
      paid_at: now,
      payment_method: 'cash',
      cash_given: 50000,
      change_given: 3000,
      items: [],
      captures: [],
      item_count: 4,
      total: 47000,
      missing_price_count: 0,
      flagged_count: 0,
    };
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/orders?range=today': () =>
        jsonResponse({
          items: [{ id: 7, status: 'paid', created_at: now, paid_at: now, total: 47000, item_count: 4, payment_method: 'cash', cashier: 'Thu ngân A' }],
        }),
      '/api/orders/7/void': byMethod({ POST: () => jsonResponse({ ...paid, status: 'void' }) }, calls),
      '/api/orders/7': () => jsonResponse(paid),
    });
    await renderApp('/admin/orders');
    const row = (await screen.findByText('Thu ngân A')).closest('tr')!;
    expect(row).toHaveTextContent('#7');
    expect(row).toHaveTextContent('47.000đ');
    await userEvent.click(within(row).getByRole('button', { name: 'Chi tiết' }));
    const detail = await screen.findByRole('dialog', { name: 'Đơn #7' });
    await userEvent.click(await within(detail).findByRole('button', { name: 'Huỷ đơn này' }));
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Xác nhận' })).getByRole('button', { name: 'Huỷ đơn' }));
    await waitFor(() => expect(calls).toEqual([{ method: 'POST', body: undefined }]));
  });
});

describe('admin users', () => {
  const members = [
    { id: 1, username: 'staff', full_name: 'Thu ngân demo', role: 'staff', is_active: true, online: true },
    { id: 2, username: 'admin', full_name: 'Quản trị', role: 'admin', is_active: true, online: false },
    { id: 3, username: 'cu', full_name: '', role: 'staff', is_active: false, online: false },
  ];

  function stubUsers(calls: Call[], create: () => Response = () => jsonResponse(members[0])) {
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/admin/users/1': byMethod({ PATCH: () => jsonResponse(members[0]) }, calls),
      '/api/admin/users': byMethod({ GET: () => jsonResponse({ items: members }), POST: create }, calls),
    });
  }

  it('shows who is on shift, locked or just active', async () => {
    stubUsers([]);
    await renderApp('/admin/users');
    expect(await screen.findByTestId('staff-staff')).toHaveTextContent('Đang trong ca');
    expect(screen.getByTestId('staff-admin')).toHaveTextContent('Hoạt động');
    expect(screen.getByTestId('staff-cu')).toHaveTextContent('Đã khoá');
    expect(within(screen.getByTestId('staff-cu')).getByRole('button', { name: 'Mở khoá' })).toBeInTheDocument();
  });

  it('adds a member, locks one, and resets a password typed twice', async () => {
    const calls: Call[] = [];
    stubUsers(calls);
    await renderApp('/admin/users');
    await screen.findByTestId('staff-staff');
    await userEvent.type(screen.getByLabelText('Tài khoản'), 'thu.ngan1');
    await userEvent.type(screen.getByLabelText('Họ tên'), 'Ngân');
    await userEvent.type(screen.getByLabelText('Mật khẩu'), 'matkhau123');
    await userEvent.click(screen.getByRole('button', { name: 'Thêm' }));
    await waitFor(() =>
      expect(calls[0]).toEqual({ method: 'POST', body: { username: 'thu.ngan1', full_name: 'Ngân', password: 'matkhau123', role: 'staff' } }),
    );

    const row = screen.getByTestId('staff-staff');
    await userEvent.click(within(row).getByRole('button', { name: 'Khoá' }));
    await waitFor(() => expect(calls).toContainEqual({ method: 'PATCH', body: { is_active: false } }));

    await userEvent.click(within(row).getByRole('button', { name: 'Đặt lại MK' }));
    const dialog = await screen.findByRole('dialog', { name: 'Đặt lại mật khẩu cho staff' });
    await userEvent.type(within(dialog).getByLabelText('Mật khẩu mới'), 'matkhau-moi-1');
    await userEvent.type(within(dialog).getByLabelText('Nhập lại mật khẩu mới'), 'khac');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Đặt lại' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Hai lần nhập không khớp');
    await userEvent.clear(within(dialog).getByLabelText('Nhập lại mật khẩu mới'));
    await userEvent.type(within(dialog).getByLabelText('Nhập lại mật khẩu mới'), 'matkhau-moi-1');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Đặt lại' }));
    await waitFor(() => expect(calls).toContainEqual({ method: 'PATCH', body: { password: 'matkhau-moi-1' } }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('keeps the form when the server refuses the new account', async () => {
    const calls: Call[] = [];
    stubUsers(calls, () => apiError(409, 'USER_EXISTS', 'Tài khoản đã tồn tại'));
    await renderApp('/admin/users');
    await screen.findByTestId('staff-staff');
    await userEvent.type(screen.getByLabelText('Tài khoản'), 'staff');
    await userEvent.type(screen.getByLabelText('Mật khẩu'), 'matkhau123');
    await userEvent.click(screen.getByRole('button', { name: 'Thêm' }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(screen.getByLabelText('Tài khoản')).toHaveValue('staff'); // not cleared: the admin fixes and retries
  });
});

describe('admin settings', () => {
  it('saves the switches and the thresholds, null meaning the engine default', async () => {
    const calls: Call[] = [];
    const stored = meBody('admin').settings;
    stubFetchRoutes({
      '/api/me': adminMe,
      '/api/settings': () => jsonResponse(stored),
      '/api/admin/settings': byMethod({ PATCH: (body) => jsonResponse({ ...stored, ...(body as object) }) }, calls),
    });
    await renderApp('/admin/settings');
    const tilt = await screen.findByRole('switch', { name: 'Chặn chụp khi máy nghiêng' });
    expect(tilt).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(tilt);
    expect(tilt).toHaveAttribute('aria-checked', 'true');

    const similarity = screen.getByLabelText('Ngưỡng nhận diện');
    expect(similarity).toBeDisabled(); // the engine's default
    await userEvent.click(screen.getAllByLabelText('Dùng mặc định của hệ thống')[0]);
    expect(similarity).toBeEnabled();
    fireEvent.change(similarity, { target: { value: '0.72' } });

    await userEvent.click(screen.getByRole('button', { name: 'Lưu cài đặt' }));
    await waitFor(() =>
      expect(calls).toEqual([
        {
          method: 'PATCH',
          // only what changed: the server logs every key it receives
          body: { similarity_threshold: 0.72, tilt_block_capture: true },
        },
      ]),
    );
  });
});
