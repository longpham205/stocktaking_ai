import { clearToken, getToken } from '@/lib/auth-token';
import { errorMessage } from '@/lib/errors';

/**
 * Thrown by `apiFetch` for any non-2xx response. The API answers errors as
 * `{"detail": "<Vietnamese text>", "code": "<STABLE_CODE>", ...extra}`: `code` is what the app
 * branches on, `message` is what the user reads (the app's own wording for a known code, else
 * the server's `detail`).
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string | undefined;
  /** Every other field of the error body (`retry_after`, `missing`, `errors`, `confirm_text`…). */
  readonly extra: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown> | null) {
    const code = typeof body?.code === 'string' ? body.code : `HTTP_${status}`;
    const detail = typeof body?.detail === 'string' ? body.detail : undefined;
    super(errorMessage(code, detail, status));
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
    const { code: _code, detail: _detail, ...extra } = body ?? {};
    this.extra = extra;
  }
}

export interface ApiFetchOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  /** A plain object is sent as JSON; a Blob (a photo) as the raw body. */
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/**
 * Fetch from the `/api` backend with the login token. Paths are relative: the Vite/nginx proxy
 * makes the API same-origin. A 401 means the session is over (expired, or logged in on another
 * device): the token is dropped, which sends the app back to the login page.
 */
export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { method = 'GET', body, headers = {}, signal } = options;
  const finalHeaders: Record<string, string> = { ...headers };
  const token = getToken();
  if (token) finalHeaders.Authorization = `Bearer ${token}`;
  let requestBody: BodyInit | undefined;
  if (body instanceof Blob) {
    finalHeaders['Content-Type'] ??= body.type || 'application/octet-stream';
    requestBody = body;
  } else if (body !== undefined) {
    finalHeaders['Content-Type'] = 'application/json';
    requestBody = JSON.stringify(body);
  }

  const response = await fetch(path, { method, headers: finalHeaders, body: requestBody, signal });
  const contentType = response.headers.get('content-type') ?? '';
  const payload = contentType.includes('application/json') ? await response.json().catch(() => null) : null;
  if (!response.ok) {
    if (response.status === 401 && token) clearToken();
    throw new ApiError(response.status, payload as Record<string, unknown> | null);
  }
  return payload as T;
}
