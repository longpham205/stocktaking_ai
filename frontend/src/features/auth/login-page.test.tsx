import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { clearToken, getToken } from '@/lib/auth-token';
import { apiError, jsonResponse, meBody, renderApp, stubFetchRoutes } from '@/test/test-utils';

afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

async function submit(username: string, password: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText('Tài khoản'), username);
  await user.type(screen.getByLabelText('Mật khẩu'), password);
  await user.click(screen.getByRole('button', { name: 'Đăng nhập' }));
}

describe('LoginPage', () => {
  it('logs in, keeps the token and opens the role home', async () => {
    const fetchMock = stubFetchRoutes({
      '/api/auth/login': () => jsonResponse({ token: 'tok-1', user: meBody('staff').user }),
      '/api/me': () => jsonResponse(meBody('staff')),
      '/api/orders/open': () => jsonResponse({ order: null }),
    });
    const { router } = await renderApp('/login');
    await submit('staff', 'staff-pass-123');
    await waitFor(() => expect(router.state.location.pathname).toBe('/pos'));
    expect(getToken()).toBe('tok-1');
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]!.body))).toEqual({ username: 'staff', password: 'staff-pass-123' });
  });

  it('says why a login was refused', async () => {
    stubFetchRoutes({ '/api/auth/login': () => apiError(401, 'AUTH_INVALID', 'Sai tài khoản hoặc mật khẩu') });
    await renderApp('/login');
    await submit('staff', 'sai');
    expect(await screen.findByRole('alert')).toHaveTextContent('Sai tài khoản hoặc mật khẩu');
    expect(getToken()).toBeNull();
  });

  it('tells a locked-out cashier how long to wait', async () => {
    stubFetchRoutes({
      '/api/auth/login': () => apiError(429, 'RATE_LIMITED', 'Đăng nhập sai quá nhiều lần', { retry_after: 241 }),
    });
    await renderApp('/login');
    await submit('staff', 'sai');
    expect(await screen.findByRole('alert')).toHaveTextContent('thử lại sau 5 phút');
  });
});
