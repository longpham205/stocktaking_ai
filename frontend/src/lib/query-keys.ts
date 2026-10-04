/** Every TanStack Query key in one place, so an invalidation names exactly what it refreshes. */
export const qk = {
  me: () => ['me'] as const,
  health: () => ['health'] as const,
  openOrder: () => ['orders', 'open'] as const,
  order: (orderId: number) => ['orders', orderId] as const,
  job: (jobId: number) => ['jobs', jobId] as const,
  products: (search: string) => ['products', search] as const,
  history: (range: string) => ['history', range] as const,
};
