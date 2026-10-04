/** Every TanStack Query key in one place, so an invalidation names exactly what it refreshes. */
export const qk = {
  me: () => ['me'] as const,
  health: () => ['health'] as const,
  openOrder: () => ['orders', 'open'] as const,
  order: (orderId: number) => ['orders', orderId] as const,
  job: (jobId: number) => ['jobs', jobId] as const,
  products: (search: string) => ['products', search] as const,
  history: (range: string) => ['history', range] as const,
  settings: () => ['settings'] as const,
  adminReport: (range: string) => ['admin', 'report', range] as const,
  adminOrders: (range: string) => ['admin', 'orders', range] as const,
  adminUsers: () => ['admin', 'users'] as const,
  adminProducts: (search: string, filter: string, page: number) => ['admin', 'products', search, filter, page] as const,
  adminEvidence: (productId: string) => ['admin', 'evidence', productId] as const,
  adminChangeLog: (scope: string) => ['admin', 'change-log', scope] as const,
  adminConfig: () => ['admin', 'config'] as const,
  adminValidation: () => ['admin', 'validation'] as const,
};
