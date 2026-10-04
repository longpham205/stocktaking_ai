import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ConfigItem, EngineConfig, Validation } from '@/features/admin-advanced/advanced-page';
import { clearToken, setToken } from '@/lib/auth-token';
import { apiError, jsonResponse, meBody, renderApp, stubFetchRoutes } from '@/test/test-utils';

const ITEMS: ConfigItem[] = [
  { key: 'retrieval.backend', label: 'Truy hồi ảnh', group: 'Mô hình', tier: 'readonly', type: 'str', default: 'siglip2', value: 'siglip2', overridden: false },
  { key: 'retrieval.top_k', label: 'Số ứng viên truy hồi (Top-K)', group: 'Quyết định', tier: 'reload', type: 'int', min: 1, max: 20, default: 5, value: 5, overridden: false },
  { key: 'detection.confidence_threshold', label: 'Ngưỡng tin cậy detector', group: 'Phát hiện', tier: 'reload', type: 'float', min: 0.05, max: 0.95, default: 0.5, value: 0.6, overridden: true },
  { key: 'plugins.ocr.enabled', label: 'Bật OCR', group: 'Plugin', tier: 'reload', type: 'bool', default: true, value: true, overridden: false },
];

const config = (overrides: Partial<EngineConfig> = {}): EngineConfig => ({
  items: ITEMS,
  reloading: false,
  config_error: null,
  advanced_password_set: true,
  pipeline_config: 'config.demo.yaml',
  ...overrides,
});

interface Call {
  url: string;
  body: unknown;
}

function post(calls: Call[], url: string, answer: (body: unknown) => Response) {
  return (init?: RequestInit) => {
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    calls.push({ url, body });
    return answer(body);
  };
}

function stub(calls: Call[], overrides: { config?: EngineConfig; validation?: Validation; apply?: () => Response } = {}) {
  return stubFetchRoutes({
    '/api/me': () => jsonResponse(meBody('admin')),
    '/api/admin/config/apply': post(calls, 'apply', overrides.apply ?? (() => jsonResponse({ ...config(), applied: true }))),
    '/api/admin/config': () => jsonResponse(overrides.config ?? config()),
    '/api/admin/validation': (init) =>
      init?.method === 'POST'
        ? post(calls, 'validation', () => jsonResponse({ status: 'running', started_at: '2026-10-04T03:00:00+00:00', by: 'admin' }))(init)
        : jsonResponse(overrides.validation ?? { status: 'idle', baseline: { f1: 0.5912, fusion_accuracy: 0.6804 } }),
    '/api/admin/change-log?table=config': () =>
      jsonResponse({
        items: [
          { id: 3, table: 'config', record_id: 'detection.confidence_threshold', field: 'detection.confidence_threshold', old: null, new: '0.6', by: 'admin', at: '2026-10-04T03:00:00+00:00', name: null },
        ],
      }),
    '/api/admin/change-log/3/revert': post(calls, 'revert', () => jsonResponse({ ok: true })),
  });
}

beforeEach(() => setToken('t'));
afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

describe('advanced settings', () => {
  it('shows read-only and editable settings, and applies a change with the password and a confirmation', async () => {
    const calls: Call[] = [];
    stub(calls);
    await renderApp('/admin/advanced');
    expect(await screen.findByText('(config.demo.yaml)')).toBeInTheDocument();
    expect(within(screen.getByTestId('config-retrieval.backend')).getByText('🔒 chỉ xem')).toBeInTheDocument();
    expect(within(screen.getByTestId('config-retrieval.backend')).queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(within(screen.getByTestId('config-detection.confidence_threshold')).getByText('đã sửa · gốc: 0.5')).toBeInTheDocument();

    const topK = screen.getByLabelText('Số ứng viên truy hồi (Top-K)');
    await userEvent.clear(topK);
    await userEvent.type(topK, '7');
    await userEvent.click(screen.getByLabelText('Bật OCR'));
    await userEvent.click(screen.getByRole('button', { name: 'Áp dụng thay đổi…' }));

    const dialog = await screen.findByRole('dialog', { name: 'Áp dụng thiết lập nâng cao' });
    expect(within(dialog).getAllByTestId('pending-change').map((row) => row.textContent)).toEqual([
      'Số ứng viên truy hồi (Top-K): 5 → 7',
      'Bật OCR: bật → tắt',
    ]);
    const apply = within(dialog).getByRole('button', { name: 'Áp dụng' });
    expect(apply).toBeDisabled(); // the confirmation is not ticked
    await userEvent.type(within(dialog).getByLabelText('Mật khẩu nâng cao'), 'advanced-pass');
    await userEvent.click(within(dialog).getByLabelText(/Tôi hiểu thay đổi này/));
    await userEvent.click(apply);
    await waitFor(() =>
      expect(calls).toEqual([
        {
          url: 'apply',
          body: { changes: { 'retrieval.top_k': 7, 'plugins.ocr.enabled': false }, advanced_password: 'advanced-pass', confirm: true },
        },
      ]),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('resets an overridden setting to the YAML value, and keeps the dialog when the password is wrong', async () => {
    const calls: Call[] = [];
    stub(calls, { apply: () => apiError(403, 'ADVANCED_PASSWORD_INVALID', 'Sai mật khẩu nâng cao') });
    await renderApp('/admin/advanced');
    await userEvent.click(await screen.findByRole('button', { name: 'Về giá trị gốc: Ngưỡng tin cậy detector' }));
    const dialog = await screen.findByRole('dialog', { name: 'Áp dụng thiết lập nâng cao' });
    expect(within(dialog).getByTestId('pending-change')).toHaveTextContent('Ngưỡng tin cậy detector: 0.6 → giá trị gốc (0.5)');
    await userEvent.type(within(dialog).getByLabelText('Mật khẩu nâng cao'), 'sai');
    await userEvent.click(within(dialog).getByLabelText(/Tôi hiểu thay đổi này/));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Áp dụng' }));
    await waitFor(() => expect(calls[0]?.body).toMatchObject({ changes: { 'detection.confidence_threshold': null } }));
    expect(screen.getByRole('dialog', { name: 'Áp dụng thiết lập nâng cao' })).toBeInTheDocument(); // refused: still open
  });

  it('warns when no advanced password is set or the stored overrides no longer fit', async () => {
    stub([], { config: config({ advanced_password_set: false, config_error: 'retrieval.top_k: không hợp lệ', reloading: true }) });
    await renderApp('/admin/advanced');
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.map((alert) => alert.textContent)).toEqual([
      expect.stringContaining('không còn hợp lệ với file config: retrieval.top_k'),
      expect.stringContaining('make reset-advanced-password'),
    ]);
    expect(screen.getByText('⏳ Đang nạp lại pipeline…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Áp dụng thay đổi…' })).toBeDisabled();
  });

  it('reverts a setting from its history with the advanced password', async () => {
    const calls: Call[] = [];
    stub(calls);
    await renderApp('/admin/advanced');
    await userEvent.click(await screen.findByRole('button', { name: /Lịch sử/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Lịch sử thiết lập nâng cao' });
    const entry = await within(dialog).findByTestId('config-entry');
    expect(entry).toHaveTextContent('detection.confidence_threshold: gốc → 0.6');
    await userEvent.click(within(entry).getByRole('button', { name: 'Hoàn tác' }));
    expect(calls).toEqual([]); // no password typed: nothing sent
    await userEvent.type(within(dialog).getByLabelText('Mật khẩu nâng cao (để hoàn tác)'), 'advanced-pass');
    await userEvent.click(within(entry).getByRole('button', { name: 'Hoàn tác' }));
    await waitFor(() => expect(calls).toEqual([{ url: 'revert', body: { advanced_password: 'advanced-pass' } }]));
  });
});

describe('validation', () => {
  it('compares a finished run with the baseline', async () => {
    stub([], {
      validation: {
        status: 'done',
        finished_at: '2026-10-04T04:00:00+00:00',
        result: { f1: 0.6, fusion_accuracy: 0.66 },
        baseline: { f1: 0.5912, fusion_accuracy: 0.6804 },
      },
    });
    await renderApp('/admin/advanced');
    const card = await screen.findByTestId('validation');
    expect(card).toHaveTextContent('F1 end-to-end: 60.00% (+0.88 điểm so với baseline)');
    expect(card).toHaveTextContent('Độ chính xác sau hợp nhất: 66.00% (-2.04 điểm so với baseline)');
    expect(card).toHaveTextContent('Baseline: F1 59.12% · sau hợp nhất 68.04%');
  });

  it('starts a run after the password and a confirmation, then shows it running', async () => {
    const calls: Call[] = [];
    stub(calls);
    await renderApp('/admin/advanced');
    expect(await screen.findByTestId('validation')).toHaveTextContent('Chưa chạy.');
    await userEvent.click(screen.getByRole('button', { name: 'Chạy kiểm định…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Kiểm định độ chính xác' });
    const start = within(dialog).getByRole('button', { name: 'Bắt đầu' });
    expect(start).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText('Mật khẩu nâng cao'), 'advanced-pass');
    await userEvent.click(within(dialog).getByLabelText(/Tôi hiểu hệ thống ngừng nhận diện/));
    await userEvent.click(start);
    await waitFor(() => expect(calls).toEqual([{ url: 'validation', body: { confirm: true, advanced_password: 'advanced-pass' } }]));
    await waitFor(() => expect(screen.getByTestId('validation')).toHaveTextContent('Đang kiểm định'));
    expect(screen.getByRole('button', { name: 'Chạy kiểm định…' })).toBeDisabled();
  });
});
