import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { clearToken, setToken } from '@/lib/auth-token';
import { apiError, jsonResponse, meBody, renderApp, stubFetchRoutes } from '@/test/test-utils';

afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

describe('route guards', () => {
  it('sends a visitor without a token to the login page', async () => {
    stubFetchRoutes({});
    const { router } = await renderApp('/pos');
    expect(router.state.location.pathname).toBe('/login');
    expect(await screen.findByRole('button', { name: 'Đăng nhập' })).toBeInTheDocument();
  });

  it('opens each role on its home, with its own navigation', async () => {
    setToken('t');
    stubFetchRoutes({ '/api/me': () => jsonResponse(meBody('staff')), '/api/orders/open': () => jsonResponse({ order: null }) });
    const staff = await renderApp('/');
    expect(staff.router.state.location.pathname).toBe('/pos');
    expect(await screen.findByRole('link', { name: /Lịch sử/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Báo cáo/ })).not.toBeInTheDocument();
    staff.unmount();

    // the admin home asks for the report: an error answer is enough for this test (it only checks the shell)
    stubFetchRoutes({
      '/api/me': () => jsonResponse(meBody('admin')),
      '/api/admin/reports': () => apiError(503, 'DB_UNAVAILABLE', 'x'),
    });
    const admin = await renderApp('/');
    expect(admin.router.state.location.pathname).toBe('/admin/reports');
    expect(await screen.findByRole('link', { name: /Báo cáo/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Bán hàng/ })).toBeInTheDocument();
  });

  it('keeps a cashier out of the admin pages', async () => {
    setToken('t');
    stubFetchRoutes({ '/api/me': () => jsonResponse(meBody('staff')), '/api/orders/open': () => jsonResponse({ order: null }) });
    const { router } = await renderApp('/admin/users');
    expect(router.state.location.pathname).toBe('/pos');
  });

  it('goes back to the login page when the session has ended', async () => {
    setToken('stale');
    stubFetchRoutes({ '/api/me': () => apiError(401, 'SESSION_INVALID', 'Ca đã kết thúc') });
    const { router } = await renderApp('/history');
    expect(router.state.location.pathname).toBe('/login');
  });

  it('shows a not-found page for an unknown address', async () => {
    setToken('t');
    stubFetchRoutes({ '/api/me': () => jsonResponse(meBody('staff')), '/api/orders/open': () => jsonResponse({ order: null }) });
    await renderApp('/khong-co');
    expect(await screen.findByText('Không có trang này')).toBeInTheDocument();
  });
});
