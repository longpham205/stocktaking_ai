import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiFetch } from '@/lib/api-client';
import { clearToken, getToken, setToken } from '@/lib/auth-token';
import { apiError, jsonResponse, stubFetchRoutes } from '@/test/test-utils';

afterEach(() => {
  vi.unstubAllGlobals();
  clearToken();
});

describe('apiFetch', () => {
  it('sends the token, a JSON body, and returns the JSON', async () => {
    setToken('tok-123');
    const fetchMock = stubFetchRoutes({ '/api/orders': () => jsonResponse({ id: 5 }) });
    await expect(apiFetch('/api/orders', { method: 'POST', body: { a: 1 } })).resolves.toEqual({ id: 5 });
    const init = fetchMock.mock.calls[0][1]!;
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok-123', 'Content-Type': 'application/json' });
    expect(init.body).toBe('{"a":1}');
  });

  it('sends a photo as the raw body', async () => {
    const fetchMock = stubFetchRoutes({ '/api/orders/1/captures': () => jsonResponse({ job_id: 3 }, { status: 202 }) });
    const photo = new Blob([new Uint8Array([0xff, 0xd8])], { type: 'image/jpeg' });
    await apiFetch('/api/orders/1/captures', { method: 'POST', body: photo });
    const init = fetchMock.mock.calls[0][1]!;
    expect(init.body).toBe(photo);
    expect(init.headers).toMatchObject({ 'Content-Type': 'image/jpeg' });
  });

  it('turns an error body into an ApiError with the code, the message and the extra fields', async () => {
    stubFetchRoutes({
      '/api/orders/1/checkout': () =>
        apiError(409, 'PRICE_MISSING_BLOCKED', 'Còn sản phẩm chưa có giá, hãy nhập giá tay', { missing: 2 }),
    });
    const error = await apiFetch('/api/orders/1/checkout', { method: 'POST', body: {} }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiErr = error as ApiError;
    expect([apiErr.status, apiErr.code, apiErr.extra]).toEqual([409, 'PRICE_MISSING_BLOCKED', { missing: 2 }]);
    expect(apiErr.message).toMatch(/nhập giá tay/);
  });

  it('keeps the server detail for a validation error, and words a server crash itself', async () => {
    stubFetchRoutes({
      '/api/a': () => apiError(422, 'VALIDATION_ERROR', "'quantity' phải trong khoảng 1..999"),
      '/api/b': () => new Response('boom', { status: 500 }),
    });
    await expect(apiFetch('/api/a')).rejects.toThrow("'quantity' phải trong khoảng 1..999");
    await expect(apiFetch('/api/b')).rejects.toMatchObject({ code: 'HTTP_500', message: 'Máy chủ gặp lỗi, thử lại sau' });
  });

  it('drops the token on a 401', async () => {
    setToken('old');
    stubFetchRoutes({ '/api/me': () => apiError(401, 'SESSION_INVALID', 'Tài khoản đã đăng nhập ở thiết bị khác') });
    await expect(apiFetch('/api/me')).rejects.toMatchObject({ status: 401, code: 'SESSION_INVALID' });
    expect(getToken()).toBeNull();
  });
});
