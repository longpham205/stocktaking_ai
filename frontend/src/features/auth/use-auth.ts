import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getMe, login, logout } from '@/lib/api';
import { clearToken, setToken } from '@/lib/auth-token';
import { qk } from '@/lib/query-keys';

/** Who is logged in, their shift and the shop settings. Read by the route guard and the shell. */
export const meQuery = queryOptions({ queryKey: qk.me(), queryFn: getMe, staleTime: 60_000 });

export function useMe() {
  return useQuery(meQuery);
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) => login(username, password),
    onSuccess: (result) => {
      queryClient.removeQueries({ queryKey: qk.me() }); // the previous account's, if any
      setToken(result.token);
    },
  });
}

/** Ends the shift on the server, then forgets the token (the app goes back to the login page). */
export function useLogout() {
  return useMutation({
    mutationFn: async () => {
      try {
        await logout();
      } finally {
        clearToken();
      }
    },
  });
}
