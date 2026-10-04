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
import { getOpenOrder } from '@/features/pos/api';
import { CapturePage } from '@/features/pos/capture-page';
import { DonePage } from '@/features/pos/done-page';
import { HistoryPage } from '@/features/pos/history-page';
import { InvoicePage } from '@/features/pos/invoice-page';
import { OnboardingPage } from '@/features/pos/onboarding-page';
import { PayPage } from '@/features/pos/pay-page';
import { ApiError } from '@/lib/api-client';
import { getToken } from '@/lib/auth-token';
import { qk } from '@/lib/query-keys';
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

/** `/pos`: a new basket. An open order with lines left from before (a reload, a new login) is resumed. */
const posRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/pos',
  beforeLoad: ({ context }) => {
    if (context.me.user.role === 'staff' && !context.me.user.has_seen_onboarding) throw redirect({ to: '/onboarding' });
  },
  loader: async ({ context }) => {
    const open = await context.queryClient.fetchQuery({ queryKey: qk.openOrder(), queryFn: getOpenOrder, staleTime: 0 });
    if (open && open.items.length > 0) {
      context.queryClient.setQueryData(qk.order(open.id), open);
      throw redirect({ to: '/pos/orders/$orderId', params: { orderId: String(open.id) }, search: { resumed: true } });
    }
  },
  component: () => <CapturePage />,
});

const onboardingRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/onboarding',
  component: OnboardingPage,
});

const orderId = (params: { orderId: string }) => Number(params.orderId);

const invoiceRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/pos/orders/$orderId',
  validateSearch: (search: Record<string, unknown>): { job?: number; resumed?: boolean } => ({
    job: search.job === undefined ? undefined : Number(search.job),
    resumed: search.resumed === true || search.resumed === 'true' ? true : undefined,
  }),
  component: function Invoice() {
    const { job, resumed } = invoiceRoute.useSearch();
    return <InvoicePage orderId={orderId(invoiceRoute.useParams())} jobId={job} resumed={resumed} />;
  },
});

const moreCaptureRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/pos/orders/$orderId/capture',
  component: function MoreCapture() {
    return <CapturePage orderId={orderId(moreCaptureRoute.useParams())} />;
  },
});

const payRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/pos/orders/$orderId/pay',
  component: function Pay() {
    return <PayPage orderId={orderId(payRoute.useParams())} />;
  },
});

const doneRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/pos/orders/$orderId/done',
  validateSearch: (search: Record<string, unknown>): { fresh?: boolean } => ({
    fresh: search.fresh === true || search.fresh === 'true' ? true : undefined,
  }),
  component: function Done() {
    return <DonePage orderId={orderId(doneRoute.useParams())} fresh={doneRoute.useSearch().fresh} />;
  },
});

const historyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/history',
  component: HistoryPage,
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
    onboardingRoute,
    invoiceRoute,
    moreCaptureRoute,
    payRoute,
    doneRoute,
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
