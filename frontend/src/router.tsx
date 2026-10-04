import type { QueryClient } from '@tanstack/react-query';
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  type RouterHistory,
} from '@tanstack/react-router';
import { AppShell } from '@/components/app-shell';
import { ComingSoon, RouteError, RouteNotFound, RoutePending } from '@/components/route-states';
import { LoginPage } from '@/features/auth/login-page';
import { meQuery } from '@/features/auth/use-auth';
import { ApiError } from '@/lib/api-client';
import { getToken } from '@/lib/auth-token';
import type { MeOut } from '@/lib/types';

export interface RouterContext {
  queryClient: QueryClient;
}

// Code-based routes (no codegen plugin, no generated route tree), like the reference app.
const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: RouteNotFound,
  errorComponent: RouteError,
  pendingComponent: RoutePending,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  beforeLoad: () => {
    if (getToken()) throw redirect({ to: '/' });
  },
  component: LoginPage,
});

/** Everything behind the login: the guard reads who is calling once and hands it to the children. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  beforeLoad: async ({ context }): Promise<{ me: MeOut }> => {
    if (!getToken()) throw redirect({ to: '/login' });
    try {
      return { me: await context.queryClient.ensureQueryData(meQuery) };
    } catch (error) {
      // the session ended (apiFetch already dropped the token): log in again
      if (error instanceof ApiError && error.status === 401) throw redirect({ to: '/login' });
      throw error;
    }
  },
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
});

/** `/`: each role's home. */
const indexRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/',
  beforeLoad: ({ context }) => {
    throw redirect({ to: context.me.user.role === 'admin' ? '/admin/reports' : '/pos' });
  },
});

const posRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/pos',
  component: () => <ComingSoon title="Bán hàng" />,
});

const historyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/history',
  component: () => <ComingSoon title="Lịch sử bán hàng" />,
});

/** `/admin/*`: admins only; a cashier who types the address lands on the POS. */
const adminRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/admin',
  beforeLoad: ({ context }) => {
    if (context.me.user.role !== 'admin') throw redirect({ to: '/pos' });
  },
  component: Outlet,
});

const adminIndexRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/admin/reports' });
  },
});

const ADMIN_TABS = [
  ['reports', 'Báo cáo'],
  ['products', 'Sản phẩm'],
  ['orders', 'Đơn hàng'],
  ['users', 'Nhân viên'],
  ['settings', 'Cài đặt'],
  ['advanced', 'Thiết lập nâng cao'],
] as const;

const adminTabRoutes = ADMIN_TABS.map(([path, title]) =>
  createRoute({ getParentRoute: () => adminRoute, path, component: () => <ComingSoon title={title} /> }),
);

const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([
    indexRoute,
    posRoute,
    historyRoute,
    adminRoute.addChildren([adminIndexRoute, ...adminTabRoutes]),
  ]),
]);

/** `history`: the tests pass a memory history; the app uses the browser's. */
export function createAppRouter(queryClient: QueryClient, history?: RouterHistory) {
  return createRouter({ routeTree, context: { queryClient }, history, defaultPreload: 'intent' });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
