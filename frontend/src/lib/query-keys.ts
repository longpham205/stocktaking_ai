/** Every TanStack Query key in one place, so an invalidation names exactly what it refreshes. */
export const qk = {
  me: () => ['me'] as const,
  health: () => ['health'] as const,
};
