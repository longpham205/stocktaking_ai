import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/lib/api-client';

/** Statuses retrying cannot fix: the session, permission, not found, conflict, validation. */
const NO_RETRY_STATUSES = new Set([401, 403, 404, 409, 422]);

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          if (error instanceof ApiError && NO_RETRY_STATUSES.has(error.status)) return false;
          return failureCount < 2;
        },
        staleTime: 5_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
