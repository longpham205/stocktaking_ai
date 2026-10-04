import { vi, type Mock } from 'vitest';
import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, RouterProvider } from '@tanstack/react-router';
import { createAppRouter } from '@/router';

export interface RenderAppResult extends RenderResult {
  queryClient: QueryClient;
  router: ReturnType<typeof createAppRouter>;
}

/**
 * The whole app (real routes and guards) at `path`, with a fresh QueryClient: TanStack Query
 * caches by key, so a client shared across tests would leak one test's response into the next.
 */
export async function renderApp(path: string): Promise<RenderAppResult> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createAppRouter(queryClient, createMemoryHistory({ initialEntries: [path] }));
  await router.load();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return Object.assign(result, { queryClient, router });
}

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
    ...init,
  });
}

export function apiError(status: number, code: string, detail: string, extra: Record<string, unknown> = {}): Response {
  return jsonResponse({ detail, code, ...extra }, { status });
}

/** URL-dispatching fetch stub: keys match by exact URL or prefix, longest key wins. */
export function stubFetchRoutes(routes: Record<string, (init?: RequestInit) => Response>): Mock<typeof fetch> {
  const paths = Object.keys(routes).sort((a, b) => b.length - a.length);
  const mock = vi.fn<typeof fetch>((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const match = paths.find((path) => url === path || url.startsWith(path));
    if (!match) return Promise.reject(new Error(`unexpected fetch url: ${url}`));
    return Promise.resolve(routes[match](init));
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

export function meBody(role: 'staff' | 'admin') {
  return {
    user: { id: 1, username: role, full_name: role === 'admin' ? 'Quản trị' : 'Thu ngân demo', role, has_seen_onboarding: true },
    shift: { id: 7, started_at: '2026-10-04T01:00:00+00:00', total_collected: 0 },
    settings: {
      allow_checkout_without_price: false,
      similarity_threshold: null,
      min_confidence_accept: null,
      tilt_block_capture: false,
      auto_print_receipt: false,
    },
  };
}
