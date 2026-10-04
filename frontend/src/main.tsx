import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { Toaster } from 'sonner';
import { getToken, onTokenChange } from '@/lib/auth-token';
import { createQueryClient } from '@/lib/query-client';
import { createAppRouter } from '@/router';
import './index.css';

const queryClient = createQueryClient();
const router = createAppRouter(queryClient);

// The token went away (logout, or a 401 anywhere): forget the account's data, back to the login page.
onTokenChange(() => {
  if (getToken()) return;
  queryClient.clear();
  void router.navigate({ to: '/login' });
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster richColors position="top-center" />
    </QueryClientProvider>
  </StrictMode>,
);
